from pydantic import BaseModel, field_validator
from datetime import date as dt_date, time as dt_time, datetime as dt_datetime
from typing import Optional, Dict, Any

class ScheduledEventBase(BaseModel):
    type: str
    date: dt_date  # Required - when to send the message
    time: dt_time  # Required - what time to send
    value: Dict[str, Any]

    @field_validator('date')
    @classmethod
    def validate_date(cls, v):
        if v is None:
            raise ValueError('date is required for scheduling')
        return v

    @field_validator('time')
    @classmethod
    def validate_time(cls, v):
        if v is None:
            raise ValueError('time is required for scheduling')
        return v

class ScheduledEventCreate(ScheduledEventBase):
    pass

class ScheduledEventResponse(ScheduledEventBase):
    id: int
    status: str = "pending"
    retry_count: int = 0
    last_error: Optional[str] = None
    created_at: Optional[dt_datetime] = None
    updated_at: Optional[dt_datetime] = None
    executed_at: Optional[dt_datetime] = None

    class Config:
        from_attributes = True
