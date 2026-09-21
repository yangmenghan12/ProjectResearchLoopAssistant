from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import CodebaseMap, Project, Source, SourceSnapshot
from app.services.audit import record_event
from app.tools.code_scan_tools import scan_codebase
from app.tools.file_tools import read_local_file
from app.tools.git_tools import clone_public_github_repo
from app.tools.web_tools import fetch_web_url


def list_inventory(session: Session, project: Project) -> dict:
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
            snapshot_data, codebase_data = collect_one_source(source, project, settings)
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


def collect_one_source(source: Source, project: Project, settings: Settings) -> tuple[dict, dict | None]:
    if source.source_type == "web_url":
        return (
            fetch_web_url(
                source.normalized_uri,
                timeout_seconds=settings.web_fetch_timeout_seconds,
                excerpt_chars=settings.source_excerpt_chars,
            ),
            None,
        )
    if source.source_type == "local_file":
        return (
            read_local_file(
                Path(source.normalized_uri),
                max_file_bytes=settings.source_max_file_bytes,
                excerpt_chars=settings.source_excerpt_chars,
            ),
            None,
        )
    if source.source_type == "local_directory":
        codebase_data = scan_codebase(
            Path(source.normalized_uri),
            max_file_bytes=settings.source_max_file_bytes,
            excerpt_chars=settings.source_excerpt_chars,
        )
        snapshot_data = {
            "status": "collected",
            "title": codebase_data["root_label"],
            "content_excerpt": codebase_data.get("readme_excerpt"),
            "metadata": codebase_data["metadata"],
            "skipped_items": codebase_data["skipped_items"],
        }
        return snapshot_data, codebase_data
    if source.source_type == "github_repo":
        clone_data = clone_public_github_repo(
            source.normalized_uri,
            workspace_root=settings.workspace_root,
            project_id=project.id,
            timeout_seconds=settings.git_clone_timeout_seconds,
        )
        codebase_data = scan_codebase(
            Path(clone_data["metadata"]["local_path"]),
            max_file_bytes=settings.source_max_file_bytes,
            excerpt_chars=settings.source_excerpt_chars,
        )
        snapshot_data = {
            **clone_data,
            "content_excerpt": codebase_data.get("readme_excerpt"),
            "metadata": {
                **clone_data["metadata"],
                "scan": codebase_data["metadata"],
            },
            "skipped_items": codebase_data["skipped_items"],
        }
        return snapshot_data, codebase_data
    raise ValueError(f"Unsupported source type: {source.source_type}")
