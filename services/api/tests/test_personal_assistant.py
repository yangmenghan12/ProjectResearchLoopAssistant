import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
import httpx
from sqlalchemy import create_engine, func, inspect, select

from app.assistant import mcp_server
from app.tools.gateway import ToolContext, ToolGateway
from app.tools.mcp_adapter import call_bundled_mcp
from app.assistant.models import AssistantMessage, ResearchEvidencePackage
from app.assistant.runtime import validate_answer
from app.db.base import Base
from app.db.models import AnalysisRun, Project, Source, SourceSnapshot, ToolCall
from app.llm.openai_compatible import OpenAICompatibleClient
from app.llm.router import LLMRouter
from app.workers.run_worker import run_one_job
from tests.test_projects_sources_api import build_client_with_session


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "README.md").write_text("# Demo\nA Python application.\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="demo"\n', encoding="utf-8")
    (tmp_path / "main.py").write_text("from helper import answer\nprint(answer())\n", encoding="utf-8")
    (tmp_path / "helper.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=do-not-read", encoding="utf-8")
    return tmp_path


@pytest.fixture
def local_transport(monkeypatch):
    """Exercise actual MCP server handlers in integration flows without subprocess startup per call."""
    async def call(name, args, env):
        monkeypatch.setattr(mcp_server, "ROOT", Path(env["RESEARCH_MCP_ROOT"]))
        if env.get("RESEARCH_MCP_ONLY_FILE"):
            monkeypatch.setenv("RESEARCH_MCP_ONLY_FILE", env["RESEARCH_MCP_ONLY_FILE"])
        else:
            monkeypatch.delenv("RESEARCH_MCP_ONLY_FILE", raising=False)
        return getattr(mcp_server, name)(**args)
    monkeypatch.setattr("app.tools.mcp_adapter.call_bundled_mcp", call)


def fake_model(self, **kwargs):
    if kwargs["task_type"] == "assistant_chat":
        return {"status": "succeeded", "text": "你好，我们可以聊聊项目调研。"}
    if kwargs["task_type"] != "assistant_qa":
        return {"status": "skipped", "text": ""}
    messages = kwargs["messages"]
    tool_results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
    if not tool_results:
        return {"status": "succeeded", "text": "", "tool_calls": [{"id": "read-helper", "type": "function",
            "function": {"name": "read_project_file", "arguments": json.dumps({"path": "helper.py", "start_line": 1, "end_line": 2})}}]}
    item = next((r["evidence"] for r in tool_results if r.get("evidence")), None)
    if not item:
        return {"status": "succeeded", "text": json.dumps({"insufficient_evidence": True})}
    value = 43 if "return 43" in item["content"] else 42
    return {"status": "succeeded", "text": json.dumps({"answer": f"answer 函数返回 {value}。[{item['id']}]",
            "citations": [item["id"]], "insufficient_evidence": False})}


def test_chat_does_not_create_project_or_tool_call(monkeypatch):
    monkeypatch.setattr(LLMRouter, "chat", fake_model)
    client, sessions = build_client_with_session()
    cid = client.post("/assistant/conversations").json()["id"]
    response = client.post(f"/assistant/conversations/{cid}/messages", json={"content": "你好"})
    assert response.status_code == 202
    assert response.json()["mode"] == "chat"
    assert client.get(f"/assistant/conversations/{cid}").json()["project_id"] is None
    with sessions() as session:
        assert not session.scalars(select(Project)).all()
        assert not session.scalars(select(ToolCall)).all()


def test_qa_runs_tools_persists_evidence_and_revalidates_changed_files(repo, local_transport, monkeypatch):
    monkeypatch.setattr(LLMRouter, "chat", fake_model)
    client, sessions = build_client_with_session()
    cid = client.post("/assistant/conversations").json()["id"]
    reply = client.post(f"/assistant/conversations/{cid}/messages", json={
        "source": str(repo), "content": "answer 函数做什么？", "mode": "qa"}).json()
    assert reply["status"] == "queued"
    assert run_one_job() is True
    conversation = client.get(f"/assistant/conversations/{cid}").json()
    answer = conversation["messages"][-1]
    with sessions() as session:
        assert answer["status"] == "completed", [(c.tool_name, c.error_message) for c in session.scalars(select(ToolCall))]
    assert answer["evidence_package_id"]
    package = client.get(f"/assistant/conversations/{cid}/evidence/{answer['evidence_package_id']}").json()
    helper = next(i for i in package["items"] if i["path"] == "helper.py")
    old_hash = helper["file_hash"]
    (repo / "helper.py").write_text("def answer():\n    return 43\n", encoding="utf-8")
    client.post(f"/assistant/conversations/{cid}/messages", json={"content": "重新检查", "mode": "qa"})
    run_one_job()
    second = client.get(f"/assistant/conversations/{cid}").json()["messages"][-1]
    assert "43" in second["content"]
    updated = client.get(f"/assistant/conversations/{cid}/evidence/{second['evidence_package_id']}").json()
    assert updated["parent_id"] == package["id"]
    assert next(i for i in updated["items"] if i["path"] == "helper.py")["file_hash"] != old_hash
    # Old package is immutable and still represents the earlier version.
    assert client.get(f"/assistant/conversations/{cid}/evidence/{package['id']}").json()["items"] == package["items"]
    other = client.post("/assistant/conversations").json()["id"]
    assert client.get(f"/assistant/conversations/{other}/evidence/{package['id']}").status_code == 404
    with sessions() as session:
        assert any(c.tool_name == "mcp.read_project_file" for c in session.scalars(select(ToolCall)))


def test_report_reuses_legacy_pipeline_and_links_the_same_evidence(repo, local_transport, monkeypatch):
    monkeypatch.setattr(LLMRouter, "chat", fake_model)
    client, sessions = build_client_with_session()
    cid = client.post("/assistant/conversations").json()["id"]
    response = client.post(f"/assistant/conversations/{cid}/messages", json={
        "source": str(repo), "content": "生成调研报告", "mode": "report"})
    assert response.status_code == 202
    run_one_job()
    answer = client.get(f"/assistant/conversations/{cid}").json()["messages"][-1]
    with sessions() as session:
        assert answer["status"] == "completed", session.get(AnalysisRun, answer["run_id"]).stop_reason
    assert answer["report_id"]
    assert "本次源码证据包" in answer["content"]
    assert "helper.py:1-2" in answer["content"]


def test_gateway_rejects_cross_project_and_unknown_tools(repo, local_transport):
    client, sessions = build_client_with_session()
    with sessions() as session:
        project = Project(name="A", topic="test")
        other = Project(name="B", topic="test")
        session.add_all([project, other]); session.flush()
        source = Source(project_id=other.id, source_type="local_directory", uri=str(repo), normalized_uri=str(repo))
        run = AnalysisRun(project_id=project.id)
        session.add_all([source, run]); session.commit()
        gateway = ToolGateway(session, ToolContext(project_id=project.id, run_id=run.id, source=source, root=repo))
        with pytest.raises(ValueError, match="does not belong"):
            gateway.invoke("inspect_project", {})
        assert session.scalars(select(ToolCall)).first().status == "failed"
        source.project_id = project.id; session.commit()
        with pytest.raises(ValueError):
            gateway.invoke("shell", {"command": "anything"})
        with pytest.raises(ValueError):
            gateway.invoke("prepare_github", {})
        with pytest.raises(ValueError):
            gateway.invoke("read_project_file", {"path": "main.py", "root": "/"})


def test_mcp_paths_sensitive_files_and_single_file_scope(repo, monkeypatch):
    monkeypatch.setattr(mcp_server, "ROOT", repo)
    monkeypatch.delenv("RESEARCH_MCP_ONLY_FILE", raising=False)
    for path in ["../outside.py", ".env", str(repo / "main.py")]:
        with pytest.raises(ValueError):
            mcp_server.read_project_file(path)
    assert not mcp_server.search_project("SECRET")["matches"]
    monkeypatch.setenv("RESEARCH_MCP_ONLY_FILE", str(repo / "main.py"))
    assert mcp_server.inspect_project()["files"] == ["main.py"]
    with pytest.raises(ValueError):
        mcp_server.read_project_file("helper.py")


def test_invalid_or_missing_citation_is_not_published():
    for payload in [{"answer": "编造答案", "citations": []},
                    {"answer": "错误 [E1234567890123456]", "citations": ["E1234567890123456"]},
                    {"answer": "无需证据", "citations": ["bad-id"]}]:
        answer, citations, status = validate_answer(json.dumps(payload), [])
        assert status == "insufficient"
        assert not citations
        assert "编造答案" not in answer


def test_cancelled_task_allows_followup(repo, local_transport, monkeypatch):
    monkeypatch.setattr(LLMRouter, "chat", fake_model)
    client, _ = build_client_with_session()
    cid = client.post("/assistant/conversations").json()["id"]
    reply = client.post(f"/assistant/conversations/{cid}/messages", json={
        "source": str(repo), "content": "介绍项目", "mode": "qa"}).json()
    project_id = client.get(f"/assistant/conversations/{cid}").json()["project_id"]
    assert client.post(f"/projects/{project_id}/runs/{reply['run_id']}/cancel").status_code == 200
    assert client.post(f"/assistant/conversations/{cid}/messages", json={"content": "再试一次", "mode": "qa"}).status_code == 202


def test_real_stdio_mcp_reads_and_rejects_out_of_scope_paths(repo):
    env = {"RESEARCH_MCP_ROOT": str(repo), "RESEARCH_MCP_ONLY_FILE": str(repo / "helper.py")}
    result = asyncio.run(call_bundled_mcp("read_project_file", {"path": "helper.py", "start_line": 1, "end_line": 2}, env))
    assert result["content"] == "def answer():\n    return 42"
    assert len(result["file_hash"]) == 64
    with pytest.raises(ValueError, match="Only the explicitly attached file"):
        asyncio.run(call_bundled_mcp("read_project_file", {"path": "main.py"}, env))


def test_native_tool_call_payload_and_response_are_preserved():
    calls = [{"id": "call1", "type": "function", "function": {"name": "search_project", "arguments": '{"query":"answer"}'}}]
    tools = [{"type": "function", "function": {"name": "search_project", "parameters": {"type": "object"}}}]
    def respond(request):
        payload = json.loads(request.content)
        assert payload["tools"] == tools
        assert payload["tool_choice"] == "auto"
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": calls}}]})
    client = OpenAICompatibleClient("test", None, "http://test/v1", "test", api_key_required=False,
                                    transport=httpx.MockTransport(respond))
    assert client.chat([{"role": "user", "content": "find answer"}], tools=tools)["tool_calls"] == calls


def test_report_retry_reuses_persisted_evidence_package(repo, local_transport, monkeypatch):
    from app.assistant import runtime
    monkeypatch.setattr(LLMRouter, "chat", fake_model)
    original = runtime.execute_report
    def fail_after_evidence(*args, **kwargs):
        raise RuntimeError("Simulated worker interruption after evidence persistence")
        yield
    monkeypatch.setattr(runtime, "execute_report", fail_after_evidence)
    client, sessions = build_client_with_session()
    cid = client.post("/assistant/conversations").json()["id"]
    queued = client.post(f"/assistant/conversations/{cid}/messages", json={
        "source": str(repo), "content": "生成报告", "mode": "report"}).json()
    run_one_job()
    conversation = client.get(f"/assistant/conversations/{cid}").json()
    failed = conversation["messages"][-1]
    assert failed["status"] == "failed"
    assert failed["evidence_package_id"]
    monkeypatch.setattr(runtime, "execute_report", original)
    response = client.post(f"/projects/{conversation['project_id']}/runs/{queued['run_id']}/retry")
    assert response.status_code == 202
    run_one_job()
    completed = client.get(f"/assistant/conversations/{cid}").json()["messages"][-1]
    assert completed["status"] == "completed"
    assert completed["report_id"]
    assert completed["evidence_package_id"] == failed["evidence_package_id"]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ResearchEvidencePackage)) == 1
        package = session.get(ResearchEvidencePackage, completed["evidence_package_id"])
        assert session.scalar(select(func.count()).select_from(SourceSnapshot)) == len(package.items_json)


def test_missing_model_never_publishes_uncited_project_answer(repo, local_transport, monkeypatch):
    monkeypatch.setattr(LLMRouter, "chat", lambda *args, **kwargs: {"status": "skipped", "text": ""})
    client, _ = build_client_with_session()
    cid = client.post("/assistant/conversations").json()["id"]
    assert client.post(f"/assistant/conversations/{cid}/messages", json={"content": "分析项目", "mode": "qa"}).status_code == 409
    client.post(f"/assistant/conversations/{cid}/messages", json={"source": str(repo), "content": "介绍项目", "mode": "qa"})
    run_one_job()
    messages = client.get(f"/assistant/conversations/{cid}").json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[-1]["status"] == "insufficient"
    assert messages[-1]["evidence_package_id"]
    assert not messages[-1]["citations_json"]


def test_assistant_migration_upgrades_existing_schema_and_reverses(tmp_path):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).parents[1] / "app/db/migrations/versions/20261022_0015_personal_assistant.py"
    spec = importlib.util.spec_from_file_location("assistant_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{(tmp_path / 'migration.sqlite').as_posix()}")
    new_tables = {"assistant_conversations", "assistant_messages", "research_evidence_packages"}
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in new_tables])
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert new_tables <= set(inspect(connection).get_table_names())
            assert "sequence" in {c["name"] for c in inspect(connection).get_columns("assistant_messages")}
            migration.downgrade()
            assert not new_tables.intersection(inspect(connection).get_table_names())
            assert "projects" in inspect(connection).get_table_names()
