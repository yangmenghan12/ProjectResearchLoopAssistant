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
    """将多个用户输入规范化为 source bundle，并登记可采集的 Source。

    Args: project_id 为项目 ID；payload 包含 1–20 个来源项；session 为数据库会话。
    Returns: 已提交并刷新后的 SourceBundle。
    Raises: 项目不存在或输入校验失败时返回 HTTP 404/422。
    副作用：写入 bundle、items、可用 Source 和审计事件。
    """
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
    """按创建顺序返回项目的 source bundles 及其 item 状态。

    Args: project_id 为项目 ID；session 为数据库会话。
    Returns: SourceBundle 列表。
    Raises: 项目不存在时返回 HTTP 404；数据库异常向上抛出。
    副作用：仅读取数据库；由项目资料页调用。
    """
    project = get_project_or_404(session, project_id)
    return list_source_bundles(session, project)


@router.get("/{bundle_id}", response_model=SourceBundleRead)
def get_project_source_bundle(
    project_id: str,
    bundle_id: str,
    session: Session = Depends(get_session),
) -> SourceBundle:
    """读取属于指定项目的单个 source bundle。

    Args: project_id/bundle_id 为资源 ID；session 为数据库会话。
    Returns: 匹配的 SourceBundle。
    Raises: bundle 不存在或归属不匹配时返回 HTTP 404。
    副作用：仅读取数据库；由 bundle 详情页调用。
    """
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
    """采集 bundle 中尚未完成的来源，并刷新整体状态。

    Args: project_id/bundle_id 为资源 ID；session 为数据库会话；settings 为运行配置。
    Returns: 状态为 collected、partial 或 failed 的 SourceBundle。
    Raises: bundle 不存在时返回 HTTP 404；单项错误保留在 item 状态中。
    副作用：执行文件、网页或 Git IO，并写入快照、状态和审计事件。
    """
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
