import json

from app.db.models import RunJob
from app.services.run_events import (
    encode_keepalive_sse,
    encode_sse,
    record_run_event,
    stream_run_events_live,
)
from app.workers.run_worker import run_one_job
from tests.test_run_jobs_events import create_project_and_run, open_test_session
from tests.test_run_streaming_api import parse_sse_frames
from tests.test_runs_api import build_client, create_collected_project


def test_encode_keepalive_sse_returns_sse_comment():
    encoded = encode_keepalive_sse()

    assert encoded.startswith(": keepalive")
    assert encoded.endswith("\n\n")


def test_encode_sse_includes_sequence_id_event_and_iso_created_at():
    session_iter = open_test_session()
    session = next(session_iter)
    try:
        project, run = create_project_and_run(session)
        event = record_run_event(session, project.id, run.id, "run_started", {"status": "running"})
        session.commit()

        encoded = encode_sse(event)

        assert f"id: {event.sequence}" in encoded
        assert "event: run_started" in encoded
        assert "data: " in encoded
        payload = json.loads(encoded.split("data: ", 1)[1])
        assert payload["sequence"] == event.sequence
        assert payload["created_at"] == event.created_at.isoformat()
    finally:
        session_iter.close()


def test_events_stream_after_sequence_only_replays_newer_events():
    session_iter = open_test_session()
    session = next(session_iter)
    try:
        project, run = create_project_and_run(session)
        first = record_run_event(session, project.id, run.id, "run_queued", {"status": "queued"})
        second = record_run_event(session, project.id, run.id, "run_started", {"status": "running"})
        third = record_run_event(session, project.id, run.id, "run_completed", {"status": "ready_for_review"})
        run.status = "ready_for_review"
        session.commit()

        chunks = list(
            stream_run_events_live(
                lambda: session,
                project_id=project.id,
                run_id=run.id,
                after_sequence=first.sequence,
                poll_interval_seconds=0,
            )
        )

        frames = parse_sse_frames("".join(chunks))
        assert [frame["data"]["sequence"] for frame in frames] == [second.sequence, third.sequence]
        assert [frame["event"] for frame in frames] == ["run_started", "run_completed"]
    finally:
        session_iter.close()


def test_completed_run_events_stream_replays_history_and_finishes(tmp_path):
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


def test_completed_run_events_stream_respects_after_sequence(tmp_path):
    client = build_client()
    project = create_collected_project(client, tmp_path)
    created = client.post(f"/projects/{project['id']}/runs").json()

    run_one_job()
    history_response = client.get(f"/projects/{project['id']}/runs/{created['run']['id']}/events")
    first_sequence = history_response.json()[0]["sequence"]

    response = client.get(
        f"/projects/{project['id']}/runs/{created['run']['id']}/events/stream",
        params={"after_sequence": first_sequence},
    )

    assert response.status_code == 200
    frames = parse_sse_frames(response.text)
    assert frames[0]["data"]["sequence"] > first_sequence
    assert frames[0]["event"] == "run_started"
    assert frames[-1]["event"] == "run_completed"


def test_active_job_live_stream_replays_history_before_waiting():
    session_iter = open_test_session()
    session = next(session_iter)
    try:
        project, run = create_project_and_run(session)
        first = record_run_event(session, project.id, run.id, "run_queued", {"status": "queued"})
        session.add(RunJob(project_id=project.id, run_id=run.id, job_type="start", status="queued"))
        session.commit()

        stream = stream_run_events_live(
            lambda: session,
            project_id=project.id,
            run_id=run.id,
            poll_interval_seconds=0,
            keepalive_seconds=0,
        )

        first_chunk = next(stream)
        second_chunk = next(stream)

        frames = parse_sse_frames(first_chunk)
        assert frames[0]["event"] == "run_queued"
        assert frames[0]["data"]["sequence"] == first.sequence
        assert second_chunk.startswith(": keepalive")
        stream.close()
    finally:
        session_iter.close()
