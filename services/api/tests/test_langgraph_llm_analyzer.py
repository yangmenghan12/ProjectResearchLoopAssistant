import json
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import LLMAnalysisArtifact
from app.db.session import get_session
from app.engine import langgraph_runner
from tests.test_runs_api import build_client, create_collected_project, enqueue_and_process_run


@contextmanager
def client_db_session(client: TestClient) -> Generator[Session, None, None]:
    override_session = client.app.dependency_overrides[get_session]
    session_generator = override_session()
    session = next(session_generator)
    try:
        yield session
    finally:
        session_generator.close()


def llm_artifacts_for_run(client: TestClient, run_id: str) -> list[dict[str, Any]]:
    with client_db_session(client) as session:
        artifacts = list(
            session.scalars(
                select(LLMAnalysisArtifact)
                .where(LLMAnalysisArtifact.run_id == run_id)
                .order_by(LLMAnalysisArtifact.created_at.asc(), LLMAnalysisArtifact.id.asc())
            ).all()
        )
        return [
            {
                "accepted": bool(artifact.accepted),
                "artifact_type": artifact.artifact_type,
                "call_id": artifact.call_id,
                "content_json": artifact.content_json,
                "validation_result_json": artifact.validation_result_json,
            }
            for artifact in artifacts
        ]


def valid_fake_analyzer_payload() -> dict[str, Any]:
    return {
        "evidence": [
            {
                "client_ref": "llm:evidence:project-summary",
                "title": "LLM 补充分析",
                "summary": "基于已有证据补充项目结构判断，没有引入外部事实。",
                "evidence_type": "llm_analysis",
                "confidence": 88,
                "metadata": {"basis": "deterministic_analysis"},
            }
        ],
        "findings": [
            {
                "title": "LLM 增强发现",
                "summary": "已有采集结果可以支撑一次轻量级人工复核。",
                "category": "llm_analysis",
                "impact_level": "medium",
                "confidence": 82,
                "evidence_refs": ["llm:evidence:project-summary"],
                "metadata": {"basis": "existing_evidence_refs"},
            }
        ],
        "risks": [],
        "questions": [],
        "recommendations": [
            {
                "summary": "复核 LLM 增强发现。",
                "rationale": "该建议只基于已采集证据和确定性分析结果。",
                "confidence": 80,
                "next_steps": ["人工复核新增分析证据。"],
                "metadata": {"basis": "llm_analyzer"},
            }
        ],
        "reasoning_summary": "仅基于已有证据做结构化补充。",
        "coverage_notes": ["未访问外部资料。"],
    }


def test_llm_analyzer_node_falls_back_when_analyzer_profile_unconfigured(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.llm.router.get_settings",
        lambda: Settings(llm_base_url="", llm_model=""),
    )
    client = build_client()
    project = create_collected_project(client, tmp_path)

    package = enqueue_and_process_run(client, project["id"])

    assert package["run"]["status"] == "ready_for_review_with_risks"
    llm_turn = next(turn for turn in package["turns"] if turn["node_name"] == "llm_analyze_sources")
    assert llm_turn["status"] == "completed"
    assert llm_turn["metadata_json"]["llm_status"] == "skipped"
    assert "回退" in llm_turn["observation"]
    calls_response = client.get(f"/llm/calls?run_id={package['run']['id']}")
    assert calls_response.status_code == 200
    calls = calls_response.json()
    calls_by_task = {call["task_type"]: call for call in calls}
    assert set(calls_by_task) == {"analyzer", "verifier"}
    assert calls_by_task["analyzer"]["status"] == "skipped"
    assert calls_by_task["verifier"]["status"] == "skipped"
    assert llm_artifacts_for_run(client, package["run"]["id"]) == []


def test_llm_analyzer_accepts_valid_fake_output(tmp_path, monkeypatch):
    class FakeLLMRouter:
        def __init__(self, session: Session) -> None:
            self.session = session

        def chat(self, **kwargs: Any) -> dict[str, Any]:
            assert kwargs["task_type"] == "analyzer"
            assert kwargs["report_id"] is None
            assert kwargs["input_refs"] == [
                f"project:{kwargs['project_id']}",
                f"run:{kwargs['run_id']}",
            ]
            return {
                "status": "succeeded",
                "call_id": "fake-analyzer-call",
                "text": json.dumps(valid_fake_analyzer_payload(), ensure_ascii=False),
                "usage": {},
            }

    monkeypatch.setattr(langgraph_runner, "LLMRouter", FakeLLMRouter, raising=False)
    client = build_client()
    project = create_collected_project(client, tmp_path)

    package = enqueue_and_process_run(client, project["id"])

    assert package["run"]["status"] == "ready_for_review_with_risks"
    assert any(item["evidence_type"] == "llm_analysis" for item in package["evidence_items"])
    llm_turn = next(turn for turn in package["turns"] if turn["node_name"] == "llm_analyze_sources")
    assert llm_turn["metadata_json"]["accepted"] is True
    assert llm_turn["metadata_json"]["call_id"] == "fake-analyzer-call"
    artifacts = llm_artifacts_for_run(client, package["run"]["id"])
    assert len(artifacts) == 1
    assert artifacts[0]["accepted"] is True
    assert artifacts[0]["artifact_type"] == "analyzer_output"
    assert artifacts[0]["call_id"] is None
    assert artifacts[0]["content_json"]["evidence"][0]["evidence_type"] == "llm_analysis"
    assert artifacts[0]["validation_result_json"]["passed"] is True


def test_llm_analyzer_rejects_malformed_fake_output(tmp_path, monkeypatch):
    class FakeLLMRouter:
        def __init__(self, session: Session) -> None:
            self.session = session

        def chat(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "status": "succeeded",
                "call_id": "fake-malformed-call",
                "text": "不是 JSON",
                "usage": {},
            }

    monkeypatch.setattr(langgraph_runner, "LLMRouter", FakeLLMRouter, raising=False)
    client = build_client()
    project = create_collected_project(client, tmp_path)

    package = enqueue_and_process_run(client, project["id"])

    assert package["run"]["status"] == "ready_for_review_with_risks"
    assert not any(item["evidence_type"] == "llm_analysis" for item in package["evidence_items"])
    llm_turn = next(turn for turn in package["turns"] if turn["node_name"] == "llm_analyze_sources")
    assert llm_turn["metadata_json"]["accepted"] is False
    assert "回退" in llm_turn["observation"]
    artifacts = llm_artifacts_for_run(client, package["run"]["id"])
    assert len(artifacts) == 1
    assert artifacts[0]["accepted"] is False
    assert artifacts[0]["call_id"] is None
    assert artifacts[0]["validation_result_json"]["passed"] is False
    assert any("JSON object" in message for message in artifacts[0]["validation_result_json"]["messages"])
