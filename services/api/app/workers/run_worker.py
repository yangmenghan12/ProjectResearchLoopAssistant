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
    """领取并执行一个 job，确保 lease 丢失时不会继续写入结果。

    Args: worker_id 为可选 worker 标识；queue_name 为队列名；lease_seconds 为正整数租约秒数。
    Returns: 成功领取并处理 job 返回 True；没有可领取任务返回 False。
    Raises: 租约丢失会停止本轮；其他异常会记录失败状态后向上处理。
    副作用：读写 job/run 数据、执行 LangGraph、提交心跳和运行事件；由 worker loop 调用。
    """

    # 未指定时生成本进程唯一 worker ID，用于 lease owner 校验。
    worker_id = worker_id or f"local-worker-{uuid4()}"
    # worker 使用独立 Session 工厂，领取、执行、心跳和收尾分别创建短事务。
    session_factory = get_worker_session_factory()
    with session_factory() as session:
        job = claim_next_job(
            session,
            worker_id=worker_id,
            queue_name=queue_name,
            lease_seconds=lease_seconds,
        )
        # 没有可领取任务时返回 False，worker loop 可据此等待下一轮轮询。
        if job is None:
            return False
        run = require_run(session, job.run_id)
        project = require_project(session, job.project_id)
        run.status = "running" if job.job_type != "resume" else "resuming"
        project.status = run.status
        record_run_event(session, project.id, run.id, "run_started", {"status": run.status, "job_id": job.id})
        # 保存后续 graph 执行所需的稳定 ID 和任务参数；离开该 Session 后不再依赖 ORM 对象。
        job_id = job.id
        project_id = project.id
        run_id = run.id
        thread_id = run.thread_id or f"run:{run.id}"
        job_type = job.job_type
        payload = job.payload_json
        session.commit()

    try:
        with session_factory() as session:
            if job_type == "assistant":
                from app.assistant.runtime import execute_assistant_job
                for event_payload in execute_assistant_job(session, project_id, run_id, payload,
                        check_owned=lambda: ensure_job_still_owned(session_factory, job_id, worker_id)):
                    ensure_job_still_owned(session_factory, job_id, worker_id)
                    record_run_event(session, project_id, run_id, "node_completed", event_payload)
                    session.commit()
                    heartbeat_current_job(session_factory, job_id, worker_id, lease_seconds)
            elif job_type == "resume":
                ensure_job_still_owned(session_factory, job_id, worker_id)
                langgraph_runner.resume_run_graph(session, project_id, run_id, thread_id, payload)
                from app.assistant.runtime import finalize_assistant_report
                finalize_assistant_report(session, require_project(session, project_id), require_run(session, run_id),
                    check_owned=lambda: ensure_job_still_owned(session_factory, job_id, worker_id))
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
        # 任务已完成并写入 run_completed 事件。
        return True
    except JobLeaseLost:
        # 租约已转移给其他 worker 时停止写入，避免覆盖新 worker 的结果。
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
            if job_type == "assistant":
                from app.assistant.models import AssistantMessage
                reply = session.get(AssistantMessage, payload.get("message_id"))
                if reply:
                    reply.status = "failed"
                    reply.error = "调研执行失败，请查看运行记录或重试。"
                    reply.content = reply.error
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
    """确认内存中的 job 仍由当前 worker 持有，防止租约丢失后继续写状态。

    Args: job 为重新读取的 RunJob；worker_id 为当前 worker 标识。
    Returns: None。
    Raises: 租约状态不匹配时抛出 JobLeaseLost。
    副作用：无；由节点提交前的租约保护流程调用。
    """
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
    """按 ID 读取 job，不存在时终止当前 worker 操作。

    Args: session 为数据库会话；job_id 为任务 ID。
    Returns: 对应的 RunJob。
    Raises: 任务不存在时抛出 RuntimeError；数据库异常向上抛出。
    副作用：仅读取数据库；由 worker 租约和心跳流程调用。
    """
    job = session.get(RunJob, job_id)
    if job is None:
        raise RuntimeError("Run job not found")
    return job


def require_run(session: Session, run_id: str) -> AnalysisRun:
    """按 ID 读取分析 run，不存在时抛出运行时错误。

    Args: session 为数据库会话；run_id 为运行 ID。
    Returns: 对应的 AnalysisRun。
    Raises: run 不存在时抛出 RuntimeError；数据库异常向上抛出。
    副作用：仅读取数据库；由 worker 执行流程调用。
    """
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise RuntimeError("Run not found")
    return run


def require_project(session: Session, project_id: str) -> Project:
    """按 ID 读取项目，不存在时抛出运行时错误。

    Args: session 为数据库会话；project_id 为项目 ID。
    Returns: 对应的 Project。
    Raises: 项目不存在时抛出 RuntimeError；数据库异常向上抛出。
    副作用：仅读取数据库；由 worker 执行流程调用。
    """
    project = session.get(Project, project_id)
    if project is None:
        raise RuntimeError("Project not found")
    return project
