from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


class SourceValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedSource:
    normalized_uri: str
    metadata: dict[str, str]


SUPPORTED_SOURCE_TYPES = {"web_url", "local_file", "local_directory", "github_repo"}


def validate_source_input(source_type: str, uri: str) -> ValidatedSource:
    """Validate source boundaries without reading content, scanning, or networking."""

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
    if repo.lower().endswith(".git"):
        return repo[:-4]
    return repo


def _validate_local_path(uri: str, expected_kind: str) -> ValidatedSource:
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
