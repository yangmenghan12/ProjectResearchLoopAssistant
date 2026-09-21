from copy import deepcopy
from dataclasses import dataclass
from typing import Any


REQUIRED_COLLECTION_KEYS = ["evidence", "findings", "risks", "questions", "recommendations"]


@dataclass
class LLMAnalysisValidation:
    accepted: bool
    content: dict[str, Any]
    validation: dict[str, Any]


def _normalize_collection(
    payload: dict[str, Any],
    key: str,
    messages: list[str],
    *,
    required: bool = False,
) -> list[Any]:
    if key not in payload:
        if required:
            messages.append(f"{key} is required")
        return []

    value = payload[key]
    if not isinstance(value, list):
        messages.append(f"{key} must be a list")
        return []
    return deepcopy(value)


def _reasoning_summary(payload: dict[str, Any]) -> str:
    value = payload.get("reasoning_summary", "")
    if not isinstance(value, str):
        return ""
    return value.strip()


def _message_prefix(key: str, index: int, field: str) -> str:
    return f"{key}[{index}].{field}"


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_required_string(
    item: dict[str, Any], key: str, index: int, field: str, messages: list[str]
) -> None:
    if not _is_non_empty_string(item.get(field)):
        messages.append(f"{_message_prefix(key, index, field)} must be a non-empty string")


def _validate_optional_string(
    item: dict[str, Any], key: str, index: int, field: str, messages: list[str]
) -> None:
    if field in item and not isinstance(item[field], str):
        messages.append(f"{_message_prefix(key, index, field)} must be a string")


def _validate_optional_confidence(
    item: dict[str, Any], key: str, index: int, messages: list[str]
) -> None:
    if "confidence" not in item:
        return

    value = item["confidence"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
        messages.append(f"{_message_prefix(key, index, 'confidence')} must be between 0 and 100")


def _validate_optional_metadata(
    item: dict[str, Any], key: str, index: int, messages: list[str]
) -> None:
    if "metadata" not in item:
        item["metadata"] = {}
        return
    if not isinstance(item["metadata"], dict):
        messages.append(f"{_message_prefix(key, index, 'metadata')} must be an object")


def _validate_evidence_refs(
    item: dict[str, Any],
    key: str,
    index: int,
    allowed_evidence_refs: set[str],
    messages: list[str],
) -> None:
    refs = item.get("evidence_refs")
    if not isinstance(refs, list) or len(refs) == 0:
        messages.append(f"{_message_prefix(key, index, 'evidence_refs')} must be a non-empty list")
        item["evidence_refs"] = []
        return

    for ref in refs:
        if not isinstance(ref, str) or not ref.strip():
            messages.append(
                f"{_message_prefix(key, index, 'evidence_refs')} must be a non-empty list of strings"
            )
            continue
        if ref not in allowed_evidence_refs:
            messages.append(f"Unknown evidence ref: {ref}")


def _validate_items_are_objects(
    content: dict[str, Any], key: str, messages: list[str]
) -> list[tuple[int, dict[str, Any]]]:
    objects: list[tuple[int, dict[str, Any]]] = []
    for index, item in enumerate(content[key]):
        if not isinstance(item, dict):
            messages.append(f"{key}[{index}] must be an object")
            continue
        objects.append((index, item))
    return objects


def _validate_evidence_client_ref(
    item: dict[str, Any],
    index: int,
    allowed_evidence_refs: set[str],
    seen_client_refs: set[str],
    messages: list[str],
) -> str | None:
    client_ref = item.get("client_ref")
    if not _is_non_empty_string(client_ref):
        return None

    valid = True
    if client_ref in allowed_evidence_refs:
        messages.append(
            f"evidence[{index}].client_ref conflicts with existing evidence ref: {client_ref}"
        )
        valid = False
    if not client_ref.startswith("llm:"):
        messages.append(f"evidence[{index}].client_ref must start with llm:")
        valid = False
    if client_ref in seen_client_refs:
        messages.append(f"Duplicate evidence client_ref: {client_ref}")
        valid = False
    seen_client_refs.add(client_ref)
    if valid:
        return client_ref
    return None


def _validate_evidence_items(
    content: dict[str, Any], allowed_evidence_refs: set[str], messages: list[str]
) -> set[str]:
    seen_client_refs: set[str] = set()
    payload_llm_evidence_refs: set[str] = set()
    for index, item in _validate_items_are_objects(content, "evidence", messages):
        for field in ["client_ref", "title", "summary", "evidence_type"]:
            _validate_required_string(item, "evidence", index, field, messages)
        payload_ref = _validate_evidence_client_ref(
            item,
            index,
            allowed_evidence_refs,
            seen_client_refs,
            messages,
        )
        if payload_ref is not None:
            payload_llm_evidence_refs.add(payload_ref)
        _validate_optional_confidence(item, "evidence", index, messages)
        _validate_optional_metadata(item, "evidence", index, messages)
    return payload_llm_evidence_refs


def _validate_finding_items(
    content: dict[str, Any], allowed_evidence_refs: set[str], messages: list[str]
) -> None:
    for index, item in _validate_items_are_objects(content, "findings", messages):
        for field in ["title", "summary", "category", "impact_level"]:
            _validate_required_string(item, "findings", index, field, messages)
        _validate_optional_confidence(item, "findings", index, messages)
        _validate_evidence_refs(item, "findings", index, allowed_evidence_refs, messages)
        _validate_optional_metadata(item, "findings", index, messages)


def _validate_risk_items(
    content: dict[str, Any], allowed_evidence_refs: set[str], messages: list[str]
) -> None:
    for index, item in _validate_items_are_objects(content, "risks", messages):
        for field in ["title", "summary", "severity", "mitigation"]:
            _validate_required_string(item, "risks", index, field, messages)
        _validate_evidence_refs(item, "risks", index, allowed_evidence_refs, messages)
        _validate_optional_metadata(item, "risks", index, messages)


def _validate_question_items(content: dict[str, Any], messages: list[str]) -> None:
    for index, item in _validate_items_are_objects(content, "questions", messages):
        for field in ["prompt", "reason", "impact"]:
            _validate_required_string(item, "questions", index, field, messages)
        _validate_optional_string(item, "questions", index, "status", messages)
        _validate_optional_metadata(item, "questions", index, messages)


def _validate_recommendation_items(content: dict[str, Any], messages: list[str]) -> None:
    for index, item in _validate_items_are_objects(content, "recommendations", messages):
        for field in ["summary", "rationale"]:
            _validate_required_string(item, "recommendations", index, field, messages)
        _validate_optional_confidence(item, "recommendations", index, messages)
        if "next_steps" in item and (
            not isinstance(item["next_steps"], list)
            or any(not isinstance(step, str) for step in item["next_steps"])
        ):
            messages.append(
                f"{_message_prefix('recommendations', index, 'next_steps')} must be a list of strings"
            )
        _validate_optional_metadata(item, "recommendations", index, messages)


def validate_llm_analysis_output(
    payload: dict[str, Any], allowed_evidence_refs: set[str]
) -> LLMAnalysisValidation:
    messages: list[str] = []
    content: dict[str, Any] = {}

    for key in REQUIRED_COLLECTION_KEYS:
        content[key] = _normalize_collection(payload, key, messages, required=True)

    content["reasoning_summary"] = _reasoning_summary(payload)
    content["coverage_notes"] = _normalize_collection(payload, "coverage_notes", messages)

    payload_llm_evidence_refs = _validate_evidence_items(content, allowed_evidence_refs, messages)
    merged_allowed_refs = allowed_evidence_refs | payload_llm_evidence_refs
    _validate_finding_items(content, merged_allowed_refs, messages)
    _validate_risk_items(content, merged_allowed_refs, messages)
    _validate_question_items(content, messages)
    _validate_recommendation_items(content, messages)

    validation = {"passed": not messages, "messages": messages}
    return LLMAnalysisValidation(
        accepted=validation["passed"],
        content=content,
        validation=validation,
    )
