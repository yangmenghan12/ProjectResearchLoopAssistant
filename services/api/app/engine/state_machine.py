STARTABLE_PROJECT_STATUSES = {
    "draft",
    "ready",
    "ready_for_review",
    "ready_for_review_with_risks",
    "failed",
}


def can_start_run(project_status: str) -> bool:
    return project_status in STARTABLE_PROJECT_STATUSES


def ensure_can_start_run(project_status: str) -> None:
    if not can_start_run(project_status):
        raise ValueError(f"Cannot start a run while project status is {project_status}")


def has_open_high_impact_question(questions: list[dict]) -> bool:
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
    """基于外部证据和确定性评分决定停止状态，不依赖 LLM 自评。"""

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
