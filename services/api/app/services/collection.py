
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import CodebaseMap, Project, Source, SourceSnapshot
from app.services.audit import record_event
from app.tools.gateway import ToolContext, ToolGateway


def list_inventory(session: Session, project: Project) -> dict:
    """读取项目当前保存的来源快照和代码库地图。

    Args: session 为数据库会话；project 为已加载的 Project。
    Returns: 包含 `project_id`、`snapshots`、`codebase_maps` 的字典。
    Raises: 数据库查询异常向上抛出。
    副作用：仅读取数据库；由采集接口和分析上下文构建器调用。
    """
    snapshots = list(
        session.scalars(
            select(SourceSnapshot)
            .where(SourceSnapshot.project_id == project.id)
            .order_by(SourceSnapshot.created_at.asc(), SourceSnapshot.id.asc())
        ).all()
    )
    codebase_maps = list(
        session.scalars(
            select(CodebaseMap)
            .where(CodebaseMap.project_id == project.id)
            .order_by(CodebaseMap.created_at.asc(), CodebaseMap.id.asc())
        ).all()
    )
    return {
        "project_id": project.id,
        "snapshots": snapshots,
        "codebase_maps": codebase_maps,
    }


def collect_project_sources(
    session: Session,
    project: Project,
    settings: Settings,
    source_ids: set[str] | None = None,
) -> dict:
    """逐个采集项目来源，并把成功或失败结果保存为 inventory 快照。

    Args: session/project 为项目上下文；settings 为 IO 限制；source_ids 为可选来源 ID 集合。
    Returns: 采集完成后的 inventory 字典。
    Raises: 单个来源异常会被记录并继续；数据库提交异常向上抛出。
    副作用：执行文件、网页、Git IO，写入快照、代码地图、来源状态和审计事件。
    """
    statement = select(Source).where(Source.project_id == project.id)
    if source_ids is not None:
        if not source_ids:
            return list_inventory(session, project)
        statement = statement.where(Source.id.in_(source_ids))
    sources = list(
        session.scalars(
            statement.order_by(Source.created_at.asc(), Source.id.asc())
        ).all()
    )
    for source in sources:
        record_event(
            session,
            event_type="source.collection_started",
            message="Source collection started",
            project_id=project.id,
            payload={"source_id": source.id, "source_type": source.source_type},
        )
        try:
            result = ToolGateway(session, ToolContext(project_id=project.id, source=source,
                project=project, settings=settings, allowed_tools=frozenset({"source.collect"}),
                allowed_permission_levels=frozenset({"L0", "L1"}))).invoke("source.collect", {})
            snapshot_data, codebase_data = result["snapshot"], result["codebase"]
            snapshot = SourceSnapshot(
                project_id=project.id,
                source_id=source.id,
                source_type=source.source_type,
                status=snapshot_data["status"],
                title=snapshot_data.get("title"),
                content_excerpt=snapshot_data.get("content_excerpt"),
                content_path=snapshot_data.get("content_path"),
                metadata_json=snapshot_data.get("metadata", {}),
                skipped_items_json=snapshot_data.get("skipped_items", []),
            )
            session.add(snapshot)
            if codebase_data is not None:
                session.add(
                    CodebaseMap(
                        project_id=project.id,
                        source_id=source.id,
                        root_label=codebase_data["root_label"],
                        tech_stack_json=codebase_data["tech_stack"],
                        dependency_files_json=codebase_data["dependency_files"],
                        config_files_json=codebase_data["config_files"],
                        test_files_json=codebase_data["test_files"],
                        entrypoint_files_json=codebase_data["entrypoint_files"],
                        readme_excerpt=codebase_data.get("readme_excerpt"),
                        file_tree_json=codebase_data["file_tree"],
                        skipped_items_json=codebase_data["skipped_items"],
                        metadata_json=codebase_data["metadata"],
                    )
                )
            source.status = snapshot_data["status"]
            record_event(
                session,
                event_type="source.collected",
                message="Source collected",
                project_id=project.id,
                payload={"source_id": source.id, "status": source.status},
            )
        except Exception as exc:  # noqa: BLE001 - 按源隔离失败，继续保留可审计快照。
            source.status = "failed"
            session.add(
                SourceSnapshot(
                    project_id=project.id,
                    source_id=source.id,
                    source_type=source.source_type,
                    status="failed",
                    title=source.normalized_uri,
                    metadata_json={},
                    skipped_items_json=[],
                    error_message=str(exc),
                )
            )
            record_event(
                session,
                event_type="source.collection_failed",
                message="Source collection failed",
                project_id=project.id,
                payload={"source_id": source.id, "error": str(exc)},
            )
    session.flush()
    return list_inventory(session, project)
