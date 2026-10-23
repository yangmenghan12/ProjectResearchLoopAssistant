"""Personal assistant conversations and immutable source evidence packages."""
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from alembic import op

revision = "20261022_0015"
down_revision = "20260706_0014"
branch_labels = None
depends_on = None


def timestamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade():
    op.create_table("assistant_conversations",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("title", sa.String(160), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id")), *timestamps())
    op.create_index("ix_assistant_conversations_project_id", "assistant_conversations", ["project_id"])
    op.create_table("research_evidence_packages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("analysis_runs.id"), nullable=False),
        sa.Column("items_json", sa.JSON(), nullable=False), sa.Column("inventory_json", sa.JSON(), nullable=False),
        sa.Column("parent_id", sa.String(36)), *timestamps(),
        sa.UniqueConstraint("run_id", name="uq_research_evidence_run"))
    op.create_index("ix_research_evidence_packages_project_id", "research_evidence_packages", ["project_id"])
    op.create_index("ix_research_evidence_packages_run_id", "research_evidence_packages", ["run_id"])
    op.create_table("assistant_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("assistant_conversations.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False), sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("content", sa.Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("analysis_runs.id")),
        sa.Column("evidence_package_id", sa.String(36), sa.ForeignKey("research_evidence_packages.id")),
        sa.Column("citations_json", sa.JSON(), nullable=False),
        sa.Column("report_id", sa.String(36), sa.ForeignKey("report_snapshots.id")),
        sa.Column("error", sa.Text()), *timestamps(),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_assistant_message_sequence"))
    op.create_index("ix_assistant_messages_conversation_id", "assistant_messages", ["conversation_id"])
    op.create_index("ix_assistant_messages_run_id", "assistant_messages", ["run_id"])


def downgrade():
    op.drop_table("assistant_messages")
    op.drop_table("research_evidence_packages")
    op.drop_table("assistant_conversations")
