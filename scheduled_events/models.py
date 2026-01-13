from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, BigInteger, JSON, Date, Time
from sqlalchemy.orm import relationship
from config.database import Base
from datetime import datetime

class ScheduledEvent(Base):
    __tablename__ = "scheduled_events"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(String(50), nullable=False)
    value = Column(JSON, nullable=False)
    date = Column(Date, nullable=True)  # Stores the date of the event
    time = Column(Time, nullable=True)  # Stores the time of the event
    tenant_id = Column(String(50), ForeignKey("tenant_tenant.id"), nullable=True)

    # New fields for reliability tracking
    status = Column(String(20), default="pending", nullable=False)  # pending, processing, completed, failed
    retry_count = Column(Integer, default=0, nullable=False)
    max_retries = Column(Integer, default=3, nullable=False)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    executed_at = Column(DateTime, nullable=True)

    tenant = relationship("Tenant", back_populates="scheduled_events")
