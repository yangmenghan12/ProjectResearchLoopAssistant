from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMReportValidation:
    accepted: bool
    normalized: dict[str, Any] | None = None
    messages: list[str] = field(default_factory=list)


MAX_EXECUTIVE_SUMMARY_LENGTH = 2000
MAX_SECTION_HEADING_LENGTH = 120
MAX_SECTION_BODY_LENGTH = 4000
MAX_REPORT_SECTIONS = 6


def validate_llm_report_sections(payload: Any, *, allowed_evidence_refs: set[str]) -> LLMReportValidation:
    """校验 LLM 报告增强片段，只允许引用既有 EvidenceItem.id。

    LLM 段落只是 advisory：它可以改善表达和解读，但必须被 evidence refs 约束，
    不能凭空新增证据，也不能影响确定性 verifier 的状态与结论。
    """

    messages: list[str] = []
    if not isinstance(payload, dict):
        return LLMReportValidation(False, messages=["payload must be an object"])

    executive_summary = _required_string(
        payload.get("executive_summary"),
        "executive_summary",
        messages,
        max_length=MAX_EXECUTIVE_SUMMARY_LENGTH,
    )

    sections_value = payload.get("sections")
    if not isinstance(sections_value, list):
        messages.append("sections must be a list")
        sections_value = []
    elif len(sections_value) > MAX_REPORT_SECTIONS:
        messages.append(f"sections must include at most {MAX_REPORT_SECTIONS} items")

    normalized_sections: list[dict[str, Any]] = []
    for index, section in enumerate(sections_value):
        if not isinstance(section, dict):
            messages.append(f"sections[{index}] must be an object")
            continue

        heading = _required_string(
            section.get("heading"),
            f"sections[{index}].heading",
            messages,
            max_length=MAX_SECTION_HEADING_LENGTH,
        )
        body = _required_string(
            section.get("body"),
            f"sections[{index}].body",
            messages,
            max_length=MAX_SECTION_BODY_LENGTH,
        )
        refs_value = section.get("evidence_refs")
        if not isinstance(refs_value, list):
            messages.append(f"sections[{index}].evidence_refs must be a list")
            refs_value = []

        normalized_refs: list[str] = []
        for ref_index, ref in enumerate(refs_value):
            clean_ref = _clean_string(ref) if isinstance(ref, str) else ""
            if not clean_ref:
                messages.append(f"sections[{index}].evidence_refs[{ref_index}] must be a non-empty string")
                continue
            if clean_ref not in allowed_evidence_refs:
                messages.append(f"unknown evidence ref: {clean_ref}")
                continue
            normalized_refs.append(clean_ref)

        if not normalized_refs:
            messages.append(f"sections[{index}].evidence_refs must include at least one known evidence ref")

        if heading and body and normalized_refs:
            normalized_sections.append(
                {
                    "heading": heading,
                    "body": body,
                    "evidence_refs": normalized_refs,
                }
            )

    accepted = not messages
    normalized = {"executive_summary": executive_summary, "sections": normalized_sections} if accepted else None
    return LLMReportValidation(accepted=accepted, normalized=normalized, messages=messages)


def render_llm_sections_markdown(base_markdown: str, normalized: dict[str, Any]) -> str:
    lines = [base_markdown.rstrip(), "", "## 智能报告增强", ""]
    executive_summary = _clean_string(normalized.get("executive_summary"))
    if executive_summary:
        lines.extend([executive_summary, ""])

    for section in normalized.get("sections", []):
        if not isinstance(section, dict):
            continue
        heading = _clean_string(section.get("heading"))
        body = _clean_string(section.get("body"))
        evidence_refs = [_clean_string(ref) for ref in section.get("evidence_refs", [])]
        evidence_refs = [ref for ref in evidence_refs if ref]
        if not heading or not body or not evidence_refs:
            continue
        lines.extend(
            [
                f"### {heading}",
                "",
                body,
                "",
                f"证据引用：{', '.join(f'`{ref}`' for ref in evidence_refs)}",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def _clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _required_string(value: Any, field_name: str, messages: list[str], *, max_length: int) -> str:
    if not isinstance(value, str):
        messages.append(f"{field_name} must be a non-empty string")
        return ""

    cleaned = _clean_string(value)
    if not cleaned:
        messages.append(f"{field_name} must be a non-empty string")
        return ""
    if len(cleaned) > max_length:
        messages.append(f"{field_name} must be at most {max_length} characters")
        return ""
    return cleaned
