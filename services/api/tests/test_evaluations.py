from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy.orm import Session

import app.evaluations.runner as evaluation_runner
from app.evaluations.cases import EvaluationCase, evaluation_cases
from app.evaluations.runner import run_evaluations
from tests.test_runs_api import build_client


@contextmanager
def create_test_session() -> Iterator[Session]:
    client = build_client()
    override_session = next(iter(client.app.dependency_overrides.values()))
    session_iter = override_session()
    session = next(session_iter)
    try:
        yield session
    finally:
        session_iter.close()


def test_evaluation_cases_keep_deterministic_order():
    cases = evaluation_cases()
    assert [case.case_id for case in cases] == [
        "healthy-small",
        "missing-docs",
        "sensitive-skip",
        "profile-quick-onboarding",
        "profile-architecture-understanding",
        "profile-extension-evaluation",
        "profile-teaching-breakdown",
        "profile-delivery-acceptance",
    ]
    assert [case.case_type for case in cases[:3]] == ["scan_regression"] * 3
    profile_cases = cases[3:]
    assert [case.case_type for case in profile_cases] == ["profile_benchmark"] * 5
    assert [case.research_profile for case in profile_cases] == [
        "quick_onboarding",
        "architecture_understanding",
        "extension_evaluation",
        "teaching_breakdown",
        "delivery_acceptance",
    ]
    assert all(case.expected_profile_checks for case in profile_cases)
    assert all(case.expected_report_sections for case in profile_cases)


def test_run_evaluations_persists_summary_and_sensitive_skip(tmp_path: Path):
    with create_test_session() as session:
        run = run_evaluations(session, workspace_root=tmp_path)

        assert run.status == "succeeded"
        assert run.completed_at is not None
        assert run.summary_json["total"] == 8
        assert run.summary_json["passed"] == 8
        assert run.summary_json["failed"] == 0
        assert run.summary_json["by_type"] == {
            "scan_regression": {"total": 3, "passed": 3, "failed": 0},
            "profile_benchmark": {"total": 5, "passed": 5, "failed": 0},
        }
        assert set(run.summary_json["profiles"]) == {
            "quick_onboarding",
            "architecture_understanding",
            "extension_evaluation",
            "teaching_breakdown",
            "delivery_acceptance",
        }
        assert len(run.case_results_json) == 8
        for result in run.case_results_json:
            check_names = {check["name"] for check in result["checks"]}
            if result["case_type"] == "scan_regression":
                assert "profile_aware_report_sections" in check_names
            if result["case_type"] == "profile_benchmark":
                assert result["research_profile"]["key"] in run.summary_json["profiles"]
                assert result["quality_summary"]["overall_score"] >= 70


def test_evaluations_api_run_and_latest():
    client = build_client()
    repo_evaluation_workspace = (
        Path(__file__).resolve().parents[3] / "workspace" / "evaluations"
    )

    empty_response = client.get("/evaluations/latest")
    assert empty_response.status_code == 404

    run_response = client.post("/evaluations/run")
    assert run_response.status_code == 201
    run_payload = run_response.json()
    assert run_payload["summary_json"]["total"] == 8
    assert run_payload["summary_json"]["passed"] == 8
    assert run_payload["summary_json"]["failed"] == 0
    assert run_payload["summary_json"]["by_type"]["profile_benchmark"]["total"] == 5

    latest_response = client.get("/evaluations/latest")
    assert latest_response.status_code == 200
    assert latest_response.json()["id"] == run_payload["id"]
    assert not repo_evaluation_workspace.exists()


def test_expected_check_mismatch_fails_case(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        evaluation_runner,
        "evaluation_cases",
        lambda: [
            EvaluationCase(
                case_id="healthy-small",
                description="healthy fixture with intentionally wrong expected checks",
                expected_checks=["readme_present", "missing_expected_check"],
            )
        ],
    )

    with create_test_session() as session:
        run = run_evaluations(session, workspace_root=tmp_path)

    assert run.status == "failed"
    assert run.summary_json == {
        "total": 1,
        "passed": 0,
        "failed": 1,
        "by_type": {"scan_regression": {"total": 1, "passed": 0, "failed": 1}},
        "profiles": {},
    }
    result = run.case_results_json[0]
    assert result["passed"] is False
    assert result["missing_checks"] == ["missing_expected_check"]
    assert result["unexpected_checks"] == [
        "entrypoint_detected",
        "files_scanned",
        "profile_aware_report_sections",
        "project_research_package_sections",
    ]


def test_unknown_case_type_fails_case_without_writing_fixture(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        evaluation_runner,
        "evaluation_cases",
        lambda: [
            EvaluationCase(
                case_id="future-case-without-fixture",
                description="future evaluation case type",
                expected_checks=["known_case_type"],
                case_type="future_case_type",
            )
        ],
    )

    with create_test_session() as session:
        run = run_evaluations(session, workspace_root=tmp_path)

    assert run.status == "failed"
    assert run.summary_json == {
        "total": 1,
        "passed": 0,
        "failed": 1,
        "by_type": {"future_case_type": {"total": 1, "passed": 0, "failed": 1}},
        "profiles": {},
    }
    result = run.case_results_json[0]
    assert result["passed"] is False
    assert result["missing_checks"] == []
    assert result["unexpected_checks"] == []
    assert result["checks"] == [
        {
            "name": "known_case_type",
            "passed": False,
            "message": "未知 evaluation case type: future_case_type",
        }
    ]
