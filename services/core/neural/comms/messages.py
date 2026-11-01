import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MessageType(str, Enum):
    TASK_ASSIGNMENT = "task_assignment"
    TASK_STATUS_UPDATE = "task_status_update"
    TASK_EVENT = "task_event"
    BROADCAST = "broadcast"
    DEPARTMENT_BROADCAST = "department_broadcast"
    DIRECT_MESSAGE = "direct_message"
    ARTIFACT_HANDOFF = "artifact_handoff"
    HEARTBEAT = "heartbeat"
    EMERGENCY_STOP = "emergency_stop"


class BaseMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    message_id: str = Field(default_factory=lambda: f"MSG-{uuid.uuid4().hex.upper()}")
    message_type: MessageType
    sender: str
    recipient: Optional[str] = None
    channel: Optional[str] = None
    timestamp: datetime = Field(default_factory=utc_now)
    correlation_id: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: Union[str, bytes, dict[str, Any]]) -> "BaseMessage":
        parsed = json.loads(data) if isinstance(data, (str, bytes)) else dict(data)
        m_type = parsed.get("message_type")
        if hasattr(m_type, "value"):
            m_type = m_type.value
        type_map = {
            MessageType.TASK_ASSIGNMENT.value: TaskAssignmentMessage,
            MessageType.TASK_EVENT.value: TaskEventMessage,
            MessageType.BROADCAST.value: BroadcastMessage,
            MessageType.DEPARTMENT_BROADCAST.value: DepartmentBroadcastMessage,
            MessageType.DIRECT_MESSAGE.value: DirectMessage,
            MessageType.ARTIFACT_HANDOFF.value: ArtifactHandoffMessage,
        }
        target_cls = type_map.get(m_type, BaseMessage)
        obj = target_cls.model_validate(parsed)
        return obj


class DirectMessage(BaseMessage):
    message_type: MessageType = MessageType.DIRECT_MESSAGE
    content: str = ""
    subject: Optional[str] = None

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        if not self.content and "content" in self.payload:
            self.content = str(self.payload["content"])
        self.payload.setdefault("content", self.content)
        if self.subject:
            self.payload.setdefault("subject", self.subject)


class DepartmentBroadcastMessage(BaseMessage):
    message_type: MessageType = MessageType.DEPARTMENT_BROADCAST
    department: str
    title: str
    content: str
    priority: str = "normal"

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        self.payload.setdefault("department", self.department)
        self.payload.setdefault("title", self.title)
        self.payload.setdefault("content", self.content)
        self.payload.setdefault("priority", self.priority)



class TaskAssignmentMessage(BaseMessage):
    message_type: MessageType = MessageType.TASK_ASSIGNMENT
    task_id: str
    project_id: str
    title: str
    objective: str
    acceptance_criteria: str
    deadline: Optional[datetime] = None

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        self.payload.setdefault("task_id", self.task_id)
        self.payload.setdefault("project_id", self.project_id)
        self.payload.setdefault("title", self.title)
        self.payload.setdefault("objective", self.objective)
        self.payload.setdefault("acceptance_criteria", self.acceptance_criteria)


class TaskEventMessage(BaseMessage):
    message_type: MessageType = MessageType.TASK_EVENT
    task_id: str
    event_type: str  # e.g., "dispatched", "completed", "failed", "blocked"
    status: str
    details: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        self.payload.setdefault("task_id", self.task_id)
        self.payload.setdefault("event_type", self.event_type)
        self.payload.setdefault("status", self.status)
        self.payload.setdefault("details", self.details)


class BroadcastMessage(BaseMessage):
    message_type: MessageType = MessageType.BROADCAST
    title: str
    content: str
    priority: str = "normal"

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        self.payload.setdefault("title", self.title)
        self.payload.setdefault("content", self.content)
        self.payload.setdefault("priority", self.priority)


class ArtifactHandoffMessage(BaseMessage):
    message_type: MessageType = MessageType.ARTIFACT_HANDOFF
    task_id: str
    artifact_name: str
    artifact_path: str
    checksum: str
    artifact_type: str = "code"

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        self.payload.setdefault("task_id", self.task_id)
        self.payload.setdefault("artifact_name", self.artifact_name)
        self.payload.setdefault("artifact_path", self.artifact_path)
        self.payload.setdefault("checksum", self.checksum)

