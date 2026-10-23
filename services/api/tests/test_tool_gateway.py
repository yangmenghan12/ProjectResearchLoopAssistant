from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AnalysisRun, Project, Source, ToolCall
from app.tools.gateway import (
    DEFAULT_READONLY_TOOL_REGISTRY,
    TOOL_REGISTRY,
    ToolContext,
    ToolDefinition,
    ToolGateway,
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
    assert names == set(TOOL_REGISTRY)
    assert {t["name"] for t in tools if t["model_visible"]} == {
        "mcp.inspect_project", "mcp.read_project_file", "mcp.search_project"}
    assert next(t for t in tools if t["name"] == "mcp.prepare_github")["host_only"] is True
    assert next(t for t in tools if t["name"] == "source.collect")["transport"] == "local"


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


def attach_directory(session, project, path):
    source = Source(project_id=project.id, source_type="local_directory", uri=str(path), normalized_uri=str(path))
    session.add(source)
    session.commit()
    return source


def test_local_and_mcp_calls_share_a_run_budget_and_audit_path(tmp_path, monkeypatch):
    attempts = []
    async def transport(name, args, env):
        attempts.append(name)
        return {"files": ["README.md"]}
    monkeypatch.setattr("app.tools.mcp_adapter.call_bundled_mcp", transport)
    (tmp_path / "README.md").write_text("# Shared gateway", encoding="utf-8")
    with create_test_session() as session:
        project, run = create_project_and_run(session)
        source = attach_directory(session, project, tmp_path)
        context = ToolContext(project_id=project.id, run_id=run.id, source=source, root=tmp_path)
        # The existing read-only API is just a compatibility entry to ToolGateway.
        invoke_readonly_tool(session, tool_name="local_filesystem_scan", input_json={"path": str(tmp_path)},
                             project_id=project.id, run_id=run.id)
        gateway = ToolGateway(session, context, max_calls=2)
        assert gateway.invoke("inspect_project", {})["files"] == ["README.md"]
        recovered = ToolGateway(session, context, max_calls=2)
        with pytest.raises(ToolGatewayError, match="budget"):
            recovered.invoke("mcp.inspect_project", {})
        calls = list_tool_calls(session, run.id)
        assert len(calls) == 3  # Exactly one record for each attempt, including denial.
        assert {c.tool_name for c in calls} == {"local_filesystem_scan", "mcp.inspect_project"}
        assert sorted(c.status for c in calls) == ["failed", "succeeded", "succeeded"]
        assert attempts == ["inspect_project"]


@pytest.mark.parametrize("name,args", [
    ("local_filesystem_scan", {"path": "example", "unexpected": True}),
    ("read_project_file", {"path": "main.py", "start_line": "1"}),
])
def test_common_gateway_validates_parameters_before_either_adapter(name, args, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid arguments reached an execution adapter")
    monkeypatch.setattr("app.tools.mcp_adapter.execute_mcp", forbidden)
    with create_test_session() as session:
        project, run = create_project_and_run(session)
        source = attach_directory(session, project, tmp_path)
        gateway = ToolGateway(session, ToolContext(project_id=project.id, run_id=run.id, source=source, root=tmp_path))
        with pytest.raises(ToolGatewayError):
            gateway.invoke(name, args)
        assert list_tool_calls(session, run.id)[0].status == "failed"


@pytest.mark.parametrize("name", ["local_filesystem_scan", "mcp.inspect_project"])
def test_disabled_tools_are_hidden_or_rejected_by_same_registry(name, tmp_path):
    registry = {**TOOL_REGISTRY, name: replace(TOOL_REGISTRY[name], enabled=False)}
    with create_test_session() as session:
        project, run = create_project_and_run(session)
        source = attach_directory(session, project, tmp_path)
        gateway = ToolGateway(session, ToolContext(project_id=project.id, run_id=run.id, source=source, root=tmp_path),
                              registry=registry)
        assert not any(t["function"]["name"] == registry[name].model_name for t in gateway.schemas())
        with pytest.raises(ToolGatewayError, match="disabled"):
            gateway.invoke(name, {})
        assert list_tool_calls(session, run.id)[0].status == "failed"


def test_model_cannot_invoke_host_tools_even_with_elevated_host_context(tmp_path, monkeypatch):
    async def forbidden(*args):
        pytest.fail("The model reached a host-only tool")
    monkeypatch.setattr("app.tools.mcp_adapter.call_bundled_mcp", forbidden)
    with create_test_session() as session:
        project, run = create_project_and_run(session)
        source = Source(project_id=project.id, source_type="github_repo",
            uri="https://github.com/example/repo", normalized_uri="https://github.com/example/repo")
        session.add(source); session.commit()
        gateway = ToolGateway(session, ToolContext(project_id=project.id, run_id=run.id, source=source, root=tmp_path,
            allowed_permission_levels=frozenset({"L0", "L1"})))
        assert {t["function"]["name"] for t in gateway.schemas()} == {
            "inspect_project", "read_project_file", "search_project"}
        for name in ["mcp.prepare_github", "source.collect", "local_filesystem_scan"]:
            with pytest.raises(ToolGatewayError, match="not available to the model"):
                gateway.invoke(name, {}, caller="model")
        assert len(list_tool_calls(session, run.id)) == 3


def test_legacy_path_tools_cannot_escape_bound_source(tmp_path):
    with create_test_session() as session:
        project, run = create_project_and_run(session)
        source = attach_directory(session, project, tmp_path)
        gateway = ToolGateway(session, ToolContext(project_id=project.id, run_id=run.id, source=source, root=tmp_path))
        with pytest.raises(ToolGatewayError, match="escapes"):
            gateway.invoke("local_filesystem_scan", {"path": str(tmp_path.parent)})
        assert list_tool_calls(session, run.id)[0].status == "failed"


def test_task_allowlist_applies_to_canonical_names_and_model_aliases(tmp_path):
    with create_test_session() as session:
        project, run = create_project_and_run(session)
        source = attach_directory(session, project, tmp_path)
        gateway = ToolGateway(session, ToolContext(project_id=project.id, run_id=run.id, source=source, root=tmp_path,
            allowed_tools=frozenset({"mcp.inspect_project"})))
        assert [t["function"]["name"] for t in gateway.schemas()] == ["inspect_project"]
        for name in ["read_project_file", "mcp.read_project_file"]:
            with pytest.raises(ToolGatewayError, match="allowlist"):
                gateway.invoke(name, {"path": "main.py"}, caller="model")


def test_workbench_collection_uses_the_gateway_and_records_failure(tmp_path):
    from tests.test_collection_api import build_client as collection_client, create_project
    client, sessions = collection_client()
    project = create_project(client)
    file = tmp_path / "source.md"
    file.write_text("# Source", encoding="utf-8")
    client.post(f"/projects/{project['id']}/sources", json={"source_type": "local_file", "uri": str(file)})
    assert client.post(f"/projects/{project['id']}/collect").status_code == 200
    file.unlink()
    response = client.post(f"/projects/{project['id']}/collect")
    assert response.status_code == 200
    assert any(s["status"] == "failed" for s in response.json()["snapshots"])
    with sessions() as session:
        calls = list(session.scalars(select(ToolCall).where(ToolCall.project_id == project["id"])))
        assert len(calls) == 2
        assert {c.tool_name for c in calls} == {"source.collect"}
        assert sorted(c.status for c in calls) == ["failed", "succeeded"]
