from fastapi import APIRouter, HTTPException, status

from app.research.profiles import get_research_profile, list_research_profiles, research_profile_exists
from app.schemas.research_profile import ResearchProfileRead

router = APIRouter(prefix="/research-profiles", tags=["research-profiles"])


@router.get("", response_model=list[ResearchProfileRead])
def list_profiles() -> list:
    return list_research_profiles()


@router.get("/{profile_key}", response_model=ResearchProfileRead)
def get_profile(profile_key: str):
    if not research_profile_exists(profile_key):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research profile not found")
    return get_research_profile(profile_key)
