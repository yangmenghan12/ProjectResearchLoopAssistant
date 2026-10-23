STARTABLE_PROJECT_STATUSES = {
    "draft",
    "ready",
    "ready_for_review",
    "ready_for_review_with_risks",
    "failed",
}


def can_start_run(project_status: str) -> bool:
    """判断项目当前状态是否允许创建新的分析 run。

    Args: project_status 为项目状态字符串。
    Returns: 状态属于可启动集合时为 True，否则为 False。
    Raises: 无显式异常。
    副作用：无；由 run 创建和 `ensure_can_start_run` 调用。
    """
    return project_status in STARTABLE_PROJECT_STATUSES


def ensure_can_start_run(project_status: str) -> None:
    """阻止在不允许的项目状态上重复启动分析。

    Args: project_status 为当前项目状态字符串。
    Returns: None，状态属于可启动集合时正常返回。
    Raises: 状态不允许启动时抛出 ValueError。
    副作用：无；由 run 创建流程调用。
    """
    if not can_start_run(project_status):
        raise ValueError(f"Cannot start a run while project status is {project_status}")


def has_open_high_impact_question(questions: list[dict]) -> bool:
    """检查问题列表中是否仍有 open 且 high impact 的问题。

    Args: questions 为分析包中的问题字典列表。
    Returns: 存在阻断性问题时为 True。
    Raises: 列表项不是映射对象时可能抛出属性异常。
    副作用：无；由终态决策函数调用。
    """
    return any(
        question.get("status") == "open" and question.get("impact") == "high"
        for question in questions
    )


def decide_terminal_state(
    snapshots: list[dict],
    findings: list[dict],
    risks: list[dict],
    questions: list[dict],
    recommendations: list[dict],
    quality_score: dict,
) -> dict:
    """基于外部证据和确定性评分决定停止状态，不依赖 LLM 自评。

    Args: snapshots/findings/risks/questions/recommendations 为分析包集合；quality_score 为评分字典。
    Returns: 含 `status` 与 `reason` 的终态决策字典。
    Raises: 输入结构不符合预期时可能抛出 KeyError 或 TypeError。
    副作用：无；由 LangGraph 的 `decide_next_state_node` 调用。
    """
    if not snapshots:
        return {
            "status": "failed",
            "reason": "没有可用的输入源采集快照。",
        }
    if has_open_high_impact_question(questions):
        return {
            "status": "paused_for_input",
            "reason": "存在高影响开放问题，需要用户输入。",
        }
    if not findings or not recommendations:
        return {
            "status": "failed",
            "reason": "本次运行没有形成发现和建议。",
        }

    overall_score = int(quality_score.get("overall_score", 0))
    if overall_score >= 80 and not risks:
        return {
            "status": "ready_for_review",
            "reason": "质量分达到评审阈值，且未记录风险。",
        }
    return {
        "status": "ready_for_review_with_risks",
        "reason": "分析包可进入评审，但仍存在评分缺口或已记录风险。",
    }
