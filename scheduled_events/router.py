from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import orm, and_, or_
from config.database import get_db, SessionLocal
from .models import ScheduledEvent
from typing import List, Optional
from .schema import ScheduledEventCreate, ScheduledEventResponse, ScheduledEventBase
from datetime import datetime, timedelta, time as dt_time
import requests, threading
from collections import deque
import json
import logging
import pytz

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

# Use proper timezone
IST = pytz.timezone('Asia/Kolkata')

# Scheduler control
scheduler_running = threading.Event()
scheduler_running.set()

# ===================== HELPER FUNCTIONS =====================
def get_ist_now():
    """Get current time in IST using proper timezone handling"""
    return datetime.now(IST)

def process_single_event(event: ScheduledEvent, db: orm.Session) -> bool:
    """Process a single scheduled event with proper error handling and status tracking"""
    try:
        # Mark as processing
        event.status = "processing"
        event.updated_at = datetime.utcnow()
        db.commit()

        body = event.value
        if isinstance(body, str):
            body = json.loads(body)

        logger.info(f"[Event {event.id}] Sending to whatsappbotserver...")

        response = requests.post(
            'https://whatsappbotserver.azurewebsites.net/send-template',
            json=body,
            timeout=60  # Increased timeout
        )

        if response.status_code == 200:
            # Mark as completed
            event.status = "completed"
            event.executed_at = datetime.utcnow()
            event.updated_at = datetime.utcnow()
            db.commit()
            logger.info(f"[Event {event.id}] Processed successfully")
            return True
        else:
            raise Exception(f"HTTP {response.status_code}: {response.text[:200]}")

    except Exception as e:
        error_msg = str(e)[:500]
        event.retry_count += 1
        event.last_error = error_msg
        event.updated_at = datetime.utcnow()

        if event.retry_count >= event.max_retries:
            event.status = "failed"
            logger.error(f"[Event {event.id}] Failed permanently after {event.retry_count} retries: {error_msg}")
        else:
            event.status = "pending"  # Will be retried
            logger.warning(f"[Event {event.id}] Failed (attempt {event.retry_count}/{event.max_retries}): {error_msg}")

        db.commit()
        return False


def process_due_events():
    """Process all events that are due (including past due events)"""
    db = SessionLocal()
    try:
        now_ist = get_ist_now()
        today = now_ist.date()
        current_time = now_ist.time()

        logger.info(f"[Scheduler] Checking for due events at {now_ist.strftime('%Y-%m-%d %H:%M:%S')} IST")

        # Find events that are due:
        # 1. Events scheduled for today with time <= current time
        # 2. Events from past dates that were never processed
        # Status must be 'pending' and retry_count < max_retries
        due_events = db.query(ScheduledEvent).filter(
            ScheduledEvent.status == "pending",
            or_(
                # Past dates (missed events)
                ScheduledEvent.date < today,
                # Today's events that are due
                and_(
                    ScheduledEvent.date == today,
                    ScheduledEvent.time <= current_time
                )
            )
        ).order_by(ScheduledEvent.date, ScheduledEvent.time).all()

        if due_events:
            logger.info(f"[Scheduler] Found {len(due_events)} due events to process")
            for event in due_events:
                if not scheduler_running.is_set():
                    logger.info("[Scheduler] Stopping due to shutdown signal")
                    break
                logger.info(f"[Scheduler] Processing event {event.id} (scheduled: {event.date} {event.time})")
                process_single_event(event, db)
        else:
            logger.debug("[Scheduler] No due events found")

    except Exception as e:
        logger.error(f"[Scheduler] Error in process_due_events: {e}")
    finally:
        db.close()


def scheduler_loop():
    """Main scheduler loop - checks for due events every 30 seconds"""
    logger.info("[Scheduler] Started - checking every 30 seconds")

    # Process any missed events on startup
    logger.info("[Scheduler] Processing any missed events from previous runs...")
    process_due_events()

    while scheduler_running.is_set():
        try:
            # Wait 30 seconds between checks
            for _ in range(30):
                if not scheduler_running.is_set():
                    break
                threading.Event().wait(1)  # Non-blocking 1 second wait

            if scheduler_running.is_set():
                process_due_events()

        except Exception as e:
            logger.error(f"[Scheduler] Error in scheduler loop: {e}")
            threading.Event().wait(10)  # Wait longer on error


# ===================== SCHEDULER STARTUP =====================
@router.on_event("startup")
def startup_event():
    """Start the scheduler on FastAPI startup"""
    try:
        logger.info("[Scheduler] Initializing...")
        scheduler_running.set()
        scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True, name="ScheduledEventsScheduler")
        scheduler_thread.start()
        logger.info("[Scheduler] Started successfully")
    except Exception as e:
        logger.error(f"[Scheduler] Error starting: {e}")


@router.on_event("shutdown")
def shutdown_event():
    """Stop the scheduler on FastAPI shutdown"""
    logger.info("[Scheduler] Shutting down...")
    scheduler_running.clear()


# ===================== ROUTES =====================
@router.get("/")
def read_root():
    return {"message": "FastAPI server with scheduled task is running"}


@router.post("/scheduled-events/", response_model=ScheduledEventResponse)
def create_scheduled_event(
    event: ScheduledEventCreate,
    x_tenant_id: Optional[str] = Header(None),
    db: orm.Session = Depends(get_db)
):
    """Create scheduled event with proper error handling"""
    try:
        if not x_tenant_id:
            raise HTTPException(status_code=400, detail="Tenant ID is required in the headers.")

        # Validate event data
        event_dict = event.dict()

        # Ensure value is properly formatted
        if isinstance(event_dict.get('value'), str):
            try:
                json.loads(event_dict['value'])  # Validate JSON
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid JSON in value field")

        db_event = ScheduledEvent(
            **event_dict,
            tenant_id=x_tenant_id,
            status="pending",
            retry_count=0,
            max_retries=3
        )
        db.add(db_event)
        db.commit()
        db.refresh(db_event)

        logger.info(f"Created scheduled event {db_event.id} for tenant {x_tenant_id} at {db_event.date} {db_event.time}")
        return db_event

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating scheduled event: {e}")
        raise HTTPException(status_code=500, detail="Internal server error creating event")


@router.get("/scheduled-events/{event_id}/", response_model=ScheduledEventResponse)
def get_scheduled_event(event_id: int, db: orm.Session = Depends(get_db)):
    """Get scheduled event with error handling"""
    try:
        db_event = db.query(ScheduledEvent).filter(ScheduledEvent.id == event_id).first()
        if db_event is None:
            raise HTTPException(status_code=404, detail="Scheduled event not found")
        return db_event
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting scheduled event {event_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/scheduled-events/", response_model=List[ScheduledEventResponse])
def list_scheduled_events(
    x_tenant_id: Optional[str] = Header(None),
    db: orm.Session = Depends(get_db)
):
    """List scheduled events with error handling"""
    try:
        if not x_tenant_id:
            raise HTTPException(status_code=400, detail="Tenant ID is required")

        events = db.query(ScheduledEvent).filter(ScheduledEvent.tenant_id == x_tenant_id).all()
        return events
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing scheduled events for tenant {x_tenant_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/scheduled-events/{event_id}/", status_code=204)
def delete_scheduled_event(event_id: int, db: orm.Session = Depends(get_db)):
    """Delete scheduled event with error handling"""
    try:
        db_event = db.query(ScheduledEvent).filter(ScheduledEvent.id == event_id).first()
        if db_event is None:
            raise HTTPException(status_code=404, detail="Scheduled event not found")

        db.delete(db_event)
        db.commit()

        logger.info(f"Deleted scheduled event {event_id}")
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting scheduled event {event_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put("/scheduled-events-editing/{event_id}/", response_model=ScheduledEventResponse)
def update_scheduled_event(
    event_id: int,
    updated_event: ScheduledEventBase,
    x_tenant_id: Optional[str] = Header(None),
    db: orm.Session = Depends(get_db)
):
    """Update scheduled event with error handling"""
    try:
        if not x_tenant_id:
            raise HTTPException(status_code=400, detail="Tenant ID is required in the headers.")

        db_event = db.query(ScheduledEvent).filter(
            ScheduledEvent.id == event_id,
            ScheduledEvent.tenant_id == x_tenant_id
        ).first()

        if not db_event:
            raise HTTPException(status_code=404, detail="Scheduled event not found or unauthorized")

        # Validate value field if it's a string
        if isinstance(updated_event.value, str):
            try:
                json.loads(updated_event.value)  # Validate JSON
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid JSON in value field")

        # Update fields
        db_event.type = updated_event.type
        db_event.date = updated_event.date
        db_event.time = updated_event.time
        db_event.value = updated_event.value
        # Reset status if updating a failed event
        if db_event.status == "failed":
            db_event.status = "pending"
            db_event.retry_count = 0
            db_event.last_error = None

        db.commit()
        db.refresh(db_event)

        logger.info(f"Updated scheduled event {event_id}")
        return db_event

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating scheduled event {event_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/events/group", response_model=dict)
def group_events_for_next_day(tenant_id: str = Header(...), db: orm.Session = Depends(get_db)):
    """Group events with proper error handling"""
    try:
        logger.info(f"Grouping today's and tomorrow's events for tenant_id: {tenant_id}")

        # Get current IST date
        now_ist = get_ist_now()
        today_date = now_ist.date()
        tomorrow_date = (now_ist + timedelta(days=1)).date()

        # Query events for today and tomorrow, only for this tenant
        events = db.query(ScheduledEvent).filter(
            ScheduledEvent.date.in_([today_date, tomorrow_date]),
            ScheduledEvent.tenant_id == tenant_id,
            ScheduledEvent.status == "pending"
        ).all()

        if not events:
            return {"message": "No events scheduled for today or tomorrow for this tenant."}

        grouped_events = {}

        for event in events:
            try:
                value_data = event.value
                if isinstance(value_data, str):
                    value_data = json.loads(value_data)

                template_name = value_data.get("template", {}).get("name")

                if template_name:
                    key = (template_name, event.date)
                    if key not in grouped_events:
                        grouped_events[key] = []
                    grouped_events[key].append({
                        "id": event.id,
                        "time": event.time,
                        "value": value_data,
                        "date": event.date
                    })
            except (json.JSONDecodeError, AttributeError) as e:
                logger.error(f"Error processing event {event.id}: {e}")
                continue

        result = []

        for (template_name, event_date), event_list in grouped_events.items():
            if len(event_list) <= 1:
                continue  # no need to merge if only 1

            try:
                latest_event = max(event_list, key=lambda x: x["time"])
                latest_time = latest_event["time"]
                template = latest_event["value"].get("template")
                business_id = latest_event["value"].get("business_phone_number_id")

                # Merge phone numbers from all events
                all_phone_numbers = set()
                for evt in event_list:
                    phone_numbers = evt["value"].get("phoneNumbers", [])
                    all_phone_numbers.update(phone_numbers)

                merged_value = {
                    "bg_id": "null",
                    "template": template,
                    "business_phone_number_id": business_id,
                    "phoneNumbers": list(all_phone_numbers)
                }

                # Create the merged event
                merged_event = ScheduledEvent(
                    date=event_date,
                    time=latest_time,
                    type="Template",
                    value=merged_value,
                    tenant_id=tenant_id,
                    status="pending",
                    retry_count=0,
                    max_retries=3
                )

                db.add(merged_event)
                db.flush()  # Use flush instead of commit to get the ID

                # Delete old events
                event_ids_to_delete = [e["id"] for e in event_list]
                db.query(ScheduledEvent).filter(ScheduledEvent.id.in_(event_ids_to_delete)).delete(synchronize_session=False)

                db.commit()  # Single commit for all operations

                result.append({
                    "merged_event_id": merged_event.id,
                    "template_name": template_name,
                    "event_date": str(event_date),
                    "deleted_event_ids": event_ids_to_delete
                })

            except Exception as e:
                logger.error(f"Error merging events for template {template_name}: {e}")
                db.rollback()
                continue

        return {"message": "Events grouped and merged successfully.", "results": result}

    except Exception as e:
        logger.error(f"Error in group_events_for_next_day: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error during event grouping")


# ===================== HEALTH & ADMIN ENDPOINTS =====================
@router.get("/health/scheduler")
def scheduler_health():
    """Health check endpoint for scheduler"""
    return {
        "status": "running" if scheduler_running.is_set() else "stopped",
        "timezone": "Asia/Kolkata",
        "current_time_ist": get_ist_now().strftime("%Y-%m-%d %H:%M:%S")
    }


@router.post("/scheduled-events/{event_id}/retry")
def retry_failed_event(event_id: int, db: orm.Session = Depends(get_db)):
    """Manually retry a failed event"""
    try:
        db_event = db.query(ScheduledEvent).filter(ScheduledEvent.id == event_id).first()
        if db_event is None:
            raise HTTPException(status_code=404, detail="Scheduled event not found")

        if db_event.status != "failed":
            raise HTTPException(status_code=400, detail=f"Event is not failed (status: {db_event.status})")

        # Reset for retry
        db_event.status = "pending"
        db_event.retry_count = 0
        db_event.last_error = None
        db.commit()

        logger.info(f"Reset failed event {event_id} for retry")
        return {"message": f"Event {event_id} has been reset for retry"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrying event {event_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/scheduled-events/failed/", response_model=List[ScheduledEventResponse])
def list_failed_events(
    x_tenant_id: Optional[str] = Header(None),
    db: orm.Session = Depends(get_db)
):
    """List all failed scheduled events for debugging"""
    try:
        query = db.query(ScheduledEvent).filter(ScheduledEvent.status == "failed")
        if x_tenant_id:
            query = query.filter(ScheduledEvent.tenant_id == x_tenant_id)
        return query.all()
    except Exception as e:
        logger.error(f"Error listing failed events: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
