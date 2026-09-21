import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import LLMCall


def prompt_hash(messages: list[dict[str, Any]]) -> str:
    serialized = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def record_llm_call(
    session: Session,
    *,
    task_type: str,
    provider: str,
    model: str,
    messages: list[dict[str, Any]],
    status: str,
    project_id: str | None = None,
    run_id: str | None = None,
    report_id: str | None = None,
    profile_id: str | None = None,
    input_refs: list[str] | None = None,
    output_ref: str | None = None,
    usage: dict[str, Any] | None = None,
    latency_ms: int | None = None,
    error_message: str | None = None,
) -> LLMCall:
    call = LLMCall(
        project_id=project_id,
        run_id=run_id,
        report_id=report_id,
        profile_id=profile_id,
        task_type=task_type,
        provider=provider,
        model=model,
        prompt_hash=prompt_hash(messages),
        input_refs_json=list(input_refs or []),
        output_ref=output_ref,
        usage_json=dict(usage or {}),
        latency_ms=latency_ms,
        status=status,
        error_message=error_message,
    )
    session.add(call)
    session.flush()
    return call
