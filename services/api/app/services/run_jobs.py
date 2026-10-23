from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import AnalysisRun, Project, RunJob, utc_now
from app.services.run_events import record_run_event

ACTIVE_JOB_STATUSES = {"queued", "running"}


def get_active_job_for_run(session: Session, run_id: str, job_type: str | None = None) -> RunJob | None:
    """查找同一 run 上仍处于排队或执行中的任务，避免重复入队。

    Args: session 为数据库会话；run_id 为非空运行 ID；job_type 可选任务类型过滤。
    Returns: 匹配的 RunJob，找不到时为 None。
    Raises: 数据库查询异常向上抛出。
    副作用：仅读取数据库；由 enqueue 和 retry 流程调用。
    """

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
    """创建后台运行任务；相同 run/type 的 active job 会复用原队列。

    Args: session 为数据库会话；project_id/run_id 为资源 ID；job_type 为任务类型；payload 可选。
    Returns: 新建或复用的 RunJob。
    Raises: 数据库约束或输入异常向上抛出。
    副作用：可能向 session 添加 job；由 run API 和 retry 流程调用。
    """

    # existing 表示同一 run/type 已经存在的 queued/running job，用于保证入队幂等。
    existing = get_active_job_for_run(session, run_id, job_type)
    if existing is not None:
        return existing
    # payload_json 保存 worker 执行该任务所需的可序列化参数。
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
    """按队列领取一个可运行 job，并写入 worker 租约字段。

    Args: session 为 worker 会话；worker_id 为非空标识；queue_name 为队列名；lease_seconds 为正整数。
    Returns: 成功领取的 RunJob；没有可领取任务时为 None。
    Raises: 数据库锁或更新异常向上抛出。
    副作用：更新 job 的状态、owner、lease 和尝试次数；由 worker loop 调用。
    """

    # now 同时用于筛选可执行任务和计算本次 lease 的起止时间。
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
    # 没有满足队列、可用时间和租约条件的任务时，worker 本轮保持空闲。
    if job is None:
        return None
    mark_job_running(job, worker_id, now=now, lease_seconds=lease_seconds)
    session.flush()
    return job


def heartbeat_job(job: RunJob, *, worker_id: str, lease_seconds: int = 60) -> None:
    """只有持有当前租约的 worker 可以续租，避免旧 worker 覆盖新 owner。

    Args: job 为已领取任务；worker_id 必须匹配 owner；lease_seconds 为正整数。
    Returns: None。
    Raises: job 不属于该 worker 或状态不允许心跳时抛出 ValueError。
    副作用：修改 job lease 时间；由运行过程中的 heartbeat 调用。
    """

    if job.status != "running" or job.locked_by != worker_id:
        raise ValueError("Worker does not own this run job lease")
    now = utc_now()
    job.heartbeat_at = now
    job.locked_until = now + timedelta(seconds=lease_seconds)


def recover_stale_jobs(session: Session, *, now: datetime | None = None) -> list[RunJob]:
    """恢复租约过期的 running job；耗尽重试次数的 job 直接失败。

    Args: session 为数据库会话；now 可选，用于测试或确定性比较的当前时间。
    Returns: 被恢复的 RunJob 列表。
    Raises: 数据库更新异常向上抛出。
    副作用：更新 job/run 状态并写入恢复事件；由 claim 流程调用。
    """

    now = now or utc_now()
    # jobs 是本次扫描发现的租约过期任务，后续按 attempt_count 决定重试或失败。
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
    # 返回已处理的 job 列表，调用方可据此统计恢复数量或记录日志。
    return jobs


def mark_job_running(
    job: RunJob,
    worker_id: str,
    *,
    now: datetime | None = None,
    lease_seconds: int = 60,
) -> None:
    """把已领取的 job 标记为执行中，并建立本次 worker 的租约。

    Args: job 为待执行任务；worker_id 为持有者；lease_seconds 为正整数；now 可选当前时间。
    Returns: None。
    Raises: 本函数不主动校验状态；调用者需先完成领取检查。
    副作用：修改 job 状态、租约、心跳和尝试次数；由 worker 执行入口调用。
    """
    now = now or utc_now()
    job.status = "running"
    job.locked_by = worker_id
    job.locked_at = now
    job.locked_until = now + timedelta(seconds=lease_seconds)
    job.heartbeat_at = now
    job.attempt_count += 1


def mark_job_succeeded(job: RunJob) -> None:
    """记录 job 成功并清理 worker 租约，避免任务继续被误认为运行中。

    Args: job 为当前 worker 已完成的任务。
    Returns: None。
    Raises: 无显式异常；属性赋值异常向上抛出。
    副作用：修改 job 状态并清空租约字段；由 worker 成功收尾流程调用。
    """
    job.status = "succeeded"
    job.last_error = None
    clear_job_lock(job)


def mark_job_failed(job: RunJob, error: str) -> None:
    """记录 job 失败原因并释放租约，供后续重试或人工诊断使用。

    Args: job 为当前任务；error 为非空或可诊断的错误文本。
    Returns: None。
    Raises: 无显式异常；属性赋值异常向上抛出。
    副作用：修改 job 状态、错误信息和租约；由 worker 异常收尾流程调用。
    """
    job.status = "failed"
    job.last_error = error
    clear_job_lock(job)


def mark_job_cancelled(job: RunJob) -> None:
    """将任务标记为取消并释放租约，阻止后续 worker 继续持有它。

    Args: job 为待取消的任务。
    Returns: None。
    Raises: 无显式异常；属性赋值异常向上抛出。
    副作用：修改 job 状态并清空租约；由 run 取消接口调用。
    """
    job.status = "cancelled"
    clear_job_lock(job)


def clear_job_lock(job: RunJob) -> None:
    """清空 job 的 worker 租约字段，使任务可以安全结束或重新领取。

    Args: job 为需要释放租约的 RunJob。
    Returns: None。
    Raises: 无显式异常；属性赋值异常向上抛出。
    副作用：仅修改内存中的租约字段，事务提交由调用者负责。
    """
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
