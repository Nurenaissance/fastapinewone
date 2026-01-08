from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import orm
from config.database import get_db, SessionLocal
from .models import ScheduledEvent
from typing import List, Optional
from .schema import ScheduledEventCreate, ScheduledEventResponse, ScheduledEventBase
from datetime import datetime, timedelta
import schedule, time as datetime_time, requests, threading
from collections import deque
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

restart_event = threading.Event()

# ===================== DAILY TASK =====================
def daily_task():
    """Daily task with proper database session management"""
    logger.info("TASK BOOTS UP")
    now_utc = datetime.utcnow()

    # Add 5 hours and 30 minutes for IST    
    ist_offset = timedelta(hours=5, minutes=30)
    now_ist = now_utc + ist_offset
    today = now_ist.date()
    current_time = now_ist.time()
    
    # Use context manager for proper session handling
    db: orm.Session = SessionLocal()
    
    try:
        events_today = db.query(ScheduledEvent).filter(
            ScheduledEvent.date == today, 
            ScheduledEvent.time > current_time
        ).order_by(ScheduledEvent.time).all()

        if events_today:
            events_queue = deque(events_today)
            logger.info(f"{len(events_queue)} Events scheduled for today")

            while events_queue:
                # Check for restart signal before processing each event
                if restart_event.is_set():
                    logger.info("Restarting daily_task due to new event...")
                    restart_event.clear()
                    return daily_task()

                event = events_queue.popleft()
                logger.info(f"Processing event '{event.type}' scheduled at {event.time}")
                
                now = datetime.now()
                event_time = datetime.combine(now_ist.date(), event.time)
                time_diff = event_time - now_ist
                logger.info(f"Time diff: {time_diff}")

                time_to_wait = time_diff.total_seconds()
                if time_to_wait < 0:
                    logger.warning(f"Event {event.id} is in the past, skipping")
                    continue

                # Wait for the event time with restart checks
                sleep_time = int(time_to_wait)
                interval = 5  # Check every 5 seconds

                for _ in range(0, sleep_time, interval):
                    if restart_event.is_set():
                        logger.info("Restarting daily_task due to new event...")
                        restart_event.clear()
                        return daily_task()
                    datetime_time.sleep(interval)

                # Make the request
                try:
                    body = event.value
                    if isinstance(body, str):
                        body = json.loads(body)
                    
                    response = requests.post(
                        'https://whatsappbotserver.azurewebsites.net/send-template',
                        json=body,
                        timeout=30  # Add timeout to prevent hanging requests
                    )
                    
                    if response.status_code == 200:
                        logger.info(f"Event '{event.type}' processed successfully")
                        
                        # Delete the processed event
                        db_event = db.query(ScheduledEvent).filter(ScheduledEvent.id == event.id).first()
                        if db_event:
                            db.delete(db_event)
                            db.commit()
                            logger.info(f"Deleted scheduled event with ID: {event.id}")
                    else:
                        logger.error(f"Failed to process event '{event.type}'. Status code: {response.status_code}")

                except requests.RequestException as e:
                    logger.error(f"Request failed for event '{event.type}': {e}")
                except Exception as e:
                    logger.error(f"Unexpected error processing event {event.id}: {e}")
        else:
            logger.info("No events scheduled for today")

    except Exception as e:
        logger.error(f"Error in daily_task: {e}")
    finally:
        # CRITICAL: Always close the database session
        if db:
            db.close()
            logger.debug("Database session closed in daily_task")

        
@router.post("/events/group", response_model=dict)
def group_events_for_next_day(tenant_id: str = Header(...), db: orm.Session = Depends(get_db)):
    """Group events with proper error handling"""
    try:
        logger.info(f"🔍 Grouping today's and tomorrow's events for tenant_id: {tenant_id}")

        # Get current IST date
        now_utc = datetime.utcnow()
        ist_offset = timedelta(hours=5, minutes=30)
        now_ist = now_utc + ist_offset
        today_date = now_ist.date()
        tomorrow_date = (now_ist + timedelta(days=1)).date()

        # Query events for today and tomorrow, only for this tenant
        events = db.query(ScheduledEvent).filter(
            ScheduledEvent.date.in_([today_date, tomorrow_date]),
            ScheduledEvent.tenant_id == tenant_id
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
                for event in event_list:
                    phone_numbers = event["value"].get("phoneNumbers", [])
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
                    tenant_id=tenant_id
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


# ===================== SCHEDULER =====================
schedule.every().day.at("00:00:00").do(daily_task)

def run_scheduler():
    """Scheduler with better error handling"""
    logger.info("[SCHEDULER] Started")
    while True:
        try:
            schedule.run_pending()
            
            if restart_event.is_set():
                logger.info("Restarting daily_task in run scheduler")
                restart_event.clear()
                try:
                    daily_task()
                except Exception as e:
                    logger.error(f"Error in daily_task restart: {e}")
            
            datetime_time.sleep(5)
        
        except Exception as e:
            logger.error(f"Error in scheduler loop: {e}")
            datetime_time.sleep(10)  # Wait longer on error


@router.on_event("startup")
def startup_event():
    """Startup event with error handling"""
    try:
        logger.info("Starting scheduler...")
        logger.info(f"Scheduled jobs: {schedule.get_jobs()}")
        scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()
        restart_event.set()
        logger.info("Scheduler started successfully")
    except Exception as e:
        logger.error(f"Error starting scheduler: {e}")


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

        db_event = ScheduledEvent(**event_dict, tenant_id=x_tenant_id)
        db.add(db_event)
        db.commit()
        db.refresh(db_event)
        
        # Signal scheduler restart
        restart_event.set()
        
        logger.info(f"Created scheduled event {db_event.id} for tenant {x_tenant_id}")
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
        
        # Signal scheduler restart
        restart_event.set()
        
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


# ===================== HEALTH CHECK =====================
@router.get("/health/scheduler")
def scheduler_health():
    """Health check endpoint for scheduler"""
    return {
        "status": "running",
        "scheduled_jobs": len(schedule.get_jobs()),
        "restart_event_set": restart_event.is_set()
    }