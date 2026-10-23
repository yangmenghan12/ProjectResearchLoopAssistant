from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AnalysisRun, Project, ToolCall
from app.db.session import get_session
from app.schemas.tooling import ToolCallRead, ToolRegistrationRead
from app.tools.gateway import list_default_tools

router = APIRouter(tags=["tools"])


@router.get("/tools", response_model=list[ToolRegistrationRead])
def list_tools() -> list:
    """返回统一注册表中的本地、MCP 与宿主采集工具，不依赖数据库 seed。

    Args: 无。
    Returns: ToolDefinition 列表。
    Raises: 不主动抛出业务异常。
    副作用：仅读取内存中的默认注册表；由 tools 页面调用。
    """

    return list_default_tools()


@router.get("/projects/{project_id}/runs/{run_id}/tool-calls", response_model=list[ToolCallRead])
def list_run_tool_calls(
    project_id: str,
    run_id: str,
    session: Session = Depends(get_session),
) -> list[ToolCall]:
    """返回指定 run 的工具调用账本，限制在对应项目内。

    Args: project_id/run_id 为资源 ID；session 为数据库会话。
    Returns: ToolCall 列表，按创建时间升序排列。
    Raises: 项目或 run 不存在时返回 HTTP 404。
    副作用：仅读取数据库；由运行详情或调试页面调用。
    """
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    run = session.get(AnalysisRun, run_id)
    if run is None or run.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    return list(
        session.scalars(
            select(ToolCall)
            .where(ToolCall.project_id == project.id, ToolCall.run_id == run.id)
            .order_by(ToolCall.created_at.asc(), ToolCall.id.asc())
        ).all()
    )
