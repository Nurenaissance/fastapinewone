"""add auto_rules to broadcast_groups

Revision ID: 2026_01_09_auto_rules
Revises: 2026_01_06_create_flow_data_table
Create Date: 2026-01-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2026_01_09_auto_rules'
down_revision: Union[str, None] = 'flow_data_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - add auto_rules column to broadcast_groups."""
    op.add_column('broadcast_groups', sa.Column('auto_rules', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema - remove auto_rules column from broadcast_groups."""
    op.drop_column('broadcast_groups', 'auto_rules')
