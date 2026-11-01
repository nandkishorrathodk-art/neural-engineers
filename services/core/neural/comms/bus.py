import asyncio
import json
import uuid
from typing import Any, Callable, Coroutine, Optional, Union

from neural.comms.messages import (
    BaseMessage,
    BroadcastMessage,
    DepartmentBroadcastMessage,
    DirectMessage,
    MessageType,
    TaskEventMessage,
)
from neural.config import get_settings
from neural.logger import get_logger

logger = get_logger("ancos.comms.bus")


class InMemoryBusBackend:
    """
    In-Memory Pub/Sub Message Broker for Hermetic Testing and Offline Local Dev.
    Matches Redis Pub/Sub semantics.
    """

    def __init__(self) -> None:
        self.subscribers: dict[str, dict[str, asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, channel: str, message_json: str) -> int:
        async with self._lock:
            subs = self.subscribers.get(channel, {})
            count = 0
            for sub_id, queue in list(subs.items()):
                try:
                    queue.put_nowait(message_json)
                    count += 1
                except Exception as e:
                    logger.warning("Failed to deliver message to subscriber queue", sub_id=sub_id, error=str(e))
            return count

    async def subscribe(self, channel: str) -> tuple[str, asyncio.Queue]:
        async with self._lock:
            if channel not in self.subscribers:
                self.subscribers[channel] = {}
            sub_id = str(uuid.uuid4())
            queue: asyncio.Queue = asyncio.Queue()
            self.subscribers[channel][sub_id] = queue
            return sub_id, queue

    async def unsubscribe(self, channel: str, sub_id: str) -> None:
        async with self._lock:
            if channel in self.subscribers and sub_id in self.subscribers[channel]:
                del self.subscribers[channel][sub_id]
                if not self.subscribers[channel]:
                    del self.subscribers[channel]


class MessageBus:
    """
    Inter-Agent Message Bus with a durable production outbox and typed routing.
    Production delivery is at-least-once to Redis Pub/Sub; local tests can use
    the explicitly selected in-memory backend.
    Standard channels:
    - boss:broadcast: Executive broadcast announcements
    - dept:{department}:broadcast: Department-wide announcements
    - employee:{id}:inbox: Direct assignments and 1-to-1 communications
    - tasks:{id}:events: Real-time lifecycle events for a specific task
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        prefer_in_memory: bool = False,
        db_session: Optional[Any] = None,
        session_factory: Optional[Callable[[], Any]] = None
    ):
        self.settings = get_settings()
        self.redis_url = redis_url or self.settings.REDIS_URL
        self.prefer_in_memory = prefer_in_memory
        self.db_session = db_session
        self.session_factory = session_factory
        self.is_connected = False
        self._redis_client: Optional[Any] = None
        self._in_memory_backend = InMemoryBusBackend()
        self._active_listeners: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._outbox_wakeup = asyncio.Event()
        self._outbox_task: Optional[asyncio.Task[None]] = None
        self._outbox_worker_id = f"api-{uuid.uuid4().hex}"

    def set_db_session(self, session: Any) -> None:
        """Sets the active database session for persistent message auditing."""
        self.db_session = session

    async def persist_message(
        self,
        message: Union[BaseMessage, dict[str, Any], str],
        channel: str,
        db_session: Optional[Any] = None,
        delivery_payload: Optional[str] = None,
    ) -> Optional[Any]:
        """
        Persists message to database `messages` audit table using MessageRepository.
        """
        session = db_session or self.db_session
        try:
            from neural.db.repositories import MessageRepository

            if session is not None:
                return await MessageRepository(session).save_message(
                    message,
                    channel=channel,
                    delivery_payload=delivery_payload,
                )
            if self.session_factory is not None:
                async with self.session_factory() as owned_session:
                    return await MessageRepository(owned_session).save_message(
                        message,
                        channel=channel,
                        delivery_payload=delivery_payload,
                    )
            if self.settings.APP_ENV == "production":
                raise RuntimeError("Production message bus requires a database session factory.")
        except Exception as e:
            logger.error("Failed to persist message before delivery", channel=channel, error=str(e))
            if self.settings.APP_ENV == "production":
                raise
        return None

    async def connect(self) -> None:
        """Connects to Redis; production queues through SQL when Redis is unavailable."""
        if self.prefer_in_memory:
            if self.settings.APP_ENV == "production":
                raise RuntimeError("In-memory message bus is prohibited in production.")
            self.is_connected = True
            logger.info("Message bus initialized with in-memory backend (prefer_in_memory=True)")
            return

        try:
            await self._connect_redis()
            self.is_connected = True
            logger.info("Connected to Redis Pub/Sub message bus", url=self.redis_url)
        except Exception as e:
            if self.settings.APP_ENV == "production":
                if self.session_factory is None:
                    self.is_connected = False
                    raise RuntimeError("Production message bus requires the durable database outbox.") from e
                self._redis_client = None
                self.is_connected = True
                logger.warning("Redis unavailable; production messages will remain queued in the database outbox", error=str(e))
                return
            logger.warning(
                "Redis connection failed, falling back to in-memory message bus backend",
                error=str(e)
            )
            self._redis_client = None
            self.is_connected = True

    async def _connect_redis(self) -> None:
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            self.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
        )
        try:
            await client.ping()
        except Exception:
            await client.close()
            raise
        self._redis_client = client

    async def start_outbox_dispatcher(self) -> None:
        if self.settings.APP_ENV != "production":
            raise RuntimeError("The durable outbox dispatcher is reserved for production mode.")
        if self.session_factory is None:
            raise RuntimeError("Outbox dispatcher requires a database session factory.")
        if self._outbox_task and not self._outbox_task.done():
            return
        self._outbox_task = asyncio.create_task(self._outbox_dispatch_loop())

    async def _dispatch_outbox_once(self, batch_size: int = 32) -> int:
        if self._redis_client is None:
            await self._connect_redis()
        if self.session_factory is None or self._redis_client is None:
            raise RuntimeError("Outbox delivery dependencies are unavailable.")

        from neural.db.repositories import OutboxRepository

        async with self.session_factory() as session:
            deliveries = await OutboxRepository(session).claim_ready(
                worker_id=self._outbox_worker_id,
                limit=batch_size,
            )

        delivered = 0
        for item in deliveries:
            try:
                await self._redis_client.publish(item["channel"], item["payload"])
            except Exception as err:
                retry_delay = min(300, 2 ** min(item["attempt_count"], 8))
                async with self.session_factory() as session:
                    status = await OutboxRepository(session).record_failure(
                        event_id=item["event_id"],
                        lease_owner=item["lease_owner"],
                        error=str(err),
                        retry_delay_seconds=retry_delay,
                        max_attempts=12,
                    )
                logger.error(
                    "Outbox event delivery failed",
                    event_id=item["event_id"],
                    status=status,
                    attempt=item["attempt_count"],
                    error=str(err),
                )
                continue

            async with self.session_factory() as session:
                acknowledged = await OutboxRepository(session).mark_delivered(
                    event_id=item["event_id"],
                    lease_owner=item["lease_owner"],
                )
            if acknowledged:
                delivered += 1
            else:
                logger.warning("Outbox lease expired before delivery acknowledgement", event_id=item["event_id"])
        return delivered

    async def _outbox_dispatch_loop(self) -> None:
        while True:
            try:
                delivered = await self._dispatch_outbox_once()
                if delivered:
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as err:
                logger.warning("Outbox dispatcher will retry", error=str(err))
            try:
                await asyncio.wait_for(self._outbox_wakeup.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            self._outbox_wakeup.clear()

    async def publish(
        self,
        channel: str,
        message: Union[BaseMessage, dict[str, Any], str],
        db_session: Optional[Any] = None
    ) -> int:
        """
        Publishes a message to a designated channel and audits to database if session available.
        Serializes typed BaseMessage instances into JSON.
        """
        if not self.is_connected:
            await self.connect()

        # Update channel on message if it's a BaseMessage
        if isinstance(message, BaseMessage) and not message.channel:
            message.channel = channel

        if self.settings.APP_ENV == "production":
            if isinstance(message, dict):
                message = dict(message)
                message.setdefault("message_id", f"MSG-{uuid.uuid4().hex.upper()}")
            elif isinstance(message, str):
                message = BaseMessage(
                    message_type=MessageType.DIRECT_MESSAGE,
                    sender="system",
                    channel=channel,
                    payload={"raw": message},
                )

        if isinstance(message, BaseMessage):
            msg_str = message.to_json()
        elif isinstance(message, dict):
            msg_str = json.dumps(message)
        else:
            msg_str = str(message)

        if self.settings.APP_ENV == "production":
            await self.persist_message(
                message,
                channel=channel,
                db_session=db_session,
                delivery_payload=msg_str,
            )
            self._outbox_wakeup.set()
            return 0

        # Development retains immediate in-memory/Redis fanout for local workflows.
        await self.persist_message(message, channel=channel, db_session=db_session)

        if self._redis_client:
            try:
                receivers = await self._redis_client.publish(channel, msg_str)
                return int(receivers)
            except Exception as e:
                if self.settings.APP_ENV == "production":
                    raise RuntimeError("Redis publish failed; message is retained in the database log.") from e
                logger.warning("Redis publish failed, falling back to in-memory delivery", error=str(e))
                return await self._in_memory_backend.publish(channel, msg_str)
        else:
            return await self._in_memory_backend.publish(channel, msg_str)

    async def subscribe(
        self,
        channel: str,
        callback: Callable[[BaseMessage], Coroutine[Any, Any, None]]
    ) -> str:
        """
        Subscribes a callback to a channel. Returns subscription listener ID.
        """
        if not self.is_connected:
            await self.connect()

        if self.settings.APP_ENV == "production" and self._redis_client is None:
            raise RuntimeError("Redis is currently unavailable; production subscriptions cannot use volatile memory.")

        sub_id = f"sub-{uuid.uuid4().hex[:8]}"

        if self._redis_client:
            pubsub = self._redis_client.pubsub()
            await pubsub.subscribe(channel)
            queue = None
            backend_sub_id = None
        else:
            backend_sub_id, queue = await self._in_memory_backend.subscribe(channel)
            pubsub = None

        async def _listen_loop():
            if pubsub:
                try:
                    async for raw in pubsub.listen():
                        if raw["type"] == "message":
                            try:
                                msg = BaseMessage.from_json(raw["data"])
                            except Exception:
                                msg = BaseMessage(
                                    message_type=MessageType.DIRECT_MESSAGE,
                                    sender="unknown",
                                    payload={"raw": raw["data"]}
                                )
                            await callback(msg)
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning("Redis subscription loop exited", error=str(e))
                finally:
                    try:
                        await pubsub.unsubscribe(channel)
                        await pubsub.close()
                    except Exception:
                        pass
            elif queue:
                while True:
                    data = await queue.get()
                    try:
                        msg = BaseMessage.from_json(data)
                    except Exception:
                        try:
                            parsed = json.loads(data)
                            msg = BaseMessage(
                                message_type=parsed.get("message_type", MessageType.DIRECT_MESSAGE),
                                sender=parsed.get("sender", "unknown"),
                                payload=parsed
                            )
                        except Exception:
                            msg = BaseMessage(
                                message_type=MessageType.DIRECT_MESSAGE,
                                sender="unknown",
                                payload={"raw": data}
                            )
                    try:
                        await callback(msg)
                    except Exception as cb_err:
                        logger.error("Message bus subscriber callback error", error=str(cb_err))

        listener_task = asyncio.create_task(_listen_loop())
        self._active_listeners[sub_id] = listener_task
        return sub_id

    async def unsubscribe(self, channel: str, sub_id: str) -> None:
        """Unsubscribes and cancels listener task."""
        if sub_id in self._active_listeners:
            self._active_listeners[sub_id].cancel()
            del self._active_listeners[sub_id]
        await self._in_memory_backend.unsubscribe(channel, sub_id)

    async def broadcast(
        self,
        title: str,
        content: str,
        sender: str = "boss",
        priority: str = "normal",
        db_session: Optional[Any] = None
    ) -> int:
        """Publishes an executive broadcast message to boss:broadcast."""
        msg = BroadcastMessage(
            sender=sender,
            title=title,
            content=content,
            priority=priority,
            channel="boss:broadcast"
        )
        return await self.publish("boss:broadcast", msg, db_session=db_session)

    async def send_to_employee(
        self,
        employee_id: str,
        message: Union[BaseMessage, dict[str, Any]],
        db_session: Optional[Any] = None
    ) -> int:
        """Publishes a direct assignment or communication to employee:{id}:inbox."""
        channel = f"employee:{employee_id}:inbox"
        return await self.publish(channel, message, db_session=db_session)

    async def send_direct_message(
        self,
        sender: str,
        recipient: str,
        content: str,
        subject: Optional[str] = None,
        db_session: Optional[Any] = None
    ) -> int:
        """
        Sends 1-to-1 direct message to an employee's inbox channel.
        """
        channel = f"employee:{recipient}:inbox"
        msg = DirectMessage(
            sender=sender,
            recipient=recipient,
            content=content,
            subject=subject,
            channel=channel
        )
        return await self.publish(channel, msg, db_session=db_session)

    async def broadcast_to_department(
        self,
        department: str,
        title: str,
        content: str,
        sender: str = "boss",
        priority: str = "normal",
        db_session: Optional[Any] = None
    ) -> int:
        """Publishes a broadcast announcement to dept:{department}:broadcast."""
        channel = f"dept:{department}:broadcast"
        msg = DepartmentBroadcastMessage(
            sender=sender,
            department=department,
            title=title,
            content=content,
            priority=priority,
            channel=channel
        )
        return await self.publish(channel, msg, db_session=db_session)

    async def send_to_department(
        self,
        department: str,
        message: Union[BaseMessage, dict[str, Any], str],
        db_session: Optional[Any] = None
    ) -> int:
        """Publishes a message to dept:{department}:broadcast."""
        channel = f"dept:{department}:broadcast"
        return await self.publish(channel, message, db_session=db_session)

    async def subscribe_department(
        self,
        department: str,
        callback: Callable[[BaseMessage], Coroutine[Any, Any, None]]
    ) -> str:
        """Subscribes to dept:{department}:broadcast channel."""
        channel = f"dept:{department}:broadcast"
        return await self.subscribe(channel, callback)

    @classmethod
    def list_standard_channels(cls) -> list[str]:
        """Returns standard system channel templates."""
        return [
            "boss:broadcast",
            "dept:{department}:broadcast",
            "employee:{id}:inbox",
            "tasks:{id}:events"
        ]

    async def publish_task_event(
        self,
        task_id: str,
        event_type: str,
        status: str,
        details: Optional[dict[str, Any]] = None,
        sender: str = "scheduler",
        db_session: Optional[Any] = None
    ) -> int:
        """Publishes a lifecycle event to tasks:{id}:events."""
        channel = f"tasks:{task_id}:events"
        msg = TaskEventMessage(
            sender=sender,
            task_id=task_id,
            event_type=event_type,
            status=status,
            channel=channel,
            details=details or {}
        )
        return await self.publish(channel, msg, db_session=db_session)

    async def close(self) -> None:
        """Closes all active listener tasks and Redis connections."""
        if self._outbox_task and not self._outbox_task.done():
            self._outbox_task.cancel()
            try:
                await self._outbox_task
            except asyncio.CancelledError:
                pass
        self._outbox_task = None
        for sub_id, task in list(self._active_listeners.items()):
            task.cancel()
        self._active_listeners.clear()
        if self._redis_client:
            try:
                await self._redis_client.close()
            except Exception:
                pass
            self._redis_client = None
        self.is_connected = False
