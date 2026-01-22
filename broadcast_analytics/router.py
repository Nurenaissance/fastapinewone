import time
import os
import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
import httpx
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from config.database import get_db, SessionLocal
from broadcast_analytics.models import BroadcastAnalytics
from models import Tenant
from whatsapp_tenant.models import WhatsappTenantData
from .schema import TemplateAnalyticsRequest, AnalyticsResponse
from sqlalchemy import func

# Configure logging
logger = logging.getLogger(__name__)

# In-memory template log storage (for frontend visibility)
template_logs: List[dict] = []
MAX_LOG_ENTRIES = 500

def add_template_log(level: str, message: str, template_id: str = None, tenant_id: str = None, extra: dict = None):
    """Add a log entry for template operations"""
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "level": level,
        "message": message,
        "template_id": template_id,
        "tenant_id": tenant_id,
        "extra": extra or {}
    }
    template_logs.append(log_entry)
    # Keep only last MAX_LOG_ENTRIES
    if len(template_logs) > MAX_LOG_ENTRIES:
        template_logs.pop(0)
    # Also log to standard logger
    getattr(logger, level.lower(), logger.info)(f"[Template] {message}")

router = APIRouter(
    prefix="/broadcast-analytics",
    tags=["broadcast-analytics"]
)

def get_start_date(days_ago: int):
    start_date = datetime.now() - timedelta(days=days_ago)
    return int(start_date.timestamp())

async def fetch_analytics_for_template_async(template_id: str, access_token: str, business_account_id: str, tenant_id: str = None):
    """Async version using httpx - non-blocking"""
    durations = [1, 7, 30, 60, 90]  # in days
    last_successful_data = None

    async with httpx.AsyncClient(timeout=30.0) as client:
        for days in durations:
            start = get_start_date(days)
            end = int(time.time())

            add_template_log("info", f"Fetching analytics for {days} days", template_id=template_id, tenant_id=tenant_id)

            try:
                response = await client.get(
                    f"https://graph.facebook.com/v22.0/{business_account_id}/template_analytics",
                    headers={'Authorization': f'Bearer {access_token.strip()}'},
                    params={
                        'start': start,
                        'end': end,
                        'granularity': 'daily',
                        'metric_types': 'cost,delivered,read,sent',
                        'template_ids': template_id,
                    }
                )

                if response.status_code == 200:
                    add_template_log("info", f"Successfully fetched data ({days} days)", template_id=template_id, tenant_id=tenant_id)
                    last_successful_data = response.json()
                else:
                    add_template_log("warning", f"Error fetching data (status: {response.status_code})", template_id=template_id, tenant_id=tenant_id, extra={"response": response.text[:200]})
                    break

            except httpx.TimeoutException:
                add_template_log("error", f"Timeout fetching analytics ({days} days)", template_id=template_id, tenant_id=tenant_id)
                break
            except Exception as e:
                add_template_log("error", f"Exception: {str(e)}", template_id=template_id, tenant_id=tenant_id)
                break

    if last_successful_data:
        add_template_log("info", "Returning analytics data", template_id=template_id, tenant_id=tenant_id)
        return last_successful_data

    add_template_log("warning", "No data found after checking all durations", template_id=template_id, tenant_id=tenant_id)
    return None

def fetch_analytics_for_template_sync(template_id: str, access_token: str, business_account_id: str, tenant_id: str = None):
    """Sync version for scheduled jobs - uses httpx sync client"""
    durations = [1, 7, 30, 60, 90]
    last_successful_data = None

    with httpx.Client(timeout=30.0) as client:
        for days in durations:
            start = get_start_date(days)
            end = int(time.time())

            logger.info(f"Fetching analytics for template {template_id} for {days} days")

            try:
                response = client.get(
                    f"https://graph.facebook.com/v22.0/{business_account_id}/template_analytics",
                    headers={'Authorization': f'Bearer {access_token.strip()}'},
                    params={
                        'start': start,
                        'end': end,
                        'granularity': 'daily',
                        'metric_types': 'cost,delivered,read,sent',
                        'template_ids': template_id,
                    }
                )

                if response.status_code == 200:
                    logger.info(f"Successfully fetched data for {template_id} ({days} days)")
                    last_successful_data = response.json()
                else:
                    logger.warning(f"Error fetching data for {template_id} (status: {response.status_code})")
                    break

            except Exception as e:
                logger.error(f"Exception fetching data for {template_id}: {e}")
                break

    return last_successful_data

async def process_template_analytics_async(whatsapp_data, template_ids, tenant_id: str = None):
    """Async version that processes templates concurrently"""
    import asyncio

    total_sent = 0
    total_delivered = 0
    total_read = 0
    total_cost = 0

    add_template_log("info", f"Processing {len(template_ids)} templates", tenant_id=tenant_id)

    # Fetch all templates concurrently (with semaphore to limit concurrent requests)
    semaphore = asyncio.Semaphore(5)  # Max 5 concurrent requests to Meta API

    async def fetch_with_semaphore(template_id):
        async with semaphore:
            return await fetch_analytics_for_template_async(
                template_id,
                whatsapp_data.access_token,
                whatsapp_data.business_account_id,
                tenant_id
            )

    results = await asyncio.gather(*[fetch_with_semaphore(tid) for tid in template_ids], return_exceptions=True)

    for i, data in enumerate(results):
        if isinstance(data, Exception):
            add_template_log("error", f"Exception for template: {str(data)}", template_id=template_ids[i], tenant_id=tenant_id)
            continue
        if not data:
            continue
        for template in data.get('data', []):
            for dp in template.get('data_points', []):
                total_sent += dp.get('sent', 0)
                total_delivered += dp.get('delivered', 0)
                total_read += dp.get('read', 0)
                for cost in dp.get('cost', []):
                    if cost.get('type') == 'amount_spent':
                        total_cost += cost.get('value', 0)

    add_template_log("info", f"Processed: Sent={total_sent}, Delivered={total_delivered}, Read={total_read}, Cost={total_cost}", tenant_id=tenant_id)
    return total_sent, total_delivered, total_read, total_cost

def process_template_analytics_sync(whatsapp_data, template_ids, tenant_id: str = None):
    """Sync version for scheduled jobs"""
    total_sent = 0
    total_delivered = 0
    total_read = 0
    total_cost = 0

    for template_id in template_ids:
        logger.info(f"Processing analytics for template {template_id}...")
        data = fetch_analytics_for_template_sync(template_id, whatsapp_data.access_token, whatsapp_data.business_account_id, tenant_id)
        if not data:
            logger.warning(f"No data found for template {template_id}. Skipping.")
            continue
        for template in data.get('data', []):
            for dp in template.get('data_points', []):
                total_sent += dp.get('sent', 0)
                total_delivered += dp.get('delivered', 0)
                total_read += dp.get('read', 0)
                for cost in dp.get('cost', []):
                    if cost.get('type') == 'amount_spent':
                        total_cost += cost.get('value', 0)

    logger.info(f"Total data processed: Sent={total_sent}, Delivered={total_delivered}, Read={total_read}, Cost={total_cost}")
    return total_sent, total_delivered, total_read, total_cost

@router.post("/fetch-and-save", response_model=AnalyticsResponse)
async def fetch_and_save_analytics(
    request: TemplateAnalyticsRequest,
    x_tenant_id: str = Header(...),
    db: Session = Depends(get_db)
):
    add_template_log("info", f"Fetching and saving analytics for date {request.date}", tenant_id=x_tenant_id)
    try:
        request_date = datetime.strptime(request.date, "%d-%m-%Y").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use DD-MM-YYYY.")

    existing = db.query(BroadcastAnalytics).filter_by(tenant_id=x_tenant_id, date=request_date).first()
    if existing:
        add_template_log("info", f"Analytics already exist for date {request.date}. Returning existing.", tenant_id=x_tenant_id)
        return existing

    tenant = db.query(Tenant).filter(Tenant.id == x_tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    whatsapp_data = db.query(WhatsappTenantData).filter_by(tenant_id=x_tenant_id).first()
    if not whatsapp_data:
        raise HTTPException(status_code=404, detail="WhatsApp data not found")

    # Use async version for non-blocking HTTP calls
    total_sent, total_delivered, total_read, total_cost = await process_template_analytics_async(
        whatsapp_data, request.template_ids, x_tenant_id
    )

    try:
        record = BroadcastAnalytics(
            total_sent=total_sent,
            total_delivered=total_delivered,
            total_read=total_read,
            total_cost=total_cost,
            tenant_id=x_tenant_id,
            date=request_date
        )
        db.add(record)
        db.commit()
        add_template_log("info", f"Successfully saved analytics for date {request.date}", tenant_id=x_tenant_id)
        return record
    except SQLAlchemyError as e:
        db.rollback()
        add_template_log("error", f"Database error: {str(e)}", tenant_id=x_tenant_id)
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.get("/", response_model=Optional[AnalyticsResponse])
async def get_analytics_all(
    x_tenant_id: str = Header(...),
    db: Session = Depends(get_db)
):
    """Get latest analytics for tenant"""
    logger.info(f"Fetching all analytics for tenant {x_tenant_id}")
    tenant = db.query(Tenant).filter(Tenant.id == x_tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Get today's date
    today = datetime.now().date()

     # Filter and get only analytics up to today's date
    analytics = (
        db.query(BroadcastAnalytics)
        .filter(
            BroadcastAnalytics.tenant_id == x_tenant_id,
            func.date(BroadcastAnalytics.date) <= today
        )
        .order_by(BroadcastAnalytics.date.desc())  # Optional: newest first
        .first()
    )
    return analytics


@router.get("/date-range")
async def get_analytics_by_date_range(
    x_tenant_id: str = Header(...),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Get analytics for a date range
    Query params:
    - start_date: DD-MM-YYYY (optional, defaults to 30 days ago)
    - end_date: DD-MM-YYYY (optional, defaults to today)
    """
    logger.info(f"Fetching analytics for tenant {x_tenant_id} with date range")

    tenant = db.query(Tenant).filter(Tenant.id == x_tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Parse dates
    try:
        if end_date:
            end_dt = datetime.strptime(end_date, "%d-%m-%Y").date()
        else:
            end_dt = datetime.now().date()

        if start_date:
            start_dt = datetime.strptime(start_date, "%d-%m-%Y").date()
        else:
            start_dt = end_dt - timedelta(days=30)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date format. Use DD-MM-YYYY"
        )

    # Validate date range
    if start_dt > end_dt:
        raise HTTPException(
            status_code=400,
            detail="start_date must be before or equal to end_date"
        )

    # Query analytics within date range
    analytics = (
        db.query(BroadcastAnalytics)
        .filter(
            BroadcastAnalytics.tenant_id == x_tenant_id,
            BroadcastAnalytics.date >= start_dt,
            BroadcastAnalytics.date <= end_dt
        )
        .order_by(BroadcastAnalytics.date.desc())
        .all()
    )

    if not analytics:
        return {
            "message": "No analytics found for the specified date range",
            "start_date": start_dt.strftime("%d-%m-%Y"),
            "end_date": end_dt.strftime("%d-%m-%Y"),
            "data": []
        }

    # Calculate totals
    total_sent = sum(a.total_sent or 0 for a in analytics)
    total_delivered = sum(a.total_delivered or 0 for a in analytics)
    total_read = sum(a.total_read or 0 for a in analytics)
    total_cost = sum(float(a.total_cost or 0) for a in analytics)

    return {
        "start_date": start_dt.strftime("%d-%m-%Y"),
        "end_date": end_dt.strftime("%d-%m-%Y"),
        "total_records": len(analytics),
        "summary": {
            "total_sent": total_sent,
            "total_delivered": total_delivered,
            "total_read": total_read,
            "total_cost": total_cost,
            "delivery_rate": round((total_delivered / total_sent * 100), 2) if total_sent > 0 else 0,
            "read_rate": round((total_read / total_delivered * 100), 2) if total_delivered > 0 else 0
        },
        "data": [
            {
                "date": a.date.strftime("%d-%m-%Y"),
                "total_sent": a.total_sent,
                "total_delivered": a.total_delivered,
                "total_read": a.total_read,
                "total_cost": float(a.total_cost) if a.total_cost else 0
            }
            for a in analytics
        ]
    }


@router.get("/template/{template_id}")
async def get_template_analytics(
    template_id: str,
    x_tenant_id: str = Header(...),
    days: int = 30,
    db: Session = Depends(get_db)
):
    """
    Get analytics for a specific template
    Query params:
    - days: Number of days to look back (default: 30)
    """
    add_template_log("info", f"Fetching analytics for {days} days", template_id=template_id, tenant_id=x_tenant_id)

    tenant = db.query(Tenant).filter(Tenant.id == x_tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    whatsapp_data = db.query(WhatsappTenantData).filter_by(tenant_id=x_tenant_id).first()
    if not whatsapp_data:
        raise HTTPException(status_code=404, detail="WhatsApp data not found")

    # Fetch analytics from Meta API using async version
    data = await fetch_analytics_for_template_async(
        template_id,
        whatsapp_data.access_token,
        whatsapp_data.business_account_id,
        x_tenant_id
    )

    if not data:
        raise HTTPException(
            status_code=404,
            detail=f"No analytics found for template {template_id}"
        )

    # Process the data
    template_analytics = []
    total_sent = 0
    total_delivered = 0
    total_read = 0
    total_cost = 0

    for template in data.get('data', []):
        for dp in template.get('data_points', []):
            sent = dp.get('sent', 0)
            delivered = dp.get('delivered', 0)
            read = dp.get('read', 0)

            cost_value = 0
            for cost in dp.get('cost', []):
                if cost.get('type') == 'amount_spent':
                    cost_value = cost.get('value', 0)

            total_sent += sent
            total_delivered += delivered
            total_read += read
            total_cost += cost_value

            template_analytics.append({
                "date": datetime.fromtimestamp(dp.get('start', 0)).strftime("%d-%m-%Y"),
                "sent": sent,
                "delivered": delivered,
                "read": read,
                "cost": cost_value
            })

    return {
        "template_id": template_id,
        "days": days,
        "summary": {
            "total_sent": total_sent,
            "total_delivered": total_delivered,
            "total_read": total_read,
            "total_cost": total_cost,
            "delivery_rate": round((total_delivered / total_sent * 100), 2) if total_sent > 0 else 0,
            "read_rate": round((total_read / total_delivered * 100), 2) if total_delivered > 0 else 0,
            "cost_per_message": round((total_cost / total_sent), 4) if total_sent > 0 else 0
        },
        "daily_data": template_analytics
    }


@router.get("/campaign/{campaign_id}")
async def get_campaign_analytics(
    campaign_id: str,
    x_tenant_id: str = Header(...),
    db: Session = Depends(get_db)
):
    """
    Get analytics for a specific campaign
    Note: This is a placeholder - enhance with actual campaign tracking
    """
    logger.info(f"Fetching analytics for campaign {campaign_id}, tenant {x_tenant_id}")

    tenant = db.query(Tenant).filter(Tenant.id == x_tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # TODO: Implement campaign-specific analytics
    # For now, return placeholder structure
    # This should be enhanced to track messages sent per campaign

    return {
        "campaign_id": campaign_id,
        "message": "Campaign analytics endpoint - implementation pending",
        "note": "Enhance with campaign message tracking to provide accurate analytics",
        "summary": {
            "total_sent": 0,
            "total_delivered": 0,
            "total_read": 0,
            "total_cost": 0
        }
    }

def run_scheduled_job():
    """Scheduled job that runs in background thread - uses sync functions"""
    logger.info("Running scheduled broadcast analytics job...")
    db = SessionLocal()
    try:
        tenants = db.query(Tenant).all()
        for tenant in tenants:
            logger.info(f"Processing tenant {tenant.id}")
            whatsapp_data = db.query(WhatsappTenantData).filter_by(tenant_id=tenant.id).first()
            if not whatsapp_data:
                logger.warning(f"No WhatsApp data found for tenant {tenant.id}. Skipping.")
                continue

            try:
                with httpx.Client(timeout=30.0) as client:
                    template_response = client.get(
                        f"https://graph.facebook.com/v20.0/{whatsapp_data.business_account_id}/message_templates",
                        headers={'Authorization': f'Bearer {whatsapp_data.access_token.strip()}'},
                        params={
                            'fields': 'name,status,components,language,category'
                        }
                    )
                if template_response.status_code != 200:
                    logger.warning(f"Failed to fetch templates for tenant {tenant.id}. Status: {template_response.status_code}")
                    continue

                template_data = template_response.json().get('data', [])
                template_ids = [t['id'] for t in template_data]

                logger.info(f"Found {len(template_ids)} templates for tenant {tenant.id}")

                total_sent, total_delivered, total_read, total_cost = process_template_analytics_sync(
                    whatsapp_data, template_ids, str(tenant.id)
                )

                record = BroadcastAnalytics(
                    total_sent=total_sent,
                    total_delivered=total_delivered,
                    total_read=total_read,
                    total_cost=total_cost,
                    tenant_id=tenant.id,
                    date=datetime.now().date()
                )
                db.add(record)
                db.commit()
                logger.info(f"Successfully saved analytics for tenant {tenant.id}.")
            except Exception as e:
                db.rollback()
                logger.error(f"Failed to process tenant {tenant.id}: {str(e)}")
    finally:
        db.close()

# Template logs endpoint for frontend visibility
@router.get("/logs")
async def get_template_logs(
    limit: int = 100,
    level: Optional[str] = None,
    template_id: Optional[str] = None,
    tenant_id: Optional[str] = None
):
    """
    Get template operation logs for debugging and monitoring.
    Query params:
    - limit: Max number of logs to return (default 100)
    - level: Filter by log level (info, warning, error)
    - template_id: Filter by template ID
    - tenant_id: Filter by tenant ID
    """
    logs = template_logs.copy()

    # Apply filters
    if level:
        logs = [l for l in logs if l['level'].lower() == level.lower()]
    if template_id:
        logs = [l for l in logs if l.get('template_id') == template_id]
    if tenant_id:
        logs = [l for l in logs if l.get('tenant_id') == tenant_id]

    # Return most recent logs first
    logs = list(reversed(logs))[:limit]

    return {
        "total_logs": len(template_logs),
        "filtered_count": len(logs),
        "logs": logs
    }

@router.delete("/logs")
async def clear_template_logs():
    """Clear all template logs"""
    global template_logs
    count = len(template_logs)
    template_logs = []
    return {"message": f"Cleared {count} log entries"}

# Scheduler setup - only start if not using gunicorn preload to avoid duplicate schedulers
_scheduler_started = False
scheduler = None

def start_analytics_scheduler():
    """Start the scheduler - should be called once from main.py lifespan"""
    global _scheduler_started, scheduler
    if _scheduler_started:
        logger.warning("Scheduler already started, skipping")
        return

    scheduler = BackgroundScheduler()
    scheduler.add_job(run_scheduled_job, 'cron', hour=0, minute=0, id='broadcast_analytics_job')
    scheduler.start()
    _scheduler_started = True
    logger.info("Broadcast analytics scheduler started (daily at 00:00)")

def stop_analytics_scheduler():
    """Stop the scheduler - should be called from main.py lifespan shutdown"""
    global _scheduler_started, scheduler
    if scheduler and _scheduler_started:
        scheduler.shutdown(wait=False)
        _scheduler_started = False
        logger.info("Broadcast analytics scheduler stopped")
