"""create_flow_data_table

Revision ID: flow_data_001
Revises:
Create Date: 2026-01-06

SECURITY FIX: Migrate flowsAPI from JSON file to PostgreSQL database
- Adds tenant_id for multi-tenant isolation
- Creates proper indexes for performance
- Enables ACID transactions and proper backups
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision = 'flow_data_001'
down_revision = None  # Update this if you have other migrations
branch_labels = None
depends_on = None


def upgrade():
    """Create flow_data table with tenant isolation"""

    # Create flow_data table
    op.create_table(
        'flow_data',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('pan', sa.String(length=50), nullable=False),
        sa.Column('phone', sa.String(length=20), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=True),
        sa.Column('password', sa.String(length=255), nullable=True),
        sa.Column('questions', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('tenant_id', sa.String(length=50), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes for performance
    op.create_index('idx_flow_data_pan_tenant', 'flow_data', ['pan', 'tenant_id'], unique=False)
    op.create_index('idx_flow_data_tenant', 'flow_data', ['tenant_id'], unique=False)
    op.create_index('idx_flow_data_created', 'flow_data', ['created_at'], unique=False)

    # Create unique constraint: PAN must be unique within each tenant
    op.create_index('idx_flow_data_pan_tenant_unique', 'flow_data', ['pan', 'tenant_id'], unique=True)

    print("✅ Created flow_data table with tenant isolation")


def downgrade():
    """Drop flow_data table and all indexes"""

    # Drop indexes first
    op.drop_index('idx_flow_data_pan_tenant_unique', table_name='flow_data')
    op.drop_index('idx_flow_data_created', table_name='flow_data')
    op.drop_index('idx_flow_data_tenant', table_name='flow_data')
    op.drop_index('idx_flow_data_pan_tenant', table_name='flow_data')

    # Drop table
    op.drop_table('flow_data')

    print("⚠️ Dropped flow_data table - data loss!")
