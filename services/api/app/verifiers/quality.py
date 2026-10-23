from app.research.profiles import get_research_profile


def clamp(value: int) -> int:
    """把质量分数限制在 0 到 100 的闭区间内。

    Args: value 为待限制的整数分数。
    Returns: 不小于 0 且不大于 100 的整数。
    Raises: 非整数输入可能在比较时抛出异常。
    副作用：无；由各维度评分函数调用。
    """
    return max(0, min(100, value))


def score_structure(snapshots: list[dict], findings: list[dict], recommendations: list[dict]) -> int:
    """按快照、发现和建议是否存在计算结构完整度。

    Args: 三个参数分别为采集快照、findings 和 recommendations 列表。
    Returns: 0 到 100 的结构分数。
    Raises: 无显式异常。
    副作用：无；由 `score_analysis_package` 调用。
    """
    score = 0
    if snapshots:
        score += 35
    if findings:
        score += 35
    if recommendations:
        score += 30
    return score


def score_evidence(evidence: list[dict], findings: list[dict]) -> int:
    """按 findings 中带有证据引用的比例计算证据覆盖分数。

    Args: evidence 为证据列表；findings 为发现列表。
    Returns: 0 到 100 的证据覆盖分数。
    Raises: 无显式异常。
    副作用：无；由 `score_analysis_package` 调用。
    """
    if not evidence or not findings:
        return 0
    supported = sum(1 for finding in findings if finding.get("evidence_refs"))
    return clamp(int((supported / len(findings)) * 100))


def score_codebase(codebase_maps: list[dict]) -> tuple[int, list[str]]:
    """根据技术栈、依赖、入口、README 和测试线索计算代码库覆盖度。

    Args: codebase_maps 为每个代码库的采集映射列表。
    Returns: `(平均分数, 待补充行动列表)`。
    Raises: 输入字段类型不符合预期时可能向上抛出。
    副作用：无；由 `score_analysis_package` 调用。
    """
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
    """按风险是否同时包含 severity 和 mitigation 计算透明度分数。

    Args: risks 为风险列表。
    Returns: 0 到 100 的风险透明度分数；无风险时为 100。
    Raises: 无显式异常。
    副作用：无；由 `score_analysis_package` 调用。
    """
    if not risks:
        return 100
    complete = sum(1 for risk in risks if risk.get("severity") and risk.get("mitigation"))
    return clamp(int((complete / len(risks)) * 100))


def score_questions(questions: list[dict]) -> tuple[int, list[str]]:
    """根据未回答问题及其影响级别计算问题解决度和后续行动。

    Args: questions 为问题列表，每项可含 status 与 impact。
    Returns: `(分数, 待处理行动列表)`。
    Raises: 无显式异常。
    副作用：无；由 `score_analysis_package` 调用。
    """
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
    """以建议中的最高 confidence 作为建议可信度维度分数。

    Args: recommendations 为建议列表，confidence 取值预期为 0 到 100。
    Returns: 0 到 100 的建议可信度分数；无建议时为 0。
    Raises: confidence 无法转换为整数时抛出异常。
    副作用：无；由 `score_analysis_package` 调用。
    """
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
    """按多个维度计算 Analysis Quality Rubric，结果可直接持久化和展示。

    Args: 各列表为当前分析包集合；previous_overall 可选上次总分；research_profile 为 profile key。
    Returns: 含 overall_score、各维度分数、原因和 next_actions 的字典。
    Raises: 输入集合结构异常时可能抛出 KeyError 或 TypeError。
    副作用：无；由 `verify_package_node` 和 evaluation benchmark 调用。
    """

    # profile 提供各质量维度的权重和展示清单，决定 overall_score 的计算方式。
    profile = get_research_profile(research_profile)
    next_actions: list[str] = []
    reasons: list[str] = []
    # 两个返回值分别是维度分数和待补充行动，后者会并入 next_actions。
    codebase_coverage, codebase_actions = score_codebase(codebase_maps)
    question_resolution, question_actions = score_questions(questions)
    # dimensions 保存各维度原始分数，既用于加权总分，也用于输出逐项诊断。
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

    # overall 是按 profile 权重得到的总分；score_delta 表示相对历史评分的变化。
    overall = int(sum(dimensions[name] * profile.quality_weights[name] for name in dimensions))
    score_delta = overall if previous_overall is None else overall - previous_overall
    # 返回可持久化的评分包，包含维度分数、总分、变化量、原因和后续行动。
    return {
        **dimensions,
        "overall_score": overall,#综合得分
        "score_delta": score_delta,#相对上次评分的变化量
        "reasons": reasons,          #评分不达标的原因
        "next_actions": next_actions,#后续行动建议
        "metadata": {
            "research_profile": profile.key,
            "quality_weights": dict(profile.quality_weights),
            "checklist": list(profile.checklist),
        },
    }


def dimension_label(name: str) -> str:
    """把质量维度键转换为报告展示用中文名称。

    Args: name 为内部质量维度键。
    Returns: 对应中文标签；未知键原样返回。
    Raises: 无显式异常。
    副作用：无；由质量评分摘要生成流程调用。
    """
    labels = {
        "structure_completeness": "结构完整度",
        "evidence_coverage": "证据覆盖",
        "codebase_coverage": "代码覆盖",
        "risk_transparency": "风险透明度",
        "question_resolution": "问题关闭率",
        "recommendation_confidence": "建议可信度",
    }
    return labels.get(name, name)
