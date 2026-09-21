"""normalize llm profile temperature values

Revision ID: 20260701_0008
Revises: 20260701_0007
Create Date: 2026-07-01 17:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260701_0008"
down_revision: Union[str, Sequence[str], None] = "20260701_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NORMALIZE_SCALED_TEMPERATURE_SQL = """
UPDATE llm_profiles
SET temperature = temperature / 100
WHERE temperature > 2
"""


def upgrade() -> None:
    op.execute(NORMALIZE_SCALED_TEMPERATURE_SQL)


def downgrade() -> None:
    # 数据归一化不可安全反推：0.2 可能是原始值，也可能来自旧的 20 缩放值。
    pass
