import json
from collections.abc import Iterable
from time import monotonic, sleep

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import RunEvent, RunJob

TERMINAL_RUN_EVENTS = {"run_completed", "run_failed", "run_cancelled"}
RUN_EVENT_SEQUENCE_RETRIES = 5


def next_sequence(session: Session, run_id: str) -> int:
    """为单个 run 生成单调递增事件序号，便于前端断线后补历史。"""

    current = session.scalar(select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run_id))
    return int(current or 0) + 1


def is_run_event_sequence_conflict(exc: IntegrityError) -> bool:
    message = str(getattr(exc, "orig", exc)).lower()
    return "uq_run_events_run_sequence" in message or (
        "unique" in message and "run_events" in message and "sequence" in message
    )


def record_run_event(
    session: Session,
    project_id: str,
    run_id: str,
    event_type: str,
    payload: dict | None = None,
) -> RunEvent:
    """记录 durable run event。

    前端事件流从这里读取；事件不替代 Analysis Package，只描述运行过程。
    """

    last_conflict: IntegrityError | None = None
    for _ in range(RUN_EVENT_SEQUENCE_RETRIES):
        event = RunEvent(
            project_id=project_id,
            run_id=run_id,
            event_type=event_type,
            sequence=next_sequence(session, run_id),
            payload_json=payload or {},
        )
        try:
            # sequence 仍由当前最大值推导；并发撞号时只回滚本次事件插入，再重读序号。
            with session.begin_nested():
                session.add(event)
                session.flush()
            return event
        except IntegrityError as exc:
            if event in session:
                session.expunge(event)
            if not is_run_event_sequence_conflict(exc):
                raise
            last_conflict = exc

    raise RuntimeError("Failed to allocate a unique run event sequence") from last_conflict


def list_run_events(session: Session, project_id: str, run_id: str, after_sequence: int = 0) -> list[RunEvent]:
    return list(
        session.scalars(
            select(RunEvent)
            .where(
                RunEvent.project_id == project_id,
                RunEvent.run_id == run_id,
                RunEvent.sequence > after_sequence,
            )
            .order_by(RunEvent.sequence.asc(), RunEvent.id.asc())
        ).all()
    )


def encode_sse(event: RunEvent) -> str:
    payload = {
        "id": event.id,
        "project_id": event.project_id,
        "run_id": event.run_id,
        "sequence": event.sequence,
        "created_at": event.created_at.isoformat(),
        **event.payload_json,
    }
    return (
        f"id: {event.sequence}\n"
        f"event: {event.event_type}\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    )


def encode_keepalive_sse() -> str:
    return ": keepalive\n\n"


def encode_many_sse(events: Iterable[RunEvent]) -> str:
    return "".join(encode_sse(event) for event in events)


def run_has_active_job(session: Session, run_id: str) -> bool:
    return (
        session.scalar(
            select(RunJob.id)
            .where(
                RunJob.run_id == run_id,
                RunJob.status.in_({"queued", "running"}),
            )
            .limit(1)
        )
        is not None
    )


def stream_run_events_live(
    session_factory,
    *,
    project_id: str,
    run_id: str,
    after_sequence: int = 0,
    poll_interval_seconds: float = 0.5,
    keepalive_seconds: float = 10.0,
):
    """持续读取 durable run events，支持断线后按 sequence 补发。

    每次轮询都使用新的 session，避免长连接占住请求 session，也让 worker 新提交的
    事件能被下一轮读取到。遇到终态事件直接结束；没有活动 job 时也结束，避免已完成
    或孤立 run 的 TestClient/浏览器一直等待。
    """

    last_sequence = max(0, after_sequence)
    last_keepalive_at = monotonic()
    while True:
        with session_factory() as session:
            events = list_run_events(session, project_id, run_id, last_sequence)
            if events:
                for event in events:
                    yield encode_sse(event)
                    last_sequence = event.sequence
                    last_keepalive_at = monotonic()
                    if event.event_type in TERMINAL_RUN_EVENTS:
                        return
                continue

            if not run_has_active_job(session, run_id):
                return

        now = monotonic()
        if now - last_keepalive_at >= keepalive_seconds:
            yield encode_keepalive_sse()
            last_keepalive_at = now
        sleep(poll_interval_seconds)
