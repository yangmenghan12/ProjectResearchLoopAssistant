"""source bundles

Revision ID: 20260702_0012
Revises: 20260702_0011
Create Date: 2026-07-02 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260702_0012"
down_revision: Union[str, Sequence[str], None] = "20260702_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "source_bundles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_source_bundles_project_id"),
        "source_bundles",
        ["project_id"],
        unique=False,
    )

    op.create_table(
        "source_bundle_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("bundle_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=True),
        sa.Column("input_type", sa.String(length=40), nullable=False),
        sa.Column("raw_value", sa.Text(), nullable=False),
        sa.Column("normalized_uri", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["bundle_id"], ["source_bundles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_source_bundle_items_bundle_id"),
        "source_bundle_items",
        ["bundle_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_source_bundle_items_project_id"),
        "source_bundle_items",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_source_bundle_items_source_id"),
        "source_bundle_items",
        ["source_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_source_bundle_items_source_id"), table_name="source_bundle_items")
    op.drop_index(op.f("ix_source_bundle_items_project_id"), table_name="source_bundle_items")
    op.drop_index(op.f("ix_source_bundle_items_bundle_id"), table_name="source_bundle_items")
    op.drop_table("source_bundle_items")
    op.drop_index(op.f("ix_source_bundles_project_id"), table_name="source_bundles")
    op.drop_table("source_bundles")
