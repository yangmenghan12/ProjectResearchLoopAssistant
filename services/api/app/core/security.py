from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


class SourceValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedSource:
    normalized_uri: str  # 经过类型校验和规范化后的来源地址或本地绝对路径。
    metadata: dict[str, str]  # 校验阶段提取的 host、owner、repo 或路径类型等元数据。


SUPPORTED_SOURCE_TYPES = {"web_url", "local_file", "local_directory", "github_repo"}


def validate_source_input(source_type: str, uri: str) -> ValidatedSource:
    """在采集前校验来源类型和 URI，并返回统一的规范化表示。

    Args: source_type 必须属于支持集合；uri 为非空原始来源字符串。
    Returns: 含 normalized_uri 和 metadata 的 ValidatedSource。
    Raises: 输入非法时抛出 SourceValidationError。
    副作用：仅读取路径元数据，不读取内容、不联网；由 source API 和 bundle service 调用。
    """

    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise SourceValidationError(f"Unsupported source type: {source_type}")

    trimmed_uri = uri.strip()
    if not trimmed_uri:
        raise SourceValidationError("Source uri cannot be empty")

    if source_type == "web_url":
        return _validate_web_url(trimmed_uri)
    if source_type == "github_repo":
        return _validate_github_repo(trimmed_uri)
    if source_type == "local_file":
        return _validate_local_path(trimmed_uri, expected_kind="file")
    return _validate_local_path(trimmed_uri, expected_kind="directory")


def _validate_web_url(uri: str) -> ValidatedSource:
    """校验 HTTP(S) URL 的基本结构并提取 host、scheme 和端口元数据。

    Args: uri 为已去除首尾空白的 URL。
    Returns: 规范化来源对象。
    Raises: URL 协议、主机、凭据或端口非法时抛出 SourceValidationError。
    副作用：无网络访问；由 `validate_source_input` 调用。
    """
    parsed = urlparse(uri)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SourceValidationError("web_url must be an http or https URL")
    if parsed.username or parsed.password:
        raise SourceValidationError("web_url must not contain user credentials")

    metadata = {"scheme": parsed.scheme, "host": parsed.hostname}
    try:
        port = parsed.port
    except ValueError as exc:
        raise SourceValidationError("web_url contains an invalid port") from exc
    if port is not None:
        metadata["port"] = str(port)

    return ValidatedSource(
        normalized_uri=uri,
        metadata=metadata,
    )


def _validate_github_repo(uri: str) -> ValidatedSource:
    """校验 GitHub 仓库 URL，拒绝查询参数、凭据和非标准 host。

    Args: uri 为待校验的 GitHub URL。
    Returns: 去除 `.git` 后缀并提取 owner/repo 元数据的来源对象。
    Raises: URL 不符合公开 GitHub 仓库格式时抛出 SourceValidationError。
    副作用：无网络访问；由 `validate_source_input` 调用。
    """
    parsed = urlparse(uri)
    try:
        port = parsed.port
    except ValueError as exc:
        raise SourceValidationError("github_repo contains an invalid port") from exc

    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or port is not None
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or len(path_parts) != 2
    ):
        raise SourceValidationError("github_repo must be an https://github.com/<owner>/<repo> URL")

    repo = _remove_git_suffix(path_parts[1])
    if not repo:
        raise SourceValidationError("github_repo must include a repository name")

    normalized = f"https://github.com/{path_parts[0]}/{repo}"
    return ValidatedSource(
        normalized_uri=normalized,
        metadata={
            "scheme": "https",
            "host": "github.com",
            "owner": path_parts[0],
            "repo": repo,
        },
    )


def _remove_git_suffix(repo: str) -> str:
    """移除仓库名末尾可选的 `.git` 后缀。

    Args: repo 为仓库路径末段。
    Returns: 去除后缀的仓库名。
    Raises: 无显式异常。
    副作用：无；由 GitHub URL 校验调用。
    """
    if repo.lower().endswith(".git"):
        return repo[:-4]
    return repo


def _validate_local_path(uri: str, expected_kind: str) -> ValidatedSource:
    """解析本地路径并确认其存在且类型符合 file 或 directory 要求。

    Args: uri 为本地路径；expected_kind 取 `file` 或 `directory`。
    Returns: 含绝对路径和 kind 元数据的来源对象。
    Raises: 路径不存在或类型不匹配时抛出 SourceValidationError。
    副作用：仅读取文件系统元数据，不读取文件内容；由来源校验入口调用。
    """
    path = Path(uri).expanduser().resolve()
    if not path.exists():
        raise SourceValidationError(f"Local path does not exist: {path}")
    if expected_kind == "file" and not path.is_file():
        raise SourceValidationError(f"Expected a local file: {path}")
    if expected_kind == "directory" and not path.is_dir():
        raise SourceValidationError(f"Expected a local directory: {path}")
    return ValidatedSource(
        normalized_uri=str(path),
        metadata={"kind": expected_kind},
    )
