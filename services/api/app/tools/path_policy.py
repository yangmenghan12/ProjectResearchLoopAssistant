from pathlib import Path


GENERATED_OR_VENDOR_PARTS = {
    ".git",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "target",
}
SENSITIVE_EXACT_NAMES = {
    ".env",
    "id_rsa",
}
SENSITIVE_PREFIXES = (
    ".env.",
    "credentials.",
    "secrets.",
)
SENSITIVE_SUFFIXES = (
    ".key",
    ".pem",
)


def to_relative_posix(path: Path, root: Path) -> str:
    """统一用 POSIX 风格相对路径，方便前端展示和测试断言。"""

    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def should_skip_path(path: Path, root: Path, max_file_bytes: int) -> dict | None:
    """判断路径是否应该跳过，并返回可审计的跳过原因。"""

    relative_path = to_relative_posix(path, root)
    name = path.name
    lowered_name = name.lower()
    parts = {part.lower() for part in path.relative_to(root).parts} if path != root else set()

    if path.is_symlink():
        return {
            "path": relative_path,
            "reason": "symlink",
        }

    if parts & GENERATED_OR_VENDOR_PARTS:
        return {
            "path": relative_path,
            "reason": "generated_or_vendor_path",
        }

    if (
        lowered_name in SENSITIVE_EXACT_NAMES
        or lowered_name.startswith(SENSITIVE_PREFIXES)
        or lowered_name.endswith(SENSITIVE_SUFFIXES)
    ):
        return {
            "path": relative_path,
            "reason": "sensitive_path",
        }

    if path.is_file():
        size_bytes = path.stat().st_size
        if size_bytes > max_file_bytes:
            return {
                "path": relative_path,
                "reason": "large_file",
                "size_bytes": size_bytes,
            }

    return None
