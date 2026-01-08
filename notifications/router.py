from fastapi import APIRouter, Request, Depends, HTTPException, Query
from sqlalchemy import orm, func, and_, text
import re
from sqlalchemy.orm import Session
from config.database import get_db, engine
from .models import Notifications
from contacts.models import Contact
from typing import Optional, List
from datetime import datetime, timedelta
import logging
from functools import lru_cache

router = APIRouter()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cache for phone number to contact_id mapping (helps with repeated notifications)
@lru_cache(maxsize=1000)
def get_contact_id_by_phone_cached(phone: str, tenant_id: str, cache_key: str) -> Optional[int]:
    """Cache contact lookups - cache_key can be timestamp to bust cache when needed"""
    return None  # This would be implemented with Redis in production

def get_tenant_id_from_request(request: Request) -> str:
    """Helper to extract tenant_id consistently"""
    tenant_id = request.headers.get('X-Tenant-Id')
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID is required in headers")
    return tenant_id

def convert_time_optimized(datetime_str: str) -> Optional[datetime]:
    """
    Optimized datetime conversion with better error handling.
    Returns datetime object directly instead of string.
    """
    if not datetime_str:
        return None
        
    try:
        # Handle multiple possible formats
        formats_to_try = [
            "%d/%m/%Y, %H:%M:%S.%f",  # Original format
            "%d/%m/%Y, %H:%M:%S",     # Without microseconds
            "%Y-%m-%d %H:%M:%S.%f",   # Already PostgreSQL format
            "%Y-%m-%d %H:%M:%S",      # PostgreSQL without microseconds
            "%d/%m/%Y %H:%M:%S",      # Alternative format
        ]
        
        for format_str in formats_to_try:
            try:
                return datetime.strptime(datetime_str, format_str)
            except ValueError:
                continue
        
        logger.warning(f"Could not parse datetime: {datetime_str}")
        return None
        
    except Exception as e:
        logger.error(f"Error converting datetime {datetime_str}: {e}")
        return None

def extract_phone_number_optimized(content: str) -> Optional[str]:
    """
    Optimized phone number extraction with better regex and validation.
    """
    if not content:
        return None
    
    try:
        # More comprehensive phone number patterns
        patterns = [
            r'^(\d{10,15})',              # Leading digits (10-15 digits)
            r'(\+?\d{10,15})',            # With optional +
            r'(\d{3,4}[-.\s]?\d{3,4}[-.\s]?\d{4,6})',  # Formatted numbers
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content.strip())
            if match:
                phone = re.sub(r'[-.\s+]', '', match.group(1))  # Clean the number
                if 10 <= len(phone) <= 15:  # Validate length
                    return phone
        
        return None
        
    except Exception as e:
        logger.error(f"Error extracting phone number from '{content}': {e}")
        return None

def get_contact_id_by_phone(phone: str, tenant_id: str, db: Session) -> Optional[int]:
    """
    Optimized contact lookup with caching consideration.
    """
    try:
        contact = (db.query(Contact.id)
                  .filter(
                      Contact.phone == phone,
                      Contact.tenant_id == tenant_id
                  )
                  .first())
        
        return contact.id if contact else None
        
    except Exception as e:
        logger.error(f"Error finding contact for phone {phone}: {e}")
        return None

@router.post("/notifications")
async def add_notifications(request: Request, db: Session = Depends(get_db)):
    """
    Optimized notification creation with better error handling and validation.
    """
    try:
        tenant_id = get_tenant_id_from_request(request)
        body = await request.json()
        
        content = body.get('content', '').strip()
        created_on = body.get('created_on')
        
        # Validation
        if not content:
            raise HTTPException(status_code=400, detail="Content is required and cannot be empty")
        
        # Convert timestamp
        created_on_datetime = None
        if created_on:
            created_on_datetime = convert_time_optimized(created_on)
            if created_on_datetime is None:
                logger.warning(f"Could not parse timestamp: {created_on}, using current time")
        
        if created_on_datetime is None:
            created_on_datetime = datetime.now()
        
        # Extract and find contact
        contact_id = None
        phone_number = extract_phone_number_optimized(content)
        
        if phone_number:
            logger.info(f"Extracted phone number: {phone_number}")
            contact_id = get_contact_id_by_phone(phone_number, tenant_id, db)
            
            if contact_id:
                logger.info(f"Found contact ID: {contact_id}")
            else:
                logger.info(f"No contact found for phone: {phone_number}")
        else:
            logger.info("No phone number found in content")
        
        # Create notification
        notification = Notifications(
            content=content,
            tenant_id=tenant_id,
            created_on=created_on_datetime,
            contact_id=contact_id
        )
        
        db.add(notification)
        db.commit()
        db.refresh(notification)
        
        return {
            "message": "Notification added successfully",
            "notification_id": notification.id,
            "contact_id": contact_id,
            "phone_extracted": phone_number
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error in add_notifications: {e}")
        raise HTTPException(status_code=500, detail="Failed to add notification")

@router.get("/notifications")
def get_notifications(
    request: Request,
    day: Optional[int] = Query(None, ge=0, le=365),  # Validate day range
    limit: Optional[int] = Query(100, ge=1, le=1000),  # Add limit parameter
    db: Session = Depends(get_db)
):
    """
    Optimized notifications retrieval with better filtering and limits.
    """
    try:
        tenant_id = get_tenant_id_from_request(request)
        
        query = (db.query(Notifications)
                .filter(Notifications.tenant_id == tenant_id))
        
        if day is not None:
            if day == 0:
                # Today
                today = datetime.now().date()
                start_of_day = datetime.combine(today, datetime.min.time())
                end_of_day = datetime.combine(today, datetime.max.time())
            else:
                # Specific day in the past
                target_date = (datetime.now() - timedelta(days=day)).date()
                start_of_day = datetime.combine(target_date, datetime.min.time())
                end_of_day = datetime.combine(target_date, datetime.max.time())
            
            query = query.filter(
                and_(
                    Notifications.created_on >= start_of_day,
                    Notifications.created_on <= end_of_day
                )
            )
        
        notifications = (query
                        .order_by(Notifications.created_on.desc())
                        .limit(limit)
                        .all())
        
        return {
            "notifications": notifications,
            "count": len(notifications),
            "filtered_by_day": day,
            "limit_applied": limit
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_notifications: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch notifications")

@router.get("/notifications/{page_no}")
def get_limited_notifications(
    page_no: int,
    request: Request,
    page_size: int = Query(10, ge=1, le=100),  # Configurable page size
    include_contact_details: bool = Query(False),  # Optional contact details
    db: Session = Depends(get_db),
):
    """
    Optimized paginated notifications with optional contact details.
    """
    try:
        tenant_id = get_tenant_id_from_request(request)
        
        if page_no < 1:
            raise HTTPException(status_code=400, detail="Page number must be greater than 0")
        
        offset = page_size * (page_no - 1)
        
        # Efficient count query
        total = (db.query(func.count(Notifications.id))
                .filter(Notifications.tenant_id == tenant_id)
                .scalar())
        
        total_pages = (total + page_size - 1) // page_size
        
        if include_contact_details:
            # Join query when contact details are needed
            notifications_with_contacts = (
                db.query(
                    Notifications.id,
                    Notifications.content,
                    Notifications.created_on,
                    Notifications.contact_id,
                    Contact.phone,
                    Contact.name
                )
                .outerjoin(Contact, Notifications.contact_id == Contact.id)
                .filter(Notifications.tenant_id == tenant_id)
                .order_by(Notifications.created_on.desc())
                .offset(offset)
                .limit(page_size)
                .all()
            )
            
            enhanced_notifications = [
                {
                    "id": n.id,
                    "content": n.content,
                    "created_on": n.created_on,
                    "contact_id": n.contact_id,
                    "contact_phone": n.phone,
                    "contact_name": n.name
                }
                for n in notifications_with_contacts
            ]
        else:
            # Simple query without joins
            notifications = (
                db.query(Notifications)
                .filter(Notifications.tenant_id == tenant_id)
                .order_by(Notifications.created_on.desc())
                .offset(offset)
                .limit(page_size)
                .all()
            )
            
            enhanced_notifications = [
                {
                    "id": n.id,
                    "content": n.content,
                    "created_on": n.created_on,
                    "contact_id": n.contact_id
                }
                for n in notifications
            ]
        
        return {
            "notifications": enhanced_notifications,
            "page_no": page_no,
            "page_size": page_size,
            "total_notifications": total,
            "total_pages": total_pages,
            "has_next": page_no < total_pages,
            "has_prev": page_no > 1
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_limited_notifications: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch notifications")

@router.delete("/notifications/{notification_id}")
def delete_notification(
    notification_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Delete a specific notification with tenant validation.
    """
    try:
        tenant_id = get_tenant_id_from_request(request)
        
        # Include tenant_id in filter for security
        deleted_count = (db.query(Notifications)
                        .filter(
                            Notifications.id == notification_id,
                            Notifications.tenant_id == tenant_id
                        )
                        .delete())
        
        if deleted_count == 0:
            raise HTTPException(status_code=404, detail="Notification not found")
        
        db.commit()
        
        return {"message": "Notification deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting notification {notification_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete notification")

@router.delete("/notifications/bulk")
def delete_notifications_bulk(
    request: Request,
    notification_ids: List[int],
    db: Session = Depends(get_db)
):
    """
    Bulk delete multiple notifications efficiently.
    """
    try:
        tenant_id = get_tenant_id_from_request(request)
        
        if not notification_ids:
            raise HTTPException(status_code=400, detail="No notification IDs provided")
        
        if len(notification_ids) > 100:  # Prevent too large bulk operations
            raise HTTPException(status_code=400, detail="Cannot delete more than 100 notifications at once")
        
        deleted_count = (db.query(Notifications)
                        .filter(
                            Notifications.id.in_(notification_ids),
                            Notifications.tenant_id == tenant_id
                        )
                        .delete(synchronize_session=False))
        
        if deleted_count == 0:
            raise HTTPException(status_code=404, detail="No notifications found to delete")
        
        db.commit()
        
        return {
            "message": f"Successfully deleted {deleted_count} notifications",
            "deleted_count": deleted_count
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error in bulk delete: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete notifications")

@router.delete("/notifications/all")
def delete_all_notifications(
    request: Request,
    confirm: bool = Query(False),  # Require explicit confirmation
    db: Session = Depends(get_db)
):
    """
    Delete all notifications for a tenant with confirmation.
    """
    try:
        tenant_id = get_tenant_id_from_request(request)
        
        if not confirm:
            raise HTTPException(
                status_code=400, 
                detail="Set confirm=true to delete all notifications"
            )
        
        # Get count first
        count = (db.query(func.count(Notifications.id))
                .filter(Notifications.tenant_id == tenant_id)
                .scalar())
        
        if count == 0:
            raise HTTPException(status_code=404, detail="No notifications found for this tenant")
        
        # Bulk delete
        deleted_count = (db.query(Notifications)
                        .filter(Notifications.tenant_id == tenant_id)
                        .delete(synchronize_session=False))
        
        db.commit()
        
        return {
            "message": f"Successfully deleted all {deleted_count} notifications",
            "deleted_count": deleted_count
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting all notifications: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete all notifications")

@router.delete("/notifications/by-contact/{contact_id}")
def delete_notifications_by_contact(
    contact_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Optimized deletion of notifications by contact using foreign key relationship.
    """
    try:
        tenant_id = get_tenant_id_from_request(request)
        
        # Verify contact exists and belongs to tenant
        contact = (db.query(Contact.id, Contact.phone)
                  .filter(
                      Contact.id == contact_id,
                      Contact.tenant_id == tenant_id
                  )
                  .first())
        
        if not contact:
            raise HTTPException(status_code=404, detail="Contact not found")
        
        # Primary deletion using contact_id foreign key (much faster)
        deleted_count = (db.query(Notifications)
                        .filter(
                            Notifications.tenant_id == tenant_id,
                            Notifications.contact_id == contact_id
                        )
                        .delete(synchronize_session=False))
        
        # Fallback: Delete by phone number pattern for older notifications without contact_id
        if deleted_count == 0:
            phone_number = contact.phone
            clean_phone = ''.join(filter(str.isdigit, phone_number))
            
            if clean_phone:
                fallback_count = (db.query(Notifications)
                                .filter(
                                    Notifications.tenant_id == tenant_id,
                                    Notifications.content.like(f"{clean_phone}%"),
                                    Notifications.contact_id.is_(None)  # Only unlinked notifications
                                )
                                .delete(synchronize_session=False))
                deleted_count = fallback_count
        
        if deleted_count == 0:
            raise HTTPException(status_code=404, detail="No notifications found for this contact")
        
        db.commit()
        
        return {
            "message": f"Successfully deleted {deleted_count} notifications for contact {contact_id}",
            "deleted_count": deleted_count,
            "contact_phone": contact.phone
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting notifications by contact {contact_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete notifications")

@router.get("/notifications/stats")
def get_notification_stats(
    request: Request,
    days_back: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db)
):
    """
    Get notification statistics for analytics.
    """
    try:
        tenant_id = get_tenant_id_from_request(request)
        
        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)
        
        # Get comprehensive stats in a single query
        stats = (db.query(
            func.count(Notifications.id).label('total_notifications'),
            func.count(Notifications.contact_id).label('notifications_with_contact'),
            func.count(func.nullif(Notifications.contact_id, None)).label('linked_notifications'),
            func.min(Notifications.created_on).label('oldest_notification'),
            func.max(Notifications.created_on).label('newest_notification')
        )
        .filter(
            Notifications.tenant_id == tenant_id,
            Notifications.created_on >= start_date,
            Notifications.created_on <= end_date
        )
        .first())
        
        # Get daily breakdown
        daily_stats = (db.query(
            func.date(Notifications.created_on).label('date'),
            func.count(Notifications.id).label('count')
        )
        .filter(
            Notifications.tenant_id == tenant_id,
            Notifications.created_on >= start_date,
            Notifications.created_on <= end_date
        )
        .group_by(func.date(Notifications.created_on))
        .order_by(func.date(Notifications.created_on))
        .all())
        
        return {
            "period_days": days_back,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "total_notifications": stats.total_notifications or 0,
            "linked_to_contacts": stats.linked_notifications or 0,
            "unlinked_notifications": (stats.total_notifications or 0) - (stats.linked_notifications or 0),
            "oldest_notification": stats.oldest_notification,
            "newest_notification": stats.newest_notification,
            "daily_breakdown": [
                {"date": str(day.date), "count": day.count}
                for day in daily_stats
            ]
        }
        
    except Exception as e:
        logger.error(f"Error getting notification stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get notification statistics")

# Health check endpoints (useful for monitoring)
@router.get("/notifications/health")
def health_check(db: Session = Depends(get_db)):
    """Basic health check for notifications service"""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "healthy", "service": "notifications"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Service unhealthy: {str(e)}")