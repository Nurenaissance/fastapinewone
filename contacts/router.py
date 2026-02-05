from fastapi import APIRouter, Request, Depends, HTTPException, responses
from sqlalchemy import orm, or_, and_, text, nulls_first, nulls_last, func
from config.database import get_db
from .models import Contact
from whatsapp_tenant.models import WhatsappTenantData
from whatsapp_tenant.group_service import GroupService
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
from collections import defaultdict
import math
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

# Helper function to get tenant_id from headers (supports both X-Tenant-Id and X-Tenant-ID for backward compatibility)
def get_tenant_id(request: Request) -> Optional[str]:
    return request.headers.get("X-Tenant-Id") or request.headers.get("X-Tenant-ID")

@router.get("/contacts/filter/{page_no}")
def get_filtered_contacts(
    request: Request,
    page_no: int = 1,
    engagement_type: Optional[str] = None,
    contact_type: Optional[str] = None,
    sort_by: Optional[str] = None,
    db: orm.Session = Depends(get_db)
):
    try:
        tenant_id = get_tenant_id(request)
        if not tenant_id:
            raise HTTPException(status_code=400, detail="Tenant ID missing in headers")

        today = datetime.now()
        page_size = 50

        # Build base query with only necessary columns for counting
        base_filter = Contact.tenant_id == tenant_id
        
        # Add engagement/contact type filters
        if engagement_type == "high":
            delivered = today - timedelta(days=3)
            replied = today - timedelta(days=7)
            base_filter = and_(base_filter, Contact.last_delivered >= delivered, Contact.last_replied >= replied)
        
        elif engagement_type == "medium":
            seen = today - timedelta(days=30)
            delivered = today - timedelta(days=14)
            base_filter = and_(base_filter, Contact.last_seen >= seen, Contact.last_delivered >= delivered)
        
        elif engagement_type == "low":
            created = today - timedelta(days=90)
            not_seen = today - timedelta(days=60)
            base_filter = and_(base_filter, Contact.createdOn >= created, 
                             or_(Contact.last_seen <= not_seen, Contact.last_seen.is_(None)))
        
        elif contact_type == "fresh":
            created = today - timedelta(days=14)
            base_filter = and_(base_filter, Contact.createdOn >= created, Contact.last_delivered.is_(None))
        
        elif contact_type == "dormant":
            not_deli = today - timedelta(days=30)
            base_filter = and_(base_filter, or_(Contact.last_delivered <= not_deli, Contact.last_delivered.is_(None)))

        # Get count efficiently using func.count()
        total_contacts = db.query(func.count(Contact.id)).filter(base_filter).scalar()
        total_pages = (total_contacts + page_size - 1) // page_size
        offset = page_size * (page_no - 1)

        # Build the main query for actual data
        contacts_query = db.query(Contact).filter(base_filter)
        
        # Handle special ordering for last_replied
        if contact_type == "last_replied":
            replied = today - timedelta(days=7)
            contacts_query = contacts_query.filter(Contact.last_replied >= replied)
            contacts_query = contacts_query.order_by(Contact.last_replied.desc())
        elif sort_by:
            contacts_query = contacts_query.order_by(getattr(Contact, sort_by).desc())

        contacts = contacts_query.offset(offset).limit(page_size).all()

        return {
            "contacts": contacts,
            "page_no": page_no,
            "page_size": page_size,
            "total_contacts": total_contacts,
            "total_pages": total_pages,
        }
    except Exception as e:
        print("Exception Occurred: ", str(e))
        raise HTTPException(status_code=500, detail=f"An Error Occurred: {str(e)}")

@router.get("/contacts")
def read_contacts(request: Request, db: orm.Session = Depends(get_db)):
    tenant_id = request.headers.get("X-Tenant-Id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID missing in headers")

    # Add limit to prevent large data dumps
    contacts = (db.query(Contact)
               .filter(Contact.tenant_id == tenant_id)
               .order_by(Contact.id.asc())
               .limit(1000)  # Add reasonable limit
               .all())
    
    if not contacts:
        raise HTTPException(status_code=404, detail="No contacts found for this tenant")

    return contacts

@router.get("/contacts/{page_no}") 
def get_limited_contacts(
    req: Request,
    page_no: int = 1,
    phone: Optional[str] = None,
    order_by: Optional[str] = "id",
    sort_by: Optional[str] = "asc",
    db: orm.Session = Depends(get_db),
):
    tenant_id = get_tenant_id(req)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID missing in headers")

    page_size = 50  # Reduced from 300 for better performance
    
    # Simplified phone search - just return the contact directly
    if phone:
        contact = db.query(Contact).filter(
            Contact.tenant_id == tenant_id,
            Contact.phone == phone
        ).first()

        if contact is None:
            raise HTTPException(status_code=404, detail="Contact not found")

        # Calculate page number
        row_number = (
            db.query(func.count(Contact.id))
            .filter(
                Contact.tenant_id == tenant_id,
                Contact.id <= contact.id
            )
            .scalar()
        )

        page_no = math.ceil(row_number / page_size)

        return {
            "contacts": [contact],
            "page_no": page_no,
            "page_size": page_size,
            "total_contacts": 1,
            "total_pages": None,
            "found_contact": True
        }

    offset = page_size * (page_no - 1)
    
    # Single query for count
    total_contacts = db.query(func.count(Contact.id)).filter(Contact.tenant_id == tenant_id).scalar()
    total_pages = (total_contacts + page_size - 1) // page_size 

    # Build order clause
    order_attr = getattr(Contact, order_by)
    order_by_clause = nulls_last(order_attr.desc()) if sort_by == "desc" else nulls_last(order_attr.asc())
    
    contacts = (
        db.query(Contact)
        .filter(Contact.tenant_id == tenant_id)
        .order_by(order_by_clause)
        .offset(offset)
        .limit(page_size)
        .all()
    )

    return {
        "contacts": contacts,
        "page_no": page_no,
        "page_size": page_size,
        "total_contacts": len(contacts),  # This should be total_contacts for consistency
        "total_pages": total_pages,
    }

@router.patch("/contacts/")
async def update_contact(request: Request, db: orm.Session = Depends(get_db)):
    body = await request.json()
    contacts = body.get('contact_id', [])
    bg_id = body.get('bgId')
    bg_name = body.get('name')
    
    if not contacts:
        raise HTTPException(status_code=400, detail="No contact IDs provided")
    
    try:
        # Bulk update approach - more efficient
        updated_count = (db.query(Contact)
                        .filter(Contact.id.in_(contacts))
                        .update({
                            Contact.bg_id: bg_id,
                            Contact.bg_name: bg_name
                        }, synchronize_session=False))
        
        db.commit()
        
        if updated_count == 0:
            raise HTTPException(status_code=404, detail="No contacts found to update")
            
        return {"message": f"Successfully updated {updated_count} contacts"}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error updating contacts: {str(e)}")

@router.delete("/contacts/")
async def delete_contacts(request: Request, db: orm.Session = Depends(get_db)):
    body = await request.json()
    contact_ids = body.get('contact_ids', [])
    
    if not contact_ids:
        raise HTTPException(status_code=400, detail="No contact IDs provided")

    tenant_id = request.headers.get("X-Tenant-Id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID missing in headers")

    try:
        # Bulk delete - more efficient
        deleted_count = (db.query(Contact)
                        .filter(Contact.id.in_(contact_ids), Contact.tenant_id == tenant_id)
                        .delete(synchronize_session=False))
        
        db.commit()
        
        if deleted_count == 0:
            raise HTTPException(status_code=404, detail="No contacts found to delete")
            
        return {"message": f"Successfully deleted {deleted_count} contacts"}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error deleting contacts: {str(e)}")

@router.delete("/contacts/{contact_id}/", status_code=204)
def delete_contact(contact_id: int, request: Request, db: orm.Session = Depends(get_db)):
    tenant_id = request.headers.get("X-Tenant-Id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID missing in headers")

    try:
        deleted_count = (db.query(Contact)
                        .filter(Contact.id == contact_id, Contact.tenant_id == tenant_id)
                        .delete())
        
        if deleted_count == 0:
            raise HTTPException(status_code=404, detail="Contact not found for this tenant")
        
        db.commit()
        return {"message": "Contact deleted successfully"}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error deleting contact: {str(e)}")

@router.post("/contacts/", status_code=201)
async def create_contact(request: Request, db: orm.Session = Depends(get_db)):
    """Create new contact"""
    tenant_id = request.headers.get("X-Tenant-Id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID missing in headers")

    try:
        body = await request.json()

        # Validate required fields
        if 'phone' not in body:
            raise HTTPException(status_code=400, detail="Phone number is required")

        # Check if contact already exists
        existing_contact = db.query(Contact).filter(
            Contact.phone == body['phone'],
            Contact.tenant_id == tenant_id
        ).first()

        if existing_contact:
            raise HTTPException(
                status_code=409,
                detail=f"Contact with phone {body['phone']} already exists for this tenant"
            )

        # Create new contact
        new_contact = Contact(
            phone=body['phone'],
            name=body.get('name'),
            email=body.get('email'),
            bg_id=body.get('bg_id'),
            bg_name=body.get('bg_name'),
            tenant_id=tenant_id,
            last_delivered=body.get('last_delivered'),
            last_seen=body.get('last_seen'),
            last_replied=body.get('last_replied'),
            customField=body.get('customField'),
            manual_mode=body.get('manual_mode', False)
        )

        db.add(new_contact)
        db.commit()
        db.refresh(new_contact)

        # AUTO-ASSIGN TO GROUPS WITH MATCHING RULES
        assigned_groups = GroupService.auto_assign_contact_to_groups(new_contact, db)

        return {
            "contact": new_contact,
            "auto_assigned_groups": assigned_groups
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error creating contact: {str(e)}")


@router.put("/contacts/{contact_id}")
@router.patch("/contacts/{contact_id}")
async def update_single_contact(
    contact_id: int,
    request: Request,
    db: orm.Session = Depends(get_db)
):
    """Update single contact (full or partial update)"""
    tenant_id = request.headers.get("X-Tenant-Id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID missing in headers")

    try:
        # Find contact
        contact = db.query(Contact).filter(
            Contact.id == contact_id,
            Contact.tenant_id == tenant_id
        ).first()

        if not contact:
            raise HTTPException(
                status_code=404,
                detail=f"Contact with ID {contact_id} not found for this tenant"
            )

        # Get update data
        body = await request.json()

        # Update fields if provided
        if 'name' in body:
            contact.name = body['name']
        if 'email' in body:
            contact.email = body['email']
        if 'phone' in body:
            contact.phone = body['phone']
        if 'bg_id' in body:
            contact.bg_id = body['bg_id']
        if 'bg_name' in body:
            contact.bg_name = body['bg_name']
        if 'last_delivered' in body:
            contact.last_delivered = body['last_delivered']
        if 'last_seen' in body:
            contact.last_seen = body['last_seen']
        if 'last_replied' in body:
            contact.last_replied = body['last_replied']
        if 'customField' in body:
            contact.customField = body['customField']
        if 'manual_mode' in body:
            contact.manual_mode = body['manual_mode']

        db.commit()
        db.refresh(contact)

        # RE-EVALUATE GROUP MEMBERSHIP AFTER UPDATE
        assigned_groups = GroupService.auto_assign_contact_to_groups(contact, db)

        return {
            "contact": contact,
            "auto_assigned_groups": assigned_groups
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error updating contact: {str(e)}")


@router.patch("/contacts/{contact_id}/manual-mode")
async def toggle_manual_mode(
    contact_id: int,
    request: Request,
    db: orm.Session = Depends(get_db)
):
    """Toggle manual mode for a contact to disable/enable automation"""
    tenant_id = get_tenant_id(request)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID missing in headers")

    try:
        body = await request.json()
        manual_mode = body.get('manual_mode')

        if manual_mode is None:
            raise HTTPException(status_code=400, detail="manual_mode field is required")

        # Find contact
        contact = db.query(Contact).filter(
            Contact.id == contact_id,
            Contact.tenant_id == tenant_id
        ).first()

        if not contact:
            raise HTTPException(
                status_code=404,
                detail=f"Contact with ID {contact_id} not found for this tenant"
            )

        # Update manual mode
        contact.manual_mode = manual_mode
        db.commit()
        db.refresh(contact)

        return {
            "contact_id": contact.id,
            "phone": contact.phone,
            "name": contact.name,
            "manual_mode": contact.manual_mode,
            "message": f"Manual mode {'enabled' if manual_mode else 'disabled'} for contact"
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error toggling manual mode: {str(e)}")


@router.get("/contact")
def get_contact(phone: str, request: Request, db: orm.Session = Depends(get_db)):
    """Get contact by phone number"""
    tenant_id = request.headers.get("X-Tenant-Id")
    bpid = request.headers.get('bpid')

    if not tenant_id and not bpid:
        raise HTTPException(status_code=400, detail="At least one of tenant id or bpid must be provided")

    if bpid and not tenant_id:
        # Use a more efficient query with select only needed fields
        bpid_data = (db.query(WhatsappTenantData.tenant_id)
                    .filter(WhatsappTenantData.business_phone_number_id == bpid)
                    .first())

        if not bpid_data:
            raise HTTPException(status_code=404, detail="Business phone ID not found")

        tenant_id = bpid_data.tenant_id

    contact = (db.query(Contact)
              .filter(Contact.phone == phone, Contact.tenant_id == tenant_id)
              .first())

    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found for this tenant")

    return contact


def calculate_contact_richness(contact: Contact) -> int:
    """
    Calculate the "richness" score of a contact based on how many fields are populated.
    Higher score = more data stored.
    """
    score = 0

    # Basic string fields (1 point each)
    string_fields = ['name', 'email', 'address', 'description', 'bg_name']
    for field in string_fields:
        value = getattr(contact, field, None)
        if value and isinstance(value, str) and value.strip():
            score += 1

    # Timestamp fields (2 points each - more valuable for engagement tracking)
    timestamp_fields = ['last_seen', 'last_delivered', 'last_replied']
    for field in timestamp_fields:
        value = getattr(contact, field, None)
        if value is not None:
            score += 2

    # Custom fields (1 point per key in JSON)
    if contact.customField and isinstance(contact.customField, dict):
        # Count non-empty values in customField
        for key, value in contact.customField.items():
            if value is not None and value != '' and value != []:
                score += 1

    # Boolean fields that are explicitly set (1 point)
    if contact.manual_mode is not None:
        score += 1

    # ID fields (1 point each if set)
    if contact.bg_id:
        score += 1

    return score


@router.post("/contacts/cleanup-duplicates")
async def cleanup_duplicate_contacts(
    request: Request,
    tenant_id: Optional[str] = None,
    dry_run: bool = False,
    db: orm.Session = Depends(get_db)
):
    """
    Delete duplicate contacts across all tenants or a specific tenant.
    Keeps the contact with the most data (highest richness score).

    Query Parameters:
    - tenant_id: Optional. If provided, only cleanup this tenant. Otherwise, process all tenants.
    - dry_run: If true, returns what would be deleted without actually deleting.

    This is a PUBLIC endpoint for administrative cleanup operations.
    """
    try:
        start_time = datetime.now()
        logger.info(f"Starting duplicate contact cleanup (tenant_id={tenant_id}, dry_run={dry_run})")

        # Build base query
        query = db.query(Contact)
        if tenant_id:
            query = query.filter(Contact.tenant_id == tenant_id)

        # Get all contacts
        all_contacts = query.all()

        if not all_contacts:
            return {
                "status": "success",
                "message": "No contacts found",
                "statistics": {
                    "total_contacts_scanned": 0,
                    "tenants_processed": 0,
                    "duplicates_found": 0,
                    "contacts_deleted": 0,
                    "contacts_kept": 0
                }
            }

        # Group contacts by tenant_id and phone number
        contact_groups: Dict[tuple, List[Contact]] = defaultdict(list)
        for contact in all_contacts:
            key = (contact.tenant_id, contact.phone)
            contact_groups[key].append(contact)

        # Track statistics
        stats = {
            "total_contacts_scanned": len(all_contacts),
            "tenants_processed": len(set(c.tenant_id for c in all_contacts)),
            "duplicates_found": 0,
            "contacts_deleted": 0,
            "contacts_kept": 0,
            "phone_numbers_with_duplicates": 0
        }

        # Detailed breakdown
        deletion_details: List[Dict[str, Any]] = []
        contacts_to_delete: List[int] = []

        # Process each group
        for (tenant, phone), contacts in contact_groups.items():
            if len(contacts) <= 1:
                # No duplicates
                continue

            # Found duplicates
            stats["duplicates_found"] += len(contacts) - 1
            stats["phone_numbers_with_duplicates"] += 1

            # Calculate richness for each contact
            contact_scores = []
            for contact in contacts:
                richness = calculate_contact_richness(contact)
                contact_scores.append({
                    "contact": contact,
                    "richness": richness,
                    "id": contact.id,
                    "created_on": contact.createdOn
                })

            # Sort by richness (desc), then by creation date (older = keep)
            contact_scores.sort(
                key=lambda x: (x["richness"], x["created_on"] or datetime.min),
                reverse=True
            )

            # Keep the richest (first one), delete the rest
            keeper = contact_scores[0]
            to_delete = contact_scores[1:]

            stats["contacts_kept"] += 1
            stats["contacts_deleted"] += len(to_delete)

            # Build deletion detail
            detail = {
                "tenant_id": tenant,
                "phone": phone,
                "total_duplicates": len(contacts),
                "kept_contact": {
                    "id": keeper["id"],
                    "richness_score": keeper["richness"],
                    "name": keeper["contact"].name,
                    "email": keeper["contact"].email,
                    "created_on": str(keeper["created_on"]) if keeper["created_on"] else None
                },
                "deleted_contacts": [
                    {
                        "id": item["id"],
                        "richness_score": item["richness"],
                        "name": item["contact"].name,
                        "email": item["contact"].email,
                        "created_on": str(item["created_on"]) if item["created_on"] else None
                    }
                    for item in to_delete
                ]
            }
            deletion_details.append(detail)

            # Add to deletion list
            contacts_to_delete.extend([item["id"] for item in to_delete])

        # Perform actual deletion if not dry run
        if not dry_run and contacts_to_delete:
            deleted_count = db.query(Contact).filter(
                Contact.id.in_(contacts_to_delete)
            ).delete(synchronize_session=False)

            db.commit()
            logger.info(f"Deleted {deleted_count} duplicate contacts")
        else:
            if dry_run:
                logger.info(f"Dry run completed. Would delete {len(contacts_to_delete)} contacts")

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        response = {
            "status": "success",
            "dry_run": dry_run,
            "message": f"{'Would delete' if dry_run else 'Deleted'} {stats['contacts_deleted']} duplicate contacts",
            "statistics": stats,
            "execution_time_seconds": round(duration, 2),
            "deletion_details": deletion_details[:50]  # Limit to first 50 for response size
        }

        if len(deletion_details) > 50:
            response["note"] = f"Showing first 50 of {len(deletion_details)} phone numbers with duplicates"

        return response

    except Exception as e:
        db.rollback()
        logger.error(f"Error during duplicate cleanup: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error during duplicate cleanup: {str(e)}"
        )