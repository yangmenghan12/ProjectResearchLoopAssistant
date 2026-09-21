from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import Project, SourceBundle
from app.db.session import get_session
from app.schemas.source_bundle import SourceBundleCreate, SourceBundleRead
from app.services.collection import collect_project_sources
from app.services.source_bundles import (
    collectable_source_ids,
    create_source_bundle,
    get_source_bundle,
    get_source_bundle_for_collection,
    list_source_bundles,
    refresh_bundle_item_collection_state,
)

router = APIRouter(prefix="/projects/{project_id}/source-bundles", tags=["source-bundles"])


@router.post("", response_model=SourceBundleRead, status_code=status.HTTP_201_CREATED)
def create_project_source_bundle(
    project_id: str,
    payload: SourceBundleCreate,
    session: Session = Depends(get_session),
) -> SourceBundle:
    project = get_project_or_404(session, project_id)
    bundle = create_source_bundle(session, project, payload)
    bundle_id = bundle.id
    session.commit()
    refreshed_bundle = get_source_bundle(session, project, bundle_id)
    if refreshed_bundle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source bundle not found")
    return refreshed_bundle


@router.get("", response_model=list[SourceBundleRead])
def list_project_source_bundles(
    project_id: str,
    session: Session = Depends(get_session),
) -> list[SourceBundle]:
    project = get_project_or_404(session, project_id)
    return list_source_bundles(session, project)


@router.get("/{bundle_id}", response_model=SourceBundleRead)
def get_project_source_bundle(
    project_id: str,
    bundle_id: str,
    session: Session = Depends(get_session),
) -> SourceBundle:
    project = get_project_or_404(session, project_id)
    bundle = get_source_bundle(session, project, bundle_id)
    if bundle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source bundle not found")
    return bundle


@router.post("/{bundle_id}/collect", response_model=SourceBundleRead)
def collect_project_source_bundle(
    project_id: str,
    bundle_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SourceBundle:
    project = get_project_or_404(session, project_id)
    bundle = get_source_bundle_for_collection(session, project, bundle_id)
    if bundle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source bundle not found")

    collect_project_sources(session, project, settings, source_ids=collectable_source_ids(bundle))
    refresh_bundle_item_collection_state(session, bundle)
    session.commit()
    refreshed_bundle = get_source_bundle(session, project, bundle_id)
    if refreshed_bundle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source bundle not found")
    return refreshed_bundle


def get_project_or_404(session: Session, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project
