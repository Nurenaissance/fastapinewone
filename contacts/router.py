from fastapi import APIRouter, Request, Depends, HTTPException, responses
from sqlalchemy import orm, or_, and_, text, nulls_first, nulls_last, func
from config.database import get_db
from .models import Contact
from whatsapp_tenant.models import WhatsappTenantData
from whatsapp_tenant.group_service import GroupService
from typing import Optional
from datetime import datetime, timedelta
import math

router = APIRouter()

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
            customField=body.get('customField')
            # manual_mode=body.get('manual_mode', False)  # TODO: Uncomment when column is added to database
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
        # if 'manual_mode' in body:  # TODO: Uncomment when column is added to database
        #     contact.manual_mode = body['manual_mode']

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


# TODO: Uncomment when manual_mode column is added to database
# @router.patch("/contacts/{contact_id}/manual-mode")
# async def toggle_manual_mode(
#     contact_id: int,
#     request: Request,
#     db: orm.Session = Depends(get_db)
# ):
#     """Toggle manual mode for a contact to disable/enable automation"""
#     tenant_id = request.headers.get("X-Tenant-Id")
#     if not tenant_id:
#         raise HTTPException(status_code=400, detail="Tenant ID missing in headers")
#
#     try:
#         body = await request.json()
#         manual_mode = body.get('manual_mode')
#
#         if manual_mode is None:
#             raise HTTPException(status_code=400, detail="manual_mode field is required")
#
#         # Find contact
#         contact = db.query(Contact).filter(
#             Contact.id == contact_id,
#             Contact.tenant_id == tenant_id
#         ).first()
#
#         if not contact:
#             raise HTTPException(
#                 status_code=404,
#                 detail=f"Contact with ID {contact_id} not found for this tenant"
#             )
#
#         # Update manual mode
#         contact.manual_mode = manual_mode
#         db.commit()
#         db.refresh(contact)
#
#         return {
#             "contact_id": contact.id,
#             "phone": contact.phone,
#             "name": contact.name,
#             "manual_mode": contact.manual_mode,
#             "message": f"Manual mode {'enabled' if manual_mode else 'disabled'} for contact"
#         }
#
#     except HTTPException:
#         raise
#     except Exception as e:
#         db.rollback()
#         raise HTTPException(status_code=500, detail=f"Error toggling manual mode: {str(e)}")


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