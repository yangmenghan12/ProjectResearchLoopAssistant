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
    prompt: str = Field(default="Return one short sentence confirming the provider is reachable.")


def build_llm_client(settings: Settings) -> OpenAICompatibleClient:
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
    return build_llm_client(settings).status()


@router.get("/profiles", response_model=list[LLMProfileRead])
def list_llm_profiles(session: Session = Depends(get_session)) -> list[dict]:
    statement = select(LLMProfile).order_by(LLMProfile.updated_at.desc(), LLMProfile.id.desc())
    return [safe_profile_dict(profile) for profile in session.scalars(statement).all()]


@router.post("/profiles", response_model=LLMProfileRead, status_code=201)
def create_llm_profile(
    payload: LLMProfileCreate,
    session: Session = Depends(get_session),
) -> dict:
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
