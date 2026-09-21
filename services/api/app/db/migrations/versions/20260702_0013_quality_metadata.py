"""quality metadata

Revision ID: 20260702_0013
Revises: 20260702_0012
Create Date: 2026-07-02 20:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260702_0013"
down_revision: Union[str, Sequence[str], None] = "20260702_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("quality_scores", sa.Column("metadata_json", sa.JSON(), nullable=True))
    op.execute(sa.text("UPDATE quality_scores SET metadata_json = '{}' WHERE metadata_json IS NULL"))
    with op.batch_alter_table("quality_scores") as batch_op:
        batch_op.alter_column("metadata_json", existing_type=sa.JSON(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("quality_scores") as batch_op:
        batch_op.drop_column("metadata_json")
