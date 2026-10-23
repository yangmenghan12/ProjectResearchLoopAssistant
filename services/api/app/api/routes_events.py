from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent, Project
from app.db.session import get_session
from app.schemas.project import AuditEventRead

router = APIRouter(prefix="/projects/{project_id}/events", tags=["events"])


@router.get("", response_model=list[AuditEventRead])
def list_project_events(project_id: str, session: Session = Depends(get_session)) -> list[AuditEvent]:
    """按时间顺序返回项目审计事件，供调试和操作追踪使用。

    Args: project_id 为项目 ID；session 为数据库会话。
    Returns: AuditEvent 列表。
    Raises: 项目不存在时返回 HTTP 404；数据库异常向上抛出。
    副作用：仅读取数据库；由项目事件页面调用。
    """
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    statement = (
        select(AuditEvent)
        .where(AuditEvent.project_id == project.id)
        .order_by(AuditEvent.sequence.asc(), AuditEvent.created_at.asc(), AuditEvent.id.asc())
    )
    return list(session.scalars(statement).all())
