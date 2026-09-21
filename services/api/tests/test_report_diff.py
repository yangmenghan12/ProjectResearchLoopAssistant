import json
from pathlib import Path

from app.reports.diff import diff_report_snapshots
from app.workers.run_worker import run_one_job
from tests.test_reports_api import create_project_with_run


REVIEWABLE_RUN_STATUSES = {"ready_for_review", "ready_for_review_with_risks"}


def test_diff_report_snapshots_reports_quality_delta_and_collection_changes():
    before = {
        "findings": [{"title": "Old"}],
        "quality_scores": [{"overall_score": 70}],
    }
    after = {
        "findings": [{"title": "New"}],
        "quality_scores": [{"overall_score": 85}],
    }

    diff = diff_report_snapshots(before, after)

    assert diff["quality_before"] == 70
    assert diff["quality_after"] == 85
    assert diff["quality_delta"] == 15
    assert diff["findings"]["added"] == [{"title": "New"}]
    assert diff["findings"]["removed"] == [{"title": "Old"}]
    assert diff["findings"]["changed"] == []


def test_diff_report_snapshots_ignores_run_local_evidence_refs_for_changed_items():
    before = {
        "findings": [
            {
                "title": "Same finding",
                "summary": "Same finding summary",
                "category": "architecture",
                "evidence_refs_json": ["before-evidence-id"],
            }
        ],
        "risks": [
            {
                "title": "Same risk",
                "summary": "Same risk summary",
                "severity": "medium",
                "mitigation": "Same mitigation",
                "evidence_refs_json": ["before-evidence-id"],
            }
        ],
    }
    after = {
        "findings": [
            {
                "title": "Same finding",
                "summary": "Same finding summary",
                "category": "architecture",
                "evidence_refs_json": ["after-evidence-id"],
            }
        ],
        "risks": [
            {
                "title": "Same risk",
                "summary": "Same risk summary",
                "severity": "medium",
                "mitigation": "Same mitigation",
                "evidence_refs_json": ["after-evidence-id"],
            }
        ],
    }

    diff = diff_report_snapshots(before, after)

    assert diff["findings"]["changed"] == []
    assert diff["risks"]["changed"] == []


def test_report_diff_endpoint_compares_report_run_packages(tmp_path: Path):
    client, project, first_package = create_project_with_run(tmp_path)
    first_report = client.post(f"/projects/{project['id']}/reports").json()

    review_response = client.post(
        f"/projects/{project['id']}/reports/{first_report['id']}/review",
        json={"decision": "needs_more", "comment": "Need stronger evidence coverage."},
    )
    assert review_response.status_code == 200
    assert run_one_job() is True

    runs_response = client.get(f"/projects/{project['id']}/runs")
    assert runs_response.status_code == 200
    followup_run = next(
        run
        for run in runs_response.json()
        if run["id"] != first_package["run"]["id"] and run["status"] in REVIEWABLE_RUN_STATUSES
    )
    second_response = client.post(
        f"/projects/{project['id']}/reports",
        json={"run_id": followup_run["id"]},
    )
    assert second_response.status_code == 201
    second_report = second_response.json()

    diff_response = client.get(
        f"/projects/{project['id']}/reports/{second_report['id']}/diff",
        params={"base_report_id": first_report["id"]},
    )

    assert diff_response.status_code == 200
    payload = diff_response.json()
    assert payload["base_report_id"] == first_report["id"]
    assert payload["target_report_id"] == second_report["id"]
    assert "quality_delta" in payload["diff"]
    assert set(payload["diff"]["findings"]) == {"added", "removed", "changed"}
    assert "Need stronger evidence coverage." not in json.dumps(
        payload["diff"]["questions"],
        ensure_ascii=False,
    )


def test_report_diff_endpoint_returns_404_for_missing_or_cross_project_report(tmp_path: Path):
    client, project, _ = create_project_with_run(tmp_path)
    report = client.post(f"/projects/{project['id']}/reports").json()

    missing_base_response = client.get(
        f"/projects/{project['id']}/reports/{report['id']}/diff",
        params={"base_report_id": "missing-report"},
    )
    assert missing_base_response.status_code == 404

    other_project_response = client.post(
        "/projects",
        json={
            "name": "Other project",
            "topic": "Cross-project boundary",
            "analysis_goal": "Keep reports scoped to their project.",
            "max_turns": 6,
        },
    )
    assert other_project_response.status_code == 201
    other_project = other_project_response.json()

    cross_project_response = client.get(
        f"/projects/{other_project['id']}/reports/{report['id']}/diff",
        params={"base_report_id": report["id"]},
    )
    assert cross_project_response.status_code == 404
