from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AnalysisRun, Project, Source, ToolCall, utc_now
from app.tools.code_scan_tools import scan_codebase
from app.tools.source_adapter import execute_source_collection

if TYPE_CHECKING:
    from app.core.config import Settings

READONLY_PERMISSION_LEVEL = "L0"
MAX_LEDGER_LIST_ITEMS = 80
MAX_LEDGER_STRING_CHARS = 600


class ToolGatewayError(ValueError):
    """工具网关拒绝或执行失败时抛出的领域异常。"""


@dataclass(frozen=True)
class ToolContext:
    """Trusted host context, never part of the model's tool arguments."""
    project_id: str | None = None
    run_id: str | None = None
    turn_id: str | None = None
    source: Source | None = None
    root: Path | None = None
    project: Project | None = None
    settings: Settings | None = None
    allowed_tools: frozenset[str] | None = None
    allowed_permission_levels: frozenset[str] = frozenset({"L0"})
    check_owned: Callable[[], None] = lambda: None


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PathArgs(StrictArgs):
    path: str | None = Field(default=None, min_length=1, max_length=2000)


class UrlArgs(StrictArgs):
    url: str | None = Field(default=None, max_length=2000)


class ReadArgs(StrictArgs):
    path: str = Field(min_length=1, max_length=500)
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=100, ge=1)


class SearchArgs(StrictArgs):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=12, ge=1, le=20)


@dataclass(frozen=True)
class ToolDefinition:
    id: str  # 工具注册的稳定 ID。
    name: str  # 网关调用时使用的工具名。
    description: str  # 面向调用方展示的能力说明。
    permission_level: str  # L0 只读；L1 受控资料采集，仅宿主显式授权。
    input_schema_json: dict[str, Any]  # 工具输入参数的 schema。
    enabled: bool  # 是否允许网关执行该工具。
    executor: Callable[[dict[str, Any]], dict[str, Any]] | None
    created_at: datetime = field(default_factory=utc_now)  # 注册时间。
    updated_at: datetime = field(default_factory=utc_now)  # 最近更新时间。
    transport: Literal["local", "mcp"] = "local"
    input_model: type[BaseModel] | None = None
    model_name: str | None = None
    host_only: bool = False
    requires_source: bool = False
    remote_name: str | None = None
    context_executor: Callable[[dict, ToolContext], dict] | None = None

    @property
    def model_visible(self) -> bool:
        return self.model_name is not None and not self.host_only


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


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "local_filesystem_scan": ToolDefinition(
        id="local_filesystem_scan",
        name="local_filesystem_scan",
        description="只读本地目录扫描：返回目录结构、技术栈和跳过项摘要，不执行脚本或写文件。",
        permission_level=READONLY_PERMISSION_LEVEL,
        input_schema_json=PathArgs.model_json_schema(),
        enabled=True,
        executor=local_filesystem_scan,
        input_model=PathArgs,
    ),
    "git_metadata_read": ToolDefinition(
        id="git_metadata_read",
        name="git_metadata_read",
        description="只读 Git 元数据读取：检查是否为仓库，并读取当前分支和最近 commit 摘要。",
        permission_level=READONLY_PERMISSION_LEVEL,
        input_schema_json=PathArgs.model_json_schema(),
        enabled=True,
        executor=git_metadata_read,
        input_model=PathArgs,
    ),
    "web_fetch_read": ToolDefinition(
        id="web_fetch_read",
        name="web_fetch_read",
        description="只读 Web 抓取占位工具：当前不发起真实网络请求，只返回 skipped/unsupported。",
        permission_level=READONLY_PERMISSION_LEVEL,
        input_schema_json=UrlArgs.model_json_schema(),
        enabled=True,
        executor=web_fetch_read,
        input_model=UrlArgs,
    ),
    "source.collect": ToolDefinition(
        id="source.collect", name="source.collect", description="采集已绑定的文件、目录、网页或仓库，生成资料快照。",
        permission_level="L1", input_schema_json=StrictArgs.model_json_schema(), enabled=True,
        executor=None, input_model=StrictArgs, host_only=True, requires_source=True,
        context_executor=execute_source_collection),
}


for _name, _model, _description, _host_only in (
    ("inspect_project", StrictArgs, "只读项目目录、入口与依赖文件清单。", False),
    ("search_project", SearchArgs, "只读搜索当前项目中的文本，返回文件与行号线索。", False),
    ("read_project_file", ReadArgs, "只读项目源码片段，返回行号、文件哈希与可引用内容。", False),
    ("prepare_github", StrictArgs, "将用户绑定的公开 GitHub 仓库采集到受控工作区。", True),
):
    _key = f"mcp.{_name}"
    TOOL_REGISTRY[_key] = ToolDefinition(id=_key, name=_key, description=_description,
        permission_level="L1" if _host_only else "L0", input_schema_json=_model.model_json_schema(),
        enabled=True, executor=None, transport="mcp", input_model=_model, remote_name=_name,
        model_name=None if _host_only else _name, host_only=_host_only, requires_source=True)

# Compatibility view for existing read-only callers; definitions live in TOOL_REGISTRY only.
DEFAULT_READONLY_TOOL_REGISTRY = {name: tool for name, tool in TOOL_REGISTRY.items()
                                  if tool.permission_level == READONLY_PERMISSION_LEVEL}


def list_default_tools() -> list[ToolDefinition]:
    """返回统一工具注册表，按工具名稳定排序供 API 展示。

    Args: 无。
    Returns: 已注册工具定义列表。
    Raises: 无显式异常。
    副作用：无；由工具目录 API 调用。
    """
    return sorted(TOOL_REGISTRY.values(), key=lambda tool: tool.name)


class ToolGateway:
    """One policy and audit path for local tools, MCP tools and source collectors."""
    def __init__(self, session: Session, context: ToolContext | None = None, *,
                 registry: Mapping[str, ToolDefinition] | None = None, max_calls: int | None = 16):
        self.session = session
        self.context = context or ToolContext()
        self.registry = TOOL_REGISTRY if registry is None else registry
        self.max_calls = max_calls

    @property
    def count(self) -> int:
        if not self.context.run_id:
            return 0
        return self.session.scalar(select(func.count()).select_from(ToolCall).where(
            ToolCall.run_id == self.context.run_id)) or 0

    def _find(self, name: str) -> ToolDefinition | None:
        return self.registry.get(name) or next((tool for tool in self.registry.values()
            if tool.model_name == name), None)

    def schemas(self) -> list[dict]:
        return [{"type": "function", "function": {"name": tool.model_name,
                "description": tool.description, "parameters": tool.input_schema_json}}
                for tool in self.registry.values()
                if tool.enabled and tool.model_visible
                and tool.permission_level in self.context.allowed_permission_levels
                and (not tool.requires_source or self.context.source is not None)
                and (self.context.allowed_tools is None or tool.name in self.context.allowed_tools)]

    def _authorize(self, tool: ToolDefinition, caller: str) -> None:
        context = self.context
        if not tool.enabled:
            raise ToolGatewayError(f"Tool '{tool.name}' is disabled")
        if tool.permission_level not in context.allowed_permission_levels:
            raise ToolGatewayError(f"Only {', '.join(sorted(context.allowed_permission_levels))} tools are allowed")
        if context.allowed_tools is not None and tool.name not in context.allowed_tools:
            raise ToolGatewayError(f"Tool '{tool.name}' is outside this task's allowlist")
        if caller == "model" and not tool.model_visible:
            raise ToolGatewayError(f"Tool '{tool.name}' is not available to the model")
        if tool.requires_source and context.source is None:
            raise ToolGatewayError("A bound source is required")
        if context.source and context.source.project_id != context.project_id:
            raise ToolGatewayError("Source does not belong to this project")
        if context.project and context.project.id != context.project_id:
            raise ToolGatewayError("Project context does not match this task")
        if context.run_id:
            run = self.session.get(AnalysisRun, context.run_id)
            if not run or run.project_id != context.project_id:
                raise ToolGatewayError("Run does not belong to this project")
        if tool.transport == "mcp" and context.root is None:
            raise ToolGatewayError("A host-bound project root is required")
        if tool.context_executor and (context.project is None or context.settings is None):
            raise ToolGatewayError("Source collection requires host-bound project and settings")

    def _scope_local_path(self, tool: ToolDefinition, args: dict) -> dict:
        if tool.name not in {"local_filesystem_scan", "git_metadata_read"} or self.context.root is None:
            return args
        if self.context.source and self.context.source.source_type == "local_file":
            raise ToolGatewayError("Directory tools cannot expand a single-file source")
        root = self.context.root.resolve()
        path = Path(args.get("path") or root)
        path = path if path.is_absolute() else root / path
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ToolGatewayError("Path escapes the attached project")
        return {**args, "path": str(path.resolve())}

    def invoke(self, name: str, args: dict | None = None, *, caller: Literal["host", "model"] = "host") -> dict:
        args = {} if args is None else args
        tool = self._find(name)
        context = self.context
        started = perf_counter()
        call = ToolCall(project_id=context.project_id, run_id=context.run_id, turn_id=context.turn_id,
            tool_name=tool.name if tool else name,
            permission_level=tool.permission_level if tool else "unknown", status="running",
            input_summary_json=_summarize_json(args), output_summary_json={})
        self.session.add(call)
        self.session.commit()
        try:
            context.check_owned()
            if caller not in {"host", "model"}:
                raise ToolGatewayError("Unknown tool caller")
            if not tool:
                raise ToolGatewayError(f"Tool '{name}' is not registered in the gateway")
            self._authorize(tool, caller)
            if self.max_calls is not None and context.run_id and self.count > self.max_calls:
                raise ToolGatewayError("Task tool budget is exhausted")
            if not isinstance(args, dict):
                raise ToolGatewayError("Tool arguments must be a JSON object")
            validated = tool.input_model.model_validate(args).model_dump() if tool.input_model else args
            validated = self._scope_local_path(tool, validated)
            if tool.transport == "mcp":
                from app.tools.mcp_adapter import execute_mcp
                output = execute_mcp(tool, validated, context)
            elif tool.context_executor is not None:
                output = tool.context_executor(validated, context)
            elif tool.executor is not None:
                output = tool.executor(validated)
            else:
                raise ToolGatewayError("Tool has no execution adapter")
            context.check_owned()
            call.status = "succeeded"
            call.output_summary_json = _summarize_json(output)
            call.latency_ms = _elapsed_ms(started)
            self.session.commit()
            return output
        except Exception as exc:
            _mark_failed_call(self.session, call, started, str(exc))
            if isinstance(exc, ToolGatewayError):
                raise
            raise ToolGatewayError(f"Tool '{name}' failed: {exc}") from exc


def invoke_readonly_tool(
    session: Session,
    *,
    tool_name: str,
    input_json: dict[str, Any] | None = None,
    project_id: str | None = None,
    run_id: str | None = None,
    turn_id: str | None = None,
    registry: Mapping[str, ToolDefinition] | None = None,
) -> dict[str, Any]:
    """所有工具调用必须先过网关，并写入 ToolCall 账本。

    权限边界：这个兼容入口只允许 L0 只读工具。未知工具、禁用工具和 L1/L2 工具即使被误注册，
    也会在执行前拒绝。账本目的：让每次工具尝试都可追溯，失败同样提交，便于审计和恢复。

    Args: session 为数据库会话；tool_name 为已注册工具名；input_json 为工具参数；其余 ID 可选。
    Returns: 工具执行结果的摘要字典。
    Raises: 工具不存在、未启用、权限不符或执行失败时抛出 ToolGatewayError。
    副作用：可能读取文件/Git，并写入 ToolCall 审计记录；由 tools API 和分析流程调用。
    """

    context = ToolContext(project_id=project_id, run_id=run_id, turn_id=turn_id)
    return ToolGateway(session, context, registry=registry).invoke(tool_name, input_json)


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
