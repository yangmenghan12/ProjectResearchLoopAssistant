from pathlib import Path
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from app.tools.git_tools import clone_public_github_repo, ensure_child_path, repo_workspace_path
from app.tools.web_tools import fetch_web_url


def test_fetch_web_url_extracts_title_text_and_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.com/research"
        html = """
        <html>
          <head><title>Research Note</title><style>.hidden{}</style></head>
          <body><h1>Loop Engineering</h1><script>alert(1)</script><p>Useful evidence.</p></body>
        </html>
        """
        return httpx.Response(
            200,
            text=html,
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    result = fetch_web_url(
        "https://example.com/research",
        timeout_seconds=5,
        excerpt_chars=80,
        transport=httpx.MockTransport(handler),
    )

    assert result["status"] == "collected"
    assert result["title"] == "Research Note"
    assert "Loop Engineering" in result["content_excerpt"]
    assert "alert" not in result["content_excerpt"]
    assert result["metadata"]["content_type"].startswith("text/html")
    assert result["metadata"]["final_url"] == "https://example.com/research"


def test_repo_workspace_path_stays_under_workspace_root(tmp_path: Path):
    workspace_root = tmp_path / "workspace"

    target = repo_workspace_path(
        workspace_root=workspace_root,
        project_id="project-1",
        github_url="https://github.com/Owner/Repo.git",
    )

    assert target == workspace_root / "project-1" / "repos" / "owner-repo"
    assert ensure_child_path(target, workspace_root) == target.resolve()


def test_repo_workspace_path_compacts_long_segments(tmp_path: Path):
    workspace_root = tmp_path / "workspace"

    target = repo_workspace_path(
        workspace_root=workspace_root,
        project_id="12345678-1234-1234-1234-123456789abc",
        github_url="https://github.com/VeryLongOwnerName/very-long-repository-name-for-windows-path-tests",
    )

    assert target.parent.name == "repos"
    assert len(target.parts[-3]) <= 16
    assert len(target.name) <= 48
    assert target.name.startswith("verylongownername")


def test_ensure_child_path_rejects_workspace_escape(tmp_path: Path):
    with pytest.raises(ValueError):
        ensure_child_path(tmp_path.parent, tmp_path)


def test_clone_public_github_repo_records_branch_and_commit(tmp_path: Path):
    calls: list[list[str]] = []

    def fake_runner(args, **kwargs):
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        calls.append(list(args))
        if args[:2] == ["git", "clone"]:
            return SimpleNamespace(stdout="", stderr="")
        if args == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return SimpleNamespace(stdout="main\n", stderr="")
        if args == ["git", "rev-parse", "HEAD"]:
            return SimpleNamespace(stdout="abc123\n", stderr="")
        raise AssertionError(f"unexpected command: {args}")

    result = clone_public_github_repo(
        github_url="https://github.com/acme/demo",
        workspace_root=tmp_path / "workspace",
        project_id="project-1",
        timeout_seconds=10,
        runner=fake_runner,
    )

    assert calls[0][:4] == ["git", "clone", "--depth", "1"]
    assert calls[0][4] == "https://github.com/acme/demo"
    assert result["status"] == "collected"
    assert result["metadata"]["branch"] == "main"
    assert result["metadata"]["commit"] == "abc123"
    assert Path(result["metadata"]["local_path"]).is_absolute()


def test_clone_public_github_repo_reports_clone_stderr(tmp_path: Path):
    def fake_runner(args, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=128,
            cmd=args,
            output="",
            stderr="fatal: unable to access repository: 网络错误",
        )

    with pytest.raises(RuntimeError) as exc_info:
        clone_public_github_repo(
            github_url="https://github.com/acme/demo",
            workspace_root=tmp_path / "workspace",
            project_id="project-1",
            timeout_seconds=10,
            runner=fake_runner,
        )

    message = str(exc_info.value)
    assert "git clone failed with exit code 128" in message
    assert "网络错误" in message
