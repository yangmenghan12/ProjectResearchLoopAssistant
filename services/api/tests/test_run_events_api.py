from app.workers.run_worker import run_one_job
from tests.test_run_streaming_api import parse_sse_frames
from tests.test_runs_api import build_client, create_collected_project


def test_run_events_endpoint_returns_history(tmp_path):
    client = build_client()
    project = create_collected_project(client, tmp_path)
    created = client.post(f"/projects/{project['id']}/runs").json()
    run_one_job()

    response = client.get(f"/projects/{project['id']}/runs/{created['run']['id']}/events")

    assert response.status_code == 200
    event_types = [event["event_type"] for event in response.json()]
    assert "run_queued" in event_types
    assert "run_completed" in event_types


def test_run_events_stream_returns_existing_events(tmp_path):
    client = build_client()
    project = create_collected_project(client, tmp_path)
    created = client.post(f"/projects/{project['id']}/runs").json()
    run_one_job()

    response = client.get(f"/projects/{project['id']}/runs/{created['run']['id']}/events/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = parse_sse_frames(response.text)
    assert frames[0]["event"] == "run_queued"
    assert frames[-1]["event"] == "run_completed"
