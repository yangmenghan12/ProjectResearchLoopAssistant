"""research profile

Revision ID: 20260702_0011
Revises: 20260702_0010
Create Date: 2026-07-02 17:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision: str = "20260702_0011"
down_revision: Union[str, Sequence[str], None] = "20260702_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def normalize_audit_event_sequences() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        text(
            """
            SELECT id, project_id
            FROM audit_events
            ORDER BY project_id ASC, created_at ASC, id ASC
            """
        )
    ).mappings()

    current_project_id: object = object()
    next_sequence = 0
    for row in rows:
        if row["project_id"] != current_project_id:
            current_project_id = row["project_id"]
            next_sequence = 1
        else:
            next_sequence += 1

        connection.execute(
            text("UPDATE audit_events SET sequence = :sequence WHERE id = :id"),
            {"sequence": next_sequence, "id": row["id"]},
        )


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "research_profile",
            sa.String(length=80),
            nullable=False,
            server_default="quick_onboarding",
        ),
    )
    op.alter_column("projects", "research_profile", server_default=None)
    op.add_column(
        "audit_events",
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    normalize_audit_event_sequences()
    op.alter_column("audit_events", "sequence", server_default=None)
    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.create_unique_constraint(
            "uq_audit_events_project_sequence",
            ["project_id", "sequence"],
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.drop_constraint(
            "uq_audit_events_project_sequence",
            type_="unique",
        )
    op.drop_column("audit_events", "sequence")
    op.drop_column("projects", "research_profile")
