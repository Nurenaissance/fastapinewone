"""add manual_mode to contacts

Revision ID: 2026_01_09_manual_mode
Revises: 2026_01_09_auto_rules
Create Date: 2026-01-09 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2026_01_09_manual_mode'
down_revision: Union[str, None] = '2026_01_09_auto_rules'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - add manual_mode column to contacts_contact."""
    op.add_column('contacts_contact', sa.Column('manual_mode', sa.Boolean(), nullable=True, server_default='false'))


def downgrade() -> None:
    """Downgrade schema - remove manual_mode column from contacts_contact."""
    op.drop_column('contacts_contact', 'manual_mode')
