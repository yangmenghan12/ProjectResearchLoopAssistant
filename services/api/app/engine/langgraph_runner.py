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

    project_id: str  # 当前调研项目的唯一 ID。
    run_id: str  # 当前 Loop 运行记录的唯一 ID。
    context: dict[str, Any]  # 从已采集来源和审核反馈构建的分析上下文。
    analysis: dict[str, Any]  # 确定性分析与可接受的 LLM 补充结果。
    persisted: dict[str, Any]  # 已写入数据库的证据、发现、风险等实体引用。
    score: dict[str, Any]  # 当前分析包的确定性质量评分及 LLM 辅助意见。
    decision: dict[str, Any]  # 状态机决定的完成、暂停或失败结果。
    pending_interrupt: dict[str, Any]  # LangGraph interrupt 暂停时等待的恢复数据。


def execute_run_graph(
    session: Session,
    project_id: str,
    run_id: str,
    thread_id: str,
    checkpointer: SqlAlchemyCheckpointSaver | None = None,
) -> None:
    """通过真实 LangGraph 编排执行一次 Loop，并写入 checkpoint。

    Args: session 为数据库会话；project_id/run_id/thread_id 为非空 ID；checkpointer 可选。
    Returns: None，结果持久化到 run、turn、artifact 和 checkpoint。
    Raises: graph 节点或 checkpoint 写入失败时向上抛出异常。
    副作用：执行 LangGraph 并提交数据库步骤；由 worker 调用。
    """

    # token 用于在 graph 执行结束后恢复 ContextVar，避免污染同一线程的后续请求。
    token = _GRAPH_SESSION.set(session)
    # saver 负责 LangGraph 状态持久化；未传入时从业务 Session 的 bind 创建隔离工厂。
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
    """从 LangGraph interrupt checkpoint 恢复同一个 run/thread。

    Args: session/project_id/run_id/thread_id 标识原 run；resume_payload 为恢复输入；checkpointer 可选。
    Returns: None，恢复后的状态写回原 thread checkpoint。
    Raises: checkpoint 不存在或 graph 恢复失败时向上抛出异常。
    副作用：继续执行 LangGraph 并写入数据库；由 `resume_run` 调用。
    """

    # resume 仍使用业务 Session 执行节点，checkpoint saver 负责读取待恢复状态。
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
    *,
    continue_existing: bool = False,
) -> Generator[dict[str, Any], None, None]:
    """逐节点执行 LangGraph，并把节点完成状态暴露给外层 SSE。

    Args: session/project_id/run_id/thread_id 标识原 run；checkpointer 可选。
    Returns: 逐节点产生事件字典的生成器。
    Raises: graph 执行或数据库读取失败时在迭代过程中抛出异常。
    副作用：执行并持久化每个 graph step；由 SSE 响应生成器调用。
    """

    # stream 入口只构建一次 graph 和 saver，后续每个 chunk 对应一个节点更新。
    saver = checkpointer or SqlAlchemyCheckpointSaver.from_session_bind(session)
    graph = build_loop_graph(saver)
    initial = {"project_id": project_id, "run_id": run_id}
    if continue_existing and graph.get_state(graph_config(thread_id)).values:
        initial = None
    graph_events = graph.stream(
        initial,
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
        # chunk 是 LangGraph 的节点更新字典；这里只向 SSE 暴露节点名和数据库中的最新状态。
        for node_name in chunk:
            run = session.get(AnalysisRun, run_id)
            project = session.get(Project, project_id)
            # run/project 可能在异常或恢复场景下暂时不存在，因此状态字段使用 unknown 降级。
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
    """构建 Loop StateGraph，统一声明节点顺序和 interrupt 后的路由。

    Args: checkpointer 为可选 checkpoint saver；为空时构建无持久化编译图。
    Returns: 已编译的 LangGraph graph。
    Raises: 节点或 checkpointer 配置无效时抛出 graph 配置异常。
    副作用：创建内存中的 graph 对象；由执行、恢复和流式入口调用。
    """

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
    """构造 LangGraph 使用的 thread 配置，确保 checkpoint 属于同一运行线程。

    Args: thread_id 为非空运行线程 ID。
    Returns: 包含 thread_id 和空 checkpoint namespace 的 configurable 字典。
    Raises: 不主动抛出异常。
    副作用：无；由 graph 执行、恢复和 checkpoint 查询调用。
    """
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


def route_after_decision(state: LoopGraphState) -> str:
    """用户回答 interrupt 后回到上下文构建节点，避免沿用旧 decision。

    Args: state 为当前 LoopGraphState，可能含 pending_interrupt.resume。
    Returns: `rebuild_after_interrupt` 或 `persist` 路由名。
    Raises: 不主动抛出异常；异常 state 可能导致后续 graph 节点失败。
    副作用：无；由 LangGraph 条件边调用。
    """

    if state.get("pending_interrupt", {}).get("resume") is not None:
        return "rebuild_after_interrupt"
    return "persist"


def current_session() -> Session:
    """取得当前 graph 节点通过 ContextVar 注入的业务 Session。

    Args: 无。
    Returns: 当前执行上下文中的 SQLAlchemy Session。
    Raises: 节点在 graph 入口外调用时抛出 RuntimeError。
    副作用：无；由各 LangGraph node 调用。
    """
    session = _GRAPH_SESSION.get()
    if session is None:
        raise RuntimeError("Loop graph session is not configured")
    return session


def load_project_run(session: Session, state: LoopGraphState) -> tuple[Project, AnalysisRun]:
    """根据 graph state 加载并校验同一项目下的 Project 和 AnalysisRun。

    Args: session 为数据库会话；state 必须包含 project_id 和 run_id。
    Returns: `(Project, AnalysisRun)`。
    Raises: 任一实体不存在时抛出 ValueError。
    副作用：仅读取数据库；由各 graph node 调用。
    """
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
    """读取 thread 最新 checkpoint ID 并回写 AnalysisRun，便于查看和恢复。

    Args: session 为数据库会话；run_id/thread_id 标识运行；saver 为 checkpoint saver。
    Returns: None；找不到 checkpoint 时不更新。
    Raises: checkpoint 查询或数据库提交失败时抛出异常。
    副作用：更新 AnalysisRun.last_checkpoint_id 并提交事务；由 graph 入口调用。
    """
    latest = saver.get_tuple(graph_config(thread_id))
    if latest is None:
        return
    run = session.get(AnalysisRun, run_id)
    if run is not None:
        run.last_checkpoint_id = latest.config["configurable"].get("checkpoint_id")
        session.commit()


def load_state_node(state: LoopGraphState) -> dict[str, Any]:
    """加载项目与 run 的当前持久化状态，作为 graph 的起始上下文。

    Args: state 至少包含 `project_id` 和 `run_id`。
    Returns: 用于后续节点的状态增量字典。
    Raises: 项目或 run 不存在时抛出 ValueError。
    副作用：读取数据库并提交本节点事件；由 `build_loop_graph` 调用。
    """
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
    """从项目 inventory 和历史反馈构建本轮分析上下文。

    Args: state 为已加载项目/run 的 graph state。
    Returns: 含 snapshots、codebase_maps 等字段的 context 增量。
    Raises: 数据库查询或上下文构建失败时抛出异常。
    副作用：读取数据库并记录 graph turn；由 `load_state_node` 调用。
    """
    session = current_session()
    project, run = load_project_run(session, state)
    context = build_analysis_context(session, project.id)
    # Personal assistant reports use the exact evidence snapshot set of this task,
    # rather than mixing old source versions into the new report.
    from app.db.models import RunJob
    from sqlalchemy import select
    assistant_job = session.scalar(select(RunJob).where(RunJob.run_id == run.id,
        RunJob.job_type == "assistant").order_by(RunJob.created_at.desc()).limit(1))
    if assistant_job and assistant_job.payload_json.get("evidence_snapshot_ids"):
        snapshot_ids = set(assistant_job.payload_json["evidence_snapshot_ids"])
        context["snapshots"] = [s for s in context["snapshots"] if s["id"] in snapshot_ids]
        map_id = assistant_job.payload_json.get("codebase_map_id")
        context["codebase_maps"] = [m for m in context["codebase_maps"] if m["id"] == map_id]
        context["project"]["analysis_goal"] = assistant_job.payload_json.get("question") or project.analysis_goal
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
    """运行不依赖 LLM 的确定性分析，先建立可验证的分析基线。

    Args: state 必须包含已构建的 `context`。
    Returns: 含 `analysis` 的状态增量。
    Raises: 输入结构缺失或确定性分析失败时抛出异常。
    副作用：读取数据库、写入 turn；由 `build_context_node` 调用。
    """
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
    """调用可选 LLM 补充分析，并仅合并通过结构化校验的结果。

    Args: state 必须包含 context 和确定性 analysis。
    Returns: 含原始或合并后 `analysis` 的状态增量。
    Raises: required LLM 异常会被记录后降级为确定性结果。
    副作用：可能访问外部 LLM，写入账本、artifact 和 graph turn。
    """
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
    """把当前分析草稿写入实体表，供 verifier 和报告使用。

    Args: state 必须包含项目/run 标识和 `analysis`。
    Returns: 含 `persisted` 引用集合的状态增量。
    Raises: 分析结构或数据库写入失败时抛出异常。
    副作用：写入分析实体和 graph turn；由 LLM 分析节点调用。
    """
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
    """执行确定性质量评分，判断分析包是否具备进入审核的基础。

    Args: state 必须包含 context、persisted 和项目运行标识。
    Returns: 含 `score` 的状态增量。
    Raises: 评分输入缺失或数据库写入失败时抛出异常。
    副作用：读取分析实体并写入 QualityScore、graph turn；由更新节点调用。
    """
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
    """让 LLM 对确定性质量结果提供非阻塞的辅助复核意见。

    Args: state 必须包含 context、analysis、persisted 和 score。
    Returns: 含 `score.llm_assisted` 的状态增量。
    Raises: LLM 或 JSON 校验错误会被转为辅助复核失败状态。
    副作用：可能访问外部 LLM，并写入账本和 graph turn。
    """
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
    """根据分析包和质量评分决定完成、暂停或继续路径。

    Args: state 必须包含 context、persisted、score 和项目运行标识。
    Returns: 含 `decision` 的状态增量；暂停时还包含 interrupt 信息。
    Raises: graph 状态缺失时抛出 KeyError；用户输入通过 interrupt 暂停。
    副作用：更新 run 状态、写入 turn，必要时触发 LangGraph interrupt。
    """
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
    """将终态决策写回项目和 run，并记录停止事件。

    Args: state 必须包含项目运行标识和 `decision`。
    Returns: 空状态增量字典。
    Raises: 终态缺失或数据库写入失败时抛出异常。
    副作用：更新项目/run 状态、完成时间并写入审计事件；由终态路由调用。
    """
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
    """生成包含节点顺序和编排器标识的统一 turn metadata。

    Args: node_name 为 graph 节点名；extra 为可选的节点专属元数据。
    Returns: 合并后的 metadata 字典。
    Raises: 不主动抛出异常；未知节点使用末尾顺序。
    副作用：无；由各 graph node 记录 turn 时调用。
    """
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
