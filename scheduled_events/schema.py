from pydantic import BaseModel
from datetime import date as dt_date, time as dt_time, datetime as dt_datetime
from typing import Optional, Dict, Any

class ScheduledEventBase(BaseModel):
    type: str
    date: Optional[dt_date] = None
    time: Optional[dt_time] = None
    value: Dict[str, Any]

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
