"""Bundled read-only MCP server. Roots and repository URLs come from the host,
never from model arguments. No shell/execution/write tools are exposed.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from app.tools.git_tools import clone_public_github_repo
from app.tools.path_policy import should_skip_path

server = FastMCP("research-sources")
ROOT = Path(os.environ.get("RESEARCH_MCP_ROOT", ".")).absolute()
MAX_BYTES = 1_000_000
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt", ".toml", ".yaml", ".yml", ".java", ".go", ".rs", ".sql", ".ini", ".cfg", ".xml", ".html", ".css", ".sh", ".ps1"}


def checked_path(relative_path: str) -> Path:
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Only relative paths within the attached project are allowed")
    candidate = ROOT / path
    only_file = os.environ.get("RESEARCH_MCP_ONLY_FILE")
    if only_file and candidate.absolute() != Path(only_file).absolute():
        raise ValueError("Only the explicitly attached file may be read")
    current = ROOT
    if ROOT.is_symlink():
        raise ValueError("A symbolic link cannot be used as a project root")
    for part in path.parts:
        current = current / part
        if current.is_symlink() or should_skip_path(current, ROOT, MAX_BYTES):
            raise ValueError("Path is excluded by the source policy")
    if not candidate.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Path escapes the attached project")
    return candidate


def source_files():
    """Bound traversal as well as output size; binary and sensitive files stay out."""
    only_file = os.environ.get("RESEARCH_MCP_ONLY_FILE")
    if only_file:
        yield checked_path(Path(only_file).name)
        return
    visited = 0
    for current, dirs, names in os.walk(ROOT, followlinks=False):
        visited += 1
        if visited > 3000:
            return
        dirs[:] = sorted(d for d in dirs if not (Path(current) / d).is_symlink()
                         and not should_skip_path(Path(current) / d, ROOT, MAX_BYTES))
        for name in sorted(names):
            visited += 1
            if visited > 3000:
                return
            path = Path(current) / name
            if path.suffix.lower() not in TEXT_SUFFIXES and name not in {"Dockerfile", "Makefile"}:
                continue
            try:
                checked = checked_path(path.relative_to(ROOT).as_posix())
                if checked.is_file():
                    yield checked
            except (ValueError, OSError):
                continue


@server.tool()
def inspect_project() -> dict:
    """Read the attached project's bounded file inventory, entrypoints and dependencies."""
    # Scan a bounded set for the conversation interface; legacy full scans remain available.
    if not ROOT.is_dir():
        raise ValueError("Attached project directory no longer exists")
    files = [p.relative_to(ROOT).as_posix() for p in source_files()]
    from app.tools.code_scan_tools import DEPENDENCY_FILE_NAMES, ENTRYPOINT_FILE_NAMES
    return {"files": files[:1000], "truncated": len(files) >= 1000,
            "entrypoint_files": [p for p in files if Path(p).name in ENTRYPOINT_FILE_NAMES][:40],
            "dependency_files": [p for p in files if Path(p).name in DEPENDENCY_FILE_NAMES][:40]}


@server.tool()
def read_project_file(path: str, start_line: int = 1, end_line: int = 100) -> dict:
    """Read up to 160 lines with file hash, line range and relative source path."""
    if start_line < 1 or end_line < start_line or end_line - start_line >= 160:
        raise ValueError("Read between 1 and 160 lines")
    target = checked_path(path)
    if not target.is_file() or (target.suffix.lower() not in TEXT_SUFFIXES and target.name not in {"Dockerfile", "Makefile"}):
        raise ValueError("Only supported text/source files can be read")
    raw = target.read_bytes()
    if len(raw) > MAX_BYTES or b"\x00" in raw:
        raise ValueError("File is too large or binary")
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    selected = lines[start_line - 1:end_line]
    # Never claim line coverage beyond the actual excerpt.
    excerpt = []
    size = 0
    for line in selected:
        if size + len(line) > 12000:
            break
        excerpt.append(line)
        size += len(line) + 1
    return {"path": target.relative_to(ROOT).as_posix(), "start_line": start_line, "end_line": start_line + len(excerpt) - 1,
            "content": "\n".join(excerpt), "file_hash": hashlib.sha256(raw).hexdigest(),
            "total_lines": len(lines), "truncated": len(excerpt) < len(selected)}


@server.tool()
def search_project(query: str, limit: int = 12) -> dict:
    """Literal, case-insensitive code search. Use read_project_file for full evidence."""
    if not query.strip() or len(query) > 200 or not 1 <= limit <= 20:
        raise ValueError("Invalid search query or limit")
    matches = []
    for path in source_files():
        try:
            raw = path.read_bytes()
            if len(raw) > MAX_BYTES or b"\x00" in raw:
                continue
            for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                if query.casefold() in line.casefold():
                    matches.append({"path": path.relative_to(ROOT).as_posix(), "line": number, "text": line[:350]})
                    if len(matches) >= limit:
                        return {"matches": matches, "truncated": True}
        except OSError:
            continue
    return {"matches": matches, "truncated": False}


@server.tool()
def prepare_github() -> dict:
    """Fetch only the GitHub URL explicitly attached by the user, to host-managed storage."""
    url = os.environ.get("RESEARCH_MCP_GITHUB_URL")
    if not url:
        raise ValueError("No GitHub source was attached to this task")
    # Use a unique host-created snapshot directory: never delete an existing checkout.
    return clone_public_github_repo(url, Path(os.environ["RESEARCH_MCP_WORKSPACE"]),
                                    os.environ["RESEARCH_MCP_SNAPSHOT_ID"], 45)


if __name__ == "__main__":
    server.run(transport="stdio")
