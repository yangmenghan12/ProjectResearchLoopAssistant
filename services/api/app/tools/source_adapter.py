"""Adapter for the workbench's existing document, web and repository collectors."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from app.tools.code_scan_tools import scan_codebase
from app.tools.file_tools import read_local_file
from app.tools.git_tools import clone_public_github_repo
from app.tools.web_tools import fetch_web_url

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.db.models import Project, Source
    from app.tools.gateway import ToolContext


def execute_source_collection(args: dict, context: ToolContext) -> dict:
    # Existing parsers are implementation details of this registered host tool.
    snapshot, codebase = collect_one_source(context.source, context.project, context.settings)
    return {"snapshot": snapshot, "codebase": codebase}


def collect_one_source(source: Source, project: Project, settings: Settings) -> tuple[dict, dict | None]:
    """按 source_type 分派具体采集器，并统一返回 snapshot/codebase 数据。

    Args: source 为已校验来源；project 为所属项目；settings 提供大小、超时和 workspace 限制。
    Returns: `(snapshot_data, codebase_data)`，后者仅目录或 Git 仓库来源存在。
    Raises: 不支持的类型或底层 IO 失败时抛出异常，由上层按来源隔离处理。
    副作用：读取本地文件、发起网络请求或执行 Git clone；由网关的资料采集适配器调用。
    """
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
