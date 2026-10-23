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
    """校验调研 profile 并持久化一个新项目。

    Args: payload 为项目名称、主题、目标和轮次配置；session 为数据库会话。
    Returns: 已提交并刷新后的 Project。
    Raises: profile 不存在时返回 HTTP 400；数据库异常向上抛出。
    副作用：写入 Project 和审计事件；由 `POST /projects` 调用。
    """
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
    """按创建时间倒序返回当前数据库中的项目列表。

    Args: session 为数据库会话。
    Returns: Project 列表，最新项目在前。
    Raises: 数据库查询异常向上抛出。
    副作用：仅读取数据库；由 `GET /projects` 调用。
    """
    statement = select(Project).order_by(Project.created_at.desc(), Project.id.desc())
    return list(session.scalars(statement).all())


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: str, session: Session = Depends(get_session)) -> Project:
    """按 ID 获取项目，避免后续页面继续处理不存在的项目。

    Args: project_id 为项目 ID；session 为数据库会话。
    Returns: 匹配的 Project。
    Raises: 项目不存在时抛出 HTTP 404；数据库异常向上抛出。
    副作用：仅读取数据库；由 `GET /projects/{project_id}` 调用。
    """
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project
