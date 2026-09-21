import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import LLMProfile


def profile_is_configured(profile: LLMProfile, api_key: str | None = None) -> bool:
    """判断 profile 是否具备可调用条件；DB profile 默认只检查环境变量是否存在。"""

    has_endpoint = bool(profile.base_url and profile.model)
    if not has_endpoint:
        return False
    if not bool(profile.api_key_required):
        return True
    if api_key is not None:
        return bool(api_key)
    return bool(profile.api_key_env_name and os.getenv(profile.api_key_env_name))


def safe_profile_dict(profile: LLMProfile) -> dict:
    """返回可安全下发到 API 的 profile 形状，不包含任何 API key 值。"""

    return {
        "id": profile.id,
        "name": profile.name,
        "task_type": profile.task_type,
        "provider": profile.provider,
        "base_url": profile.base_url,
        "model": profile.model,
        "api_key_env_name": profile.api_key_env_name,
        "api_key_required": bool(profile.api_key_required),
        "max_tokens": profile.max_tokens,
        "temperature": profile.temperature,
        "timeout_seconds": profile.timeout_seconds,
        "daily_budget_cents": profile.daily_budget_cents,
        "enabled": bool(profile.enabled),
        "configured": profile_is_configured(profile),
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def get_enabled_profile(session: Session, task_type: str) -> LLMProfile | None:
    statement = (
        select(LLMProfile)
        .where(LLMProfile.task_type == task_type, LLMProfile.enabled == True)  # noqa: E712
        .order_by(LLMProfile.updated_at.desc(), LLMProfile.id.desc())
    )
    return session.scalars(statement).first()


def default_profile_from_settings(settings: Settings, task_type: str) -> LLMProfile | None:
    if not settings.llm_base_url or not settings.llm_model:
        return None

    return LLMProfile(
        name=f"Default {task_type}",
        task_type=task_type,
        provider=settings.llm_provider,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key_env_name="LLM_API_KEY",
        api_key_required=settings.llm_api_key_required,
        max_tokens=1200 if task_type == "analyzer" else 800,
        temperature=0.2,
        timeout_seconds=int(settings.llm_timeout_seconds),
        daily_budget_cents=None,
        enabled=True,
    )
