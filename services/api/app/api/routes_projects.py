from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Project
from app.db.session import get_session
from app.research.profiles import research_profile_exists
from app.schemas.project import ProjectCreate, ProjectRead
from app.services.audit import record_event

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, session: Session = Depends(get_session)) -> Project:
    if not research_profile_exists(payload.research_profile):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Research profile not found")

    project = Project(
        name=payload.name,
        topic=payload.topic,
        analysis_goal=payload.analysis_goal,
        research_profile=payload.research_profile,
        max_turns=payload.max_turns,
    )
    session.add(project)
    session.flush()
    record_event(
        session,
        event_type="project.created",
        message="Project created",
        project_id=project.id,
        payload={"name": project.name},
    )
    session.commit()
    session.refresh(project)
    return project


@router.get("", response_model=list[ProjectRead])
def list_projects(session: Session = Depends(get_session)) -> list[Project]:
    statement = select(Project).order_by(Project.created_at.desc(), Project.id.desc())
    return list(session.scalars(statement).all())


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: str, session: Session = Depends(get_session)) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project
