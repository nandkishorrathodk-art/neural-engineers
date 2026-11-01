# Durable message outbox

In production, publishing first commits the `messages` audit row and `outbox_events` row in one database transaction. A lifespan-managed dispatcher leases pending rows, publishes their stored payload to Redis, then marks them delivered. Failures return to `pending` with capped exponential delay; expired leases are reclaimable after API restart. After 12 failed attempts, an event becomes `failed` and remains stored for operator review.

This boundary is at-least-once to Redis Pub/Sub. A process crash after Redis accepts a publish but before the database records delivery can publish the same `message_id` again after lease expiry. `outbox_events.event_id` is the same identifier; consumers must deduplicate by the message's `message_id`. Redis Pub/Sub does not provide consumer acknowledgements or replay to offline subscribers, so `delivered` means Redis accepted the publish; it does not mean an employee received or completed the task. Worker claims, execution receipts, and task-state changes are not yet one transaction with this outbox.

## Inspect stalled delivery

Use a read-only query against the configured database:

```sql
SELECT event_id, channel, status, attempt_count, available_at, lease_expires_at, last_error
FROM outbox_events
WHERE status != 'delivered'
ORDER BY created_at;
```

Do not delete failed rows to clear an alert. Preserve the message and error evidence, restore broker connectivity or correct the payload/consumer issue, then use an explicit replay operation once that operation is implemented and reviewed.

Production startup requires the latest Alembic revision. Apply the reviewed migration with `uv run alembic upgrade head` before restarting the API; the API does not run schema mutations itself.
