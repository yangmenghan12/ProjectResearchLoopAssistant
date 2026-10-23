from fastapi import APIRouter, HTTPException, status

from app.research.profiles import get_research_profile, list_research_profiles, research_profile_exists
from app.schemas.research_profile import ResearchProfileRead

router = APIRouter(prefix="/research-profiles", tags=["research-profiles"])


@router.get("", response_model=list[ResearchProfileRead])
def list_profiles() -> list:
    """返回内置调研 profile 的公开定义和质量配置。

    Args: 无。
    Returns: ResearchProfile 字典列表。
    Raises: 不主动抛出业务异常。
    副作用：仅读取内置配置；由项目创建页调用。
    """
    return list_research_profiles()


@router.get("/{profile_key}", response_model=ResearchProfileRead)
def get_profile(profile_key: str):
    """按 profile key 返回单个调研模式。

    Args: profile_key 为非空 profile 标识。
    Returns: 对应 ResearchProfile 字典。
    Raises: key 不存在时返回 HTTP 404。
    副作用：仅读取内置配置；由 profile 详情请求调用。
    """
    if not research_profile_exists(profile_key):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research profile not found")
    return get_research_profile(profile_key)
