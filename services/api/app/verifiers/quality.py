from app.research.profiles import get_research_profile


def clamp(value: int) -> int:
    return max(0, min(100, value))


def score_structure(snapshots: list[dict], findings: list[dict], recommendations: list[dict]) -> int:
    score = 0
    if snapshots:
        score += 35
    if findings:
        score += 35
    if recommendations:
        score += 30
    return score


def score_evidence(evidence: list[dict], findings: list[dict]) -> int:
    if not evidence or not findings:
        return 0
    supported = sum(1 for finding in findings if finding.get("evidence_refs"))
    return clamp(int((supported / len(findings)) * 100))


def score_codebase(codebase_maps: list[dict]) -> tuple[int, list[str]]:
    if not codebase_maps:
        return 100, []

    next_actions: list[str] = []
    per_map_scores: list[int] = []
    for codebase_map in codebase_maps:
        score = 0
        if codebase_map.get("tech_stack"):
            score += 25
        if codebase_map.get("dependency_files"):
            score += 20
        if codebase_map.get("entrypoint_files"):
            score += 20
        if codebase_map.get("readme_excerpt"):
            score += 20
        if codebase_map.get("test_files"):
            score += 15
        else:
            next_actions.append("为扫描到的代码库补充测试，或提供测试证据。")
        per_map_scores.append(score)
    return int(sum(per_map_scores) / len(per_map_scores)), next_actions


def score_risks(risks: list[dict]) -> int:
    if not risks:
        return 100
    complete = sum(1 for risk in risks if risk.get("severity") and risk.get("mitigation"))
    return clamp(int((complete / len(risks)) * 100))


def score_questions(questions: list[dict]) -> tuple[int, list[str]]:
    open_high = [
        question
        for question in questions
        if question.get("status") == "open" and question.get("impact") == "high"
    ]
    if open_high:
        return 40, ["评审前先回答高影响开放问题。"]
    open_any = [question for question in questions if question.get("status") == "open"]
    if open_any:
        return 70, ["处理剩余开放问题。"]
    return 100, []


def score_recommendations(recommendations: list[dict]) -> int:
    if not recommendations:
        return 0
    return clamp(max(int(recommendation.get("confidence", 0)) for recommendation in recommendations))


def score_analysis_package(
    snapshots: list[dict],
    codebase_maps: list[dict],
    evidence: list[dict],
    findings: list[dict],
    risks: list[dict],
    questions: list[dict],
    recommendations: list[dict],
    previous_overall: int | None,
    research_profile: str | None = None,
) -> dict:
    """计算 Analysis Quality Rubric；返回值可直接持久化和展示。"""

    profile = get_research_profile(research_profile)
    next_actions: list[str] = []
    reasons: list[str] = []
    codebase_coverage, codebase_actions = score_codebase(codebase_maps)
    question_resolution, question_actions = score_questions(questions)
    dimensions = {
        "structure_completeness": score_structure(snapshots, findings, recommendations),
        "evidence_coverage": score_evidence(evidence, findings),
        "codebase_coverage": codebase_coverage,
        "risk_transparency": score_risks(risks),
        "question_resolution": question_resolution,
        "recommendation_confidence": score_recommendations(recommendations),
    }
    for name, value in dimensions.items():
        if value < 80:
            reasons.append(f"{dimension_label(name)} 得分为 {value}。")
    next_actions.extend(codebase_actions)
    next_actions.extend(question_actions)
    if dimensions["evidence_coverage"] < 80:
        next_actions.append("为所有重要发现补充证据引用。")
    if dimensions["recommendation_confidence"] < 80:
        next_actions.append("通过更多证据或更低风险的范围提高建议可信度。")

    overall = int(sum(dimensions[name] * profile.quality_weights[name] for name in dimensions))
    score_delta = overall if previous_overall is None else overall - previous_overall
    return {
        **dimensions,
        "overall_score": overall,
        "score_delta": score_delta,
        "reasons": reasons,
        "next_actions": next_actions,
        "metadata": {
            "research_profile": profile.key,
            "quality_weights": dict(profile.quality_weights),
            "checklist": list(profile.checklist),
        },
    }


def dimension_label(name: str) -> str:
    labels = {
        "structure_completeness": "结构完整度",
        "evidence_coverage": "证据覆盖",
        "codebase_coverage": "代码覆盖",
        "risk_transparency": "风险透明度",
        "question_resolution": "问题关闭率",
        "recommendation_confidence": "建议可信度",
    }
    return labels.get(name, name)
