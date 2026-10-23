import json
from collections.abc import Generator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AnalysisRun,
    EvidenceItem,
    Finding,
    LoopTurn,
    Project,
    QualityScore,
    Question,
    Recommendation,
    Risk,
    RunJob,
    utc_now,
)
from app.db.session import get_session, get_worker_session_factory
from app.engine.langgraph_runner import stream_run_graph_events
from app.engine.runner import enqueue_run, resume_run
from app.schemas.run import (
    AnalysisPackageRead,
    AnalysisRunRead,
    QuestionAnswerCreate,
    QuestionRead,
    RunEnqueueRead,
    RunEventRead,
)
from app.services.audit import record_event
from app.services.run_events import encode_many_sse, list_run_events, record_run_event, stream_run_events_live
from app.services.run_jobs import cancel_run_jobs, create_run_job
from app.workers.run_worker import run_one_job

router = APIRouter(prefix="/projects/{project_id}", tags=["runs"])


@router.post("/runs", response_model=RunEnqueueRead, status_code=status.HTTP_202_ACCEPTED)
def create_run(project_id: str, session: Session = Depends(get_session)) -> dict:
    """为项目创建一个幂等 run job，交给后台 worker 异步执行。

    Args: project_id 为非空项目 ID；session 为数据库会话。
    Returns: `{run: AnalysisRun, job: RunJob}`，HTTP 层返回 202。
    Raises: 项目不存在返回 404；已有活动 run 返回 409。
    副作用：写入 run、job 和事件；由 `POST /projects/{id}/runs` 调用。
    """
    project = get_project_or_404(session, project_id)
    try:
        run, job = enqueue_run(session, project)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    session.refresh(run)
    session.refresh(job)
    return {"run": run, "job": job}


@router.post("/runs/stream")
def create_run_stream(project_id: str, session: Session = Depends(get_session)) -> StreamingResponse:
    """创建并立即在当前请求中执行 run，返回已持久化事件的 SSE 响应。

    Args: project_id 为项目 ID；session 为数据库会话。
    Returns: `text/event-stream` StreamingResponse，内容来自该 run 的事件。
    Raises: 项目不存在返回 404；无法排队返回 409；执行异常由 worker 处理。
    副作用：写入 run/job、执行分析并读取数据库事件；由流式启动接口调用。
    """
    project = get_project_or_404(session, project_id)
    try:
        run, _job = enqueue_run(session, project)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    run_one_job()
    session.expire_all()
    events = list_run_events(session, project.id, run.id)
    return StreamingResponse(
        iter([encode_many_sse(events)]),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/runs", response_model=list[AnalysisRunRead])
def list_runs(project_id: str, session: Session = Depends(get_session)) -> list[AnalysisRun]:
    """按创建时间倒序列出项目的分析运行记录。

    Args: project_id 为项目 ID；session 为数据库会话。
    Returns: AnalysisRun 列表，最新 run 在前。
    Raises: 项目不存在返回 404；数据库异常向上抛出。
    副作用：仅读取数据库；由项目运行历史页面调用。
    """
    project = get_project_or_404(session, project_id)
    return list(
        session.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.project_id == project.id)
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
        ).all()
    )


@router.get("/runs/{run_id}", response_model=AnalysisPackageRead)
def get_run(project_id: str, run_id: str, session: Session = Depends(get_session)) -> dict:
    """读取一个 run 及其 turns、证据、发现、风险和质量评分。

    Args: project_id、run_id 均为非空 ID；session 为数据库会话。
    Returns: 可序列化的 AnalysisPackage 字典。
    Raises: 项目或 run 不存在、或不属于该项目时返回 404。
    副作用：仅读取数据库；由运行详情页调用。
    """
    get_project_or_404(session, project_id)
    run = get_run_or_404(session, project_id, run_id)
    return build_analysis_package(session, run.id)


@router.post("/runs/{run_id}/cancel", response_model=AnalysisRunRead)
def cancel_run(project_id: str, run_id: str, session: Session = Depends(get_session)) -> AnalysisRun:
    """取消指定 run 的活动 job，并把项目和 run 标记为 cancelled。

    Args: project_id、run_id 均为非空 ID；session 为数据库会话。
    Returns: 已提交并刷新的 AnalysisRun。
    Raises: 项目或 run 不存在时返回 404。
    副作用：更新数据库状态、取消 job 并写入运行事件；由取消接口调用。
    """
    project = get_project_or_404(session, project_id)
    run = get_run_or_404(session, project_id, run_id)
    cancel_run_jobs(session, run.id)
    run.status = "cancelled"
    run.stop_reason = "User cancelled the run."
    project.status = "cancelled"
    record_run_event(session, project.id, run.id, "run_cancelled", {"status": "cancelled"})
    session.commit()
    session.refresh(run)
    return run


@router.post("/runs/{run_id}/retry", response_model=RunEnqueueRead, status_code=status.HTTP_202_ACCEPTED)
def retry_run(project_id: str, run_id: str, session: Session = Depends(get_session)) -> dict:
    """为 failed 或 cancelled 的 run 创建一次 retry job。

    Args: project_id、run_id 均为非空 ID；session 为数据库会话。
    Returns: `{run: AnalysisRun, job: RunJob}`，HTTP 层返回 202。
    Raises: run 不是 failed/cancelled 时返回 409；资源不存在返回 404。
    副作用：重置运行状态、写入 job 和 retry 事件；由重试接口调用。
    """
    project = get_project_or_404(session, project_id)
    run = get_run_or_404(session, project_id, run_id)
    if run.status not in {"failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Only failed or cancelled runs can be retried")
    run.status = "queued"
    run.stop_reason = None
    project.status = "queued"
    assistant_job = session.scalar(select(RunJob).where(RunJob.run_id == run.id,
        RunJob.job_type == "assistant").order_by(RunJob.created_at.desc()).limit(1))
    if assistant_job:
        from app.assistant.models import AssistantMessage
        reply = session.get(AssistantMessage, assistant_job.payload_json.get("message_id"))
        if reply:
            reply.status, reply.error = "queued", None
        # Preserve the canonical assistant payload across retries (including snapshot IDs).
        # A single assistant job per run avoids ambiguous job ordering on MySQL timestamps.
        from app.services.run_jobs import clear_job_lock
        job = assistant_job
        job.status, job.last_error, job.next_run_at = "queued", None, None
        job.available_at, job.attempt_count = utc_now(), 0
        clear_job_lock(job)
    else:
        job = create_run_job(session, project.id, run.id, "retry")
    record_run_event(session, project.id, run.id, "run_retry_queued", {"job_id": job.id, "status": run.status})
    session.commit()
    session.refresh(run)
    session.refresh(job)
    return {"run": run, "job": job}


@router.get("/runs/{run_id}/events", response_model=list[RunEventRead])
def get_run_events(
    project_id: str,
    run_id: str,
    after_sequence: int = 0,
    session: Session = Depends(get_session),
):
    """按 sequence 游标读取 run 的持久化事件。

    Args: project_id/run_id 为资源 ID；after_sequence 为非负游标，默认 0。
    Returns: RunEvent 列表，按 sequence 升序排列。
    Raises: run 不存在或不属于项目时返回 404。
    副作用：仅读取数据库；由断线重连和运行详情页调用。
    """
    get_run_or_404(session, project_id, run_id)
    return list_run_events(session, project_id, run_id, after_sequence)


@router.get("/runs/{run_id}/events/stream")
def stream_run_events(
    project_id: str,
    run_id: str,
    after_sequence: int = 0,
) -> StreamingResponse:
    """校验 run 后以 SSE 形式持续推送其新增持久化事件。

    Args: project_id/run_id 为资源 ID；after_sequence 为断线续传游标。
    Returns: `text/event-stream` StreamingResponse。
    Raises: run 不存在或不属于项目时返回 404。
    副作用：创建 worker 数据库会话并持续轮询数据库；由前端实时面板调用。
    """
    session_factory = get_worker_session_factory()
    with session_factory() as session:
        get_run_or_404(session, project_id, run_id)
    return StreamingResponse(
        stream_run_events_live(
            session_factory,
            project_id=project_id,
            run_id=run_id,
            after_sequence=after_sequence,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/runs/{run_id}/resume", response_model=AnalysisPackageRead)
def resume_existing_run(project_id: str, run_id: str, session: Session = Depends(get_session)) -> dict:
    """从 LangGraph checkpoint 恢复一个暂停的 run，并返回最新分析包。

    Args: project_id、run_id 均为非空 ID；session 为数据库会话。
    Returns: 恢复后的 AnalysisPackage 字典。
    Raises: 资源不存在返回 404；run 不可恢复时返回 409。
    副作用：读取并更新 checkpoint、run 状态及分析产物；由恢复接口调用。
    """
    project = get_project_or_404(session, project_id)
    run = get_run_or_404(session, project_id, run_id)
    try:
        resume_run(session, project, run)
        from app.assistant.runtime import finalize_assistant_report
        finalize_assistant_report(session, project, run)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return build_analysis_package(session, run.id)


@router.post("/questions/{question_id}/answer", response_model=QuestionRead)
def answer_question(
    project_id: str,
    question_id: str,
    payload: QuestionAnswerCreate,
    session: Session = Depends(get_session),
) -> Question:
    """保存用户对暂停问题的回答，并补齐项目缺失的分析目标。

    Args: project_id/question_id 为资源 ID；payload.answer_text 为非空回答；session 为数据库会话。
    Returns: 已提交并刷新的 Question。
    Raises: question 不存在或不属于项目时返回 404。
    副作用：写入问题、项目目标和审计事件；由问题回答接口调用。
    """
    project = get_project_or_404(session, project_id)
    question = session.get(Question, question_id)
    if question is None or question.project_id != project.id:
        raise HTTPException(status_code=404, detail="Question not found")
    question.status = "answered"
    question.answer_text = payload.answer_text
    question.answered_at = utc_now()
    if question.metadata_json.get("missing_field") == "analysis_goal" and not project.analysis_goal:
        project.analysis_goal = payload.answer_text
    record_event(
        session,
        event_type="question.answered",
        message="Loop question answered",
        project_id=project.id,
        payload={"question_id": question.id, "run_id": question.run_id},
    )
    session.commit()
    session.refresh(question)
    return question


def stream_run_response(
    session: Session,
    project_id: str,
    run_id: str,
    thread_id: str,
) -> Generator[str, None, None]:
    """把 LangGraph 运行进度转成 SSE 帧。

    当前流式入口仍然复用数据库作为事实源：每个节点完成后先提交，再把节点
    事件发给前端。这样即使浏览器中断，服务端已经完成的节点也不会丢失。
    """

    yield encode_sse(
        "run_started",
        {
            "run_id": run_id,
            "project_id": project_id,
            "status": "running",
        },
    )
    try:
        for event_payload in stream_run_graph_events(session, project_id, run_id, thread_id):
            session.commit()
            yield encode_sse("node_completed", event_payload)
        project = session.get(Project, project_id)
        run = session.get(AnalysisRun, run_id)
        session.commit()
        if project is not None:
            session.refresh(project)
        if run is not None:
            session.refresh(run)
        yield encode_sse(
            "run_completed",
            {
                "run_id": run_id,
                "project_id": project_id,
                "status": run.status if run else "unknown",
                "project_status": project.status if project else "unknown",
                "turn_count": run.current_turn if run else 0,
            },
        )
    except Exception as exc:
        session.rollback()
        project = session.get(Project, project_id)
        run = session.get(AnalysisRun, run_id)
        if run is not None:
            run.status = "failed"
            run.stop_reason = str(exc)
        if project is not None:
            project.status = "failed"
        session.commit()
        yield encode_sse(
            "error",
            {
                "run_id": run_id,
                "project_id": project_id,
                "message": str(exc),
            },
        )


def encode_sse(event_name: str, payload: dict) -> str:
    """把事件名和 JSON payload 编码为一个 SSE frame。

    Args: event_name 为非空事件名；payload 为可 JSON 序列化字典。
    Returns: 以两个换行结尾的 SSE 字符串。
    Raises: payload 不可 JSON 序列化时抛出 `TypeError`。
    副作用：无；由 run 流和事件响应构造函数调用。
    """
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def get_project_or_404(session: Session, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def get_run_or_404(session: Session, project_id: str, run_id: str) -> AnalysisRun:
    run = session.get(AnalysisRun, run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


def list_by_run(session: Session, model, run_id: str):
    if model is LoopTurn:
        order_by = (LoopTurn.turn_index.asc(), LoopTurn.id.asc())
    else:
        order_by = (model.created_at.asc(), model.id.asc())
    return list(
        session.scalars(
            select(model).where(model.run_id == run_id).order_by(*order_by)
        ).all()
    )


def build_analysis_package(session: Session, run_id: str) -> dict:
    """按 run 聚合分析过程和产物，形成前端与报告共用的数据包。

    Args: session 为数据库会话；run_id 为非空运行 ID。
    Returns: 包含 run、turns、evidence、findings、risks 等集合的字典。
    Raises: run 不存在时抛出 HTTP 404；数据库异常向上抛出。
    副作用：仅读取数据库；由 run 详情接口调用。
    """
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {
        "run": run,
        "turns": list_by_run(session, LoopTurn, run.id),
        "evidence_items": list_by_run(session, EvidenceItem, run.id),
        "findings": list_by_run(session, Finding, run.id),
        "risks": list_by_run(session, Risk, run.id),
        "questions": list_by_run(session, Question, run.id),
        "recommendations": list_by_run(session, Recommendation, run.id),
        "quality_scores": list_by_run(session, QualityScore, run.id),
    }
