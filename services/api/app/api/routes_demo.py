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
    """创建一个带示例代码库来源的演示项目，便于快速体验完整流程。

    Args: session 为数据库会话；settings 提供 demo workspace 根目录。
    Returns: 已提交并刷新的 Project。
    Raises: demo 文件写入或数据库异常向上抛出。
    副作用：写入示例文件、项目、来源和审计事件；由 demo 接口调用。
    """
    project = create_code_analysis_demo_project(session, settings)
    session.commit()
    session.refresh(project)
    return project
