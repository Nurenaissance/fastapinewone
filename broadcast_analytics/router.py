import time
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
import requests
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from config.database import get_db
from broadcast_analytics.models import BroadcastAnalytics
from models import Tenant
from whatsapp_tenant.models import WhatsappTenantData
from .schema import TemplateAnalyticsRequest, AnalyticsResponse
from sqlalchemy import func

router = APIRouter(
    prefix="/broadcast-analytics",
    tags=["broadcast-analytics"]
)

def get_start_date(days_ago: int):
    start_date = datetime.now() - timedelta(days=days_ago)
    return int(start_date.timestamp())

def fetch_analytics_for_template(template_id, access_token, business_account_id):
    durations = [1, 7, 30, 60, 90]  # in days
    last_successful_data = None

    for days in durations:
        start = get_start_date(days)
        end = int(time.time())

        print(f"Fetching analytics for template {template_id} for {days} days (start: {start}, end: {end})")
        
        try:
            response = requests.get(
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
                print(f"Successfully fetched data for {template_id} (duration: {days} days)")
                last_successful_data = response.json()
            else:
                print(f"Error fetching data for {template_id} (status code: {response.status_code})")
                break  # Stop trying longer durations if we hit an error

        except Exception as e:
            print(f"Exception fetching data for {template_id} during {days} days: {e}")
            break  # Stop if an exception occurs

    if last_successful_data:
        print(f"Returning data for {template_id}")
        print(f"data>last_successful_data{last_successful_data}")
        return last_successful_data

    print(f"No data found for {template_id} after checking all durations.")
    return None

def process_template_analytics(whatsapp_data, template_ids):
    total_sent = 0
    total_delivered = 0
    total_read = 0
    total_cost = 0
    
    for template_id in template_ids:
        print(f"Processing analytics for template {template_id}...")
        data = fetch_analytics_for_template(template_id, whatsapp_data.access_token, whatsapp_data.business_account_id)
        if not data:
            print(f"No data found for template {template_id}. Skipping.")
            continue
        for template in data.get('data', []):
            for dp in template.get('data_points', []):
                total_sent += dp.get('sent', 0)
                total_delivered += dp.get('delivered', 0)
                total_read += dp.get('read', 0)
                for cost in dp.get('cost', []):
                    if cost.get('type') == 'amount_spent':
                        total_cost += cost.get('value', 0)

    print(f"Total data processed: Sent={total_sent}, Delivered={total_delivered}, Read={total_read}, Cost={total_cost}")
    return total_sent, total_delivered, total_read, total_cost

@router.post("/fetch-and-save", response_model=AnalyticsResponse)
async def fetch_and_save_analytics(
    request: TemplateAnalyticsRequest,
    x_tenant_id: str = Header(...),
    db: Session = Depends(get_db)
):
    print(f"Fetching and saving analytics for tenant {x_tenant_id} on date {request.date}")
    try:
        request_date = datetime.strptime(request.date, "%d-%m-%Y").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use DD-MM-YYYY.")

    existing = db.query(BroadcastAnalytics).filter_by(tenant_id=x_tenant_id, date=request_date).first()
    if existing:
        print(f"Analytics already exist for tenant {x_tenant_id} on date {request.date}. Returning existing data.")
        return existing

    tenant = db.query(Tenant).filter(Tenant.id == x_tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    whatsapp_data = db.query(WhatsappTenantData).filter_by(tenant_id=x_tenant_id).first()
    if not whatsapp_data:
        raise HTTPException(status_code=404, detail="WhatsApp data not found")

    total_sent, total_delivered, total_read, total_cost = process_template_analytics(whatsapp_data, request.template_ids)

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
        print(f"Successfully saved analytics for tenant {x_tenant_id} on date {request.date}")
        return record
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.get("/", response_model=Optional[AnalyticsResponse])
async def get_analytics_all(
    x_tenant_id: str = Header(...),
    db: Session = Depends(get_db)
):
    """Get latest analytics for tenant"""
    print(f"Fetching all analytics for tenant {x_tenant_id}")
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
    print(f"Fetching analytics for tenant {x_tenant_id} with date range")

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
    print(f"Fetching analytics for template {template_id}, tenant {x_tenant_id}")

    tenant = db.query(Tenant).filter(Tenant.id == x_tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    whatsapp_data = db.query(WhatsappTenantData).filter_by(tenant_id=x_tenant_id).first()
    if not whatsapp_data:
        raise HTTPException(status_code=404, detail="WhatsApp data not found")

    # Fetch analytics from Meta API
    data = fetch_analytics_for_template(
        template_id,
        whatsapp_data.access_token,
        whatsapp_data.business_account_id
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
    print(f"Fetching analytics for campaign {campaign_id}, tenant {x_tenant_id}")

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
    print("Running scheduled job...")
    db = next(get_db())
    tenants = db.query(Tenant).all()
    for tenant in tenants:
        print(f"Processing tenant {tenant.id}")
        whatsapp_data = db.query(WhatsappTenantData).filter_by(tenant_id=tenant.id).first()
        if not whatsapp_data:
            print(f"No WhatsApp data found for tenant {tenant.id}. Skipping.")
            continue

        template_response = requests.get(
            f"https://graph.facebook.com/v20.0/{whatsapp_data.business_account_id}/message_templates",
            headers={'Authorization': f'Bearer {whatsapp_data.access_token.strip()}'},
            params={
                'fields': 'name,status,components,language,category'
            }
        )
        if template_response.status_code != 200:
            print(f"Failed to fetch templates for tenant {tenant.id}. Status code: {template_response.status_code}")
            continue

        template_data = template_response.json().get('data', [])
        template_ids = [t['id'] for t in template_data]
        template_names = [t['name'] for t in template_data]

        print(f"Templates for tenant {tenant.id}:")
        for name, id_ in zip(template_names, template_ids):
            print(f"- {name} (ID: {id_})")

        total_sent, total_delivered, total_read, total_cost = process_template_analytics(whatsapp_data, template_ids)

        record = BroadcastAnalytics(
            total_sent=total_sent,
            total_delivered=total_delivered,
            total_read=total_read,
            total_cost=total_cost,
            tenant_id=tenant.id,
            date=datetime.now().date()
        )
        try:
            db.add(record)
            db.commit()
            print(f"Successfully saved analytics for tenant {tenant.id}.")
        except:
            db.rollback()
            print(f"Failed to save analytics for tenant {tenant.id}.")

# Scheduler setup
scheduler = BackgroundScheduler()
scheduler.add_job(run_scheduled_job, 'cron', hour=0, minute=0)
scheduler.start()
