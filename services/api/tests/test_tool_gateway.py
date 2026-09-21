from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AnalysisRun, Project, ToolCall
from app.tools.gateway import (
    DEFAULT_READONLY_TOOL_REGISTRY,
    ToolDefinition,
    ToolGatewayError,
    invoke_readonly_tool,
)
from tests.test_runs_api import build_client


@contextmanager
def create_test_session() -> Iterator[Session]:
    client = build_client()
    override_session = next(iter(client.app.dependency_overrides.values()))
    session_iter = override_session()
    try:
        yield next(session_iter)
    finally:
        session_iter.close()


def create_project_and_run(session: Session) -> tuple[Project, AnalysisRun]:
    project = Project(name="Tool gateway", topic="Read-only tools", analysis_goal="Audit tool usage")
    session.add(project)
    session.flush()
    run = AnalysisRun(project_id=project.id, status="running", thread_id=f"run:{project.id}")
    session.add(run)
    session.commit()
    return project, run


def list_tool_calls(session: Session, run_id: str) -> list[ToolCall]:
    return list(
        session.scalars(
            select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.created_at.asc(), ToolCall.id.asc())
        ).all()
    )


def test_default_registry_only_contains_l0_readonly_tools():
    assert {"local_filesystem_scan", "git_metadata_read", "web_fetch_read"} <= set(DEFAULT_READONLY_TOOL_REGISTRY)
    assert DEFAULT_READONLY_TOOL_REGISTRY
    assert all(tool.permission_level == "L0" for tool in DEFAULT_READONLY_TOOL_REGISTRY.values())
    assert all(tool.enabled for tool in DEFAULT_READONLY_TOOL_REGISTRY.values())
    assert all("只读" in tool.description for tool in DEFAULT_READONLY_TOOL_REGISTRY.values())


def test_unknown_tool_fails_and_writes_failed_ledger(tmp_path: Path):
    with create_test_session() as session:
        project, run = create_project_and_run(session)

        with pytest.raises(ToolGatewayError, match="not registered"):
            invoke_readonly_tool(
                session,
                tool_name="shell_write",
                input_json={"path": str(tmp_path)},
                project_id=project.id,
                run_id=run.id,
            )

        calls = list_tool_calls(session, run.id)
        assert len(calls) == 1
        assert calls[0].tool_name == "shell_write"
        assert calls[0].permission_level == "unknown"
        assert calls[0].status == "failed"
        assert "not registered" in (calls[0].error_message or "")
        assert calls[0].input_summary_json["path"] == str(tmp_path)


def test_non_l0_registry_item_is_rejected_and_ledgered(tmp_path: Path):
    def forbidden_executor(input_json: dict) -> dict:
        return {"status": "should_not_run", "input": input_json}

    registry = {
        **DEFAULT_READONLY_TOOL_REGISTRY,
        "future_write_tool": ToolDefinition(
            id="future_write_tool",
            name="future_write_tool",
            description="未来写工具，不能被只读网关放行",
            permission_level="L1",
            input_schema_json={"type": "object"},
            enabled=True,
            executor=forbidden_executor,
        ),
    }

    with create_test_session() as session:
        project, run = create_project_and_run(session)

        with pytest.raises(ToolGatewayError, match="Only L0"):
            invoke_readonly_tool(
                session,
                tool_name="future_write_tool",
                input_json={"path": str(tmp_path)},
                project_id=project.id,
                run_id=run.id,
                registry=registry,
            )

        calls = list_tool_calls(session, run.id)
        assert len(calls) == 1
        assert calls[0].tool_name == "future_write_tool"
        assert calls[0].permission_level == "L1"
        assert calls[0].status == "failed"
        assert calls[0].output_summary_json == {}


def test_successful_local_scan_writes_succeeded_tool_call(tmp_path: Path):
    (tmp_path / "README.md").write_text("# Gateway", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('hello')", encoding="utf-8")

    with create_test_session() as session:
        project, run = create_project_and_run(session)

        result = invoke_readonly_tool(
            session,
            tool_name="local_filesystem_scan",
            input_json={"path": str(tmp_path)},
            project_id=project.id,
            run_id=run.id,
        )

        assert result["status"] == "collected"
        calls = list_tool_calls(session, run.id)
        assert len(calls) == 1
        assert calls[0].status == "succeeded"
        assert calls[0].permission_level == "L0"
        assert calls[0].latency_ms is not None
        assert calls[0].input_summary_json["path"] == str(tmp_path)
        assert calls[0].output_summary_json["status"] == "collected"
        assert calls[0].output_summary_json["metadata"]["total_files"] == 2
        assert len(calls[0].output_summary_json["file_tree"]) <= 80


def test_tools_api_returns_readonly_registry():
    client = build_client()

    response = client.get("/tools")

    assert response.status_code == 200
    tools = response.json()
    names = {tool["name"] for tool in tools}
    assert {"local_filesystem_scan", "git_metadata_read", "web_fetch_read"} <= names
    filesystem_tool = next(tool for tool in tools if tool["name"] == "local_filesystem_scan")
    assert filesystem_tool["permission_level"] == "L0"
    assert filesystem_tool["enabled"] is True
    assert "只读" in filesystem_tool["description"]
    assert filesystem_tool["input_schema_json"]["type"] == "object"


def test_tool_calls_api_returns_only_requested_run():
    client = build_client()
    override_session = next(iter(client.app.dependency_overrides.values()))
    session_iter = override_session()
    session = next(session_iter)
    try:
        project = Project(name="Tool API", topic="Ledger", analysis_goal="List calls")
        session.add(project)
        session.flush()
        first_run = AnalysisRun(project_id=project.id, status="completed")
        second_run = AnalysisRun(project_id=project.id, status="completed")
        session.add_all([first_run, second_run])
        session.flush()
        session.add_all(
            [
                ToolCall(
                    project_id=project.id,
                    run_id=first_run.id,
                    tool_name="local_filesystem_scan",
                    permission_level="L0",
                    status="succeeded",
                    input_summary_json={"path": "first"},
                    output_summary_json={"status": "collected"},
                ),
                ToolCall(
                    project_id=project.id,
                    run_id=second_run.id,
                    tool_name="git_metadata_read",
                    permission_level="L0",
                    status="succeeded",
                    input_summary_json={"path": "second"},
                    output_summary_json={"status": "collected"},
                ),
            ]
        )
        session.commit()

        response = client.get(f"/projects/{project.id}/runs/{first_run.id}/tool-calls")

        assert response.status_code == 200
        calls = response.json()
        assert [call["run_id"] for call in calls] == [first_run.id]
        assert calls[0]["tool_name"] == "local_filesystem_scan"

        wrong_project_response = client.get(f"/projects/missing/runs/{first_run.id}/tool-calls")
        assert wrong_project_response.status_code == 404
    finally:
        session_iter.close()
