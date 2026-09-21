"""runtime hardening schema

Revision ID: 20260701_0005
Revises: 20260701_0004
Create Date: 2026-07-01 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260701_0005"
down_revision: Union[str, Sequence[str], None] = "20260701_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("analysis_runs", sa.Column("thread_id", sa.String(length=120), nullable=True))
    op.add_column(
        "analysis_runs",
        sa.Column("execution_mode", sa.String(length=40), nullable=False, server_default="sync"),
    )
    op.add_column("analysis_runs", sa.Column("last_checkpoint_id", sa.String(length=120), nullable=True))
    op.add_column(
        "analysis_runs",
        sa.Column("resume_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(op.f("ix_analysis_runs_thread_id"), "analysis_runs", ["thread_id"], unique=False)

    op.create_table(
        "run_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("job_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_by", sa.String(length=120), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_run_jobs_project_id"), "run_jobs", ["project_id"], unique=False)
    op.create_index(op.f("ix_run_jobs_run_id"), "run_jobs", ["run_id"], unique=False)
    op.create_index(op.f("ix_run_jobs_status"), "run_jobs", ["status"], unique=False)

    op.create_table(
        "run_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_run_events_project_id"), "run_events", ["project_id"], unique=False)
    op.create_index(op.f("ix_run_events_run_id"), "run_events", ["run_id"], unique=False)

    op.create_table(
        "graph_checkpoints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=120), nullable=False),
        sa.Column("checkpoint_ns", sa.String(length=120), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=120), nullable=False),
        sa.Column("parent_checkpoint_id", sa.String(length=120), nullable=True),
        sa.Column("checkpoint_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("channel_values_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_graph_checkpoints_thread_id"), "graph_checkpoints", ["thread_id"], unique=False)
    op.create_index(
        op.f("ix_graph_checkpoints_checkpoint_id"),
        "graph_checkpoints",
        ["checkpoint_id"],
        unique=False,
    )
    op.create_index(
        "uq_graph_checkpoints_thread_ns_checkpoint",
        "graph_checkpoints",
        ["thread_id", "checkpoint_ns", "checkpoint_id"],
        unique=True,
    )

    op.create_table(
        "graph_checkpoint_writes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=120), nullable=False),
        sa.Column("checkpoint_ns", sa.String(length=120), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=120), nullable=False),
        sa.Column("task_id", sa.String(length=120), nullable=False),
        sa.Column("write_index", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=160), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("task_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_graph_checkpoint_writes_thread_id"),
        "graph_checkpoint_writes",
        ["thread_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_graph_checkpoint_writes_checkpoint_id"),
        "graph_checkpoint_writes",
        ["checkpoint_id"],
        unique=False,
    )
    op.create_index(
        "uq_graph_checkpoint_writes_key",
        "graph_checkpoint_writes",
        ["thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "write_index"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_graph_checkpoint_writes_key", table_name="graph_checkpoint_writes")
    op.drop_index(op.f("ix_graph_checkpoint_writes_checkpoint_id"), table_name="graph_checkpoint_writes")
    op.drop_index(op.f("ix_graph_checkpoint_writes_thread_id"), table_name="graph_checkpoint_writes")
    op.drop_table("graph_checkpoint_writes")

    op.drop_index("uq_graph_checkpoints_thread_ns_checkpoint", table_name="graph_checkpoints")
    op.drop_index(op.f("ix_graph_checkpoints_checkpoint_id"), table_name="graph_checkpoints")
    op.drop_index(op.f("ix_graph_checkpoints_thread_id"), table_name="graph_checkpoints")
    op.drop_table("graph_checkpoints")

    op.drop_index(op.f("ix_run_events_run_id"), table_name="run_events")
    op.drop_index(op.f("ix_run_events_project_id"), table_name="run_events")
    op.drop_table("run_events")

    op.drop_index(op.f("ix_run_jobs_status"), table_name="run_jobs")
    op.drop_index(op.f("ix_run_jobs_run_id"), table_name="run_jobs")
    op.drop_index(op.f("ix_run_jobs_project_id"), table_name="run_jobs")
    op.drop_table("run_jobs")

    op.drop_index(op.f("ix_analysis_runs_thread_id"), table_name="analysis_runs")
    op.drop_column("analysis_runs", "resume_count")
    op.drop_column("analysis_runs", "last_checkpoint_id")
    op.drop_column("analysis_runs", "execution_mode")
    op.drop_column("analysis_runs", "thread_id")
