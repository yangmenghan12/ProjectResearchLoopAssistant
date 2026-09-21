from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.engine import langgraph_runner
from app.verifiers.llm_assisted import merge_llm_verifier_result, validate_llm_verifier_output
from app.verifiers.package import verify_analysis_package
from tests.test_package_verifier import complete_inventory, complete_package
from tests.test_runs_api import build_client, create_collected_project, enqueue_and_process_run


def test_merge_llm_verifier_result_cannot_override_deterministic_blocking():
    deterministic = {
        "passed": False,
        "blocking_reasons": ["缺少证据"],
        "warning_reasons": [],
    }
    llm_result = {
        "status": "succeeded",
        "passed": True,
        "confidence": 0.9,
        "issues": [],
        "suggested_next_actions": [],
        "summary": "LLM 认为可以通过。",
    }

    merged = merge_llm_verifier_result(deterministic, llm_result)

    assert merged["passed"] is False
    assert merged["blocking_reasons"] == ["缺少证据"]
    assert merged["llm_assisted"] == llm_result


def test_validate_llm_verifier_output_normalizes_safe_shape():
    result = validate_llm_verifier_output(
        {
            "passed": True,
            "confidence": 0.72,
            "issues": [
                {"severity": "error", "message": "  证据引用缺失  ", "details": {"ignored": True}},
                {"severity": "unknown", "message": "需要人工复核"},
                {"severity": "warning", "message": ""},
                "not an issue",
            ],
            "suggested_next_actions": ["补充证据", 42, True, {"ignored": True}, None],
            "summary": "  结构完整，但证据仍需抽样复核。  ",
            "unexpected_nested": {"must": "be ignored"},
        }
    )

    assert result == {
        "passed": True,
        "confidence": 0.72,
        "issues": [
            {"severity": "error", "message": "证据引用缺失"},
            {"severity": "warning", "message": "需要人工复核"},
        ],
        "suggested_next_actions": ["补充证据", "42", "true"],
        "summary": "结构完整，但证据仍需抽样复核。",
    }


def test_validate_llm_verifier_output_warns_when_issues_is_not_list():
    result = validate_llm_verifier_output({"issues": {"severity": "warning", "message": "bad"}})

    assert result["passed"] is False
    assert result["issues"][0]["severity"] == "warning"
    assert "issues" in result["issues"][0]["message"]
    assert "list" in result["issues"][0]["message"]


def test_validate_llm_verifier_output_clamps_confidence_to_zero_one():
    assert validate_llm_verifier_output({"confidence": 1.5})["confidence"] == 1.0
    assert validate_llm_verifier_output({"confidence": -0.1})["confidence"] == 0.0
    assert validate_llm_verifier_output({"confidence": "0.9"})["confidence"] == 0.0


def test_merge_llm_verifier_result_keeps_llm_issues_advisory_only():
    deterministic = {
        "passed": True,
        "risk_level": "low",
        "summary": "报告已通过确定性校验，暂无风险提示。",
        "blocking_reasons": [],
        "warning_reasons": ["已有提醒"],
    }
    llm_result = {
        "status": "succeeded",
        "passed": False,
        "confidence": 0.6,
        "issues": [
            {"severity": "info", "message": "普通说明"},
            {"severity": "warning", "message": "覆盖率偏低"},
            {"severity": "error", "message": "缺少人工复核"},
        ],
        "summary": "需要复核。",
    }

    merged = merge_llm_verifier_result(deterministic, llm_result)

    assert merged["passed"] is True
    assert merged["risk_level"] == "low"
    assert merged["summary"] == "报告已通过确定性校验，暂无风险提示。"
    assert merged["blocking_reasons"] == []
    assert merged["warning_reasons"] == ["已有提醒"]
    assert merged["llm_assisted"]["issues"] == llm_result["issues"]


def test_report_verifier_keeps_deterministic_blocking_even_when_llm_passes():
    package = complete_package()
    package["findings"][0].evidence_refs_json = []
    package["turns"].append(
        type(
            "Turn",
            (),
            {
                "turn_index": 3,
                "node_name": "llm_verify_package",
                "action": "智能复核分析包",
                "observation": "LLM 复核完成",
                "metadata_json": {
                    "llm_assisted": {
                        "status": "succeeded",
                        "passed": True,
                        "confidence": 0.95,
                        "issues": [],
                        "suggested_next_actions": [],
                        "summary": "LLM 认为可以通过。",
                    }
                },
            },
        )()
    )

    result = verify_analysis_package(package, complete_inventory())

    assert result["passed"] is False
    assert result["blocking_reasons"]
    assert result["llm_assisted"]["status"] == "succeeded"
    assert result["llm_assisted"]["passed"] is True


def test_llm_verify_graph_node_records_turn_and_report_exposes_result(tmp_path, monkeypatch):
    class FakeLLMRouter:
        def __init__(self, session: Session) -> None:
            self.session = session

        def chat(self, **kwargs: Any) -> dict[str, Any]:
            if kwargs["task_type"] == "analyzer":
                return {
                    "status": "skipped",
                    "call_id": "fake-analyzer-call",
                    "text": "",
                    "usage": {},
                    "error_message": "not configured",
                }

            assert kwargs["task_type"] == "verifier"
            assert kwargs["report_id"] is None
            assert kwargs["input_refs"] == [f"run:{kwargs['run_id']}", "analysis_package"]
            return {
                "status": "succeeded",
                "call_id": "fake-verifier-call",
                "text": json.dumps(
                    {
                        "passed": True,
                        "confidence": 0.83,
                        "issues": [{"severity": "warning", "message": "建议抽样复核证据。"}],
                        "suggested_next_actions": ["人工复核 LLM 提醒"],
                        "summary": "LLM 复核认为分析包结构完整。",
                    },
                    ensure_ascii=False,
                ),
                "usage": {},
            }

    monkeypatch.setattr(langgraph_runner, "LLMRouter", FakeLLMRouter, raising=False)
    client = build_client()
    project = create_collected_project(client, tmp_path)

    package = enqueue_and_process_run(client, project["id"])

    llm_turn = next(turn for turn in package["turns"] if turn["node_name"] == "llm_verify_package")
    assert llm_turn["action"] == "智能复核分析包"
    assert llm_turn["metadata_json"]["call_id"] == "fake-verifier-call"
    assert llm_turn["metadata_json"]["llm_assisted"]["status"] == "succeeded"
    assert llm_turn["metadata_json"]["llm_assisted"]["summary"] == "LLM 复核认为分析包结构完整。"

    report = client.post(f"/projects/{project['id']}/reports").json()

    assert report["verifier_result_json"]["llm_assisted"]["status"] == "succeeded"
    assert report["verifier_result_json"]["llm_assisted"]["summary"] == "LLM 复核认为分析包结构完整。"


def test_llm_verify_graph_node_records_validation_failed_for_bad_json(tmp_path, monkeypatch):
    class FakeLLMRouter:
        def __init__(self, session: Session) -> None:
            self.session = session

        def chat(self, **kwargs: Any) -> dict[str, Any]:
            if kwargs["task_type"] == "analyzer":
                return {
                    "status": "skipped",
                    "call_id": "fake-analyzer-call",
                    "text": "",
                    "usage": {},
                    "error_message": "not configured",
                }

            assert kwargs["task_type"] == "verifier"
            return {
                "status": "succeeded",
                "call_id": "fake-verifier-call",
                "text": "not-json",
                "usage": {},
            }

    monkeypatch.setattr(langgraph_runner, "LLMRouter", FakeLLMRouter, raising=False)
    client = build_client()
    project = create_collected_project(client, tmp_path)

    package = enqueue_and_process_run(client, project["id"])

    node_names = [turn["node_name"] for turn in package["turns"]]
    assert "decide_next_state" in node_names
    assert "persist_state" in node_names
    assert package["run"]["status"] in {"ready_for_review", "ready_for_review_with_risks"}

    llm_turn = next(turn for turn in package["turns"] if turn["node_name"] == "llm_verify_package")
    llm_assisted = llm_turn["metadata_json"]["llm_assisted"]
    assert llm_assisted["status"] == "validation_failed"
    assert llm_assisted["issues"][0]["severity"] == "warning"
    assert "validation failed" in llm_assisted["issues"][0]["message"]
