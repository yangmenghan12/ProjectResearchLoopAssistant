import json
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LLMCall, LLMProfile, utc_now


BUDGET_EXCEEDED_ERROR = "LLM budget exceeded"


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str | None = None
    used_cents: int = 0
    requested_cents: int = 0
    limit_cents: int | None = None


def estimate_usage_cents(usage: object) -> int:
    if not isinstance(usage, dict):
        return 0

    try:
        total_tokens = int(usage.get("total_tokens") or 0)
    except (TypeError, ValueError):
        # 历史账本中的 provider usage 可能残缺或格式异常，预算聚合不能因此中断运行。
        return 0

    if total_tokens <= 0:
        return 0
    return (total_tokens + 999) // 1000


def estimate_request_cents(messages: list[dict], max_output_tokens: int | None) -> int:
    try:
        prompt_chars = len(json.dumps(messages, ensure_ascii=False))
    except (TypeError, ValueError):
        prompt_chars = 0

    # 这里不追求 tokenizer 级精确，而是用保守估算在 provider 调用前保护预算。
    prompt_tokens = max(1, (prompt_chars + 3) // 4)
    try:
        output_tokens = max(0, int(max_output_tokens or 0))
    except (TypeError, ValueError):
        output_tokens = 0
    return estimate_usage_cents({"total_tokens": prompt_tokens + output_tokens})


def profile_budget_decision(
    session: Session,
    profile: LLMProfile,
    *,
    requested_cents: int = 0,
) -> BudgetDecision:
    requested_cents = max(0, int(requested_cents or 0))

    if profile.daily_budget_cents is None:
        return BudgetDecision(allowed=True, requested_cents=requested_cents, limit_cents=None)

    if profile.daily_budget_cents <= 0:
        return BudgetDecision(
            allowed=False,
            reason=BUDGET_EXCEEDED_ERROR,
            requested_cents=requested_cents,
            limit_cents=profile.daily_budget_cents,
        )

    since = utc_now() - timedelta(hours=24)
    calls = session.scalars(
        select(LLMCall).where(
            LLMCall.profile_id == profile.id,
            LLMCall.created_at >= since,
            LLMCall.status == "succeeded",
        )
    ).all()
    used = sum(estimate_usage_cents(call.usage_json) for call in calls)
    allowed = used + requested_cents <= profile.daily_budget_cents
    return BudgetDecision(
        allowed=allowed,
        reason=None if allowed else BUDGET_EXCEEDED_ERROR,
        used_cents=used,
        requested_cents=requested_cents,
        limit_cents=profile.daily_budget_cents,
    )
