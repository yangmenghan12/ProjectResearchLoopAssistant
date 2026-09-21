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
    get_project_or_404(session, project_id)
    run = get_run_or_404(session, project_id, run_id)
    return build_analysis_package(session, run.id)


@router.post("/runs/{run_id}/cancel", response_model=AnalysisRunRead)
def cancel_run(project_id: str, run_id: str, session: Session = Depends(get_session)) -> AnalysisRun:
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
    project = get_project_or_404(session, project_id)
    run = get_run_or_404(session, project_id, run_id)
    if run.status not in {"failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Only failed or cancelled runs can be retried")
    run.status = "queued"
    run.stop_reason = None
    project.status = "queued"
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
    get_run_or_404(session, project_id, run_id)
    return list_run_events(session, project_id, run_id, after_sequence)


@router.get("/runs/{run_id}/events/stream")
def stream_run_events(
    project_id: str,
    run_id: str,
    after_sequence: int = 0,
) -> StreamingResponse:
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
    project = get_project_or_404(session, project_id)
    run = get_run_or_404(session, project_id, run_id)
    try:
        resume_run(session, project, run)
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
