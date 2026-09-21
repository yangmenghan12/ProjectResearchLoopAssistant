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
)
from app.db.session import bind_worker_session_to
from app.engine.state_machine import ensure_can_start_run
from app.services.audit import record_event
from app.services.run_events import record_run_event
from app.services.run_jobs import create_run_job

NODE_ORDER = [
    "load_state",
    "build_context",
    "analyze_sources",
    "llm_analyze_sources",
    "update_analysis",
    "verify_package",
    "llm_verify_package",
    "decide_next_state",
    "persist_state",
]


def append_turn(
    session: Session,
    run: AnalysisRun,
    project: Project,
    node_name: str,
    action: str,
    observation: str,
    metadata: dict | None = None,
) -> LoopTurn:
    turn = LoopTurn(
        run_id=run.id,
        project_id=project.id,
        turn_index=run.current_turn + 1,
        node_name=node_name,
        action=action,
        observation=observation,
        status="completed",
        metadata_json=metadata or {},
    )
    session.add(turn)
    session.flush()
    run.current_turn = turn.turn_index
    return turn


def initialize_run(session: Session, project: Project) -> AnalysisRun:
    """创建一次 Loop 运行，但不立即执行。

    同步运行和 SSE 流式运行共享同一段初始化逻辑，避免两条入口在权限检查、
    状态流转和审计事件上出现细微偏差。
    """

    ensure_can_start_run(project.status)
    run = AnalysisRun(project_id=project.id, status="running", max_turns=project.max_turns)
    project.status = "running"
    session.add(run)
    session.flush()
    run.thread_id = f"run:{run.id}"
    record_event(
        session,
        event_type="run.started",
        message="Loop 运行已启动",
        project_id=project.id,
        payload={"run_id": run.id},
    )
    return run


def start_run(session: Session, project: Project) -> AnalysisRun:
    run = initialize_run(session, project)
    execute_run(session, project, run)
    return run


def enqueue_run(
    session: Session,
    project: Project,
    job_type: str = "start",
    payload: dict | None = None,
) -> tuple[AnalysisRun, RunJob]:
    """创建后台运行任务，不在 HTTP 请求内执行 LangGraph。

    这是 durable runtime 的入口：HTTP 只负责登记目标、任务和事件；实际执行由
    worker 接管，因此浏览器断开不会影响已经排队的 run。
    """

    ensure_can_start_run(project.status)
    bind_worker_session_to(session)
    run = AnalysisRun(
        project_id=project.id,
        status="queued",
        max_turns=project.max_turns,
        execution_mode="background",
    )
    project.status = "queued"
    session.add(run)
    session.flush()
    run.thread_id = f"run:{run.id}"
    job = create_run_job(session, project.id, run.id, job_type, payload)
    record_run_event(session, project.id, run.id, "run_queued", {"status": run.status, "job_id": job.id})
    return run, job


def resume_run(session: Session, project: Project, run: AnalysisRun) -> AnalysisRun:
    if run.status != "paused_for_input":
        raise ValueError("Only paused runs can be resumed")
    from app.engine.langgraph_runner import resume_run_graph

    project_id = project.id
    run_id = run.id
    if not run.thread_id:
        run.thread_id = f"run:{run.id}"
    thread_id = run.thread_id
    project.status = "running"
    run.status = "running"
    run.resume_count += 1
    record_event(
        session,
        event_type="run.resumed",
        message="Loop 运行已继续",
        project_id=project.id,
        payload={"run_id": run.id},
    )
    session.commit()
    resume_run_graph(
        session,
        project_id,
        run_id,
        thread_id,
        {"run_id": run_id, "resume_count": run.resume_count},
    )
    return run


def execute_run(session: Session, project: Project, run: AnalysisRun) -> None:
    """执行一次 Loop。

    真实编排由 LangGraph StateGraph 负责；本模块保留 API 入口和持久化 helper。
    """

    from app.engine.langgraph_runner import execute_run_graph

    project_id = project.id
    run_id = run.id
    if not run.thread_id:
        run.thread_id = f"run:{run.id}"
    thread_id = run.thread_id
    # LangGraph checkpoint 会独立落库；运行记录必须先提交，避免恢复点引用未提交的 run。
    session.commit()
    execute_run_graph(session, project_id, run_id, thread_id)


def latest_previous_score(session: Session, run: AnalysisRun) -> int | None:
    statement = (
        select(QualityScore.overall_score)
        .where(QualityScore.run_id == run.id)
        .order_by(QualityScore.created_at.desc(), QualityScore.id.desc())
    )
    return session.scalar(statement)


def persist_analysis_artifacts(
    session: Session,
    project: Project,
    run: AnalysisRun,
    analysis: dict,
) -> dict:
    evidence_ref_map: dict[str, str] = {}
    persisted_evidence: list[dict] = []
    for evidence in analysis["evidence"]:
        item = EvidenceItem(
            project_id=project.id,
            run_id=run.id,
            source_id=evidence.get("source_id"),
            title=evidence["title"],
            summary=evidence["summary"],
            evidence_type=evidence["evidence_type"],
            reference=evidence.get("reference"),
            confidence=evidence.get("confidence", 80),
            metadata_json=evidence.get("metadata", {}),
        )
        session.add(item)
        session.flush()
        evidence_ref_map[evidence["client_ref"]] = item.id
        persisted_evidence.append({"id": item.id})

    persisted_findings: list[dict] = []
    for finding in analysis["findings"]:
        evidence_refs = [evidence_ref_map[ref] for ref in finding.get("evidence_refs", []) if ref in evidence_ref_map]
        session.add(
            Finding(
                project_id=project.id,
                run_id=run.id,
                title=finding["title"],
                summary=finding["summary"],
                category=finding["category"],
                impact_level=finding["impact_level"],
                confidence=finding.get("confidence", 75),
                evidence_refs_json=evidence_refs,
                metadata_json=finding.get("metadata", {}),
            )
        )
        persisted_findings.append({"evidence_refs": evidence_refs})

    persisted_risks: list[dict] = []
    for risk in analysis["risks"]:
        evidence_refs = [evidence_ref_map[ref] for ref in risk.get("evidence_refs", []) if ref in evidence_ref_map]
        session.add(
            Risk(
                project_id=project.id,
                run_id=run.id,
                title=risk["title"],
                summary=risk["summary"],
                severity=risk["severity"],
                mitigation=risk["mitigation"],
                evidence_refs_json=evidence_refs,
                metadata_json=risk.get("metadata", {}),
            )
        )
        persisted_risks.append(
            {
                "severity": risk["severity"],
                "mitigation": risk["mitigation"],
                "evidence_refs": evidence_refs,
            }
        )

    persisted_questions: list[dict] = []
    for question in analysis["questions"]:
        existing_answered = find_answered_question(session, project.id, question)
        if existing_answered is not None:
            persisted_questions.append(
                {
                    "status": existing_answered.status,
                    "impact": existing_answered.impact,
                }
            )
            continue
        session.add(
            Question(
                project_id=project.id,
                run_id=run.id,
                prompt=question["prompt"],
                reason=question["reason"],
                impact=question["impact"],
                status=question.get("status", "open"),
                metadata_json=question.get("metadata", {}),
            )
        )
        persisted_questions.append({"status": question.get("status", "open"), "impact": question["impact"]})

    persisted_recommendations: list[dict] = []
    for recommendation in analysis["recommendations"]:
        session.add(
            Recommendation(
                project_id=project.id,
                run_id=run.id,
                summary=recommendation["summary"],
                rationale=recommendation["rationale"],
                confidence=recommendation.get("confidence", 70),
                next_steps_json=recommendation.get("next_steps", []),
                metadata_json=recommendation.get("metadata", {}),
            )
        )
        persisted_recommendations.append(
            {
                "summary": recommendation["summary"],
                "confidence": recommendation.get("confidence", 70),
            }
        )
    session.flush()
    return {
        "evidence": persisted_evidence,
        "findings": persisted_findings,
        "risks": persisted_risks,
        "questions": persisted_questions,
        "recommendations": persisted_recommendations,
    }


def find_answered_question(session: Session, project_id: str, question: dict) -> Question | None:
    statement = (
        select(Question)
        .where(
            Question.project_id == project_id,
            Question.prompt == question["prompt"],
            Question.status == "answered",
        )
        .order_by(Question.answered_at.desc(), Question.id.desc())
    )
    return session.scalar(statement)
