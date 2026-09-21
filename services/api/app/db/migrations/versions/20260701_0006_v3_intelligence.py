"""v3 intelligence runtime schema

Revision ID: 20260701_0006
Revises: 20260701_0005
Create Date: 2026-07-01 13:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260701_0006"
down_revision: Union[str, Sequence[str], None] = "20260701_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("task_type", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("api_key_env_name", sa.String(length=120), nullable=True),
        sa.Column("api_key_required", sa.Integer(), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("daily_budget_cents", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_llm_profiles_task_type"), "llm_profiles", ["task_type"], unique=False)

    op.create_table(
        "llm_calls",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("report_id", sa.String(length=36), nullable=True),
        sa.Column("profile_id", sa.String(length=36), nullable=True),
        sa.Column("task_type", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("prompt_hash", sa.String(length=80), nullable=False),
        sa.Column("input_refs_json", sa.JSON(), nullable=False),
        sa.Column("output_ref", sa.Text(), nullable=True),
        sa.Column("usage_json", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["llm_profiles.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["report_id"], ["report_snapshots.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_llm_calls_project_id"), "llm_calls", ["project_id"], unique=False)
    op.create_index(op.f("ix_llm_calls_run_id"), "llm_calls", ["run_id"], unique=False)
    op.create_index(op.f("ix_llm_calls_report_id"), "llm_calls", ["report_id"], unique=False)
    op.create_index(op.f("ix_llm_calls_profile_id"), "llm_calls", ["profile_id"], unique=False)
    op.create_index(op.f("ix_llm_calls_task_type"), "llm_calls", ["task_type"], unique=False)

    op.create_table(
        "llm_analysis_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("call_id", sa.String(length=36), nullable=True),
        sa.Column("artifact_type", sa.String(length=80), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("validation_result_json", sa.JSON(), nullable=False),
        sa.Column("accepted", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["call_id"], ["llm_calls.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_llm_analysis_artifacts_project_id"),
        "llm_analysis_artifacts",
        ["project_id"],
        unique=False,
    )
    op.create_index(op.f("ix_llm_analysis_artifacts_run_id"), "llm_analysis_artifacts", ["run_id"], unique=False)
    op.create_index(op.f("ix_llm_analysis_artifacts_call_id"), "llm_analysis_artifacts", ["call_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_llm_analysis_artifacts_call_id"), table_name="llm_analysis_artifacts")
    op.drop_index(op.f("ix_llm_analysis_artifacts_run_id"), table_name="llm_analysis_artifacts")
    op.drop_index(op.f("ix_llm_analysis_artifacts_project_id"), table_name="llm_analysis_artifacts")
    op.drop_table("llm_analysis_artifacts")

    op.drop_index(op.f("ix_llm_calls_task_type"), table_name="llm_calls")
    op.drop_index(op.f("ix_llm_calls_profile_id"), table_name="llm_calls")
    op.drop_index(op.f("ix_llm_calls_report_id"), table_name="llm_calls")
    op.drop_index(op.f("ix_llm_calls_run_id"), table_name="llm_calls")
    op.drop_index(op.f("ix_llm_calls_project_id"), table_name="llm_calls")
    op.drop_table("llm_calls")

    op.drop_index(op.f("ix_llm_profiles_task_type"), table_name="llm_profiles")
    op.drop_table("llm_profiles")
