from pathlib import Path

from app.tools.path_policy import should_skip_path

PDF_PAGE_LIMIT = 5


def read_text_excerpt(path: Path, excerpt_chars: int) -> str:
    """读取文本摘要，编码异常时保留可读字符，避免采集中断。"""

    return path.read_text(encoding="utf-8", errors="replace")[:excerpt_chars]


def read_pdf_excerpt(path: Path, excerpt_chars: int) -> tuple[str | None, int]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf is required to parse PDF files") from exc

    reader = PdfReader(str(path))
    page_count = len(reader.pages)
    excerpt_parts: list[str] = []
    remaining_chars = excerpt_chars
    for page in reader.pages[:PDF_PAGE_LIMIT]:
        if remaining_chars <= 0:
            break
        page_text = page.extract_text() or ""
        if not page_text:
            continue
        excerpt_parts.append(page_text[:remaining_chars])
        remaining_chars -= len(excerpt_parts[-1])
    excerpt = "\n".join(excerpt_parts)[:excerpt_chars]
    return (excerpt or None), page_count


def read_local_file(path: Path, max_file_bytes: int, excerpt_chars: int) -> dict:
    """只读读取本地文件，返回摘要和元数据，不写入或修改原文件。"""

    policy_path = path.expanduser()
    if not policy_path.is_absolute():
        policy_path = Path.cwd() / policy_path
    decision = should_skip_path(policy_path, policy_path.parent, max_file_bytes)
    if decision is not None:
        return {
            "status": "skipped",
            "title": policy_path.name,
            "content_excerpt": None,
            "metadata": {
                "path": str(policy_path.resolve()),
            },
            "skipped_items": [decision],
        }

    resolved_path = policy_path.resolve()
    metadata = {
        "path": str(resolved_path),
        "size_bytes": resolved_path.stat().st_size,
    }
    if resolved_path.suffix.lower() == ".pdf":
        metadata["file_type"] = "pdf"
        content_excerpt = None
        try:
            content_excerpt, page_count = read_pdf_excerpt(resolved_path, excerpt_chars)
            metadata["page_count"] = page_count
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001 - PDF parsing must not block collection.
            metadata["pdf_parse_error"] = str(exc)
        return {
            "status": "collected",
            "title": resolved_path.name,
            "content_excerpt": content_excerpt,
            "metadata": metadata,
            "skipped_items": [],
        }

    return {
        "status": "collected",
        "title": resolved_path.name,
        "content_excerpt": read_text_excerpt(resolved_path, excerpt_chars),
        "metadata": metadata,
        "skipped_items": [],
    }
