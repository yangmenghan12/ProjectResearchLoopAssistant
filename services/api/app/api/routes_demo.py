from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import Project
from app.db.session import get_session
from app.schemas.project import ProjectRead
from app.services.demo import create_code_analysis_demo_project

router = APIRouter(prefix="/demo", tags=["demo"])


@router.post("/projects/code-analysis", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_demo_project(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Project:
    project = create_code_analysis_demo_project(session, settings)
    session.commit()
    session.refresh(project)
    return project
