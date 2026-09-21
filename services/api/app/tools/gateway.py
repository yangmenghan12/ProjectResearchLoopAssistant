from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import ToolCall, utc_now
from app.tools.code_scan_tools import scan_codebase

READONLY_PERMISSION_LEVEL = "L0"
MAX_LEDGER_LIST_ITEMS = 80
MAX_LEDGER_STRING_CHARS = 600


class ToolGatewayError(RuntimeError):
    """工具网关拒绝或执行失败时抛出的领域异常。"""


@dataclass(frozen=True)
class ToolDefinition:
    id: str
    name: str
    description: str
    permission_level: str
    input_schema_json: dict[str, Any]
    enabled: bool
    executor: Callable[[dict[str, Any]], dict[str, Any]]
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


def local_filesystem_scan(input_json: dict[str, Any]) -> dict[str, Any]:
    """只读取目录结构和少量摘要，不读取敏感文件、不执行脚本。"""

    path_value = input_json.get("path") or Path.cwd()
    root = Path(str(path_value)).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ToolGatewayError("local_filesystem_scan requires an existing directory path")

    raw_result = scan_codebase(root, max_file_bytes=256 * 1024, excerpt_chars=500)
    file_tree = raw_result["file_tree"][:MAX_LEDGER_LIST_ITEMS]
    skipped_items = raw_result["skipped_items"][:MAX_LEDGER_LIST_ITEMS]
    return {
        "status": "collected",
        "root_label": raw_result["root_label"],
        "tech_stack": raw_result["tech_stack"],
        "dependency_files": raw_result["dependency_files"][:MAX_LEDGER_LIST_ITEMS],
        "config_files": raw_result["config_files"][:MAX_LEDGER_LIST_ITEMS],
        "test_files": raw_result["test_files"][:MAX_LEDGER_LIST_ITEMS],
        "entrypoint_files": raw_result["entrypoint_files"][:MAX_LEDGER_LIST_ITEMS],
        "readme_excerpt": raw_result["readme_excerpt"],
        "file_tree": file_tree,
        "skipped_items": skipped_items,
        "metadata": {
            **raw_result["metadata"],
            "file_tree_returned": len(file_tree),
            "skipped_items_returned": len(skipped_items),
            "file_tree_truncated": len(raw_result["file_tree"]) > len(file_tree),
            "skipped_items_truncated": len(raw_result["skipped_items"]) > len(skipped_items),
        },
    }


def git_metadata_read(input_json: dict[str, Any]) -> dict[str, Any]:
    """通过 git 只读命令读取仓库元数据，不执行 checkout、reset、commit 等写操作。"""

    path_value = input_json.get("path") or Path.cwd()
    root = Path(str(path_value)).expanduser().resolve()
    if root.is_file():
        root = root.parent
    if not root.exists():
        raise ToolGatewayError("git_metadata_read requires an existing path")

    inside_repo = _run_git(root, ["rev-parse", "--is-inside-work-tree"])
    if inside_repo["status"] == "unsupported":
        return inside_repo
    if inside_repo["returncode"] != 0 or inside_repo["stdout"].strip() != "true":
        return {"status": "not_git_repo", "is_git_repo": False, "root_path": str(root)}

    repo_root = _run_git(root, ["rev-parse", "--show-toplevel"])
    branch = _run_git(root, ["symbolic-ref", "--short", "HEAD"])
    short_head = _run_git(root, ["rev-parse", "--short", "HEAD"])
    latest_commit = _run_git(
        root,
        ["log", "-1", "--pretty=format:%H%x1f%h%x1f%an%x1f%ad%x1f%s", "--date=iso-strict"],
    )
    commit_summary: dict[str, str] | None = None
    if latest_commit["returncode"] == 0 and latest_commit["stdout"].strip():
        parts = latest_commit["stdout"].split("\x1f", maxsplit=4)
        if len(parts) == 5:
            commit_summary = {
                "hash": parts[0],
                "short_hash": parts[1],
                "author": parts[2],
                "date": parts[3],
                "subject": parts[4],
            }

    return {
        "status": "collected",
        "is_git_repo": True,
        "root_path": repo_root["stdout"].strip() if repo_root["returncode"] == 0 else str(root),
        "branch": branch["stdout"].strip() if branch["returncode"] == 0 else None,
        "head": short_head["stdout"].strip() if short_head["returncode"] == 0 else None,
        "latest_commit": commit_summary,
    }


def web_fetch_read(input_json: dict[str, Any]) -> dict[str, Any]:
    """当前只保留占位能力，避免在只读网关中引入真实网络依赖。"""

    return {
        "status": "skipped",
        "unsupported": True,
        "reason": "web_fetch_read is registered as a read-only placeholder; network fetch is disabled.",
        "requested_url": input_json.get("url"),
    }


DEFAULT_READONLY_TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "local_filesystem_scan": ToolDefinition(
        id="local_filesystem_scan",
        name="local_filesystem_scan",
        description="只读本地目录扫描：返回目录结构、技术栈和跳过项摘要，不执行脚本或写文件。",
        permission_level=READONLY_PERMISSION_LEVEL,
        input_schema_json={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "要扫描的本地目录路径"}},
        },
        enabled=True,
        executor=local_filesystem_scan,
    ),
    "git_metadata_read": ToolDefinition(
        id="git_metadata_read",
        name="git_metadata_read",
        description="只读 Git 元数据读取：检查是否为仓库，并读取当前分支和最近 commit 摘要。",
        permission_level=READONLY_PERMISSION_LEVEL,
        input_schema_json={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Git 仓库或其子目录路径"}},
        },
        enabled=True,
        executor=git_metadata_read,
    ),
    "web_fetch_read": ToolDefinition(
        id="web_fetch_read",
        name="web_fetch_read",
        description="只读 Web 抓取占位工具：当前不发起真实网络请求，只返回 skipped/unsupported。",
        permission_level=READONLY_PERMISSION_LEVEL,
        input_schema_json={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "未来只读抓取目标 URL"}},
        },
        enabled=True,
        executor=web_fetch_read,
    ),
}


def list_default_tools() -> list[ToolDefinition]:
    return sorted(DEFAULT_READONLY_TOOL_REGISTRY.values(), key=lambda tool: tool.name)


def invoke_readonly_tool(
    session: Session,
    *,
    tool_name: str,
    input_json: dict[str, Any] | None = None,
    project_id: str | None = None,
    run_id: str | None = None,
    turn_id: str | None = None,
    registry: Mapping[str, ToolDefinition] = DEFAULT_READONLY_TOOL_REGISTRY,
) -> dict[str, Any]:
    """所有工具调用必须先过网关，并写入 ToolCall 账本。

    权限边界：当前网关只允许 L0 只读工具。未知工具、禁用工具和未来 L1/L2 工具即使被误注册，
    也会在执行前拒绝。账本目的：让每次工具尝试都可追溯，失败同样提交，便于审计和恢复。
    """

    input_json = input_json or {}
    tool = registry.get(tool_name)
    permission_level = tool.permission_level if tool is not None else "unknown"
    started_at = perf_counter()
    call = ToolCall(
        project_id=project_id,
        run_id=run_id,
        turn_id=turn_id,
        tool_name=tool_name,
        permission_level=permission_level,
        status="running",
        input_summary_json=_summarize_json(input_json),
        output_summary_json={},
    )
    session.add(call)
    session.commit()
    session.refresh(call)

    try:
        if tool is None:
            raise ToolGatewayError(f"Tool '{tool_name}' is not registered in the read-only gateway")
        if not tool.enabled:
            raise ToolGatewayError(f"Tool '{tool_name}' is disabled")
        if tool.permission_level != READONLY_PERMISSION_LEVEL:
            raise ToolGatewayError(
                f"Only L0 read-only tools are allowed; '{tool_name}' is {tool.permission_level}"
            )

        output = tool.executor(input_json)
        call.status = "succeeded"
        call.output_summary_json = _summarize_json(output)
        call.error_message = None
        call.latency_ms = _elapsed_ms(started_at)
        session.commit()
        return output
    except ToolGatewayError as exc:
        _mark_failed_call(session, call, started_at, str(exc))
        raise
    except Exception as exc:
        message = f"Tool '{tool_name}' failed: {exc}"
        _mark_failed_call(session, call, started_at, message)
        raise ToolGatewayError(message) from exc


def _mark_failed_call(session: Session, call: ToolCall, started_at: float, message: str) -> None:
    call.status = "failed"
    call.error_message = message
    call.latency_ms = _elapsed_ms(started_at)
    session.commit()


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((perf_counter() - started_at) * 1000))


def _run_git(root: Path, args: list[str]) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except FileNotFoundError:
        return {"status": "unsupported", "unsupported": True, "reason": "git executable not found"}
    except subprocess.TimeoutExpired:
        return {"status": "failed", "returncode": 124, "stdout": "", "stderr": "git command timed out"}

    return {
        "status": "completed",
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _summarize_json(value: Any) -> dict[str, Any]:
    summarized = _summarize_value(value)
    if isinstance(summarized, dict):
        return summarized
    return {"value": summarized}


def _summarize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _summarize_value(nested) for key, nested in value.items()}
    if isinstance(value, list):
        limited = [_summarize_value(item) for item in value[:MAX_LEDGER_LIST_ITEMS]]
        if len(value) > len(limited):
            limited.append({"truncated": True, "omitted_count": len(value) - len(limited)})
        return limited
    if isinstance(value, tuple):
        return _summarize_value(list(value))
    if isinstance(value, str):
        if len(value) <= MAX_LEDGER_STRING_CHARS:
            return value
        return f"{value[:MAX_LEDGER_STRING_CHARS]}...<truncated {len(value) - MAX_LEDGER_STRING_CHARS} chars>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)
