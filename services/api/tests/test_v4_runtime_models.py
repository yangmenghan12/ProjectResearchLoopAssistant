import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    AnalysisRun,
    EvaluationRun,
    LLMReportArtifact,
    Project,
    RunJob,
    ToolCall,
    ToolRegistration,
    utc_now,
)
from app.schemas.evaluation import EvaluationRunRead
from app.schemas.run import RunJobRead
from app.schemas.tooling import ToolCallRead, ToolRegistrationRead
from tests.test_runs_api import build_client


_client = build_client()


@contextmanager
def create_test_session() -> Iterator[Session]:
    override_session = next(iter(_client.app.dependency_overrides.values()))
    session_iter = override_session()
    session = next(session_iter)
    try:
        yield session
    finally:
        session_iter.close()


def create_project_and_run(session: Session) -> tuple[Project, AnalysisRun]:
    project = Project(name="V4", topic="V4 runtime", analysis_goal="Assess V4 runtime")
    session.add(project)
    session.flush()
    run = AnalysisRun(project_id=project.id, status="queued", thread_id=f"run:{project.id}")
    session.add(run)
    session.flush()
    return project, run


def test_run_job_has_v4_lease_fields_and_read_schema():
    with create_test_session() as session:
        project, run = create_project_and_run(session)
        job = RunJob(
            project_id=project.id,
            run_id=run.id,
            job_type="start",
            queue_name="default",
            locked_by="worker-a",
            locked_at=utc_now(),
            locked_until=utc_now() + timedelta(seconds=30),
            heartbeat_at=utc_now(),
            next_run_at=utc_now(),
            payload_json={},
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        payload = RunJobRead.model_validate(job).model_dump()

        assert payload["queue_name"] == "default"
        assert payload["locked_by"] == "worker-a"
        assert payload["locked_at"] is not None
        assert payload["locked_until"] is not None
        assert payload["heartbeat_at"] is not None
        assert payload["available_at"] is not None
        assert payload["next_run_at"] is not None


def test_v4_runtime_migration_creates_schema_indexes_and_enforces_foreign_keys(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    old_schema = MetaData()

    def project_fk() -> Column:
        return Column("project_id", String(36), ForeignKey("projects.id"), nullable=False)

    def run_fk() -> Column:
        return Column("run_id", String(36), ForeignKey("analysis_runs.id"), nullable=False)

    def timestamp_columns() -> list[Column]:
        return [
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
        ]

    # 0009 之前只需要这些 FK 目标表和 run_jobs 旧列，避免 Base.metadata 掩盖迁移缺口。
    Table("projects", old_schema, Column("id", String(36), primary_key=True))
    Table(
        "analysis_runs",
        old_schema,
        Column("id", String(36), primary_key=True),
        Column("project_id", String(36), ForeignKey("projects.id"), nullable=False),
    )
    Table(
        "loop_turns",
        old_schema,
        Column("id", String(36), primary_key=True),
        project_fk(),
        run_fk(),
    )
    Table(
        "report_snapshots",
        old_schema,
        Column("id", String(36), primary_key=True),
        project_fk(),
        run_fk(),
    )
    Table(
        "llm_calls",
        old_schema,
        Column("id", String(36), primary_key=True),
        project_fk(),
        run_fk(),
    )
    Table(
        "run_jobs",
        old_schema,
        Column("id", String(36), primary_key=True),
        project_fk(),
        run_fk(),
        Column("job_type", String(40), nullable=False),
        Column("status", String(40), nullable=False),
        Column("attempt_count", Integer, nullable=False),
        Column("max_attempts", Integer, nullable=False),
        Column("locked_by", String(120), nullable=True),
        Column("locked_at", DateTime(timezone=True), nullable=True),
        Column("available_at", DateTime(timezone=True), nullable=False),
        Column("last_error", Text, nullable=True),
        Column("payload_json", JSON, nullable=False),
        *timestamp_columns(),
    )
    assert "run_jobs" in old_schema.tables

    with engine.connect() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        old_schema.create_all(connection)

        migration = importlib.import_module(
            "app.db.migrations.versions.20260701_0009_v4_runtime_patrol"
        )
        context = MigrationContext.configure(connection)
        monkeypatch.setattr(migration, "op", Operations(context))
        migration.upgrade()

        inspector = inspect(connection)
        run_job_columns = {column["name"] for column in inspector.get_columns("run_jobs")}
        assert {"queue_name", "locked_until", "heartbeat_at", "next_run_at"} <= run_job_columns

        run_job_indexes = {index["name"] for index in inspector.get_indexes("run_jobs")}
        assert {
            "ix_run_jobs_queue_name",
            "ix_run_jobs_locked_until",
            "ix_run_jobs_next_run_at",
        } <= run_job_indexes

        assert {
            "tool_registrations",
            "tool_calls",
            "llm_report_artifacts",
            "evaluation_runs",
        } <= set(inspector.get_table_names())

        def fk_targets(table_name: str) -> set[tuple[tuple[str, ...], str]]:
            return {
                (tuple(fk["constrained_columns"]), fk["referred_table"])
                for fk in inspector.get_foreign_keys(table_name)
            }

        assert {
            (("project_id",), "projects"),
            (("run_id",), "analysis_runs"),
            (("turn_id",), "loop_turns"),
        } <= fk_targets("tool_calls")
        assert {
            (("project_id",), "projects"),
            (("run_id",), "analysis_runs"),
            (("report_id",), "report_snapshots"),
            (("call_id",), "llm_calls"),
        } <= fk_targets("llm_report_artifacts")

        tool_calls = MetaData()
        tool_calls_table = Table("tool_calls", tool_calls, autoload_with=connection)
        with pytest.raises(IntegrityError):
            connection.execute(
                tool_calls_table.insert().values(
                    id="tool-call-1",
                    project_id="missing",
                    run_id=None,
                    turn_id=None,
                    tool_name="local_filesystem_scan",
                    permission_level="L0",
                    status="succeeded",
                    input_summary_json={},
                    output_summary_json={},
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            )


def test_tool_report_and_evaluation_models_persist_and_serialize():
    with create_test_session() as session:
        project, run = create_project_and_run(session)
        tool = ToolRegistration(
            name="local_filesystem_scan",
            description="Read-only local filesystem scanner",
            permission_level="L0",
            input_schema_json={"type": "object"},
            enabled=True,
        )
        session.add(tool)
        session.flush()
        call = ToolCall(
            project_id=project.id,
            run_id=run.id,
            turn_id=None,
            tool_name=tool.name,
            permission_level="L0",
            status="succeeded",
            input_summary_json={"root": "demo"},
            output_summary_json={"files": 3},
            latency_ms=12,
        )
        report_artifact = LLMReportArtifact(
            project_id=project.id,
            run_id=run.id,
            report_id=None,
            call_id=None,
            status="accepted",
            content_json={"sections": []},
            validation_result_json={"accepted": True},
        )
        evaluation = EvaluationRun(
            status="succeeded",
            summary_json={"passed": 3, "failed": 0},
            case_results_json=[{"case_id": "healthy-small", "passed": True}],
        )
        session.add_all([call, report_artifact, evaluation])
        session.commit()

        saved_tool = session.get(ToolRegistration, tool.id)
        saved_call = session.get(ToolCall, call.id)
        saved_artifact = session.get(LLMReportArtifact, report_artifact.id)
        saved_evaluation = session.get(EvaluationRun, evaluation.id)

        assert saved_tool is not None
        assert saved_call is not None
        assert saved_artifact is not None
        assert saved_evaluation is not None
        assert ToolRegistrationRead.model_validate(saved_tool).name == "local_filesystem_scan"
        assert ToolCallRead.model_validate(saved_call).status == "succeeded"
        assert saved_artifact.status == "accepted"
        assert EvaluationRunRead.model_validate(saved_evaluation).summary_json["passed"] == 3
