from collections.abc import Iterable
from typing import Any

from app.research.profiles import ResearchProfile, get_research_profile
from app.verifiers.llm_assisted import merge_llm_verifier_result


def verify_analysis_package(package: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    """对 Analysis Package 做确定性校验。

    这里不让 LLM 自己判断“我完成了”，而是检查已落库的证据、发现、风险、
    问题和质量分数。warning 会影响风险等级，但不会阻塞人工评审入口。
    """

    profile = resolve_research_profile(package)
    checks: list[dict[str, Any]] = []
    checks.append(check_structure(package))
    checks.append(check_evidence_coverage(package))
    checks.append(check_codebase_coverage(inventory))
    checks.append(check_risk_transparency(package))
    checks.append(check_question_resolution(package))
    checks.append(check_quality_score(package))
    checks.extend(check_profile_requirements(profile, package, inventory))

    blocking_reasons = [
        message
        for check in checks
        if not check["passed"] and check["severity"] == "error"
        for message in check["messages"]
    ]
    warning_reasons = [
        message
        for check in checks
        if not check["passed"] and check["severity"] == "warning"
        for message in check["messages"]
    ]
    passed = not blocking_reasons
    if blocking_reasons:
        risk_level = "high"
    elif warning_reasons or has_high_or_critical_risk(package):
        risk_level = "medium"
    else:
        risk_level = "low"

    deterministic_result = {
        "passed": passed,
        "risk_level": risk_level,
        "checks": checks,
        "research_profile": profile_summary(profile),
        "summary": build_summary(passed, risk_level, blocking_reasons, warning_reasons),
        "blocking_reasons": blocking_reasons,
        "warning_reasons": warning_reasons,
    }
    return merge_llm_verifier_result(deterministic_result, latest_llm_assisted_result(package))


def resolve_research_profile(package: dict[str, Any]) -> ResearchProfile:
    """从 package/run/project/quality metadata 中解析调研档位，未知值回落默认档位。"""

    candidates: list[Any] = [value_of(package, "research_profile")]
    for owner_key in ("project", "run"):
        owner = value_of(package, owner_key, {})
        candidates.append(value_of(owner, "research_profile"))
        candidates.extend(metadata_profile_candidates(owner))
    candidates.extend(metadata_profile_candidates(package))

    latest_score = latest_item(value_of(package, "quality_scores", []))
    if latest_score is not None:
        candidates.extend(metadata_profile_candidates(latest_score))

    for candidate in candidates:
        key = normalize_profile_key(candidate)
        if key:
            return get_research_profile(key)
    return get_research_profile(None)


def metadata_profile_candidates(item: Any) -> list[Any]:
    candidates: list[Any] = []
    for key in ("metadata", "metadata_json"):
        metadata = value_of(item, key, {})
        if isinstance(metadata, dict):
            candidates.append(metadata.get("research_profile"))
    return candidates


def normalize_profile_key(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict):
        nested = value.get("key") or value.get("research_profile")
        if isinstance(nested, str) and nested:
            return nested
    return None


def profile_summary(profile: ResearchProfile) -> dict[str, Any]:
    return {
        "key": profile.key,
        "label": profile.label,
        "checklist": list(profile.checklist),
    }


def check_profile_requirements(
    profile: ResearchProfile,
    package: dict[str, Any],
    inventory: dict[str, Any],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for check_name in profile.verifier_checks:
        handler = PROFILE_CHECK_HANDLERS.get(check_name)
        passed = bool(handler(package, inventory)) if handler else False
        checks.append(
            check_result(
                name=f"profile:{check_name}",
                passed=passed,
                severity="info" if passed else "warning",
                messages=[
                    (
                        f"Profile check `{check_name}` has deterministic clues."
                        if passed
                        else f"Profile check `{check_name}` lacks deterministic clues."
                    )
                ],
            )
        )
    return checks


def codebase_has_list(inventory: dict[str, Any], *keys: str) -> bool:
    for codebase_map in as_list(inventory.get("codebase_maps")):
        for key in keys:
            if as_list(value_of(codebase_map, key, [])):
                return True
    return False


def finding_category_contains_any(package: dict[str, Any], keywords: list[str]) -> bool:
    categories = [
        str(value_of(finding, "category", "")).lower()
        for finding in as_list(package.get("findings"))
    ]
    return any(
        keyword.lower() in category
        for category in categories
        for keyword in keywords
    )


def has_entrypoint(package: dict[str, Any], inventory: dict[str, Any]) -> bool:
    return codebase_has_list(inventory, "entrypoint_files_json", "entrypoint_files")


def has_dependency_clues(package: dict[str, Any], inventory: dict[str, Any]) -> bool:
    return codebase_has_list(inventory, "dependency_files_json", "dependency_files")


def has_module_boundaries(package: dict[str, Any], inventory: dict[str, Any]) -> bool:
    return (
        codebase_has_list(inventory, "file_tree_json", "file_tree")
        or finding_category_contains_any(package, ["architecture", "module", "boundary"])
    )


def has_architecture_clues(package: dict[str, Any], inventory: dict[str, Any]) -> bool:
    return has_module_boundaries(package, inventory) or has_entrypoint(package, inventory) or codebase_has_list(
        inventory,
        "dependency_files_json",
        "dependency_files",
        "config_files_json",
        "config_files",
    )


def has_test_clues(package: dict[str, Any], inventory: dict[str, Any]) -> bool:
    return codebase_has_list(inventory, "test_files_json", "test_files") or any(
        "test" in str(value_of(evidence, "evidence_type", "")).lower()
        for evidence in as_list(package.get("evidence_items"))
    )


def has_risk_register(package: dict[str, Any], inventory: dict[str, Any]) -> bool:
    return bool(as_list(package.get("risks")))


def has_mitigation(package: dict[str, Any], inventory: dict[str, Any]) -> bool:
    return any(bool(value_of(risk, "mitigation")) for risk in as_list(package.get("risks")))


def has_key_files(package: dict[str, Any], inventory: dict[str, Any]) -> bool:
    return codebase_has_list(
        inventory,
        "entrypoint_files_json",
        "entrypoint_files",
        "dependency_files_json",
        "dependency_files",
        "config_files_json",
        "config_files",
        "test_files_json",
        "test_files",
        "file_tree_json",
        "file_tree",
    )


def has_learning_path(package: dict[str, Any], inventory: dict[str, Any]) -> bool:
    return finding_category_contains_any(package, ["learning_path", "teaching"]) or has_reading_path(package, inventory)


def has_exercises(package: dict[str, Any], inventory: dict[str, Any]) -> bool:
    return finding_category_contains_any(package, ["exercise", "practice"]) or codebase_has_list(
        inventory,
        "test_files_json",
        "test_files",
    )


def has_runbook_clues(package: dict[str, Any], inventory: dict[str, Any]) -> bool:
    return (
        codebase_has_list(
            inventory,
            "entrypoint_files_json",
            "entrypoint_files",
            "config_files_json",
            "config_files",
            "dependency_files_json",
            "dependency_files",
        )
        or any(bool(value_of(codebase_map, "readme_excerpt")) for codebase_map in as_list(inventory.get("codebase_maps")))
    )


def has_open_questions(package: dict[str, Any], inventory: dict[str, Any]) -> bool:
    return not any(
        value_of(question, "status") == "open" and value_of(question, "impact") == "high"
        for question in as_list(package.get("questions"))
    )


def has_delivery_readiness_summary(package: dict[str, Any], inventory: dict[str, Any]) -> bool:
    return finding_category_contains_any(package, ["delivery_readiness", "delivery", "acceptance"])


def has_reading_path(package: dict[str, Any], inventory: dict[str, Any]) -> bool:
    return (
        finding_category_contains_any(package, ["reading_path", "onboarding"])
        or codebase_has_list(
            inventory,
            "entrypoint_files_json",
            "entrypoint_files",
            "config_files_json",
            "config_files",
            "test_files_json",
            "test_files",
        )
    )


PROFILE_CHECK_HANDLERS = {
    "has_entrypoint": has_entrypoint,
    "has_dependency_clues": has_dependency_clues,
    "has_reading_path": has_reading_path,
    "has_module_boundaries": has_module_boundaries,
    "has_architecture_clues": has_architecture_clues,
    "has_test_clues": has_test_clues,
    "has_risk_register": has_risk_register,
    "has_mitigation": has_mitigation,
    "has_key_files": has_key_files,
    "has_learning_path": has_learning_path,
    "has_exercises": has_exercises,
    "has_runbook_clues": has_runbook_clues,
    "has_open_questions": has_open_questions,
    "has_delivery_readiness_summary": has_delivery_readiness_summary,
}


def latest_llm_assisted_result(package: dict[str, Any]) -> dict[str, Any] | None:
    """从最近的 LLM 复核 turn 读取第二意见，避免把它混入确定性评分表。"""

    turns = sorted(as_list(package.get("turns")), key=lambda turn: int(value_of(turn, "turn_index", 0) or 0))
    for turn in reversed(turns):
        if value_of(turn, "node_name") != "llm_verify_package":
            continue
        metadata = value_of(turn, "metadata_json", {})
        if isinstance(metadata, dict) and isinstance(metadata.get("llm_assisted"), dict):
            return metadata["llm_assisted"]
    return None


def check_structure(package: dict[str, Any]) -> dict[str, Any]:
    required_sections = {
        "turns": "缺少 Loop 运行时间线。",
        "evidence_items": "缺少证据部分。",
        "findings": "缺少发现部分。",
        "recommendations": "缺少建议部分。",
        "quality_scores": "缺少质量评分部分。",
    }
    messages = [message for key, message in required_sections.items() if not as_list(package.get(key))]
    return check_result(
        name="structure_completeness",
        passed=not messages,
        severity="error",
        messages=messages or ["分析包包含所有必需部分。"],
    )


def check_evidence_coverage(package: dict[str, Any]) -> dict[str, Any]:
    evidence_ids = {str(value_of(evidence, "id")) for evidence in as_list(package.get("evidence_items"))}
    messages: list[str] = []

    for finding in as_list(package.get("findings")):
        title = value_of(finding, "title", "未命名发现")
        refs = [str(ref) for ref in as_list(value_of(finding, "evidence_refs_json", []))]
        if not refs:
            messages.append(f"发现「{title}」缺少证据引用。")
        elif not set(refs).intersection(evidence_ids):
            messages.append(f"发现「{title}」引用了分析包中不存在的证据。")

    for risk in as_list(package.get("risks")):
        title = value_of(risk, "title", "未命名风险")
        refs = [str(ref) for ref in as_list(value_of(risk, "evidence_refs_json", []))]
        if not refs:
            messages.append(f"风险「{title}」缺少证据引用。")
        elif not set(refs).intersection(evidence_ids):
            messages.append(f"风险「{title}」引用了分析包中不存在的证据。")

    return check_result(
        name="evidence_coverage",
        passed=not messages,
        severity="error",
        messages=messages or ["发现和风险都具备分析包内证据支撑。"],
    )


def check_codebase_coverage(inventory: dict[str, Any]) -> dict[str, Any]:
    snapshots = as_list(inventory.get("snapshots"))
    codebase_maps = as_list(inventory.get("codebase_maps"))
    messages: list[str] = []
    severity = "info"

    if not snapshots:
        messages.append("没有可用的输入源采集快照。")
        severity = "error"

    if not codebase_maps:
        messages.append("没有可用的代码库地图；非代码输入仍可继续评审。")
        if severity != "error":
            severity = "warning"
    else:
        for codebase_map in codebase_maps:
            root_label = value_of(codebase_map, "root_label", "codebase")
            if not as_list(value_of(codebase_map, "tech_stack_json", [])):
                messages.append(f"代码库「{root_label}」未识别到技术栈。")
                severity = "error"
            if not as_list(value_of(codebase_map, "entrypoint_files_json", [])) and not value_of(
                codebase_map, "readme_excerpt"
            ):
                messages.append(f"代码库「{root_label}」缺少入口文件或 README 证据。")
                severity = "error"
            if not as_list(value_of(codebase_map, "test_files_json", [])):
                messages.append(f"代码库「{root_label}」未识别到测试文件。")
                if severity != "error":
                    severity = "warning"

    return check_result(
        name="codebase_coverage",
        passed=not messages,
        severity=severity if messages else "info",
        messages=messages or ["已采集的代码库地图包含技术栈、入口和测试线索。"],
    )


def check_risk_transparency(package: dict[str, Any]) -> dict[str, Any]:
    messages: list[str] = []
    for risk in as_list(package.get("risks")):
        title = value_of(risk, "title", "未命名风险")
        if not value_of(risk, "severity"):
            messages.append(f"风险「{title}」缺少严重程度。")
        if not value_of(risk, "mitigation"):
            messages.append(f"风险「{title}」缺少缓解建议。")
    return check_result(
        name="risk_transparency",
        passed=not messages,
        severity="error",
        messages=messages or ["所有风险都包含严重程度和缓解建议。"],
    )


def check_question_resolution(package: dict[str, Any]) -> dict[str, Any]:
    messages: list[str] = []
    severity = "warning"
    for question in as_list(package.get("questions")):
        if value_of(question, "status") != "open":
            continue
        prompt = value_of(question, "prompt", "未命名问题")
        if value_of(question, "impact") == "high":
            severity = "error"
            messages.append(f"高影响问题仍未回答：{prompt}")
        else:
            messages.append(f"问题仍未回答：{prompt}")
    return check_result(
        name="question_resolution",
        passed=not messages,
        severity=severity,
        messages=messages or ["没有阻塞性的开放问题。"],
    )


def check_quality_score(package: dict[str, Any]) -> dict[str, Any]:
    latest_score = latest_item(package.get("quality_scores"))
    if latest_score is None:
        return check_result(
            name="quality_score",
            passed=False,
            severity="error",
            messages=["没有可用的分析质量评分。"],
        )
    overall_score = int(value_of(latest_score, "overall_score", 0) or 0)
    if overall_score < 70:
        return check_result(
            name="quality_score",
            passed=False,
            severity="error",
            messages=[f"最新质量分为 {overall_score}，低于最低评审阈值 70。"],
        )
    if overall_score < 85:
        return check_result(
            name="quality_score",
            passed=False,
            severity="warning",
            messages=[f"最新质量分为 {overall_score}，低于推荐目标 85。"],
        )
    return check_result(
        name="quality_score",
        passed=True,
        severity="info",
        messages=[f"最新质量分为 {overall_score}。"],
    )


def check_result(name: str, passed: bool, severity: str, messages: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "passed": passed,
        "severity": severity,
        "messages": messages,
    }


def build_summary(
    passed: bool,
    risk_level: str,
    blocking_reasons: list[str],
    warning_reasons: list[str],
) -> str:
    if not passed:
        return f"报告存在 {len(blocking_reasons)} 个阻塞问题；风险等级为 {risk_label(risk_level)}。"
    if warning_reasons:
        return f"报告可评审，但仍有 {len(warning_reasons)} 个风险提示；风险等级为 {risk_label(risk_level)}。"
    return "报告已通过确定性校验，暂无风险提示。"


def risk_label(risk_level: str) -> str:
    labels = {
        "low": "低",
        "medium": "中",
        "high": "高",
        "critical": "严重",
    }
    return labels.get(risk_level, risk_level)


def has_high_or_critical_risk(package: dict[str, Any]) -> bool:
    high_values = {"high", "critical"}
    return any(str(value_of(risk, "severity", "")).lower() in high_values for risk in as_list(package.get("risks")))


def latest_item(items: Any) -> Any | None:
    values = as_list(items)
    return values[-1] if values else None


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        return list(value)
    return [value]


def value_of(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)
