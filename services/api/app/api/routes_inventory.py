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
    """同步采集项目全部来源，并提交生成的 inventory 快照。

    Args: project_id 为项目 ID；session 为数据库会话；settings 为运行配置。
    Returns: 包含 snapshots 和 codebase_maps 的项目 inventory。
    Raises: 项目不存在时抛出 HTTP 404；采集异常由服务层按来源记录。
    副作用：执行文件、网页或 Git IO，并写入快照、代码地图和审计事件。
    上游调用者：前端项目资料页通过 `POST /collect` 调用。
    """
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    inventory = collect_project_sources(session, project, settings)
    session.commit()
    return inventory


@router.get("/inventory", response_model=ProjectInventoryRead)
def get_inventory(project_id: str, session: Session = Depends(get_session)) -> dict:
    """读取项目已有的来源快照和代码库地图，不触发重新采集。

    Args: project_id 为项目 ID；session 为数据库会话。
    Returns: 项目 inventory 字典。
    Raises: 项目不存在时抛出 HTTP 404；数据库异常向上抛出。
    副作用：仅读取数据库；由项目 inventory 页面调用。
    """
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return list_inventory(session, project)
