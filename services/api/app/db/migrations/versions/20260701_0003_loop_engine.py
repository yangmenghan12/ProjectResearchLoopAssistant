"""loop engine schema

Revision ID: 20260701_0003
Revises: 20260701_0002
Create Date: 2026-07-01 00:03:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260701_0003"
down_revision: Union[str, Sequence[str], None] = "20260701_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("current_turn", sa.Integer(), nullable=False),
        sa.Column("max_turns", sa.Integer(), nullable=False),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_analysis_runs_project_id"), "analysis_runs", ["project_id"], unique=False)

    op.create_table(
        "loop_turns",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("node_name", sa.String(length=80), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("observation", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_loop_turns_project_id"), "loop_turns", ["project_id"], unique=False)
    op.create_index(op.f("ix_loop_turns_run_id"), "loop_turns", ["run_id"], unique=False)

    op.create_table(
        "evidence_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence_type", sa.String(length=80), nullable=False),
        sa.Column("reference", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_evidence_items_project_id"), "evidence_items", ["project_id"], unique=False)
    op.create_index(op.f("ix_evidence_items_run_id"), "evidence_items", ["run_id"], unique=False)
    op.create_index(op.f("ix_evidence_items_source_id"), "evidence_items", ["source_id"], unique=False)

    op.create_table(
        "findings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("impact_level", sa.String(length=40), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_findings_project_id"), "findings", ["project_id"], unique=False)
    op.create_index(op.f("ix_findings_run_id"), "findings", ["run_id"], unique=False)

    op.create_table(
        "risks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=40), nullable=False),
        sa.Column("mitigation", sa.Text(), nullable=False),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_risks_project_id"), "risks", ["project_id"], unique=False)
    op.create_index(op.f("ix_risks_run_id"), "risks", ["run_id"], unique=False)

    op.create_table(
        "questions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("impact", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_questions_project_id"), "questions", ["project_id"], unique=False)
    op.create_index(op.f("ix_questions_run_id"), "questions", ["run_id"], unique=False)

    op.create_table(
        "recommendations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("next_steps_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_recommendations_project_id"), "recommendations", ["project_id"], unique=False)
    op.create_index(op.f("ix_recommendations_run_id"), "recommendations", ["run_id"], unique=False)

    op.create_table(
        "quality_scores",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("turn_id", sa.String(length=36), nullable=True),
        sa.Column("overall_score", sa.Integer(), nullable=False),
        sa.Column("structure_completeness", sa.Integer(), nullable=False),
        sa.Column("evidence_coverage", sa.Integer(), nullable=False),
        sa.Column("codebase_coverage", sa.Integer(), nullable=False),
        sa.Column("risk_transparency", sa.Integer(), nullable=False),
        sa.Column("question_resolution", sa.Integer(), nullable=False),
        sa.Column("recommendation_confidence", sa.Integer(), nullable=False),
        sa.Column("score_delta", sa.Integer(), nullable=False),
        sa.Column("reasons_json", sa.JSON(), nullable=False),
        sa.Column("next_actions_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"]),
        sa.ForeignKeyConstraint(["turn_id"], ["loop_turns.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_quality_scores_project_id"), "quality_scores", ["project_id"], unique=False)
    op.create_index(op.f("ix_quality_scores_run_id"), "quality_scores", ["run_id"], unique=False)
    op.create_index(op.f("ix_quality_scores_turn_id"), "quality_scores", ["turn_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_quality_scores_turn_id"), table_name="quality_scores")
    op.drop_index(op.f("ix_quality_scores_run_id"), table_name="quality_scores")
    op.drop_index(op.f("ix_quality_scores_project_id"), table_name="quality_scores")
    op.drop_table("quality_scores")
    op.drop_index(op.f("ix_recommendations_run_id"), table_name="recommendations")
    op.drop_index(op.f("ix_recommendations_project_id"), table_name="recommendations")
    op.drop_table("recommendations")
    op.drop_index(op.f("ix_questions_run_id"), table_name="questions")
    op.drop_index(op.f("ix_questions_project_id"), table_name="questions")
    op.drop_table("questions")
    op.drop_index(op.f("ix_risks_run_id"), table_name="risks")
    op.drop_index(op.f("ix_risks_project_id"), table_name="risks")
    op.drop_table("risks")
    op.drop_index(op.f("ix_findings_run_id"), table_name="findings")
    op.drop_index(op.f("ix_findings_project_id"), table_name="findings")
    op.drop_table("findings")
    op.drop_index(op.f("ix_evidence_items_source_id"), table_name="evidence_items")
    op.drop_index(op.f("ix_evidence_items_run_id"), table_name="evidence_items")
    op.drop_index(op.f("ix_evidence_items_project_id"), table_name="evidence_items")
    op.drop_table("evidence_items")
    op.drop_index(op.f("ix_loop_turns_run_id"), table_name="loop_turns")
    op.drop_index(op.f("ix_loop_turns_project_id"), table_name="loop_turns")
    op.drop_table("loop_turns")
    op.drop_index(op.f("ix_analysis_runs_project_id"), table_name="analysis_runs")
    op.drop_table("analysis_runs")
