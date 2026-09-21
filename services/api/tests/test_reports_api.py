from pathlib import Path

from app.llm.router import LLMRouter

from tests.test_runs_api import build_client, create_collected_project, enqueue_and_process_run


def create_project_with_run(tmp_path: Path):
    client = build_client()
    project = create_collected_project(client, tmp_path)
    return client, project, enqueue_and_process_run(client, project["id"])


def test_create_get_latest_and_export_report(tmp_path: Path):
    client, project, run_package = create_project_with_run(tmp_path)

    create_response = client.post(f"/projects/{project['id']}/reports")
    latest_response = client.get(f"/projects/{project['id']}/reports/latest")

    assert create_response.status_code == 201
    report = create_response.json()
    assert report["project_id"] == project["id"]
    assert report["run_id"] == run_package["run"]["id"]
    assert report["status"] == "draft"
    assert report["verifier_result_json"]["checks"]
    assert "# Run project 项目调研报告" in report["markdown_content"]

    assert latest_response.status_code == 200
    assert latest_response.json()["id"] == report["id"]

    markdown_response = client.get(f"/projects/{project['id']}/reports/{report['id']}/markdown")
    assert markdown_response.status_code == 200
    assert "## 校验摘要" in markdown_response.json()["markdown"]


def test_create_intelligent_report_falls_back_when_llm_raises(tmp_path: Path, monkeypatch):
    def fake_chat(self, **kwargs):
        raise RuntimeError("provider timeout")

    monkeypatch.setattr(LLMRouter, "chat", fake_chat)
    client, project, run_package = create_project_with_run(tmp_path)

    create_response = client.post(f"/projects/{project['id']}/reports/intelligent")

    assert create_response.status_code == 201
    report = create_response.json()
    assert report["project_id"] == project["id"]
    assert report["run_id"] == run_package["run"]["id"]
    assert "智能报告增强" not in report["markdown_content"]

    latest_response = client.get(f"/projects/{project['id']}/reports/latest")
    assert latest_response.status_code == 200
    assert latest_response.json()["id"] == report["id"]


def test_approved_report_marks_project_completed(tmp_path: Path):
    client, project, _ = create_project_with_run(tmp_path)
    report = client.post(f"/projects/{project['id']}/reports").json()

    review_response = client.post(
        f"/projects/{project['id']}/reports/{report['id']}/review",
        json={"decision": "approved", "comment": "Approved for the next milestone."},
    )

    assert review_response.status_code == 200
    reviewed_report = review_response.json()
    assert reviewed_report["status"] == "approved"
    assert reviewed_report["review_decision"] == "approved"
    assert reviewed_report["review_comment"] == "Approved for the next milestone."
    assert client.get(f"/projects/{project['id']}").json()["status"] == "completed"


def test_needs_more_report_keeps_project_reviewable_with_risks(tmp_path: Path):
    client, project, _ = create_project_with_run(tmp_path)
    report = client.post(f"/projects/{project['id']}/reports").json()

    review_response = client.post(
        f"/projects/{project['id']}/reports/{report['id']}/review",
        json={"decision": "needs_more", "comment": "Need stronger evidence coverage."},
    )

    assert review_response.status_code == 200
    reviewed_report = review_response.json()
    assert reviewed_report["status"] == "needs_more"
    assert reviewed_report["review_decision"] == "needs_more"
    assert client.get(f"/projects/{project['id']}").json()["status"] == "queued"


def test_followup_review_requires_comment(tmp_path: Path):
    client, project, _ = create_project_with_run(tmp_path)
    report = client.post(f"/projects/{project['id']}/reports").json()

    needs_more_response = client.post(
        f"/projects/{project['id']}/reports/{report['id']}/review",
        json={"decision": "needs_more", "comment": ""},
    )
    rejected_response = client.post(
        f"/projects/{project['id']}/reports/{report['id']}/review",
        json={"decision": "rejected"},
    )

    assert needs_more_response.status_code == 400
    assert rejected_response.status_code == 400


def test_needs_more_review_records_feedback_and_starts_followup_run(tmp_path: Path):
    client, project, first_package = create_project_with_run(tmp_path)
    report = client.post(f"/projects/{project['id']}/reports").json()

    review_response = client.post(
        f"/projects/{project['id']}/reports/{report['id']}/review",
        json={"decision": "needs_more", "comment": "Need stronger evidence coverage."},
    )

    assert review_response.status_code == 200
    reviewed_report = review_response.json()
    assert reviewed_report["status"] == "needs_more"
    assert reviewed_report["review_decision"] == "needs_more"
    assert reviewed_report["review_comment"] == "Need stronger evidence coverage."

    runs = client.get(f"/projects/{project['id']}/runs").json()
    assert len(runs) == 2
    assert runs[0]["id"] != first_package["run"]["id"]
    assert runs[0]["status"] == "queued"
    assert runs[0]["execution_mode"] == "background"
    events = client.get(f"/projects/{project['id']}/runs/{runs[0]['id']}/events").json()
    assert events[0]["event_type"] == "run_queued"

    reviewed_package = client.get(f"/projects/{project['id']}/runs/{first_package['run']['id']}").json()
    assert any(
        question["status"] == "answered"
        and question["answer_text"] == "Need stronger evidence coverage."
        and question["metadata_json"]["source"] == "report_review"
        and question["metadata_json"]["decision"] == "needs_more"
        for question in reviewed_package["questions"]
    )
