"""v4 closeout hardening

Revision ID: 20260702_0010
Revises: 20260701_0009
Create Date: 2026-07-02 16:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


revision: str = "20260702_0010"
down_revision: Union[str, Sequence[str], None] = "20260701_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def normalize_run_event_sequences() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        text(
            """
            SELECT id, run_id
            FROM run_events
            ORDER BY run_id ASC, sequence ASC, created_at ASC, id ASC
            """
        )
    ).mappings()

    current_run_id: str | None = None
    next_sequence = 0
    for row in rows:
        if row["run_id"] != current_run_id:
            current_run_id = row["run_id"]
            next_sequence = 1
        else:
            next_sequence += 1

        connection.execute(
            text("UPDATE run_events SET sequence = :sequence WHERE id = :id"),
            {"sequence": next_sequence, "id": row["id"]},
        )


def upgrade() -> None:
    normalize_run_event_sequences()
    with op.batch_alter_table("run_events") as batch_op:
        batch_op.create_unique_constraint(
            "uq_run_events_run_sequence",
            ["run_id", "sequence"],
        )


def downgrade() -> None:
    with op.batch_alter_table("run_events") as batch_op:
        batch_op.drop_constraint(
            "uq_run_events_run_sequence",
            type_="unique",
        )
