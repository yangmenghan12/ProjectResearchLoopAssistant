from pathlib import Path

import pytest

from app.core.security import SourceValidationError, validate_source_input


def test_validate_web_url_accepts_http_and_https():
    result = validate_source_input("web_url", "https://example.com/research")

    assert result.normalized_uri == "https://example.com/research"
    assert result.metadata["scheme"] == "https"


def test_validate_github_repo_accepts_public_repo_url():
    result = validate_source_input("github_repo", "https://github.com/langchain-ai/langgraph")

    assert result.normalized_uri == "https://github.com/langchain-ai/langgraph"
    assert result.metadata["host"] == "github.com"


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("https://github.com/langchain-ai/langgraph.git", "https://github.com/langchain-ai/langgraph"),
        ("https://GitHub.com/langchain-ai/LangGraph.GIT", "https://github.com/langchain-ai/LangGraph"),
    ],
)
def test_validate_github_repo_strips_git_suffix_case_insensitively(uri: str, expected: str):
    result = validate_source_input("github_repo", uri)

    assert result.normalized_uri == expected
    assert not result.metadata["repo"].lower().endswith(".git")


@pytest.mark.parametrize(
    "uri",
    [
        "https://github.com/langchain-ai/langgraph/issues/1",
        "https://github.com/langchain-ai/langgraph/tree/main",
        "https://github.com/langchain-ai/langgraph/pull/1",
    ],
)
def test_validate_github_repo_rejects_extra_path_segments(uri: str):
    with pytest.raises(SourceValidationError):
        validate_source_input("github_repo", uri)


@pytest.mark.parametrize(
    "uri",
    [
        "https://github.com/langchain-ai/langgraph?tab=readme",
        "https://github.com/langchain-ai/langgraph#readme",
    ],
)
def test_validate_github_repo_rejects_query_and_fragment(uri: str):
    with pytest.raises(SourceValidationError):
        validate_source_input("github_repo", uri)


def test_validate_github_repo_rejects_userinfo():
    with pytest.raises(SourceValidationError):
        validate_source_input("github_repo", "https://token@github.com/langchain-ai/langgraph")


def test_validate_local_file_returns_resolved_path(tmp_path: Path):
    file_path = tmp_path / "notes.md"
    file_path.write_text("# Notes", encoding="utf-8")

    result = validate_source_input("local_file", str(file_path))

    assert Path(result.normalized_uri).is_absolute()
    assert result.metadata["kind"] == "file"


def test_validate_local_directory_returns_resolved_path(tmp_path: Path):
    result = validate_source_input("local_directory", str(tmp_path))

    assert Path(result.normalized_uri).is_absolute()
    assert result.metadata["kind"] == "directory"


def test_validate_web_url_rejects_ftp():
    with pytest.raises(SourceValidationError):
        validate_source_input("web_url", "ftp://example.com/research")


def test_validate_web_url_rejects_userinfo():
    with pytest.raises(SourceValidationError):
        validate_source_input("web_url", "https://user@example.com/research")


def test_validate_web_url_with_port_stores_host_and_port_separately():
    result = validate_source_input("web_url", "https://example.com:8443/research")

    assert result.metadata["host"] == "example.com"
    assert result.metadata["port"] == "8443"


def test_validate_source_rejects_missing_local_path(tmp_path: Path):
    missing_path = tmp_path / "missing"

    with pytest.raises(SourceValidationError) as exc:
        validate_source_input("local_file", str(missing_path))

    assert "does not exist" in str(exc.value)


def test_validate_local_file_rejects_directory_path(tmp_path: Path):
    with pytest.raises(SourceValidationError) as exc:
        validate_source_input("local_file", str(tmp_path))

    assert "Expected a local file" in str(exc.value)


def test_validate_local_directory_rejects_file_path(tmp_path: Path):
    file_path = tmp_path / "notes.md"
    file_path.write_text("# Notes", encoding="utf-8")

    with pytest.raises(SourceValidationError) as exc:
        validate_source_input("local_directory", str(file_path))

    assert "Expected a local directory" in str(exc.value)


def test_validate_source_rejects_unsupported_type():
    with pytest.raises(SourceValidationError) as exc:
        validate_source_input("calendar", "https://example.com")

    assert "Unsupported source type" in str(exc.value)
