from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import orm, and_, or_, text
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
import time
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

# Use proper timezone
IST = pytz.timezone('Asia/Kolkata')

# Scheduler control
scheduler_running = threading.Event()
scheduler_running.set()

# Scheduler thread reference for health monitoring
scheduler_thread_ref = None
scheduler_thread_lock = threading.Lock()

# Configuration
POLLING_INTERVAL_SECONDS = int(os.getenv('SCHEDULER_POLLING_INTERVAL', '10'))  # Reduced from 30 to 10 seconds
STALE_PROCESSING_TIMEOUT_MINUTES = int(os.getenv('STALE_PROCESSING_TIMEOUT', '5'))  # Reset stuck events after 5 minutes
INSTANCE_ID = os.getenv('WEBSITE_INSTANCE_ID', os.getenv('HOSTNAME', 'default'))

# ===================== HELPER FUNCTIONS =====================
def get_ist_now():
    """Get current time in IST using proper timezone handling"""
    return datetime.now(IST)


def recover_stale_processing_events(db: orm.Session) -> int:
    """
    Recover events that are stuck in 'processing' status for too long.
    This handles cases where the app crashed while processing an event.
    Returns the number of events recovered.
    """
    try:
        # Calculate the cutoff time (events processing for more than X minutes are considered stuck)
        cutoff_time = datetime.utcnow() - timedelta(minutes=STALE_PROCESSING_TIMEOUT_MINUTES)

        # Find stuck events
        stuck_events = db.query(ScheduledEvent).filter(
            ScheduledEvent.status == "processing",
            ScheduledEvent.updated_at < cutoff_time
        ).all()

        recovered_count = 0
        for event in stuck_events:
            event.status = "pending"
            event.retry_count += 1  # Count this as a retry attempt
            event.last_error = f"Recovered from stuck 'processing' state after {STALE_PROCESSING_TIMEOUT_MINUTES} minutes (instance: {INSTANCE_ID})"
            event.updated_at = datetime.utcnow()

            if event.retry_count >= event.max_retries:
                event.status = "failed"
                logger.error(f"[Recovery] Event {event.id} marked as failed after recovery (max retries reached)")
            else:
                logger.warning(f"[Recovery] Event {event.id} recovered from stuck state (attempt {event.retry_count}/{event.max_retries})")

            recovered_count += 1

        if recovered_count > 0:
            db.commit()
            logger.info(f"[Recovery] Recovered {recovered_count} stuck events")

        return recovered_count

    except Exception as e:
        logger.error(f"[Recovery] Error recovering stale events: {e}")
        db.rollback()
        return 0


def acquire_event_lock(event: ScheduledEvent, db: orm.Session) -> bool:
    """
    Try to acquire a lock on an event using optimistic locking.
    Returns True if lock acquired, False otherwise.
    This prevents duplicate processing in scaled deployments.
    """
    try:
        # Use optimistic locking - only update if status is still pending
        result = db.execute(
            text("""
                UPDATE scheduled_events
                SET status = 'processing', updated_at = :now
                WHERE id = :event_id AND status = 'pending'
            """),
            {"event_id": event.id, "now": datetime.utcnow()}
        )
        db.commit()

        # If no rows were affected, another instance already picked it up
        if result.rowcount == 0:
            logger.debug(f"[Lock] Event {event.id} already being processed by another instance")
            return False

        # Refresh the event object to get updated status
        db.refresh(event)
        return True

    except Exception as e:
        logger.error(f"[Lock] Error acquiring lock for event {event.id}: {e}")
        db.rollback()
        return False


def process_single_event(event: ScheduledEvent, db: orm.Session) -> bool:
    """Process a single scheduled event with proper error handling and status tracking.
    Note: The event should already be marked as 'processing' by acquire_event_lock()
    """
    try:
        # Verify event is in processing state
        if event.status != "processing":
            logger.warning(f"[Event {event.id}] Unexpected status '{event.status}', skipping")
            return False

        body = event.value
        if isinstance(body, str):
            body = json.loads(body)

        logger.info(f"[Event {event.id}] Sending to whatsappbotserver (instance: {INSTANCE_ID})...")

        # Use session with retry logic for network resilience
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(max_retries=2)
        session.mount('https://', adapter)

        response = session.post(
            'https://whatsappbotserver.azurewebsites.net/send-template',
            json=body,
            timeout=60
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

    except requests.exceptions.Timeout as e:
        error_msg = f"Request timeout after 60s: {str(e)[:200]}"
        logger.error(f"[Event {event.id}] {error_msg}")
        return handle_event_failure(event, db, error_msg)

    except requests.exceptions.ConnectionError as e:
        error_msg = f"Connection error: {str(e)[:200]}"
        logger.error(f"[Event {event.id}] {error_msg}")
        return handle_event_failure(event, db, error_msg)

    except Exception as e:
        error_msg = str(e)[:500]
        return handle_event_failure(event, db, error_msg)


def handle_event_failure(event: ScheduledEvent, db: orm.Session, error_msg: str) -> bool:
    """Handle event failure with proper retry logic"""
    try:
        event.retry_count += 1
        event.last_error = f"{error_msg} (instance: {INSTANCE_ID})"
        event.updated_at = datetime.utcnow()

        if event.retry_count >= event.max_retries:
            event.status = "failed"
            logger.error(f"[Event {event.id}] Failed permanently after {event.retry_count} retries: {error_msg}")
        else:
            event.status = "pending"  # Will be retried
            logger.warning(f"[Event {event.id}] Failed (attempt {event.retry_count}/{event.max_retries}): {error_msg}")

        db.commit()
        return False
    except Exception as e:
        logger.error(f"[Event {event.id}] Error handling failure: {e}")
        db.rollback()
        return False


def process_due_events():
    """Process all events that are due (including past due events)"""
    db = SessionLocal()
    try:
        now_ist = get_ist_now()
        today = now_ist.date()
        current_time = now_ist.time()

        logger.info(f"[Scheduler] Checking for due events at {now_ist.strftime('%Y-%m-%d %H:%M:%S')} IST (instance: {INSTANCE_ID})")

        # Step 1: Recover any events stuck in "processing" state
        recover_stale_processing_events(db)

        # Step 2: Find events that are due:
        # 1. Events scheduled for today with time <= current time
        # 2. Events from past dates that were never processed
        # Status must be 'pending' and retry_count < max_retries
        due_events = db.query(ScheduledEvent).filter(
            ScheduledEvent.status == "pending",
            ScheduledEvent.date.isnot(None),  # Ensure date is not null
            ScheduledEvent.time.isnot(None),  # Ensure time is not null
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
            processed_count = 0
            skipped_count = 0

            for event in due_events:
                if not scheduler_running.is_set():
                    logger.info("[Scheduler] Stopping due to shutdown signal")
                    break

                # Try to acquire lock on this event (prevents duplicate processing)
                if acquire_event_lock(event, db):
                    logger.info(f"[Scheduler] Processing event {event.id} (scheduled: {event.date} {event.time})")
                    process_single_event(event, db)
                    processed_count += 1
                else:
                    skipped_count += 1

            logger.info(f"[Scheduler] Cycle complete: processed={processed_count}, skipped={skipped_count}")
        else:
            logger.debug("[Scheduler] No due events found")

    except Exception as e:
        logger.error(f"[Scheduler] Error in process_due_events: {e}")
        import traceback
        logger.error(f"[Scheduler] Traceback: {traceback.format_exc()}")
    finally:
        db.close()


def scheduler_loop():
    """Main scheduler loop - checks for due events at configured interval"""
    global scheduler_thread_ref
    logger.info(f"[Scheduler] Started - checking every {POLLING_INTERVAL_SECONDS} seconds (instance: {INSTANCE_ID})")

    # Process any missed events on startup
    logger.info("[Scheduler] Processing any missed events from previous runs...")
    process_due_events()

    consecutive_errors = 0
    max_consecutive_errors = 5

    while scheduler_running.is_set():
        try:
            # Wait for configured interval between checks
            for _ in range(POLLING_INTERVAL_SECONDS):
                if not scheduler_running.is_set():
                    break
                time.sleep(1)  # Non-blocking 1 second wait

            if scheduler_running.is_set():
                process_due_events()
                consecutive_errors = 0  # Reset on success

        except Exception as e:
            consecutive_errors += 1
            logger.error(f"[Scheduler] Error in scheduler loop (consecutive: {consecutive_errors}): {e}")
            import traceback
            logger.error(f"[Scheduler] Traceback: {traceback.format_exc()}")

            if consecutive_errors >= max_consecutive_errors:
                logger.critical(f"[Scheduler] Too many consecutive errors ({consecutive_errors}), backing off for 60 seconds")
                time.sleep(60)
                consecutive_errors = 0  # Reset after backoff
            else:
                time.sleep(10)  # Wait longer on error

    logger.info("[Scheduler] Loop ended")


def ensure_scheduler_running():
    """Ensure the scheduler thread is running, restart if needed"""
    global scheduler_thread_ref

    with scheduler_thread_lock:
        if scheduler_thread_ref is None or not scheduler_thread_ref.is_alive():
            logger.warning("[Scheduler] Thread not running, starting...")
            scheduler_running.set()
            scheduler_thread_ref = threading.Thread(
                target=scheduler_loop,
                daemon=True,
                name="ScheduledEventsScheduler"
            )
            scheduler_thread_ref.start()
            logger.info("[Scheduler] Thread restarted successfully")
            return True
        return False


# ===================== SCHEDULER STARTUP =====================
@router.on_event("startup")
def startup_event():
    """Start the scheduler on FastAPI startup"""
    global scheduler_thread_ref

    try:
        logger.info(f"[Scheduler] Initializing... (instance: {INSTANCE_ID})")
        scheduler_running.set()

        with scheduler_thread_lock:
            scheduler_thread_ref = threading.Thread(
                target=scheduler_loop,
                daemon=True,
                name="ScheduledEventsScheduler"
            )
            scheduler_thread_ref.start()

        logger.info("[Scheduler] Started successfully")
    except Exception as e:
        logger.error(f"[Scheduler] Error starting: {e}")
        import traceback
        logger.error(f"[Scheduler] Traceback: {traceback.format_exc()}")


@router.on_event("shutdown")
def shutdown_event():
    """Stop the scheduler on FastAPI shutdown"""
    global scheduler_thread_ref

    logger.info("[Scheduler] Shutting down...")
    scheduler_running.clear()

    # Wait for thread to finish (with timeout)
    if scheduler_thread_ref and scheduler_thread_ref.is_alive():
        scheduler_thread_ref.join(timeout=5)
        if scheduler_thread_ref.is_alive():
            logger.warning("[Scheduler] Thread did not stop gracefully")
        else:
            logger.info("[Scheduler] Thread stopped successfully")


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
    """Create scheduled event with proper error handling and validation"""
    try:
        if not x_tenant_id:
            raise HTTPException(status_code=400, detail="Tenant ID is required in the headers.")

        # Validate event data
        event_dict = event.dict()

        # Validate date and time are provided
        if not event_dict.get('date') or not event_dict.get('time'):
            raise HTTPException(status_code=400, detail="Both date and time are required for scheduling")

        # Check if the scheduled time is in the past
        now_ist = get_ist_now()
        scheduled_datetime = datetime.combine(event_dict['date'], event_dict['time'])
        scheduled_datetime_ist = IST.localize(scheduled_datetime)

        if scheduled_datetime_ist < now_ist:
            # Allow events scheduled for up to 5 minutes in the past (in case of minor clock differences)
            grace_period = timedelta(minutes=5)
            if scheduled_datetime_ist < now_ist - grace_period:
                logger.warning(f"Event scheduled for past time: {scheduled_datetime_ist} (current: {now_ist})")
                # Still allow it - it will be processed immediately

        # Ensure value is properly formatted
        if isinstance(event_dict.get('value'), str):
            try:
                json.loads(event_dict['value'])  # Validate JSON
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid JSON in value field")

        # Validate value has required fields for sending template
        value_data = event_dict.get('value', {})
        if not value_data.get('template'):
            logger.warning(f"Event created without template field in value")
        if not value_data.get('phoneNumbers') and not value_data.get('phone_numbers'):
            logger.warning(f"Event created without phone numbers in value")

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

        logger.info(f"Created scheduled event {db_event.id} for tenant {x_tenant_id} at {db_event.date} {db_event.time} IST")
        return db_event

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating scheduled event: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
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
def scheduler_health(db: orm.Session = Depends(get_db)):
    """Health check endpoint for scheduler with diagnostic info"""
    global scheduler_thread_ref

    # Check thread status
    thread_alive = scheduler_thread_ref is not None and scheduler_thread_ref.is_alive()

    # Auto-recover if thread is dead
    was_restarted = False
    if not thread_alive and scheduler_running.is_set():
        was_restarted = ensure_scheduler_running()
        thread_alive = scheduler_thread_ref is not None and scheduler_thread_ref.is_alive()

    # Get pending event counts
    try:
        pending_count = db.query(ScheduledEvent).filter(ScheduledEvent.status == "pending").count()
        processing_count = db.query(ScheduledEvent).filter(ScheduledEvent.status == "processing").count()
        failed_count = db.query(ScheduledEvent).filter(ScheduledEvent.status == "failed").count()

        # Check for stuck processing events
        cutoff_time = datetime.utcnow() - timedelta(minutes=STALE_PROCESSING_TIMEOUT_MINUTES)
        stuck_count = db.query(ScheduledEvent).filter(
            ScheduledEvent.status == "processing",
            ScheduledEvent.updated_at < cutoff_time
        ).count()
    except Exception as e:
        logger.error(f"Error getting health stats: {e}")
        pending_count = processing_count = failed_count = stuck_count = -1

    return {
        "status": "healthy" if thread_alive else "unhealthy",
        "scheduler_running": scheduler_running.is_set(),
        "thread_alive": thread_alive,
        "was_restarted": was_restarted,
        "instance_id": INSTANCE_ID,
        "timezone": "Asia/Kolkata",
        "current_time_ist": get_ist_now().strftime("%Y-%m-%d %H:%M:%S"),
        "polling_interval_seconds": POLLING_INTERVAL_SECONDS,
        "stale_timeout_minutes": STALE_PROCESSING_TIMEOUT_MINUTES,
        "events": {
            "pending": pending_count,
            "processing": processing_count,
            "failed": failed_count,
            "stuck": stuck_count
        }
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


@router.post("/scheduler/trigger")
def trigger_scheduler():
    """Manually trigger the scheduler to process due events immediately"""
    try:
        logger.info("[Manual] Triggering immediate event processing...")

        # Ensure scheduler thread is running
        ensure_scheduler_running()

        # Process events in a separate thread to not block the request
        def run_processing():
            process_due_events()

        processing_thread = threading.Thread(target=run_processing, daemon=True)
        processing_thread.start()

        return {
            "message": "Scheduler triggered successfully",
            "instance_id": INSTANCE_ID,
            "triggered_at": get_ist_now().strftime("%Y-%m-%d %H:%M:%S IST")
        }
    except Exception as e:
        logger.error(f"Error triggering scheduler: {e}")
        raise HTTPException(status_code=500, detail="Failed to trigger scheduler")


@router.post("/scheduler/recover-stuck")
def recover_stuck_events(db: orm.Session = Depends(get_db)):
    """Manually recover events stuck in 'processing' state"""
    try:
        recovered_count = recover_stale_processing_events(db)
        return {
            "message": f"Recovered {recovered_count} stuck events",
            "recovered_count": recovered_count,
            "instance_id": INSTANCE_ID
        }
    except Exception as e:
        logger.error(f"Error recovering stuck events: {e}")
        raise HTTPException(status_code=500, detail="Failed to recover stuck events")


@router.post("/scheduled-events/retry-all-failed")
def retry_all_failed_events(
    x_tenant_id: Optional[str] = Header(None),
    db: orm.Session = Depends(get_db)
):
    """Reset all failed events to pending so they can be retried"""
    try:
        query = db.query(ScheduledEvent).filter(ScheduledEvent.status == "failed")
        if x_tenant_id:
            query = query.filter(ScheduledEvent.tenant_id == x_tenant_id)

        failed_events = query.all()
        reset_count = 0

        for event in failed_events:
            event.status = "pending"
            event.retry_count = 0
            event.last_error = f"Manually reset for retry (instance: {INSTANCE_ID})"
            event.updated_at = datetime.utcnow()
            reset_count += 1

        db.commit()

        logger.info(f"Reset {reset_count} failed events for retry")
        return {
            "message": f"Reset {reset_count} failed events for retry",
            "reset_count": reset_count
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Error resetting failed events: {e}")
        raise HTTPException(status_code=500, detail="Failed to reset events")
