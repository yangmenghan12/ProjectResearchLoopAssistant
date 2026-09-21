from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import AnalysisRun, Project, RunJob, utc_now
from app.services.run_events import record_run_event

ACTIVE_JOB_STATUSES = {"queued", "running"}


def get_active_job_for_run(session: Session, run_id: str, job_type: str | None = None) -> RunJob | None:
    """查找同一 run 上仍处于排队或执行中的任务，避免重复入队。"""

    session.flush()
    statement = select(RunJob).where(RunJob.run_id == run_id, RunJob.status.in_(ACTIVE_JOB_STATUSES))
    if job_type:
        statement = statement.where(RunJob.job_type == job_type)
    return session.scalar(statement.order_by(RunJob.created_at.desc(), RunJob.id.desc()))


def create_run_job(
    session: Session,
    project_id: str,
    run_id: str,
    job_type: str,
    payload: dict | None = None,
    queue_name: str = "default",
) -> RunJob:
    """创建后台运行任务；相同 run/type 的 active job 会复用原队列。"""

    existing = get_active_job_for_run(session, run_id, job_type)
    if existing is not None:
        return existing
    job = RunJob(
        project_id=project_id,
        run_id=run_id,
        job_type=job_type,
        payload_json=payload or {},
        queue_name=queue_name,
    )
    session.add(job)
    session.flush()
    return job


def claim_next_job(
    session: Session,
    *,
    worker_id: str,
    queue_name: str = "default",
    lease_seconds: int = 60,
) -> RunJob | None:
    """按队列领取一个可运行 job，并写入 worker 租约字段。"""

    now = utc_now()
    job = session.scalar(
        select(RunJob)
        .where(
            RunJob.status == "queued",
            RunJob.queue_name == queue_name,
            RunJob.available_at <= now,
            or_(RunJob.next_run_at.is_(None), RunJob.next_run_at <= now),
        )
        .order_by(RunJob.available_at.asc(), RunJob.created_at.asc(), RunJob.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    mark_job_running(job, worker_id, now=now, lease_seconds=lease_seconds)
    session.flush()
    return job


def heartbeat_job(job: RunJob, *, worker_id: str, lease_seconds: int = 60) -> None:
    """只有持有当前租约的 worker 可以续租，避免旧 worker 覆盖新 owner。"""

    if job.status != "running" or job.locked_by != worker_id:
        raise ValueError("Worker does not own this run job lease")
    now = utc_now()
    job.heartbeat_at = now
    job.locked_until = now + timedelta(seconds=lease_seconds)


def recover_stale_jobs(session: Session, *, now: datetime | None = None) -> list[RunJob]:
    """恢复租约过期的 running job；耗尽重试次数的 job 直接失败。"""

    now = now or utc_now()
    jobs = list(
        session.scalars(
            select(RunJob)
            .where(
                RunJob.status == "running",
                RunJob.locked_until.is_not(None),
                RunJob.locked_until <= now,
            )
            .order_by(RunJob.locked_until.asc(), RunJob.created_at.asc(), RunJob.id.asc())
            .with_for_update(skip_locked=True)
        ).all()
    )
    for job in jobs:
        if job.attempt_count < job.max_attempts:
            job.status = "queued"
            job.last_error = None
            job.next_run_at = now
            clear_job_lock(job)
        else:
            job.status = "failed"
            job.last_error = f"Worker lease expired after {job.attempt_count} attempts"
            job.next_run_at = None
            clear_job_lock(job)
            run = session.get(AnalysisRun, job.run_id)
            project = session.get(Project, job.project_id)
            if run is not None:
                run.status = "failed"
                run.stop_reason = job.last_error
            if project is not None:
                project.status = "failed"
            if run is not None and project is not None:
                record_run_event(
                    session,
                    project.id,
                    run.id,
                    "run_failed",
                    {
                        "message": job.last_error,
                        "job_id": job.id,
                        "source": "worker_lease_recovery",
                    },
                )
    session.flush()
    return jobs


def mark_job_running(
    job: RunJob,
    worker_id: str,
    *,
    now: datetime | None = None,
    lease_seconds: int = 60,
) -> None:
    now = now or utc_now()
    job.status = "running"
    job.locked_by = worker_id
    job.locked_at = now
    job.locked_until = now + timedelta(seconds=lease_seconds)
    job.heartbeat_at = now
    job.attempt_count += 1


def mark_job_succeeded(job: RunJob) -> None:
    job.status = "succeeded"
    job.last_error = None
    clear_job_lock(job)


def mark_job_failed(job: RunJob, error: str) -> None:
    job.status = "failed"
    job.last_error = error
    clear_job_lock(job)


def mark_job_cancelled(job: RunJob) -> None:
    job.status = "cancelled"
    clear_job_lock(job)


def clear_job_lock(job: RunJob) -> None:
    job.locked_by = None
    job.locked_at = None
    job.locked_until = None
    job.heartbeat_at = None


def cancel_run_jobs(session: Session, run_id: str) -> list[RunJob]:
    """取消同一 run 上仍处于 active 状态的 job。"""

    jobs = list(
        session.scalars(
            select(RunJob).where(RunJob.run_id == run_id, RunJob.status.in_(ACTIVE_JOB_STATUSES))
        ).all()
    )
    for job in jobs:
        mark_job_cancelled(job)
    return jobs
