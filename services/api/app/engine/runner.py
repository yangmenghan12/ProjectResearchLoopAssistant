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
    """为当前 run 追加一个有序的 graph turn，并推进 current_turn。

    Args: session/run/project 为运行上下文；node_name/action/observation 描述节点；metadata 为可选诊断信息。
    Returns: 已 flush、带 turn_index 的 LoopTurn。
    Raises: 数据库写入失败时抛出异常。
    副作用：向 session 添加 LoopTurn 并修改 run.current_turn；由各 graph node 调用。
    """
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
    """同步初始化并执行一次 Loop，供本地或兼容入口使用。

    Args: session 为数据库会话；project 为可启动项目。
    Returns: 执行后的 AnalysisRun。
    Raises: 状态不允许启动或 graph 执行失败时抛出异常。
    副作用：写入 run、分析产物和运行事件；由同步执行入口调用。
    """
    run = initialize_run(session, project)
    execute_run(session, project, run)
    return run


def enqueue_run(
    session: Session,
    project: Project,
    job_type: str = "start",
    payload: dict | None = None,
) -> tuple[AnalysisRun, RunJob]:
    """创建后台 run/job，避免在 HTTP 请求中直接执行 LangGraph。

    这是 durable runtime 的入口：HTTP 只登记目标、任务和事件，实际执行由 worker 接管。
    Args: session 为数据库会话；project 为项目；job_type 为任务类型；payload 为可选任务参数。
    Returns: `(AnalysisRun, RunJob)`，两者尚未由本函数提交。
    Raises: 项目状态不允许启动时抛出 ValueError。
    副作用：向 session 添加 run、job 和运行事件；由 run API handler 调用。
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
    """在原 LangGraph thread 上恢复一个暂停的 run。

    Args: session 为数据库会话；project/run 为同一项目下的运行记录。
    Returns: 更新后的 AnalysisRun。
    Raises: run 不是 paused_for_input 时抛出 ValueError。
    副作用：执行 graph、更新 checkpoint 和分析产物；由恢复 API 调用。
    """
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
    """执行一次 Loop，交由 LangGraph 完成真实编排。

    Args: session 为 worker 数据库会话；project/run 为待执行上下文。
    Returns: None；结果通过 run、turn 和 artifact 表持久化。
    Raises: LangGraph 或数据库异常向上抛出，由 worker 负责标记失败。
    副作用：提交初始状态并调用 LangGraph；由 `run_one_job` 调用。
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
    """读取当前 run 之前最近一次质量总分，供本次 run 计算变化量。

    Args: session 为数据库会话；run 为当前分析运行记录。
    Returns: 历史 overall_score 整数；没有历史分数时为 None。
    Raises: 数据库查询或分数转换异常向上抛出。
    副作用：仅读取数据库；由分析持久化流程调用。
    """
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
    """把分析字典中的集合转换为数据库实体并建立稳定引用。

    Args: session/project/run 为持久化上下文；analysis 为结构化分析结果。
    Returns: 含已保存实体 ID 和证据引用的字典。
    Raises: 输入结构缺失或数据库写入失败时抛出异常。
    副作用：写入 evidence、findings、risks、questions、recommendations 等实体。
    """
    # evidence_ref_map 把分析阶段的 client_ref 映射到数据库生成的 evidence ID。
    evidence_ref_map: dict[str, str] = {}
    # persisted_* 保存后续 state/verifier 需要的精简引用，而不是完整 ORM 对象。
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
    # 返回各类已持久化实体的引用摘要，供后续节点继续计算状态和质量分数。
    return {
        "evidence": persisted_evidence,
        "findings": persisted_findings,
        "risks": persisted_risks,
        "questions": persisted_questions,
        "recommendations": persisted_recommendations,
    }


def find_answered_question(session: Session, project_id: str, question: dict) -> Question | None:
    """查找项目中相同 prompt 的最新已回答问题，避免重复写入历史问题。

    Args: session 为数据库会话；project_id 为项目 ID；question 至少包含 prompt。
    Returns: 最新 Question；不存在时为 None。
    Raises: 数据库查询异常或缺少 prompt 时向上抛出。
    副作用：仅读取数据库；由分析结果持久化流程调用。
    """
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
