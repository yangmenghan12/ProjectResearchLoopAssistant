from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import LLMCall, LLMProfile
from app.db.session import get_session
from app.llm.openai_compatible import OpenAICompatibleClient
from app.llm.profiles import safe_profile_dict
from app.llm.router import LLMRouter
from app.schemas.llm import LLMCallRead, LLMProfileCreate, LLMProfileRead, LLMProfileUpdate

router = APIRouter(prefix="/llm", tags=["llm"])


class SmokeTestRequest(BaseModel):
    prompt: str = Field(default="Return one short sentence confirming the provider is reachable.")  # 要发送给 provider 的连通性测试提示词。


def build_llm_client(settings: Settings) -> OpenAICompatibleClient:
    """将应用配置转换为 OpenAI-compatible 客户端。

    Args: settings 为应用 LLM 配置。
    Returns: 已配置 provider、model、超时和认证策略的客户端。
    Raises: 不主动访问网络；配置字段非法时由客户端在调用时报告。
    副作用：仅创建内存对象；由 provider status 和 smoke-test handler 调用。
    """
    return OpenAICompatibleClient(
        provider=settings.llm_provider,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key_required=settings.llm_api_key_required,
        timeout_seconds=settings.llm_timeout_seconds,
    )


@router.get("/provider")
def get_provider_status(settings: Settings = Depends(get_settings)) -> dict:
    """返回默认 LLM provider 的本地配置状态，不发起模型请求。

    Args: settings 为应用配置。
    Returns: provider.status() 字典，包含 configured 等状态字段。
    Raises: 配置读取异常向上抛出。
    副作用：无网络 IO；由 `GET /llm/provider` 调用。
    """
    return build_llm_client(settings).status()


@router.get("/profiles", response_model=list[LLMProfileRead])
def list_llm_profiles(session: Session = Depends(get_session)) -> list[dict]:
    """按更新时间倒序返回脱敏后的 LLM profile 列表。

    Args: session 为数据库会话。
    Returns: 不含明文 API key 的 profile 字典列表。
    Raises: 数据库查询异常向上抛出。
    副作用：仅读取数据库；由 LLM 设置页面调用。
    """
    statement = select(LLMProfile).order_by(LLMProfile.updated_at.desc(), LLMProfile.id.desc())
    return [safe_profile_dict(profile) for profile in session.scalars(statement).all()]


@router.post("/profiles", response_model=LLMProfileRead, status_code=201)
def create_llm_profile(
    payload: LLMProfileCreate,
    session: Session = Depends(get_session),
) -> dict:
    """创建一个按 task_type 选择的 LLM profile 并返回脱敏表示。

    Args: payload 为 provider、model、预算和启用状态；session 为数据库会话。
    Returns: 已提交并刷新的 profile 字典。
    Raises: Pydantic 或数据库约束异常向上抛出。
    副作用：写入 LLMProfile；由 `POST /llm/profiles` 调用。
    """
    profile = LLMProfile(**payload.model_dump())
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return safe_profile_dict(profile)


@router.patch("/profiles/{profile_id}", response_model=LLMProfileRead)
def update_llm_profile(
    profile_id: str,
    payload: LLMProfileUpdate,
    session: Session = Depends(get_session),
) -> dict:
    """局部更新一个 LLM profile，并拒绝非空字段被显式设为 null。

    Args: profile_id 为 profile ID；payload 为部分更新字段；session 为数据库会话。
    Returns: 已提交并刷新的脱敏 profile 字典。
    Raises: profile 不存在返回 404；不可置空字段返回 422。
    副作用：更新数据库；由 `PATCH /llm/profiles/{profile_id}` 调用。
    """
    profile = session.get(LLMProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="LLM profile not found")

    updates = payload.model_dump(exclude_unset=True)
    non_nullable_fields = {
        "name",
        "provider",
        "base_url",
        "model",
        "api_key_required",
        "max_tokens",
        "temperature",
        "timeout_seconds",
        "enabled",
    }
    null_fields = sorted(field for field in non_nullable_fields if updates.get(field) is None and field in updates)
    if null_fields:
        raise HTTPException(status_code=422, detail=f"{', '.join(null_fields)} cannot be null")

    for key, value in updates.items():
        setattr(profile, key, value)
    session.commit()
    session.refresh(profile)
    return safe_profile_dict(profile)


@router.post("/profiles/{profile_id}/smoke-test")
def smoke_test_llm_profile(
    profile_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """通过指定 profile 发起最小 LLM 请求，并把结果写入调用账本。

    Args: profile_id 为 profile ID；session 为数据库会话。
    Returns: 含 status、call_id、文本摘要和可选错误的字典。
    Raises: profile 不存在返回 404；非 required 调用失败会体现在返回状态中。
    副作用：访问外部 LLM 并写入 LLMCall；由 profile smoke-test 接口调用。
    """
    profile = session.get(LLMProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="LLM profile not found")

    result = LLMRouter(session).chat(
        task_type=profile.task_type,
        profile_id=profile.id,
        messages=[
            {"role": "system", "content": "你是连通性检测助手，只用一句中文回复。"},
            {"role": "user", "content": "请回复一句话，确认当前 LLM 配置可访问。"},
        ],
        required=False,
    )
    session.commit()
    response = {
        "status": result["status"],
        "call_id": result["call_id"],
        "text_excerpt": result["text"][:240],
    }
    if result.get("error_message"):
        response["error_message"] = result["error_message"][:500]
    return response


@router.get("/calls", response_model=list[LLMCallRead])
def list_llm_calls(
    project_id: str | None = None,
    run_id: str | None = None,
    session: Session = Depends(get_session),
) -> list[LLMCall]:
    """按项目或 run 过滤并返回 LLM 调用账本。

    Args: project_id/run_id 为可选资源过滤器；session 为数据库会话。
    Returns: LLMCall 列表，最新调用在前。
    Raises: 数据库查询异常向上抛出。
    副作用：仅读取数据库；由成本与调试页面调用。
    """
    statement = select(LLMCall)
    if project_id is not None:
        statement = statement.where(LLMCall.project_id == project_id)
    if run_id is not None:
        statement = statement.where(LLMCall.run_id == run_id)
    statement = statement.order_by(LLMCall.created_at.desc(), LLMCall.id.desc())
    return list(session.scalars(statement).all())


@router.post("/smoke-test")
def smoke_test(
    payload: SmokeTestRequest,
    settings: Settings = Depends(get_settings),
) -> dict:
    """使用默认 provider 发送连通性测试请求，不写入项目 run 账本。

    Args: payload.prompt 为测试提示词；settings 为默认 provider 配置。
    Returns: provider、model、状态、文本摘要和 usage 字典。
    Raises: provider 未配置返回 503；外部调用失败向上抛出。
    副作用：发起外部 LLM IO；由 `POST /llm/smoke-test` 调用。
    """
    client = build_llm_client(settings)
    if not client.status()["configured"]:
        raise HTTPException(status_code=503, detail="LLM provider is not configured")

    result = client.chat(
        messages=[
            {
                "role": "system",
                "content": "You are checking whether the Loop Engineering local runtime can reach its LLM provider.",
            },
            {"role": "user", "content": payload.prompt},
        ],
        max_tokens=128,
        temperature=0.2,
    )
    return {
        "provider": result["provider"],
        "model": result["model"],
        "status": "ok",
        "text_excerpt": result["text"][:240],
        "usage": result["usage"],
    }
