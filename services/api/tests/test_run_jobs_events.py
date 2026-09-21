from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.db.models import AnalysisRun, Project, RunEvent
from app.services import run_events as run_events_service
from app.services.run_events import encode_sse, list_run_events, record_run_event
from app.services.run_jobs import create_run_job, get_active_job_for_run, mark_job_succeeded
from tests.test_runs_api import build_client


def open_test_session() -> Iterator[Session]:
    client = build_client()
    override_session = next(iter(client.app.dependency_overrides.values()))
    yield from override_session()


def create_project_and_run(session: Session) -> tuple[Project, AnalysisRun]:
    project = Project(name="Runtime", topic="Runtime", analysis_goal="Assess runtime")
    session.add(project)
    session.flush()
    run = AnalysisRun(project_id=project.id, status="queued", thread_id=f"run:{project.id}")
    session.add(run)
    session.flush()
    return project, run


def test_record_run_event_assigns_monotonic_sequence():
    session_iter = open_test_session()
    session = next(session_iter)
    try:
        project, run = create_project_and_run(session)

        first = record_run_event(session, project.id, run.id, "run_queued", {"status": "queued"})
        second = record_run_event(session, project.id, run.id, "run_started", {"status": "running"})
        session.commit()

        events = list_run_events(session, project.id, run.id)

        assert first.sequence == 1
        assert second.sequence == 2
        assert [event.event_type for event in events] == ["run_queued", "run_started"]
        assert "event: run_queued" in encode_sse(first)
        assert '"status": "queued"' in encode_sse(first)
    finally:
        session_iter.close()


def test_run_event_sequence_is_unique_per_run():
    assert any(
        constraint.name == "uq_run_events_run_sequence"
        and {column.name for column in constraint.columns} == {"run_id", "sequence"}
        for constraint in RunEvent.__table__.constraints
    )


def test_record_run_event_retries_sequence_conflict(monkeypatch):
    session_iter = open_test_session()
    session = next(session_iter)
    try:
        project, run = create_project_and_run(session)
        session.add(
            RunEvent(
                project_id=project.id,
                run_id=run.id,
                event_type="run_queued",
                sequence=1,
                payload_json={"status": "queued"},
            )
        )
        session.commit()

        sequence_attempts = iter([1, 2])
        monkeypatch.setattr(
            run_events_service,
            "next_sequence",
            lambda session, run_id: next(sequence_attempts),
        )

        event = record_run_event(session, project.id, run.id, "run_started", {"status": "running"})
        session.commit()

        assert event.sequence == 2
        assert [saved.sequence for saved in list_run_events(session, project.id, run.id)] == [1, 2]
    finally:
        session_iter.close()


def test_create_run_job_reuses_active_job_for_same_run_and_type():
    session_iter = open_test_session()
    session = next(session_iter)
    try:
        project, run = create_project_and_run(session)

        first = create_run_job(session, project.id, run.id, "start")
        second = create_run_job(session, project.id, run.id, "start")
        mark_job_succeeded(first)
        third = create_run_job(session, project.id, run.id, "start")
        active = get_active_job_for_run(session, run.id, "start")

        assert first.id == second.id
        assert third.id != first.id
        assert active is not None
        assert active.id == third.id
    finally:
        session_iter.close()
