from collections.abc import Iterator
from datetime import timedelta

from sqlalchemy.orm import Session

from app.db.models import AnalysisRun, Project, RunEvent, RunJob, utc_now
from app.services.run_jobs import (
    claim_next_job,
    create_run_job,
    heartbeat_job,
    recover_stale_jobs,
)
from tests.test_runs_api import build_client


def open_test_session() -> Iterator[Session]:
    client = build_client()
    override_session = next(iter(client.app.dependency_overrides.values()))
    yield from override_session()


def create_project_and_run(session: Session) -> tuple[Project, AnalysisRun]:
    project = Project(name="Lease runtime", topic="Lease runtime", analysis_goal="Assess leases")
    session.add(project)
    session.flush()
    run = AnalysisRun(project_id=project.id, status="queued", thread_id=f"run:{project.id}")
    session.add(run)
    session.flush()
    return project, run


def test_claim_next_job_sets_lease_fields_and_excludes_other_workers():
    session_iter = open_test_session()
    session = next(session_iter)
    try:
        project, run = create_project_and_run(session)
        queued = create_run_job(session, project.id, run.id, "start")

        claimed = claim_next_job(session, worker_id="worker-a", queue_name="default", lease_seconds=30)
        second_claim = claim_next_job(session, worker_id="worker-b", queue_name="default", lease_seconds=30)

        assert claimed is not None
        assert claimed.id == queued.id
        assert claimed.status == "running"
        assert claimed.locked_by == "worker-a"
        assert claimed.locked_at is not None
        assert claimed.locked_until is not None
        assert claimed.heartbeat_at is not None
        assert claimed.locked_until > claimed.locked_at
        assert claimed.attempt_count == 1
        assert second_claim is None
    finally:
        session_iter.close()


def test_claim_next_job_filters_by_queue_name():
    session_iter = open_test_session()
    session = next(session_iter)
    try:
        project, run = create_project_and_run(session)
        create_run_job(session, project.id, run.id, "start", queue_name="report")

        claimed = claim_next_job(session, worker_id="worker-a", queue_name="default", lease_seconds=30)

        assert claimed is None
    finally:
        session_iter.close()


def test_heartbeat_extends_current_workers_lease_and_rejects_wrong_worker():
    session_iter = open_test_session()
    session = next(session_iter)
    try:
        project, run = create_project_and_run(session)
        create_run_job(session, project.id, run.id, "start")
        claimed = claim_next_job(session, worker_id="worker-a", queue_name="default", lease_seconds=30)
        assert claimed is not None
        original_locked_until = claimed.locked_until

        heartbeat_job(claimed, worker_id="worker-a", lease_seconds=60)

        assert claimed.locked_until is not None
        assert original_locked_until is not None
        assert claimed.locked_until > original_locked_until
        assert claimed.heartbeat_at is not None

        try:
            heartbeat_job(claimed, worker_id="worker-b", lease_seconds=60)
        except ValueError as exc:
            assert "worker" in str(exc).lower()
        else:
            raise AssertionError("heartbeat_job should reject a worker that does not own the lease")
    finally:
        session_iter.close()


def test_recover_stale_jobs_requeues_retryable_jobs_and_fails_exhausted_jobs():
    session_iter = open_test_session()
    session = next(session_iter)
    try:
        now = utc_now()
        project, run = create_project_and_run(session)
        retryable = RunJob(
            project_id=project.id,
            run_id=run.id,
            job_type="start",
            status="running",
            attempt_count=1,
            max_attempts=3,
            locked_by="worker-a",
            locked_at=now - timedelta(minutes=3),
            locked_until=now - timedelta(minutes=1),
            heartbeat_at=now - timedelta(minutes=2),
        )
        exhausted = RunJob(
            project_id=project.id,
            run_id=run.id,
            job_type="retry",
            status="running",
            attempt_count=3,
            max_attempts=3,
            locked_by="worker-b",
            locked_at=now - timedelta(minutes=3),
            locked_until=now - timedelta(minutes=1),
            heartbeat_at=now - timedelta(minutes=2),
        )
        fresh = RunJob(
            project_id=project.id,
            run_id=run.id,
            job_type="resume",
            status="running",
            attempt_count=1,
            max_attempts=3,
            locked_by="worker-c",
            locked_at=now,
            locked_until=now + timedelta(minutes=1),
            heartbeat_at=now,
        )
        session.add_all([retryable, exhausted, fresh])
        session.flush()

        recovered = recover_stale_jobs(session, now=now)

        assert {job.id for job in recovered} == {retryable.id, exhausted.id}
        assert retryable.status == "queued"
        assert retryable.locked_by is None
        assert retryable.locked_at is None
        assert retryable.locked_until is None
        assert retryable.heartbeat_at is None
        assert retryable.last_error is None
        assert exhausted.status == "failed"
        assert exhausted.locked_by is None
        assert exhausted.locked_at is None
        assert exhausted.locked_until is None
        assert exhausted.heartbeat_at is None
        assert exhausted.last_error is not None
        assert "expired" in exhausted.last_error.lower()
        assert run.status == "failed"
        assert run.stop_reason is not None
        assert "expired" in run.stop_reason.lower()
        assert project.status == "failed"
        event = session.query(RunEvent).filter_by(run_id=run.id, event_type="run_failed").one()
        assert event.payload_json["message"] == exhausted.last_error
        assert event.payload_json["job_id"] == exhausted.id
        assert event.payload_json["source"] == "worker_lease_recovery"
        assert fresh.status == "running"
        assert fresh.locked_by == "worker-c"
    finally:
        session_iter.close()


def test_create_run_job_writes_queue_name_and_reuses_active_job():
    session_iter = open_test_session()
    session = next(session_iter)
    try:
        project, run = create_project_and_run(session)

        first = create_run_job(session, project.id, run.id, "start", queue_name="report")
        second = create_run_job(session, project.id, run.id, "start", queue_name="default")

        assert first.id == second.id
        assert second.queue_name == "report"
    finally:
        session_iter.close()
