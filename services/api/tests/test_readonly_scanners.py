import os
import sys
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from app.tools.code_scan_tools import scan_codebase
from app.tools.file_tools import read_local_file
from app.tools.path_policy import should_skip_path


def test_path_policy_skips_sensitive_and_generated_paths(tmp_path: Path):
    sensitive_file = tmp_path / ".env"
    generated_file = tmp_path / "node_modules" / "package" / "index.js"
    sensitive_file.write_text("SECRET=value", encoding="utf-8")
    generated_file.parent.mkdir(parents=True)
    generated_file.write_text("module.exports = {}", encoding="utf-8")

    sensitive_decision = should_skip_path(sensitive_file, tmp_path, max_file_bytes=1024)
    generated_decision = should_skip_path(generated_file, tmp_path, max_file_bytes=1024)

    assert sensitive_decision is not None
    assert sensitive_decision["reason"] == "sensitive_path"
    assert generated_decision is not None
    assert generated_decision["reason"] == "generated_or_vendor_path"


def test_path_policy_skips_large_files(tmp_path: Path):
    large_file = tmp_path / "large.log"
    large_file.write_text("x" * 32, encoding="utf-8")

    decision = should_skip_path(large_file, tmp_path, max_file_bytes=8)

    assert decision is not None
    assert decision["reason"] == "large_file"
    assert decision["size_bytes"] == 32


def test_path_policy_skips_symlinks(tmp_path: Path):
    target = tmp_path / "target.txt"
    target.write_text("target", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable in this Windows environment")

    decision = should_skip_path(link, tmp_path, max_file_bytes=1024)

    assert decision is not None
    assert decision["reason"] == "symlink"


def test_read_local_file_returns_excerpt_and_metadata(tmp_path: Path):
    local_file = tmp_path / "notes.md"
    local_file.write_text("# Notes\n" + "important " * 20, encoding="utf-8")

    result = read_local_file(local_file, max_file_bytes=1024, excerpt_chars=40)

    assert result["status"] == "collected"
    assert result["title"] == "notes.md"
    assert result["content_excerpt"].startswith("# Notes")
    assert len(result["content_excerpt"]) <= 40
    assert result["metadata"]["size_bytes"] > 0


def test_read_local_file_extracts_valid_pdf_text(tmp_path: Path):
    pdf_file = tmp_path / "manual.pdf"
    pdf = canvas.Canvas(str(pdf_file))
    pdf.drawString(72, 720, "Loop PDF manual")
    pdf.save()

    result = read_local_file(pdf_file, max_file_bytes=4096, excerpt_chars=80)

    assert result["status"] == "collected"
    assert result["title"] == "manual.pdf"
    assert "Loop PDF manual" in result["content_excerpt"]
    assert result["metadata"]["file_type"] == "pdf"
    assert result["metadata"]["page_count"] == 1
    assert "pdf_parse_error" not in result["metadata"]


def test_read_local_file_collects_malformed_pdf_with_parse_error_metadata(tmp_path: Path):
    pdf_file = tmp_path / "broken.pdf"
    pdf_file.write_bytes(b"%PDF-1.4\nnot a complete pdf")

    result = read_local_file(pdf_file, max_file_bytes=1024, excerpt_chars=40)

    assert result["status"] == "collected"
    assert result["title"] == "broken.pdf"
    assert result["content_excerpt"] is None
    assert result["metadata"]["file_type"] == "pdf"
    assert result["metadata"]["path"] == str(pdf_file.resolve())
    assert result["metadata"]["size_bytes"] > 0
    assert "pdf_parse_error" in result["metadata"]
    assert result["skipped_items"] == []


def test_read_local_file_does_not_hide_missing_pdf_dependency(tmp_path: Path, monkeypatch):
    pdf_file = tmp_path / "manual.pdf"
    pdf_file.write_bytes(b"%PDF-1.4\n")
    for module_name in list(sys.modules):
        if module_name == "pypdf" or module_name.startswith("pypdf."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)
    monkeypatch.setitem(sys.modules, "pypdf", None)

    with pytest.raises(RuntimeError, match="pypdf"):
        read_local_file(pdf_file, max_file_bytes=4096, excerpt_chars=80)


def test_read_local_file_skips_oversized_pdf_before_parsing(tmp_path: Path):
    pdf_file = tmp_path / "large.pdf"
    pdf_file.write_bytes(b"%PDF-1.4\n" + b"x" * 64)

    result = read_local_file(pdf_file, max_file_bytes=8, excerpt_chars=40)

    assert result["status"] == "skipped"
    assert result["content_excerpt"] is None
    assert result["skipped_items"][0]["reason"] == "large_file"
    assert "file_type" not in result["metadata"]


def test_scan_codebase_detects_structure_and_skips_risky_files(tmp_path: Path):
    (tmp_path / "README.md").write_text("# Demo\nA small Python and TypeScript app.", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest"}}', encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text("export const x = 1", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_app(): assert True", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=value", encoding="utf-8")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "bundle.js").write_text("generated", encoding="utf-8")
    (tmp_path / "large.bin").write_text("x" * 128, encoding="utf-8")

    result = scan_codebase(tmp_path, max_file_bytes=64, excerpt_chars=80)

    assert "python" in result["tech_stack"]
    assert "typescript" in result["tech_stack"]
    assert "pyproject.toml" in result["dependency_files"]
    assert "package.json" in result["dependency_files"]
    assert "tests/test_app.py" in result["test_files"]
    assert "app/main.py" in result["entrypoint_files"]
    assert "src/index.ts" in result["entrypoint_files"]
    assert result["readme_excerpt"].startswith("# Demo")
    skipped = {(item["path"], item["reason"]) for item in result["skipped_items"]}
    assert (".env", "sensitive_path") in skipped
    assert ("dist", "generated_or_vendor_path") in skipped
    assert ("large.bin", "large_file") in skipped
    assert all(not item["path"].startswith(".git") for item in result["file_tree"])
