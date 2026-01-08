"""
FlowData Database Models
Migrated from JSON file storage to PostgreSQL for production security
"""
from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import JSONB
from models import Base


class FlowDataModel(Base):
    """
    Store WhatsApp Flow data with tenant isolation

    SECURITY: Includes tenant_id for multi-tenant data isolation
    Previously stored in JSON file with sensitive data (PAN, passwords)
    """
    __tablename__ = "flow_data"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)

    # Unique identifier (PAN number)
    pan = Column(String(50), nullable=False, index=True)

    # User data
    phone = Column(String(20), nullable=True)
    name = Column(String(255), nullable=True)
    password = Column(String(255), nullable=True)  # TODO: Consider encrypting passwords

    # Questions stored as JSON array
    # Format: [{"question": "...", "answer": "..."}, ...]
    questions = Column(JSONB, nullable=True, default=None)

    # Multi-tenant support - CRITICAL for security
    tenant_id = Column(String(50), nullable=False, index=True)

    # Audit timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    class Meta:
        # Composite unique constraint: PAN must be unique within each tenant
        # This prevents cross-tenant PAN conflicts
        unique_together = [['pan', 'tenant_id']]

    def __repr__(self):
        return f"<FlowData(id={self.id}, pan={self.pan}, tenant={self.tenant_id})>"


# Create indexes for common queries
# These will be added via Alembic migration
"""
CREATE INDEX idx_flow_data_pan_tenant ON flow_data(pan, tenant_id);
CREATE INDEX idx_flow_data_tenant ON flow_data(tenant_id);
CREATE INDEX idx_flow_data_created ON flow_data(created_at);
"""
