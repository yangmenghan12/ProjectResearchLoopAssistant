from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import SourceValidationError, validate_source_input
from app.db.models import Project, Source
from app.db.session import get_session
from app.schemas.project import SourceCreate, SourceRead
from app.services.audit import record_event

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["sources"])


@router.post("", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
def create_source(
    project_id: str,
    payload: SourceCreate,
    session: Session = Depends(get_session),
) -> Source:
    """校验并登记一个项目来源，为后续采集建立稳定的 normalized URI。

    Args: project_id 为所属项目；payload 为来源类型和原始 URI；session 为数据库会话。
    Returns: 已提交并刷新后的 Source。
    Raises: 项目或来源输入无效时抛出 HTTP 404/400。
    副作用：写入 Source 和审计事件；由 `POST /projects/{project_id}/sources` 调用。
    """
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    try:
        validated = validate_source_input(payload.source_type, payload.uri)
    except SourceValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    source = Source(
        project_id=project.id,
        source_type=payload.source_type,
        uri=payload.uri,
        normalized_uri=validated.normalized_uri,
        metadata_json=validated.metadata,
    )
    session.add(source)
    session.flush()
    record_event(
        session,
        event_type="source.created",
        message="Source created",
        project_id=project.id,
        payload={
            "source_id": source.id,
            "source_type": source.source_type,
            "normalized_uri": source.normalized_uri,
        },
    )
    session.commit()
    session.refresh(source)
    return source


@router.get("", response_model=list[SourceRead])
def list_sources(project_id: str, session: Session = Depends(get_session)) -> list[Source]:
    """返回项目登记的来源，保持创建顺序以便与采集结果对应。

    Args: project_id 为项目 ID；session 为数据库会话。
    Returns: 该项目的 Source 列表。
    Raises: 项目不存在时抛出 HTTP 404；数据库异常向上抛出。
    副作用：仅读取数据库；由 `GET /projects/{project_id}/sources` 调用。
    """
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    statement = (
        select(Source)
        .where(Source.project_id == project.id)
        .order_by(Source.created_at.asc(), Source.id.asc())
    )
    return list(session.scalars(statement).all())
