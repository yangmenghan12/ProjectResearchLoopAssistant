from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.db.models import (
    AnalysisRun,
    GraphCheckpoint,
    GraphCheckpointWrite,
    Project,
    RunEvent,
    RunJob,
)
from tests.test_runs_api import build_client


def open_test_session() -> Iterator[Session]:
    client = build_client()
    override_session = next(iter(client.app.dependency_overrides.values()))
    yield from override_session()


def test_analysis_run_runtime_fields_are_persisted():
    session_iter = open_test_session()
    session = next(session_iter)
    try:
        project = Project(name="Runtime", topic="Runtime", analysis_goal="Assess runtime")
        session.add(project)
        session.flush()
        run = AnalysisRun(
            project_id=project.id,
            status="queued",
            thread_id="run-thread-1",
            execution_mode="background",
            last_checkpoint_id="checkpoint-1",
            resume_count=2,
        )
        session.add(run)
        session.commit()

        saved = session.get(AnalysisRun, run.id)

        assert saved is not None
        assert saved.thread_id == "run-thread-1"
        assert saved.execution_mode == "background"
        assert saved.last_checkpoint_id == "checkpoint-1"
        assert saved.resume_count == 2
    finally:
        session_iter.close()


def test_runtime_tables_persist_jobs_events_and_checkpoints():
    session_iter = open_test_session()
    session = next(session_iter)
    try:
        project = Project(name="Runtime", topic="Runtime", analysis_goal="Assess runtime")
        session.add(project)
        session.flush()
        run = AnalysisRun(project_id=project.id, status="queued", thread_id="run-thread-2")
        session.add(run)
        session.flush()
        job = RunJob(project_id=project.id, run_id=run.id, job_type="start", status="queued")
        event = RunEvent(
            project_id=project.id,
            run_id=run.id,
            event_type="run_queued",
            sequence=1,
            payload_json={"status": "queued"},
        )
        checkpoint = GraphCheckpoint(
            thread_id=run.thread_id,
            checkpoint_ns="",
            checkpoint_id="checkpoint-1",
            checkpoint_json={"id": "checkpoint-1", "channel_versions": {}},
            metadata_json={"source": "test"},
            channel_values_json={},
            parent_checkpoint_id=None,
        )
        write = GraphCheckpointWrite(
            thread_id=run.thread_id,
            checkpoint_ns="",
            checkpoint_id="checkpoint-1",
            task_id="task-1",
            write_index=0,
            channel="__start__",
            value_json={"ok": True},
        )
        session.add_all([job, event, checkpoint, write])
        session.commit()

        assert session.get(RunJob, job.id).status == "queued"
        assert session.get(RunEvent, event.id).sequence == 1
        assert session.get(GraphCheckpoint, checkpoint.id).checkpoint_id == "checkpoint-1"
        assert session.get(GraphCheckpointWrite, write.id).channel == "__start__"
    finally:
        session_iter.close()
