from app.workers.run_worker import run_one_job
from tests.test_runs_api import build_client, create_collected_project


def test_start_run_enqueues_job_without_synchronous_completion(tmp_path):
    client = build_client()
    project = create_collected_project(client, tmp_path)

    response = client.post(f"/projects/{project['id']}/runs")

    assert response.status_code == 202
    body = response.json()
    assert body["run"]["status"] == "queued"
    assert body["job"]["status"] == "queued"
    assert body["run"]["thread_id"]


def test_worker_processes_queued_run_to_reviewable_package(tmp_path):
    client = build_client()
    project = create_collected_project(client, tmp_path)
    created = client.post(f"/projects/{project['id']}/runs").json()

    processed = run_one_job()

    assert processed is True
    package = client.get(f"/projects/{project['id']}/runs/{created['run']['id']}").json()
    assert package["run"]["status"] in {"ready_for_review", "ready_for_review_with_risks"}
    assert package["turns"]
