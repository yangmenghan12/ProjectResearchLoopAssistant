from app.workers.run_worker import run_one_job
from tests.test_runs_api import build_client, create_collected_project


def test_cancel_queued_run_marks_job_and_run_cancelled(tmp_path):
    client = build_client()
    project = create_collected_project(client, tmp_path)
    created = client.post(f"/projects/{project['id']}/runs").json()

    response = client.post(f"/projects/{project['id']}/runs/{created['run']['id']}/cancel")

    assert response.status_code == 200
    cancelled = client.get(f"/projects/{project['id']}/runs/{created['run']['id']}").json()
    assert cancelled["run"]["status"] == "cancelled"


def test_retry_failed_run_creates_retry_job(tmp_path, monkeypatch):
    client = build_client()
    project = create_collected_project(client, tmp_path)
    created = client.post(f"/projects/{project['id']}/runs").json()
    run_id = created["run"]["id"]

    from app.engine import langgraph_runner

    def boom(*args, **kwargs):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(langgraph_runner, "stream_run_graph_events", boom)
    run_one_job()

    retry = client.post(f"/projects/{project['id']}/runs/{run_id}/retry")

    assert retry.status_code == 202
    assert retry.json()["job"]["job_type"] == "retry"


def test_worker_stops_recording_node_events_after_running_job_is_cancelled(tmp_path, monkeypatch):
    client = build_client()
    project = create_collected_project(client, tmp_path)
    created = client.post(f"/projects/{project['id']}/runs").json()
    run_id = created["run"]["id"]

    from app.engine import langgraph_runner

    def fake_stream(*args, **kwargs):
        yield {"node": "first"}
        cancel_response = client.post(f"/projects/{project['id']}/runs/{run_id}/cancel")
        assert cancel_response.status_code == 200
        yield {"node": "second"}

    monkeypatch.setattr(langgraph_runner, "stream_run_graph_events", fake_stream)

    assert run_one_job(worker_id="worker-a") is True

    package = client.get(f"/projects/{project['id']}/runs/{run_id}").json()
    events = client.get(f"/projects/{project['id']}/runs/{run_id}/events").json()
    node_events = [event for event in events if event["event_type"] == "node_completed"]

    assert package["run"]["status"] == "cancelled"
    assert [event["payload_json"]["node"] for event in node_events] == ["first"]
