from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any, Generator, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy.orm import Session

from app.context.builder import build_analysis_context
from app.db.models import AnalysisRun, LLMAnalysisArtifact, LLMCall, Project, QualityScore, utc_now
from app.engine.analyzer import analyze_context
from app.engine.checkpointer import SqlAlchemyCheckpointSaver
from app.engine.llm_merge import merge_llm_analysis
from app.engine.runner import NODE_ORDER, append_turn, latest_previous_score, persist_analysis_artifacts
from app.engine.state_machine import decide_terminal_state
from app.llm.analyzer import validate_llm_analysis_output
from app.llm.json_tools import extract_json_object
from app.llm.router import LLMRouter
from app.services.audit import record_event
from app.verifiers.llm_assisted import validate_llm_verifier_output
from app.verifiers.quality import score_analysis_package

LANGGRAPH_NODE_ORDER = NODE_ORDER
ORCHESTRATOR_NAME = "langgraph"

_GRAPH_SESSION: ContextVar[Session | None] = ContextVar("loop_graph_session", default=None)


class LoopGraphState(TypedDict, total=False):
    """可 checkpoint 的 LangGraph 运行态，只包含 ID 和 JSON 结构。

    数据库 session 通过 context variable 注入节点执行环境，不写入 graph state。
    这样既能复用 FastAPI 测试注入的 session，也能让 checkpoint 只保存可序列化数据。
    """

    project_id: str
    run_id: str
    context: dict[str, Any]
    analysis: dict[str, Any]
    persisted: dict[str, Any]
    score: dict[str, Any]
    decision: dict[str, Any]
    pending_interrupt: dict[str, Any]


def execute_run_graph(
    session: Session,
    project_id: str,
    run_id: str,
    thread_id: str,
    checkpointer: SqlAlchemyCheckpointSaver | None = None,
) -> None:
    """通过真实 LangGraph 编排执行一次 Loop，并写入 checkpoint。"""

    token = _GRAPH_SESSION.set(session)
    saver = checkpointer or SqlAlchemyCheckpointSaver.from_session_bind(session)
    try:
        graph = build_loop_graph(saver)
        graph.invoke({"project_id": project_id, "run_id": run_id}, graph_config(thread_id), durability="sync")
        update_run_checkpoint_id(session, run_id, thread_id, saver)
    finally:
        _GRAPH_SESSION.reset(token)


def resume_run_graph(
    session: Session,
    project_id: str,
    run_id: str,
    thread_id: str,
    resume_payload: dict[str, Any],
    checkpointer: SqlAlchemyCheckpointSaver | None = None,
) -> None:
    """从 LangGraph interrupt checkpoint 恢复同一个 run/thread。"""

    token = _GRAPH_SESSION.set(session)
    saver = checkpointer or SqlAlchemyCheckpointSaver.from_session_bind(session)
    try:
        graph = build_loop_graph(saver)
        graph.invoke(Command(resume=resume_payload), graph_config(thread_id), durability="sync")
        update_run_checkpoint_id(session, run_id, thread_id, saver)
    finally:
        _GRAPH_SESSION.reset(token)


def stream_run_graph_events(
    session: Session,
    project_id: str,
    run_id: str,
    thread_id: str,
    checkpointer: SqlAlchemyCheckpointSaver | None = None,
) -> Generator[dict[str, Any], None, None]:
    """逐节点执行 LangGraph，并把节点完成状态暴露给外层 SSE。"""

    saver = checkpointer or SqlAlchemyCheckpointSaver.from_session_bind(session)
    graph = build_loop_graph(saver)
    graph_events = graph.stream(
        {"project_id": project_id, "run_id": run_id},
        graph_config(thread_id),
        stream_mode="updates",
        durability="sync",
    )
    while True:
        token = _GRAPH_SESSION.set(session)
        try:
            try:
                chunk = next(graph_events)
            except StopIteration:
                break
        finally:
            _GRAPH_SESSION.reset(token)
        for node_name in chunk:
            run = session.get(AnalysisRun, run_id)
            project = session.get(Project, project_id)
            yield {
                "run_id": run_id,
                "project_id": project_id,
                "node_name": node_name,
                "turn_index": run.current_turn if run else 0,
                "run_status": run.status if run else "unknown",
                "project_status": project.status if project else "unknown",
                "orchestrator": ORCHESTRATOR_NAME,
            }
    update_run_checkpoint_id(session, run_id, thread_id, saver)


def build_loop_graph(checkpointer: SqlAlchemyCheckpointSaver | None = None):
    """构建 Loop StateGraph。"""

    builder = StateGraph(LoopGraphState)
    builder.add_node("load_state", load_state_node)
    builder.add_node("build_context", build_context_node)
    builder.add_node("analyze_sources", analyze_sources_node)
    builder.add_node("llm_analyze_sources", llm_analyze_sources_node)
    builder.add_node("update_analysis", update_analysis_node)
    builder.add_node("verify_package", verify_package_node)
    builder.add_node("llm_verify_package", llm_verify_package_node)
    builder.add_node("decide_next_state", decide_next_state_node)
    builder.add_node("persist_state", persist_state_node)

    builder.add_edge(START, "load_state")
    builder.add_edge("load_state", "build_context")
    builder.add_edge("build_context", "analyze_sources")
    builder.add_edge("analyze_sources", "llm_analyze_sources")
    builder.add_edge("llm_analyze_sources", "update_analysis")
    builder.add_edge("update_analysis", "verify_package")
    builder.add_edge("verify_package", "llm_verify_package")
    builder.add_edge("llm_verify_package", "decide_next_state")
    builder.add_conditional_edges(
        "decide_next_state",
        route_after_decision,
        {
            "rebuild_after_interrupt": "build_context",
            "persist": "persist_state",
        },
    )
    builder.add_edge("persist_state", END)
    if checkpointer is None:
        return builder.compile()
    return builder.compile(checkpointer=checkpointer)


def graph_config(thread_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


def route_after_decision(state: LoopGraphState) -> str:
    """用户回答 interrupt 后回到上下文构建节点，避免持久化中断前的旧 decision。"""

    if state.get("pending_interrupt", {}).get("resume") is not None:
        return "rebuild_after_interrupt"
    return "persist"


def current_session() -> Session:
    session = _GRAPH_SESSION.get()
    if session is None:
        raise RuntimeError("Loop graph session is not configured")
    return session


def load_project_run(session: Session, state: LoopGraphState) -> tuple[Project, AnalysisRun]:
    project = session.get(Project, state["project_id"])
    run = session.get(AnalysisRun, state["run_id"])
    if project is None:
        raise ValueError("Project not found")
    if run is None:
        raise ValueError("Run not found")
    return project, run


def commit_graph_step(session: Session) -> None:
    """每个 LangGraph 节点结束时提交业务事实，保证 checkpoint 对应的状态可恢复。"""

    session.commit()


def update_run_checkpoint_id(
    session: Session,
    run_id: str,
    thread_id: str,
    saver: SqlAlchemyCheckpointSaver,
) -> None:
    latest = saver.get_tuple(graph_config(thread_id))
    if latest is None:
        return
    run = session.get(AnalysisRun, run_id)
    if run is not None:
        run.last_checkpoint_id = latest.config["configurable"].get("checkpoint_id")
        session.commit()


def load_state_node(state: LoopGraphState) -> dict[str, Any]:
    session = current_session()
    project, run = load_project_run(session, state)
    append_turn(
        session,
        run,
        project,
        "load_state",
        "加载项目与运行状态",
        f"项目状态为 {project.status}。",
        node_metadata("load_state"),
    )
    commit_graph_step(session)
    return {}


def build_context_node(state: LoopGraphState) -> dict[str, Any]:
    session = current_session()
    project, run = load_project_run(session, state)
    context = build_analysis_context(session, project.id)
    append_turn(
        session,
        run,
        project,
        "build_context",
        "根据采集清单构建上下文包",
        f"已加载 {len(context['snapshots'])} 个快照和 {len(context['codebase_maps'])} 个代码库地图。",
        node_metadata("build_context"),
    )
    commit_graph_step(session)
    return {"context": context, "pending_interrupt": {}}


def analyze_sources_node(state: LoopGraphState) -> dict[str, Any]:
    session = current_session()
    project, run = load_project_run(session, state)
    analysis = analyze_context(state["context"])
    append_turn(
        session,
        run,
        project,
        "analyze_sources",
        "分析已采集的输入事实",
        (
            f"生成了 {len(analysis['evidence'])} 条证据草稿、"
            f"{len(analysis['findings'])} 条发现和 {len(analysis['risks'])} 个风险。"
        ),
        node_metadata("analyze_sources"),
    )
    commit_graph_step(session)
    return {"analysis": analysis}


def llm_analyze_sources_node(state: LoopGraphState) -> dict[str, Any]:
    session = current_session()
    project, run = load_project_run(session, state)
    analysis = state["analysis"]
    allowed_refs = {
        item["client_ref"]
        for item in analysis.get("evidence", [])
        if isinstance(item, dict) and isinstance(item.get("client_ref"), str)
    }
    messages = [
        {
            "role": "system",
            "content": (
                "你是 Loop Engineering 的智能分析增强节点。只输出一个 JSON 对象，不要使用 Markdown。"
                "只能基于用户提供的上下文和已有证据，不要编造证据、文件、指标或外部事实。"
                "引用已有证据时必须使用现有 client_ref；如果需要新增派生证据，client_ref 必须以 llm: 开头。"
            ),
        },
        {
            "role": "user",
            "content": build_llm_analyzer_prompt(state["context"], analysis),
        },
    ]

    try:
        result = LLMRouter(session).chat(
            task_type="analyzer",
            project_id=project.id,
            run_id=run.id,
            report_id=None,
            messages=messages,
            input_refs=[f"project:{project.id}", f"run:{run.id}"],
        )
    except Exception as exc:
        # 路由层理论上会把非 required 调用降级为 failed；这里兜底保证图运行不中断。
        result = {
            "status": "failed",
            "call_id": None,
            "text": "",
            "usage": {},
            "error_message": str(exc),
        }

    llm_status = result.get("status")
    call_id = result.get("call_id")
    if llm_status != "succeeded":
        append_turn(
            session,
            run,
            project,
            "llm_analyze_sources",
            "智能分析增强",
            f"LLM 调用状态为 {llm_status or 'unknown'}，已回退到确定性分析。",
            node_metadata(
                "llm_analyze_sources",
                {
                    "accepted": False,
                    "llm_status": llm_status,
                    "call_id": call_id,
                    "error_message": result.get("error_message"),
                },
            ),
        )
        commit_graph_step(session)
        return {"analysis": analysis}

    try:
        payload = extract_json_object(result.get("text", ""))
        validation = validate_llm_analysis_output(payload, allowed_refs)
    except Exception as exc:
        validation = validate_llm_analysis_output({}, allowed_refs)
        validation.validation["messages"].append(f"解析 LLM JSON 失败：{exc}")

    merged_analysis = analysis
    if validation.accepted:
        try:
            merged_analysis = merge_llm_analysis(analysis, validation.content)
        except Exception as exc:
            # accepted=True 也必须先成功合并才允许进入 Analysis Package。
            validation.accepted = False
            validation.validation["passed"] = False
            validation.validation["messages"].append(f"合并 LLM 分析失败：{exc}")

    artifact_call_id = normalize_llm_call_id(session, call_id)
    session.add(
        LLMAnalysisArtifact(
            project_id=project.id,
            run_id=run.id,
            call_id=artifact_call_id,
            artifact_type="analyzer_output",
            content_json=validation.content,
            validation_result_json=validation.validation,
            accepted=validation.accepted,
        )
    )
    append_turn(
        session,
        run,
        project,
        "llm_analyze_sources",
        "智能分析增强",
        (
            "LLM 输出已通过校验并合并进分析包草稿。"
            if validation.accepted
            else "LLM 输出未通过校验，已记录产物并回退到确定性分析。"
        ),
        node_metadata(
            "llm_analyze_sources",
            {
                "accepted": validation.accepted,
                "llm_status": llm_status,
                "call_id": call_id,
                "validation": validation.validation,
            },
        ),
    )
    commit_graph_step(session)
    return {"analysis": merged_analysis}


def normalize_llm_call_id(session: Session, call_id: Any) -> str | None:
    """LLM 产物的 call_id 是外键，只有账本里存在的调用才能持久化引用。"""

    if not isinstance(call_id, str) or not call_id:
        return None
    if session.get(LLMCall, call_id) is None:
        return None
    return call_id


def build_llm_analyzer_prompt(context: dict[str, Any], analysis: dict[str, Any]) -> str:
    """构造分析增强提示词，让 LLM 只能围绕已采集事实和现有 evidence refs 补充结构化判断。"""

    prompt_payload = {
        "project": context.get("project", {}),
        "snapshots": context.get("snapshots", []),
        "codebase_maps": context.get("codebase_maps", []),
        "review_feedback": context.get("review_feedback", []),
        "deterministic_analysis": analysis,
        "output_schema": {
            "reasoning_summary": "string，可选，简述推理但不要泄露链式思考",
            "coverage_notes": ["string，可选，说明没有覆盖的事实边界"],
            "evidence": [
                {
                    "client_ref": "llm:evidence:short-name",
                    "title": "string",
                    "summary": "string",
                    "evidence_type": "llm_analysis",
                    "confidence": 0,
                    "metadata": {},
                }
            ],
            "findings": [
                {
                    "title": "string",
                    "summary": "string",
                    "category": "string",
                    "impact_level": "low|medium|high",
                    "confidence": 0,
                    "evidence_refs": ["existing client_ref or llm:*"],
                    "metadata": {},
                }
            ],
            "risks": [
                {
                    "title": "string",
                    "summary": "string",
                    "severity": "low|medium|high",
                    "mitigation": "string",
                    "evidence_refs": ["existing client_ref or llm:*"],
                    "metadata": {},
                }
            ],
            "questions": [
                {
                    "prompt": "string",
                    "reason": "string",
                    "impact": "low|medium|high",
                    "status": "open",
                    "metadata": {},
                }
            ],
            "recommendations": [
                {
                    "summary": "string",
                    "rationale": "string",
                    "confidence": 0,
                    "next_steps": ["string"],
                    "metadata": {},
                }
            ],
        },
    }
    return (
        "请基于下面的项目上下文和确定性分析结果，输出一个可被系统解析的 JSON 对象。\n"
        "硬性要求：\n"
        "1. 只输出 JSON 对象，不要解释、不要 Markdown 代码块。\n"
        "2. 不要编造上下文中不存在的源文件、测试、指标、依赖或外部事实。\n"
        "3. findings 和 risks 的 evidence_refs 只能引用 deterministic_analysis.evidence 中的 client_ref，"
        "或引用本次新增的 llm:* evidence client_ref。\n"
        "4. 如果没有可靠补充，请返回所有集合为空数组，但仍保留必需键。\n\n"
        f"{json.dumps(prompt_payload, ensure_ascii=False, indent=2, default=str)}"
    )


def update_analysis_node(state: LoopGraphState) -> dict[str, Any]:
    session = current_session()
    project, run = load_project_run(session, state)
    persisted = persist_analysis_artifacts(session, project, run, state["analysis"])
    append_turn(
        session,
        run,
        project,
        "update_analysis",
        "保存分析包草稿",
        "已保存证据、发现、风险、问题和建议草稿。",
        node_metadata("update_analysis"),
    )
    commit_graph_step(session)
    return {"persisted": persisted}


def verify_package_node(state: LoopGraphState) -> dict[str, Any]:
    session = current_session()
    project, run = load_project_run(session, state)
    previous_score = latest_previous_score(session, run)
    score = score_analysis_package(
        snapshots=state["context"]["snapshots"],
        codebase_maps=state["context"]["codebase_maps"],
        evidence=state["persisted"]["evidence"],
        findings=state["persisted"]["findings"],
        risks=state["persisted"]["risks"],
        questions=state["persisted"]["questions"],
        recommendations=state["persisted"]["recommendations"],
        previous_overall=previous_score,
        research_profile=project.research_profile,
    )
    verify_turn = append_turn(
        session,
        run,
        project,
        "verify_package",
        "计算分析质量评分",
        f"总体得分为 {score['overall_score']}。",
        node_metadata("verify_package"),
    )
    session.add(
        QualityScore(
            project_id=project.id,
            run_id=run.id,
            turn_id=verify_turn.id,
            overall_score=score["overall_score"],
            structure_completeness=score["structure_completeness"],
            evidence_coverage=score["evidence_coverage"],
            codebase_coverage=score["codebase_coverage"],
            risk_transparency=score["risk_transparency"],
            question_resolution=score["question_resolution"],
            recommendation_confidence=score["recommendation_confidence"],
            score_delta=score["score_delta"],
            reasons_json=score["reasons"],
            next_actions_json=score["next_actions"],
            metadata_json=score.get("metadata", {}),
        )
    )
    commit_graph_step(session)
    return {"score": score}


def llm_verify_package_node(state: LoopGraphState) -> dict[str, Any]:
    session = current_session()
    project, run = load_project_run(session, state)
    score = dict(state["score"])
    messages = [
        {
            "role": "system",
            "content": (
                "你是 Loop Engineering 的 LLM 辅助复核节点。只输出一个 JSON 对象，不要使用 Markdown。"
                "确定性校验已经完成，你只能提供第二意见、风险提醒和下一步建议，不能覆盖确定性阻塞结论。"
                "不要编造上下文中不存在的证据、文件、指标或外部事实。"
            ),
        },
        {
            "role": "user",
            "content": build_llm_verifier_prompt(project, state),
        },
    ]

    try:
        result = LLMRouter(session).chat(
            task_type="verifier",
            project_id=project.id,
            run_id=run.id,
            report_id=None,
            messages=messages,
            input_refs=[f"run:{run.id}", "analysis_package"],
        )
    except Exception as exc:
        result = {
            "status": "failed",
            "call_id": None,
            "text": "",
            "usage": {},
            "error_message": sanitize_llm_verifier_error(exc),
        }

    call_id = result.get("call_id")
    llm_status = result.get("status") or "unknown"
    if llm_status != "succeeded":
        llm_assisted = {
            "status": llm_status,
            "issues": [],
            "summary": "",
        }
    else:
        try:
            payload = extract_json_object(result.get("text", ""))
            llm_assisted = validate_llm_verifier_output(payload)
            llm_assisted["status"] = "succeeded"
        except Exception as exc:
            llm_assisted = {
                "status": "validation_failed",
                "passed": False,
                "confidence": 0.0,
                "issues": [
                    {
                        "severity": "warning",
                        "message": f"LLM verifier output validation failed: {sanitize_llm_verifier_error(exc)}",
                    }
                ],
                "suggested_next_actions": [],
                "summary": "",
            }

    score["llm_assisted"] = llm_assisted
    append_turn(
        session,
        run,
        project,
        "llm_verify_package",
        "智能复核分析包",
        llm_verifier_observation(llm_assisted),
        node_metadata(
            "llm_verify_package",
            {
                "llm_assisted": llm_assisted,
                "call_id": call_id,
            },
        ),
    )
    commit_graph_step(session)
    return {"score": score}


def build_llm_verifier_prompt(project: Project, state: LoopGraphState) -> str:
    """构造复核提示词，只给 LLM 已沉淀的分析包和确定性评分。"""

    prompt_payload = {
        "project": {
            "id": project.id,
            "name": project.name,
            "topic": project.topic,
            "analysis_goal": project.analysis_goal,
        },
        "context": {
            "snapshots": state.get("context", {}).get("snapshots", []),
            "codebase_maps": state.get("context", {}).get("codebase_maps", []),
        },
        "analysis_package": {
            "analysis": state.get("analysis", {}),
            "persisted": state.get("persisted", {}),
            "quality_score": state.get("score", {}),
        },
        "output_schema": {
            "passed": "boolean，表示 LLM 第二意见是否认为分析包可进入人工复核",
            "confidence": "number，0 到 1 之间",
            "issues": [
                {
                    "severity": "info|warning|error",
                    "message": "string，必须非空；只能描述复核提醒，不能宣称覆盖确定性结论",
                }
            ],
            "suggested_next_actions": ["string"],
            "summary": "string，简短说明 LLM 复核结论",
        },
    }
    return (
        "请基于下面的 Analysis Package 和确定性质量评分做一次辅助复核，输出可解析 JSON 对象。\n"
        "硬性要求：\n"
        "1. 只输出 JSON 对象，不要解释，不要 Markdown 代码块。\n"
        "2. 不能修改、覆盖或否定确定性校验的阻塞结论；如发现问题，只能写入 issues。\n"
        "3. 不要引入上下文中没有的证据、文件路径、测试结果、外部事实或性能指标。\n"
        "4. 如果没有额外提醒，issues 和 suggested_next_actions 返回空数组。\n\n"
        f"{json.dumps(prompt_payload, ensure_ascii=False, indent=2, default=str)}"
    )


def llm_verifier_observation(llm_assisted: dict[str, Any]) -> str:
    status = llm_assisted.get("status", "unknown")
    issue_count = len(llm_assisted.get("issues", [])) if isinstance(llm_assisted.get("issues"), list) else 0
    if status == "succeeded":
        return f"LLM 辅助复核完成，记录 {issue_count} 条提醒。"
    if status == "validation_failed":
        return "LLM 辅助复核输出未通过校验，已记录为提醒。"
    return f"LLM 辅助复核状态为 {status}，已保留确定性校验结果。"


def sanitize_llm_verifier_error(exc: Exception) -> str:
    return str(exc).replace("\r", " ").replace("\n", " ").strip()[:300]


def decide_next_state_node(state: LoopGraphState) -> dict[str, Any]:
    session = current_session()
    project, run = load_project_run(session, state)
    decision = decide_terminal_state(
        snapshots=state["context"]["snapshots"],
        findings=state["persisted"]["findings"],
        risks=state["persisted"]["risks"],
        questions=state["persisted"]["questions"],
        recommendations=state["persisted"]["recommendations"],
        quality_score=state["score"],
    )
    append_turn(
        session,
        run,
        project,
        "decide_next_state",
        "决定停止、暂停或进入评审",
        decision["reason"],
        node_metadata("decide_next_state", {"decision": decision}),
    )
    if decision["status"] == "paused_for_input":
        run.status = "paused_for_input"
        run.stop_reason = decision["reason"]
        project.status = "paused_for_input"
        append_turn(
            session,
            run,
            project,
            "interrupt_for_input",
            "等待用户补充关键输入",
            decision["reason"],
            node_metadata(
                "interrupt_for_input",
                {"pause_source": "langgraph_interrupt", "decision": decision},
            ),
        )
        commit_graph_step(session)
        open_questions = [
            question
            for question in state["persisted"]["questions"]
            if question.get("status") == "open"
        ]
        resume_payload = interrupt(
            {
                "run_id": run.id,
                "project_id": project.id,
                "reason": decision["reason"],
                "questions": open_questions,
            }
        )
        return {
            "pending_interrupt": {"resume": resume_payload},
            "decision": decision,
        }
    commit_graph_step(session)
    return {"decision": decision}


def persist_state_node(state: LoopGraphState) -> dict[str, Any]:
    session = current_session()
    project, run = load_project_run(session, state)
    decision = state["decision"]
    run.status = decision["status"]
    run.stop_reason = decision["reason"]
    project.status = decision["status"]
    if run.status != "paused_for_input":
        run.completed_at = utc_now()
    append_turn(
        session,
        run,
        project,
        "persist_state",
        "保存最终运行状态",
        f"运行已停止，状态为 {run.status}。",
        node_metadata("persist_state"),
    )
    record_event(
        session,
        event_type="run.stopped",
        message="Loop 运行已停止",
        project_id=project.id,
        payload={"run_id": run.id, "status": run.status, "reason": run.stop_reason},
    )
    commit_graph_step(session)
    return {}


def node_metadata(node_name: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    node_order = (
        LANGGRAPH_NODE_ORDER.index(node_name) + 1
        if node_name in LANGGRAPH_NODE_ORDER
        else len(LANGGRAPH_NODE_ORDER) + 1
    )
    metadata: dict[str, Any] = {
        "node_order": node_order,
        "orchestrator": ORCHESTRATOR_NAME,
    }
    if extra:
        metadata.update(extra)
    return metadata
