from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import Project
from app.db.session import get_session
from app.schemas.inventory import ProjectInventoryRead
from app.services.collection import collect_project_sources, list_inventory

router = APIRouter(prefix="/projects/{project_id}", tags=["inventory"])


@router.post("/collect", response_model=ProjectInventoryRead)
def collect_project(
    project_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    inventory = collect_project_sources(session, project, settings)
    session.commit()
    return inventory


@router.get("/inventory", response_model=ProjectInventoryRead)
def get_inventory(project_id: str, session: Session = Depends(get_session)) -> dict:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return list_inventory(session, project)
