from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import AuditEvent

AUDIT_EVENT_SEQUENCE_RETRIES = 5


def record_event(
    session: Session,
    *,
    event_type: str,
    message: str,
    project_id: str | None = None,
    payload: dict | None = None,
    actor_id: str = "local-user",
) -> AuditEvent:
    """所有关键动作都落审计事件，后续 Run Timeline 会复用这条链路。"""

    last_conflict: IntegrityError | None = None
    for _ in range(AUDIT_EVENT_SEQUENCE_RETRIES):
        event = AuditEvent(
            project_id=project_id,
            sequence=next_project_event_sequence(session, project_id),
            event_type=event_type,
            message=message,
            actor_id=actor_id,
            payload_json=payload or {},
        )
        try:
            # sequence 由项目内当前最大值推导；并发撞号时只回滚本次事件插入，再重读序号。
            with session.begin_nested():
                session.add(event)
                session.flush()
            return event
        except IntegrityError as exc:
            if event in session:
                session.expunge(event)
            if not is_audit_event_sequence_conflict(exc):
                raise
            last_conflict = exc

    raise RuntimeError("Failed to allocate a unique audit event sequence") from last_conflict


def is_audit_event_sequence_conflict(exc: IntegrityError) -> bool:
    message = str(getattr(exc, "orig", exc)).lower()
    return "uq_audit_events_project_sequence" in message or (
        "unique" in message and "audit_events" in message and "sequence" in message
    )


def next_project_event_sequence(session: Session, project_id: str | None) -> int:
    """按项目生成单调递增序号，避免相同时间戳下 UUID 排序打乱时间线。"""

    if project_id is None:
        return 1
    session.flush()
    latest_sequence = session.scalar(
        select(func.max(AuditEvent.sequence)).where(AuditEvent.project_id == project_id)
    )
    return int(latest_sequence or 0) + 1
