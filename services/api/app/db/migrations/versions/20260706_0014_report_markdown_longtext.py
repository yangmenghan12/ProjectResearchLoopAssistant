"""report markdown longtext

Revision ID: 20260706_0014
Revises: 20260702_0013
Create Date: 2026-07-06 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260706_0014"
down_revision: Union[str, Sequence[str], None] = "20260702_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("report_snapshots") as batch_op:
        batch_op.alter_column(
            "markdown_content",
            existing_type=sa.Text(),
            type_=mysql.LONGTEXT(),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("report_snapshots") as batch_op:
        batch_op.alter_column(
            "markdown_content",
            existing_type=mysql.LONGTEXT(),
            type_=sa.Text(),
            existing_nullable=False,
        )
