from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.models import AnalysisRun, Project, RunJob
from app.db.session import get_worker_session_factory
from app.engine import langgraph_runner
from app.services.run_events import record_run_event
from app.services.run_jobs import claim_next_job, heartbeat_job, mark_job_failed, mark_job_succeeded


class JobLeaseLost(RuntimeError):
    """当前 worker 不再持有 job 租约时停止本轮执行，避免覆盖新 worker 状态。"""


def run_one_job(
    worker_id: str | None = None,
    queue_name: str = "default",
    lease_seconds: int = 60,
) -> bool:
    """执行一个排队中的 run job，便于本地开发、测试和 daemon 复用。"""

    worker_id = worker_id or f"local-worker-{uuid4()}"
    session_factory = get_worker_session_factory()
    with session_factory() as session:
        job = claim_next_job(
            session,
            worker_id=worker_id,
            queue_name=queue_name,
            lease_seconds=lease_seconds,
        )
        if job is None:
            return False
        run = require_run(session, job.run_id)
        project = require_project(session, job.project_id)
        run.status = "running" if job.job_type != "resume" else "resuming"
        project.status = run.status
        record_run_event(session, project.id, run.id, "run_started", {"status": run.status, "job_id": job.id})
        job_id = job.id
        project_id = project.id
        run_id = run.id
        thread_id = run.thread_id or f"run:{run.id}"
        job_type = job.job_type
        payload = job.payload_json
        session.commit()

    try:
        with session_factory() as session:
            if job_type == "resume":
                ensure_job_still_owned(session_factory, job_id, worker_id)
                langgraph_runner.resume_run_graph(session, project_id, run_id, thread_id, payload)
                heartbeat_current_job(session_factory, job_id, worker_id, lease_seconds)
            else:
                for event_payload in langgraph_runner.stream_run_graph_events(session, project_id, run_id, thread_id):
                    ensure_job_still_owned(session_factory, job_id, worker_id)
                    record_run_event(session, project_id, run_id, "node_completed", event_payload)
                    session.commit()
                    heartbeat_current_job(session_factory, job_id, worker_id, lease_seconds)

        with session_factory() as session:
            job = require_job(session, job_id)
            ensure_loaded_job_still_owned(job, worker_id)
            heartbeat_job(job, worker_id=worker_id, lease_seconds=lease_seconds)
            run = require_run(session, run_id)
            project = require_project(session, project_id)
            mark_job_succeeded(job)
            record_run_event(
                session,
                project.id,
                run.id,
                "run_completed",
                {"status": run.status, "turn_count": run.current_turn, "job_id": job.id},
            )
            session.commit()
        return True
    except JobLeaseLost:
        return True
    except Exception as exc:
        with session_factory() as session:
            job = require_job(session, job_id)
            if job.status != "running" or job.locked_by != worker_id:
                return True
            run = require_run(session, run_id)
            project = require_project(session, project_id)
            run.status = "failed"
            run.stop_reason = str(exc)
            project.status = "failed"
            mark_job_failed(job, str(exc))
            record_run_event(session, project.id, run.id, "run_failed", {"message": str(exc), "job_id": job.id})
            session.commit()
        return True


def ensure_job_still_owned(
    session_factory: Callable[[], Session],
    job_id: str,
    worker_id: str,
) -> None:
    """重新读取 job 租约；取消或换 owner 后立即停止，避免继续写节点事件。"""

    with session_factory() as session:
        job = require_job(session, job_id)
        ensure_loaded_job_still_owned(job, worker_id)


def ensure_loaded_job_still_owned(job: RunJob, worker_id: str) -> None:
    if job.status != "running" or job.locked_by != worker_id:
        raise JobLeaseLost("Run job lease is no longer owned by this worker")


def heartbeat_current_job(
    session_factory: Callable[[], Session],
    job_id: str,
    worker_id: str,
    lease_seconds: int,
) -> None:
    """节点事件提交后单独续租，避免长图执行期间 daemon 误判为 stale job。"""

    try:
        with session_factory() as session:
            job = require_job(session, job_id)
            heartbeat_job(job, worker_id=worker_id, lease_seconds=lease_seconds)
            session.commit()
    except ValueError as exc:
        raise JobLeaseLost("Run job lease was claimed by another worker") from exc


def require_job(session: Session, job_id: str) -> RunJob:
    job = session.get(RunJob, job_id)
    if job is None:
        raise RuntimeError("Run job not found")
    return job


def require_run(session: Session, run_id: str) -> AnalysisRun:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise RuntimeError("Run not found")
    return run


def require_project(session: Session, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise RuntimeError("Project not found")
    return project
