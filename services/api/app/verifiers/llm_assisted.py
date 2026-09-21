from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any


VALID_ISSUE_SEVERITIES = {"info", "warning", "error"}


def validate_llm_verifier_output(payload: dict[str, Any]) -> dict[str, Any]:
    """把 LLM 复核输出压缩成可审计的安全结构。

    LLM 输出只能作为第二意见进入系统，因此这里不保留任意嵌套对象，避免后续消费者误把
    未验证字段当作确定性事实。
    """

    if not isinstance(payload, dict):
        payload = {}

    return {
        "passed": payload.get("passed") if isinstance(payload.get("passed"), bool) else False,
        "confidence": normalize_confidence(payload.get("confidence")),
        "issues": normalize_issues(payload.get("issues")),
        "suggested_next_actions": normalize_actions(payload.get("suggested_next_actions")),
        "summary": normalize_text(payload.get("summary")),
    }


def merge_llm_verifier_result(
    deterministic: dict[str, Any],
    llm_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """合并确定性校验和 LLM 复核结果，确定性校验始终是最终裁决来源。

    LLM 复核只能作为独立的第二意见保存在 llm_assisted 下；顶层风险、摘要和原因字段
    必须继续只描述确定性校验结果，避免报告出现“低风险但有 LLM warning”的自相矛盾状态。
    """

    merged = deepcopy(deterministic)
    merged["blocking_reasons"] = list(deterministic.get("blocking_reasons") or [])
    merged["warning_reasons"] = list(deterministic.get("warning_reasons") or [])
    merged["passed"] = bool(deterministic.get("passed", False))

    if not llm_result:
        merged["llm_assisted"] = {"status": "skipped"}
        return merged

    stored_llm_result = deepcopy(llm_result)
    merged["llm_assisted"] = stored_llm_result

    if deterministic.get("passed") is False:
        merged["passed"] = False
    return merged


def normalize_confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    confidence = float(value)
    if not isfinite(confidence):
        return 0.0
    return max(0.0, min(1.0, confidence))


def normalize_issues(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        return [
            {
                "severity": "warning",
                "message": "LLM verifier field `issues` must be a list; unsafe value was ignored.",
            }
        ]

    issues: list[dict[str, str]] = []
    for issue in value:
        if not isinstance(issue, dict):
            continue
        message = normalize_text(issue.get("message"))
        if not message:
            continue
        severity = normalize_text(issue.get("severity")).lower() or "warning"
        if severity not in VALID_ISSUE_SEVERITIES:
            severity = "warning"
        issues.append({"severity": severity, "message": message})
    return issues


def normalize_actions(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []

    actions: list[str] = []
    for item in value:
        action = stringify_simple_value(item)
        if action:
            actions.append(action)
    return actions


def stringify_simple_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and isfinite(value):
        return str(value)
    return ""


def normalize_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value):
        return str(value)
    return ""
