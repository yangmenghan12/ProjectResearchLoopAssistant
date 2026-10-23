import json
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LLMCall, LLMProfile, utc_now


BUDGET_EXCEEDED_ERROR = "LLM budget exceeded"


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool  # 本次请求是否通过预算门禁。
    reason: str | None = None  # 未通过时的原因；允许时通常为 None。
    used_cents: int = 0  # 最近 24 小时已使用的估算成本，单位为 cents。
    requested_cents: int = 0  # 本次请求的估算成本，单位为 cents。
    limit_cents: int | None = None  # profile 配置的额度上限；None 表示不限制。


def estimate_usage_cents(usage: object) -> int:
    """根据 provider usage 粗略估算一次调用成本，供预算门禁使用。

    Args: usage 为可能含 token/cost 字段的 JSON-like 对象。
    Returns: 非负整数 cents；无法识别用量时返回 0。
    Raises: 不主动抛出异常，异常结构按未知用量处理。
    副作用：无；由账本和预算判断函数调用。
    """
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
    """按输入文本长度和最大输出 token 估算请求预算。

    Args: messages 为消息字典列表；max_output_tokens 为可选非负上限。
    Returns: 非负整数 cents 的保守估算值。
    Raises: 消息结构异常不会主动抛出，按 0 输入估算。
    副作用：无；由 `LLMRouter.chat` 调用。
    """
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
    """比较 profile 当日已用额度和本次估算成本，决定是否允许调用。

    Args: session 为数据库会话；profile 为 LLMProfile；requested_cents 为非负成本估算。
    Returns: 包含 allowed、used、requested、limit 的 BudgetDecision。
    Raises: 数据库查询异常向上抛出。
    副作用：仅读取 LLM 账本；由 `LLMRouter.chat` 调用。
    """
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
