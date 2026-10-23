import hashlib
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

GIT_OUTPUT_KWARGS = {
    "capture_output": True,
    "text": True,
    # Windows 默认 locale 可能是 GBK；Git/远端服务输出 UTF-8 字节时会触发 reader thread 解码失败。
    # 显式使用 UTF-8 并替换非法字节，保证真正的 git stderr 能返回给上层页面。
    "encoding": "utf-8",
    "errors": "replace",
}


def ensure_child_path(path: Path, root: Path) -> Path:
    """确认目标路径位于 workspace 内，避免 clone 清理时误删外部目录。"""

    resolved_path = path.resolve()
    resolved_root = root.resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise ValueError("Path escapes workspace root")
    return resolved_path


def parse_github_repo(github_url: str) -> tuple[str, str]:
    """解析并校验公开 GitHub 仓库 URL，提取 owner 和 repo。

    Args: github_url 为形如 `https://github.com/owner/repo` 的地址。
    Returns: 小写的 `(owner, repo)` 元组。
    Raises: URL 主机、路径层级或仓库格式不符合预期时抛出 ValueError。
    副作用：无；由仓库路径和 clone 流程调用。
    """
    parsed = urlparse(github_url)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if parsed.netloc.lower() != "github.com" or len(parts) != 2:
        raise ValueError("Expected a public GitHub repository URL")
    owner = parts[0].lower()
    repo = re.sub(r"\.git$", "", parts[1], flags=re.IGNORECASE).lower()
    return owner, repo


def safe_slug(value: str) -> str:
    """把标识符转换为适合文件系统路径的短横线片段。

    Args: value 为待清理字符串。
    Returns: 仅含小写字母、数字、点、下划线和短横线的字符串。
    Raises: 无显式异常。
    副作用：无；由路径构造函数调用。
    """
    return re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-")


def compact_path_segment(value: str, *, max_length: int) -> str:
    """将长标识压缩成适合 Windows Git 的路径片段，短名称保持原样便于排查。"""

    slug = safe_slug(value) or "item"
    if len(slug) <= max_length:
        return slug
    digest = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:8]
    prefix_length = max(1, max_length - len(digest) - 1)
    return f"{slug[:prefix_length].rstrip('.-_')}-{digest}"


def repo_workspace_path(workspace_root: Path, project_id: str, github_url: str) -> Path:
    """在受控 workspace 下生成仓库 clone 目标路径并校验路径边界。

    Args: workspace_root 为允许写入的根目录；project_id 为项目 ID；github_url 为仓库地址。
    Returns: 位于 workspace 内的仓库目标路径。
    Raises: GitHub URL 非法或目标路径逃逸根目录时抛出 ValueError。
    副作用：无；由 `clone_public_github_repo` 调用。
    """
    owner, repo = parse_github_repo(github_url)
    project_segment = compact_path_segment(project_id, max_length=16)
    repo_segment = compact_path_segment(f"{owner}-{repo}", max_length=48)
    target = workspace_root / project_segment / "repos" / repo_segment
    return ensure_child_path(target, workspace_root)


def clone_public_github_repo(
    github_url: str,
    workspace_root: Path,
    project_id: str,
    timeout_seconds: float,
    runner=subprocess.run,
) -> dict:
    """在项目 workspace 下克隆公开 GitHub 仓库并返回本地路径元数据。

    Args: github_url 为已校验 GitHub URL；workspace_root 为受控根目录；project_id 为项目 ID。
    Returns: 含本地路径、远端地址和 clone 状态的字典。
    Raises: URL、路径、Git 命令或超时错误向上抛出。
    副作用：执行外部 git 进程并写入 workspace；由 `collect_one_source` 调用。
    """

    target = repo_workspace_path(workspace_root, project_id, github_url)
    target_parent = ensure_child_path(target.parent, workspace_root)
    target_parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        ensure_child_path(target, workspace_root)
        shutil.rmtree(target)

    try:
        runner(
            ["git", "clone", "--depth", "1", github_url, str(target)],
            timeout=timeout_seconds,
            check=True,
            **GIT_OUTPUT_KWARGS,
        )
        branch = runner(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=target,
            timeout=timeout_seconds,
            check=True,
            **GIT_OUTPUT_KWARGS,
        ).stdout.strip()
        commit = runner(
            ["git", "rev-parse", "HEAD"],
            cwd=target,
            timeout=timeout_seconds,
            check=True,
            **GIT_OUTPUT_KWARGS,
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(_format_git_failure("git clone", exc)) from exc

    return {
        "status": "collected",
        "title": f"{parse_github_repo(github_url)[0]}/{parse_github_repo(github_url)[1]}",
        "content_excerpt": None,
        "metadata": {
            "github_url": github_url,
            "local_path": str(target),
            "branch": branch,
            "commit": commit,
        },
        "skipped_items": [],
    }


def _format_git_failure(action: str, exc: subprocess.CalledProcessError) -> str:
    """把 git 子进程错误整理为包含命令和输出的可读诊断文本。

    Args: action 为操作名称；exc 为 git 子进程异常。
    Returns: 可直接写入错误记录的字符串。
    Raises: 无显式异常。
    副作用：无；由 clone 流程的异常处理调用。
    """
    stdout = _stringify_process_output(exc.stdout or exc.output)
    stderr = _stringify_process_output(exc.stderr)
    message_parts = [
        f"{action} failed with exit code {exc.returncode}",
        f"command: {_format_command(exc.cmd)}",
    ]
    if stderr:
        message_parts.append(f"stderr: {stderr}")
    if stdout:
        message_parts.append(f"stdout: {stdout}")
    return "\n".join(message_parts)


def _stringify_process_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip()


def _format_command(command: object) -> str:
    if isinstance(command, (list, tuple)):
        return " ".join(str(part) for part in command)
    return str(command)
