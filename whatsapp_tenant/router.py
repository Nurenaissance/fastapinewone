from fastapi import APIRouter, Request, Depends, HTTPException, Header, UploadFile, File, Form
from sqlalchemy import orm
from sqlalchemy.orm import joinedload, selectinload
from config.database import get_db
from .models import WhatsappTenantData, MessageStatus, BroadcastGroups, MessageStatistics, WhatsappChatIndividualMessageStatistics
from models import Tenant
from product.models import Product
from typing import Optional, List
from .schema import BroadcastGroupResponse, BroadcastGroupCreate, PromptUpdateRequest, BroadcastGroupContactDelete, BroadcastGroupAddContacts, BroadcastGroupMember, BroadcastGroupUpdateRules, RuleTestRequest
from .crud import create_broadcast_group, get_broadcast_group, get_all_broadcast_groups
from .rule_engine import RuleEvaluator
from .group_service import GroupService
from contacts.models import Contact
from datetime import timedelta
import asyncio
import aiohttp
from uuid import uuid4
from sqlalchemy.exc import IntegrityError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
import httpx
import pandas as pd
from io import BytesIO
from node_templates.models import NodeTemplate
import json
from config.cache import custom_cache, get_cache, set_cache, CACHE_TTL, cache_lock
import logging
from functools import lru_cache
import time

# Configure logging for better debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

# Cache timeout for different types of data
TENANT_CACHE_TTL = 300  # 5 minutes
STATUS_CACHE_TTL = 60   # 1 minute
GROUP_CACHE_TTL = 180   # 3 minutes

@router.post("/reset-cache")
def reset_cache(bpid: str = Header(default=None)):
    if not bpid:
        raise HTTPException(status_code=400, detail="bpid header must be provided.")

    keys_to_clear = [
        f"whatsapp_tenant:{bpid}",
        f"tenant_to_bpid:*",
        f"bpid_to_tenant:{bpid}",
        f"status_data:*"
    ]

    with cache_lock:
        cleared_count = 0
        for pattern in keys_to_clear:
            if '*' in pattern:
                # Clear all matching patterns
                base_pattern = pattern.replace('*', '')
                keys_to_remove = [k for k in custom_cache.keys() if k.startswith(base_pattern)]
                for key in keys_to_remove:
                    del custom_cache[key]
                    cleared_count += 1
            elif pattern in custom_cache:
                del custom_cache[pattern]
                cleared_count += 1

    return JSONResponse(content={"message": f"Cleared {cleared_count} cache entries"})

@router.get("/whatsapp_tenant")
def get_whatsapp_tenant_data(
    x_tenant_id: Optional[str] = Header(None),
    bpid: Optional[str] = Header(None),
    db: orm.Session = Depends(get_db)
):
    try:
        logger.info(f"Request - Tenant ID: {x_tenant_id}, BPID: {bpid}")

        # Resolve tenant_id and bpid with optimized caching
        tenant_id, bpid = _resolve_tenant_and_bpid(x_tenant_id, bpid, db)

        # Use bpid-based cache key for consistency
        cache_key = f"whatsapp_tenant:{bpid}"
        cached_response = get_cache(cache_key)
        
        if cached_response:
            logger.info("[CACHE HIT] Returning cached response")
            return cached_response

        logger.info(f"[CACHE MISS] Fetching data for key: {cache_key}")

        # Optimized database queries with eager loading
        response_data = _fetch_tenant_data_optimized(tenant_id, bpid, db)

        # Cache the response
        set_cache(cache_key, response_data)
        logger.info(f"[CACHE SET] Key: {cache_key}, TTL: {TENANT_CACHE_TTL}")

        return response_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error for tenant {x_tenant_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

def _resolve_tenant_and_bpid(x_tenant_id: Optional[str], bpid: Optional[str], db: orm.Session):
    """Optimized tenant and bpid resolution with better caching"""
    if x_tenant_id:
        if x_tenant_id == "demo":
            x_tenant_id = "ai"

        # Check cache first
        cached_bpid = get_cache(f"tenant_to_bpid:{x_tenant_id}")
        if cached_bpid:
            return x_tenant_id, cached_bpid

        # Database query with specific field selection
        record = db.query(WhatsappTenantData.business_phone_number_id)\
                   .filter(WhatsappTenantData.tenant_id == x_tenant_id)\
                   .order_by(WhatsappTenantData.id.asc())\
                   .first()
        
        if not record:
            raise HTTPException(status_code=404, detail="BPID not found for tenant")

        bpid = str(record.business_phone_number_id)
        # Cache both directions
        set_cache(f"tenant_to_bpid:{x_tenant_id}", bpid)
        set_cache(f"bpid_to_tenant:{bpid}", x_tenant_id)

        return x_tenant_id, bpid

    elif bpid:
        # Check cache first
        cached_tenant = get_cache(f"bpid_to_tenant:{bpid}")
        if cached_tenant:
            return cached_tenant, bpid

        # Database query
        record = db.query(WhatsappTenantData.tenant_id)\
                   .filter(WhatsappTenantData.business_phone_number_id == bpid)\
                   .first()
        
        if not record:
            raise HTTPException(status_code=404, detail="Tenant ID not found for BPID")

        tenant_id = str(record.tenant_id)
        # Cache both directions
        set_cache(f"bpid_to_tenant:{bpid}", tenant_id)
        set_cache(f"tenant_to_bpid:{tenant_id}", bpid)

        return tenant_id, bpid

    else:
        raise HTTPException(status_code=400, detail="Either X-Tenant-Id or BPID header must be provided")

def _fetch_tenant_data_optimized(tenant_id: str, bpid: str, db: orm.Session):
    """Optimized data fetching with reduced queries"""
    
    # Single query for WhatsApp data
    whatsapp_data = db.query(WhatsappTenantData)\
                      .filter(WhatsappTenantData.business_phone_number_id == bpid)\
                      .all()
    
    if not whatsapp_data:
        raise HTTPException(status_code=404, detail="WhatsappTenantData not found")

    # Optimized tenant query with eager loading of agents
    tenant_data = db.query(Tenant)\
               .filter(Tenant.id == tenant_id)\
               .first()
    
    if not tenant_data:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Optimized node templates query - only fetch required fields
    node_templates = db.query(NodeTemplate.id, NodeTemplate.name, NodeTemplate.trigger)\
                       .filter(NodeTemplate.tenant_id == tenant_id)\
                       .filter(NodeTemplate.trigger.isnot(None))\
                       .all()

    node_template_data = [
        {"id": nt.id, "name": nt.name, "trigger": nt.trigger}
        for nt in node_templates
    ]

    return {
        "whatsapp_data": jsonable_encoder(whatsapp_data),
        "agents": jsonable_encoder(tenant_data.agents),
        "triggers": node_template_data
    }

@router.patch("/whatsapp_tenant/")
async def update_whatsapp_tenant_data(
    req: Request, 
    x_tenant_id: Optional[str] = Header(None), 
    db: orm.Session = Depends(get_db)
):
    try:
        if not x_tenant_id:
            raise HTTPException(status_code=400, detail="Tenant-ID header must be provided")
        
        # Optimized query - only fetch what we need to update
        whatsapp_data = db.query(WhatsappTenantData)\
                          .filter(WhatsappTenantData.tenant_id == x_tenant_id)\
                          .all()
        
        if not whatsapp_data:
            raise HTTPException(status_code=404, detail="WhatsappTenantData not found for the given tenant")

        body = await req.json()

        # Batch update
        for record in whatsapp_data:
            for key, value in body.items():
                if hasattr(record, key):
                    setattr(record, key, value)

        db.commit()

        # Clear relevant cache entries
        bpid = whatsapp_data[0].business_phone_number_id
        with cache_lock:
            cache_keys_to_clear = [
                f"whatsapp_tenant:{bpid}",
                f"tenant_to_bpid:{x_tenant_id}",
                f"bpid_to_tenant:{bpid}"
            ]
            for key in cache_keys_to_clear:
                if key in custom_cache:
                    del custom_cache[key]

        return {
            "message": "WhatsappTenantData updated successfully", 
            "updated_records_count": len(whatsapp_data)
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Error updating tenant data for {x_tenant_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@router.get("/refresh-status/")
def refresh_status(request: Request, db: orm.Session = Depends(get_db)):
    """Optimized status refresh with better batch processing"""
    try:
        tenant_id = request.headers.get("X-Tenant-Id")
        if not tenant_id:
            raise HTTPException(status_code=400, detail="Missing Tenant ID in headers")

        # Check cache first
        cache_key = f"status_refresh:{tenant_id}"
        cached_result = get_cache(cache_key)
        if cached_result:
            logger.info("[CACHE HIT] Returning cached status refresh")
            return cached_result

        # Optimized query with indexing hints
        individual_stats = db.query(WhatsappChatIndividualMessageStatistics)\
                             .filter(WhatsappChatIndividualMessageStatistics.tenant_id == tenant_id)\
                             .all()

        # Process stats more efficiently
        message_status_map = {}
        for stat in individual_stats:
            msg_id = stat.message_id
            if msg_id not in message_status_map:
                message_status_map[msg_id] = {
                    "template_name": stat.template_name or "Unknown",
                    "status": set(),
                    "timestamp": stat.timestamp,
                    "userPhone": stat.userPhone,
                    "type": stat.type
                }
            
            if stat.status:
                message_status_map[msg_id]["status"].add(stat.status)
            
            if stat.template_name and message_status_map[msg_id]["template_name"] == "Unknown":
                message_status_map[msg_id]["template_name"] = stat.template_name

        # Build template stats
        template_stats = {}
        for message_id, data in message_status_map.items():
            template_name = data["template_name"]
            try:
                date_str = data["timestamp"].strftime("%Y-%m-%d") if data["timestamp"] else "unknown-date"
            except:
                date_str = "unknown-date"
                
            record_key = f"{template_name}_{date_str}"
            
            if record_key not in template_stats:
                template_stats[record_key] = {
                    "name": None,
                    "delivered": 0,
                    "read": 0,
                    "replied": 0,
                    "failed": 0,
                    "template_name": template_name
                }
            
            status_list = list(data["status"])
            
            # Count statuses
            if "delivered" in status_list:
                template_stats[record_key]["delivered"] += 1
            if "read" in status_list:
                template_stats[record_key]["read"] += 1
            if "failed" in status_list:
                template_stats[record_key]["failed"] += 1
                
            if data.get("type") == "reply" or "replied" in status_list:
                template_stats[record_key]["replied"] += 1

        # Batch database operations
        updated_count = 0
        try:
            for record_key, stats in template_stats.items():
                stats["sent"] = stats["delivered"] + stats["failed"]
                
                existing_record = db.query(MessageStatistics).filter(
                    MessageStatistics.tenant_id == tenant_id,
                    MessageStatistics.record_key == record_key
                ).first()

                if existing_record:
                    # Batch update
                    for attr, value in stats.items():
                        setattr(existing_record, attr, value)
                else:
                    new_record = MessageStatistics(
                        tenant_id=tenant_id,
                        record_key=record_key,
                        **stats
                    )
                    db.add(new_record)
                
                updated_count += 1

            db.commit()
            
            result = {
                "message": "Message statistics updated successfully",
                "updated_records": updated_count
            }
            
            # Cache the result
            set_cache(cache_key, result)
            
            return JSONResponse(content=jsonable_encoder(result))

        except Exception as e:
            db.rollback()
            logger.error(f"Database error during status refresh: {str(e)}")
            raise

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error in refresh_status: {str(e)}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@router.get("/get-status/")
def get_status(request: Request, db: orm.Session = Depends(get_db)):
    """Optimized status retrieval with caching"""
    try:
        tenant_id = request.headers.get("X-Tenant-Id")
        if not tenant_id:
            raise HTTPException(status_code=400, detail="Missing Tenant ID in headers")

        # Check cache first
        cache_key = f"status_data:{tenant_id}"
        cached_result = get_cache(cache_key)
        if cached_result:
            logger.info("[CACHE HIT] Returning cached status data")
            return cached_result

        # Optimized query - select only needed fields
        records = db.query(
            MessageStatistics.id,
            MessageStatistics.record_key,
            MessageStatistics.name,
            MessageStatistics.sent,
            MessageStatistics.delivered,
            MessageStatistics.read,
            MessageStatistics.replied,
            MessageStatistics.failed,
            MessageStatistics.template_name
        ).filter(MessageStatistics.tenant_id == tenant_id).all()

        # Transform data efficiently
        result = {
            record.record_key: {
                "name": record.name,
                "sent": record.sent,
                "delivered": record.delivered,
                "read": record.read,
                "replied": record.replied,
                "failed": record.failed,
                "template_name": record.template_name,
            }
            for record in records
        }

        # Cache the result
        set_cache(cache_key, result)

        return result

    except Exception as e:
        logger.error(f"Error retrieving status data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving data: {str(e)}")

def transform_data(input_list):
    """Optimized data transformation"""
    return {
        item["record_key"]: {k: v for k, v in item.items() if k not in ["record_key", "id"]}
        for item in input_list
    }

@router.post("/set-status/")
async def set_status(request: Request, db: orm.Session = Depends(get_db)):
    """Optimized status setting with better error handling"""
    try:
        data = await request.json()

        business_phone_number_id = data.get("business_phone_number_id")
        user_phone_number = data.get("user_phone_number")
        broadcast_group = data.get("broadcast_group")

        if not all([business_phone_number_id, user_phone_number, broadcast_group]):
            raise HTTPException(status_code=400, detail="Missing required fields")

        # Use get_or_create pattern for better performance
        message_status = db.query(MessageStatus).filter(
            MessageStatus.business_phone_number_id == business_phone_number_id,
            MessageStatus.user_phone_number == user_phone_number,
            MessageStatus.broadcast_group == broadcast_group
        ).first()

        if not message_status:
            message_status = MessageStatus(
                business_phone_number_id=business_phone_number_id,
                user_phone_number=user_phone_number,
                broadcast_group=broadcast_group,
                broadcast_group_name=data.get("broadcast_group_name"),
                sent=0,
                delivered=0,
                read=0,
                replied=0,
                failed=0,
            )
            db.add(message_status)

        # Update status counters
        status_fields = ["sent", "delivered", "read", "replied", "failed"]
        for field in status_fields:
            if field in data and isinstance(data[field], bool):
                current_value = getattr(message_status, field, 0)
                if data[field]:
                    setattr(message_status, field, current_value + 1)
                else:
                    setattr(message_status, field, max(current_value - 1, 0))

        db.commit()
        db.refresh(message_status)

        # Clear related cache
        tenant_id = data.get("tenant_id")
        if tenant_id:
            with cache_lock:
                status_cache_key = f"status_data:{tenant_id}"
                if status_cache_key in custom_cache:
                    del custom_cache[status_cache_key]

        return {"message": "Status updated successfully", "data": jsonable_encoder(message_status)}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error in set_status: {str(e)}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")

# Async helper for external API calls
async def make_async_request(session: aiohttp.ClientSession, method: str, url: str, timeout: int = 30, **kwargs):
    """Helper function for async HTTP requests with timeout"""
    try:
        timeout_obj = aiohttp.ClientTimeout(total=timeout)
        async with session.request(method, url, timeout=timeout_obj, **kwargs) as response:
            return response.status, await response.text()
    except asyncio.TimeoutError:
        logger.error(f"Request timeout for {url}")
        return None, "Request timeout"
    except Exception as e:
        logger.error(f"Async request failed for {url}: {str(e)}")
        return None, str(e)

async def create_group_logic(request: BroadcastGroupCreate, db, x_tenant_id):
    """Optimized group creation with better error handling"""
    try:
        members = [member.dict() for member in request.members]
        group_id = request.id or str(uuid4())

        new_group = BroadcastGroups(
            id=group_id,
            name=request.name,
            members=members,
            tenant_id=x_tenant_id,
            auto_rules=request.auto_rules
        )

        db.add(new_group)
        db.commit()
        db.refresh(new_group)

        # If auto_rules are enabled, sync members automatically
        if request.auto_rules and request.auto_rules.get('enabled'):
            logger.info(f"Auto-rules enabled for group {group_id}, syncing members...")
            sync_result = GroupService.sync_group_members(new_group, db)
            logger.info(f"Smart group created with {sync_result.get('members_after', 0)} members")

        # Clear group cache
        with cache_lock:
            group_cache_key = f"groups:{x_tenant_id}"
            if group_cache_key in custom_cache:
                del custom_cache[group_cache_key]

        return new_group

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating group: {str(e)}")
        raise

@router.post("/broadcast-groups/", response_model=BroadcastGroupResponse)
async def create_group(request: BroadcastGroupCreate, db: orm.Session = Depends(get_db), x_tenant_id: Optional[str] = Header(None)):
    try:
        if not x_tenant_id:
            raise HTTPException(status_code=400, detail="Missing X-Tenant-Id header")
            
        new_group = await create_group_logic(request, db, x_tenant_id)
        # Refresh to get updated members after sync
        db.refresh(new_group)
        return BroadcastGroupResponse(
            id=new_group.id,
            name=new_group.name,
            members=new_group.members,
            auto_rules=new_group.auto_rules
        )

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating group: {str(e)}")
        raise HTTPException(status_code=400, detail="Error creating the broadcast group")

@router.get("/broadcast-groups/", response_model=List[BroadcastGroupResponse])
def get_groups(db: orm.Session = Depends(get_db), x_tenant_id: Optional[str] = Header(None)):
    try:
        if not x_tenant_id:
            raise HTTPException(status_code=400, detail="Missing X-Tenant-Id header")
            
        # Check cache first
        cache_key = f"groups:{x_tenant_id}"
        cached_groups = get_cache(cache_key)
        if cached_groups:
            logger.info("[CACHE HIT] Returning cached groups")
            return cached_groups

        groups = get_all_broadcast_groups(x_tenant_id, db=db)
        
        # Cache the result
        set_cache(cache_key, groups)
        
        return groups
    except Exception as e:
        logger.error(f"Error fetching broadcast groups: {str(e)}")
        raise HTTPException(status_code=400, detail="Error fetching the broadcast groups")

@router.get("/broadcast-groups/{group_id}/", response_model=BroadcastGroupResponse)
def get_group(group_id: str, db: orm.Session = Depends(get_db), x_tenant_id: Optional[str] = Header(None)):
    try:
        # Check cache first
        cache_key = f"group:{group_id}"
        cached_group = get_cache(cache_key)
        if cached_group:
            logger.info("[CACHE HIT] Returning cached group")
            return cached_group
            
        group = get_broadcast_group(db=db, group_id=group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="Broadcast group not found")
            
        # Cache the result
        set_cache(cache_key, group)
        
        return group
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching group {group_id}: {str(e)}")
        raise HTTPException(status_code=400, detail="Error fetching the broadcast group")

@router.delete("/broadcast-groups/{group_id}/", response_model=dict)
def delete_group(group_id: str, db: orm.Session = Depends(get_db), x_tenant_id: Optional[str] = Header(None)):
    try:
        if not x_tenant_id:
            raise HTTPException(status_code=400, detail="Missing X-Tenant-Id header")
            
        # Fetch the group to check existence and tenant ownership
        group = db.query(BroadcastGroups).filter(
            BroadcastGroups.id == group_id, 
            BroadcastGroups.tenant_id == x_tenant_id
        ).first()
        
        if not group:
            raise HTTPException(
                status_code=404, 
                detail="Broadcast group not found or does not belong to the tenant"
            )
        
        # Delete the group
        db.delete(group)
        db.commit()
        
        # Clear related cache
        with cache_lock:
            cache_keys_to_clear = [
                f"groups:{x_tenant_id}",
                f"group:{group_id}"
            ]
            for key in cache_keys_to_clear:
                if key in custom_cache:
                    del custom_cache[key]
        
        return {"message": "Broadcast group deleted successfully"}
    
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting group {group_id}: {str(e)}")
        raise HTTPException(status_code=400, detail="Error deleting the broadcast group")

@router.post("/broadcast-groups/add-contacts/")
async def create_contact_and_add_to_group(
    payload: BroadcastGroupAddContacts,
    request: Request,
    db: orm.Session = Depends(get_db)
):
    tenant_id = request.headers.get("X-Tenant-Id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Missing X-Tenant-Id header")

    created_contacts = []
    skipped_contacts = []

    # Deduplicate contacts by phone number
    unique_contacts_dict = {contact.phone: contact for contact in payload.contacts}
    payload.contacts = list(unique_contacts_dict.values())

    # Use async HTTP client for better performance
    async with aiohttp.ClientSession() as session:
        tasks = []
        for contact in payload.contacts:
            contact_payload = {
                "phone": contact.phone,
                "name": contact.name,
                "tenant": tenant_id
            }
            
            task = make_async_request(
                session,
                'POST',
                "https://backeng4whatsapp-dxbmgpakhzf9bped.centralindia-01.azurewebsites.net/contacts/",
                json=contact_payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Tenant-Id": tenant_id
                }
            )
            tasks.append((contact.phone, task))

        # Process all requests concurrently
        for phone, task in tasks:
            try:
                status, response_text = await task
                if status == 201:
                    created_contacts.append(phone)
                else:
                    skipped_contacts.append({
                        "phone": phone,
                        "reason": response_text
                    })
            except Exception as e:
                skipped_contacts.append({
                    "phone": phone,
                    "reason": str(e)
                })

    # Add contacts to group
    response = await add_contacts_to_group(payload, request, db)
    response["created_contacts"] = created_contacts
    response["skipped_contacts"] = skipped_contacts

    return response

@router.post("/broadcast-groups/excel/")
async def upload_and_add_contacts(
    request: Request,
    db: orm.Session = Depends(get_db),
    file: UploadFile = File(...),
    name: str = Form(...),
    model_name: str = Form("Contact"),
    x_tenant_id: Optional[str] = Header(None),
):
    """
    Excel upload endpoint
    Supports:
    1. Old format -> single sheet with contact data
    2. New format -> sheet 1 = instructions, sheet 2 = contact data
    """

    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="Missing X-Tenant-Id header")

    start_time = time.time()

    try:
        # -----------------------------
        # READ FILE
        # -----------------------------
        file_bytes = await file.read()
        logger.info(f"File read completed in {time.time() - start_time:.2f}s")

        # -----------------------------
        # ASYNC EXTERNAL UPLOAD
        # -----------------------------
        async with aiohttp.ClientSession() as session:
            files_data = aiohttp.FormData()
            files_data.add_field(
                "file",
                file_bytes,
                filename=file.filename,
                content_type=file.content_type,
            )
            files_data.add_field("model_name", model_name)

            status, response_text = await make_async_request(
                session,
                "POST",
                "https://backeng4whatsapp-dxbmgpakhzf9bped.centralindia-01.azurewebsites.net/upload/",
                data=files_data,
                headers={
                    "X-Tenant-Id": x_tenant_id,
                    "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ3aGF0c2FwcF9ib3QiLCJ0ZW5hbnRfaWQiOiJhaSIsInJvbGUiOiJzeXN0ZW0iLCJ0aWVyIjoiZW50ZXJwcmlzZSIsInNjb3BlIjoic2VydmljZSIsImV4cCI6MjA1NDk3NDY2OX0.SLXxiBy00-NP9dBVcPl-9b5E0QtakNUajRKAjeXgFG8",
                },
            )

            if status != 200:
                raise HTTPException(
                    status_code=400, detail=f"Upload failed: {response_text}"
                )

        # -----------------------------
        # EXCEL PARSING (OLD + NEW FORMAT)
        # -----------------------------
        try:
            excel_file = pd.ExcelFile(BytesIO(file_bytes))
            sheet_names = [s.lower() for s in excel_file.sheet_names]

            # Decide data sheet
            if len(sheet_names) > 1 and "instruction" in sheet_names[0]:
                data_sheet_index = 1
            else:
                data_sheet_index = 0

            df = pd.read_excel(
                excel_file,
                sheet_name=data_sheet_index,
                dtype={"phone": str},
                na_values=["", "nan", "NaN", "NULL", "null"],
            )

        except Exception as e:
            raise HTTPException(
                status_code=400, detail=f"Failed to parse Excel file: {str(e)}"
            )

        logger.info(f"Excel parsing completed in {time.time() - start_time:.2f}s")

        # -----------------------------
        # CLEAN & VALIDATE DATA
        # -----------------------------
        df.columns = [col.strip().lower() for col in df.columns]
        logger.info(f"Available columns: {list(df.columns)}")

        if "phone" not in df.columns:
            raise HTTPException(
                status_code=400,
                detail="Excel must contain a 'phone' column in data sheet",
            )

        # -----------------------------
        # PROCESS CONTACTS
        # -----------------------------
        contact_models = []

        for _, row in df.iterrows():
            contact_name = _extract_contact_name(row, df.columns)
            final_phone = _process_phone_number(row.get("phone"))

            if final_phone:
                display_name = contact_name if contact_name else final_phone
                contact_models.append(
                    BroadcastGroupMember(
                        phone=final_phone,
                        name=display_name,
                    )
                )

        if not contact_models:
            raise HTTPException(
                status_code=400,
                detail="No valid phone numbers found in Excel sheet",
            )

        logger.info(f"Contact processing completed in {time.time() - start_time:.2f}s")

        # -----------------------------
        # CREATE GROUP
        # -----------------------------
        group_id = str(uuid4())
        group_create_payload = BroadcastGroupCreate(
            id=group_id,
            name=name,
            members=contact_models,
        )

        new_group = await create_group_logic(
            group_create_payload, db, x_tenant_id
        )

        logger.info(f"Total processing time: {time.time() - start_time:.2f}s")

        # -----------------------------
        # RESPONSE
        # -----------------------------
        return {
            "message": "Contacts uploaded and group created successfully.",
            "group_id": new_group.id,
            "group_name": new_group.name,
            "total_contacts_added": len(contact_models),
            "sample_contacts": [
                {"phone": c.phone, "name": c.name}
                for c in contact_models[:10]
            ],
            "processing_time": f"{time.time() - start_time:.2f}s",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error in Excel upload")
        raise HTTPException(
            status_code=500, detail=f"Failed to process Excel file: {str(e)}"
        )

# ===================================
# AUTO-RULES ENDPOINTS FOR DYNAMIC GROUP MEMBERSHIP
# ===================================

@router.put("/broadcast-groups/{group_id}/rules")
async def update_group_rules(
    group_id: str,
    payload: BroadcastGroupUpdateRules,
    request: Request,
    db: orm.Session = Depends(get_db)
):
    """Update auto-rules for a broadcast group and sync members"""
    try:
        tenant_id = request.headers.get("X-Tenant-Id")
        if not tenant_id:
            raise HTTPException(status_code=400, detail="Missing X-Tenant-Id header")

        # Get group
        group = db.query(BroadcastGroups).filter(
            BroadcastGroups.id == group_id,
            BroadcastGroups.tenant_id == tenant_id
        ).first()

        if not group:
            raise HTTPException(status_code=404, detail="Group not found")

        # Update rules
        group.auto_rules = payload.auto_rules.dict()

        # Sync members if rules are enabled
        sync_result = GroupService.sync_group_members(group, db)

        # Clear cache
        with cache_lock:
            cache_keys = [
                f"groups:{tenant_id}",
                f"group:{group_id}"
            ]
            for key in cache_keys:
                if key in custom_cache:
                    del custom_cache[key]

        return {
            "message": "Rules updated successfully",
            "group_id": group_id,
            "sync_result": sync_result
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating group rules: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating rules: {str(e)}")


@router.post("/broadcast-groups/{group_id}/sync")
async def sync_group_members(
    group_id: str,
    request: Request,
    db: orm.Session = Depends(get_db)
):
    """Manually trigger synchronization of group members based on rules"""
    try:
        tenant_id = request.headers.get("X-Tenant-Id")
        if not tenant_id:
            raise HTTPException(status_code=400, detail="Missing X-Tenant-Id header")

        # Get group
        group = db.query(BroadcastGroups).filter(
            BroadcastGroups.id == group_id,
            BroadcastGroups.tenant_id == tenant_id
        ).first()

        if not group:
            raise HTTPException(status_code=404, detail="Group not found")

        # Sync members
        sync_result = GroupService.sync_group_members(group, db)

        # Clear cache
        with cache_lock:
            cache_keys = [
                f"groups:{tenant_id}",
                f"group:{group_id}"
            ]
            for key in cache_keys:
                if key in custom_cache:
                    del custom_cache[key]

        return {
            "message": "Group synchronized successfully",
            "group_id": group_id,
            "result": sync_result
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error syncing group: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error syncing group: {str(e)}")


@router.post("/broadcast-groups/test-rules")
async def test_rules(
    payload: RuleTestRequest,
    request: Request,
    db: orm.Session = Depends(get_db)
):
    """Test auto-rules against contacts without saving"""
    try:
        tenant_id = request.headers.get("X-Tenant-Id")
        if not tenant_id:
            raise HTTPException(status_code=400, detail="Missing X-Tenant-Id header")

        rules_dict = payload.rules.dict()

        # If specific contact provided, test against it
        if payload.sample_contact_id:
            contact = db.query(Contact).filter(
                Contact.id == payload.sample_contact_id,
                Contact.tenant_id == tenant_id
            ).first()

            if not contact:
                raise HTTPException(status_code=404, detail="Contact not found")

            matches = RuleEvaluator.evaluate_contact(contact, rules_dict)

            return {
                "test_mode": "single_contact",
                "contact_id": contact.id,
                "contact_phone": contact.phone,
                "contact_name": contact.name,
                "matches": matches
            }

        # Otherwise, get all matching contacts
        matching_contacts = RuleEvaluator.get_matching_contacts(
            db, tenant_id, rules_dict
        )

        return {
            "test_mode": "all_contacts",
            "total_matches": len(matching_contacts),
            "sample_matches": [
                {
                    "id": c.id,
                    "name": c.name,
                    "phone": c.phone,
                    "createdOn": c.createdOn.isoformat() if c.createdOn else None
                }
                for c in matching_contacts[:10]  # First 10
            ]
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error testing rules: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error testing rules: {str(e)}")


@router.delete("/broadcast-groups/{group_id}/rules")
async def reset_group_rules(
    group_id: str,
    request: Request,
    keep_members: bool = False,
    db: orm.Session = Depends(get_db)
):
    """
    Reset/disable auto-rules for a broadcast group

    Query Parameters:
    - keep_members: If True, keeps existing members. If False (default), clears members list.
    """
    try:
        tenant_id = request.headers.get("X-Tenant-Id")
        if not tenant_id:
            raise HTTPException(status_code=400, detail="Missing X-Tenant-Id header")

        # Get group
        group = db.query(BroadcastGroups).filter(
            BroadcastGroups.id == group_id,
            BroadcastGroups.tenant_id == tenant_id
        ).first()

        if not group:
            raise HTTPException(status_code=404, detail="Group not found")

        # Store counts before reset
        members_before = len(group.members or [])
        had_rules = bool(group.auto_rules)

        # Reset rules
        group.auto_rules = None

        # Optionally clear members
        if not keep_members:
            group.members = []

        db.commit()

        # Clear cache
        with cache_lock:
            cache_keys = [
                f"groups:{tenant_id}",
                f"group:{group_id}"
            ]
            for key in cache_keys:
                if key in custom_cache:
                    del custom_cache[key]

        return {
            "message": "Automation reset successfully",
            "group_id": group_id,
            "group_name": group.name,
            "had_rules": had_rules,
            "members_before": members_before,
            "members_after": len(group.members or []),
            "members_kept": keep_members
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error resetting group rules: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error resetting automation: {str(e)}")

# ===================================
# END AUTO-RULES ENDPOINTS
# ===================================

def _extract_contact_name(row, columns):
    """Helper to extract contact name from Excel row"""
    first_name = str(row.get("first_name", "")).strip() if "first_name" in columns else ""
    last_name = str(row.get("last_name", "")).strip() if "last_name" in columns else ""
    
    # Clean up 'nan' values
    if first_name.lower() == "nan":
        first_name = ""
    if last_name.lower() == "nan":
        last_name = ""
        
    contact_name = f"{first_name} {last_name}".strip()
    
    # Fallback to company name
    if not contact_name and "company_name" in columns:
        company = str(row.get("company_name", "")).strip()
        if company.lower() != "nan":
            contact_name = company
            
    return contact_name

import re
import phonenumbers

def _process_phone_number(phone_val):
    if pd.isna(phone_val) or str(phone_val).strip().lower() in ["nan", ""]:
        return None

    raw_phone = str(phone_val).strip()
    cleaned = re.sub(r"[^\d+]", "", raw_phone)

    # Try parsing with Google libphonenumber
    try:
        if not cleaned.startswith("+"):
            cleaned = "+" + cleaned

        number = phonenumbers.parse(cleaned, None)

        if phonenumbers.is_valid_number(number):
            # return E.164 format: +919876543210
            return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    except:
        pass

    # Fallback: Indian logic (your existing rules)
    digits = re.sub(r"\D", "", cleaned)

    if len(digits) == 10:
        return f"+91{digits}"
    if len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"

    return None

@router.post("/")
async def add_contacts_to_group(
    payload: BroadcastGroupAddContacts,
    request: Request,
    db: orm.Session = Depends(get_db)
):
    """Optimized contact addition to group"""
    try:
        tenant_id = request.headers.get("X-Tenant-Id")
        if not tenant_id:
            raise HTTPException(status_code=400, detail="Missing X-Tenant-Id")

        # Optimized query with specific fields
        group = db.query(BroadcastGroups).filter(
            BroadcastGroups.name == payload.groupName,
            BroadcastGroups.tenant_id == tenant_id
        ).first()

        if not group:
            raise HTTPException(status_code=404, detail="Broadcast group not found")

        existing_members = group.members or []
        existing_phones = {str(member["phone"]) for member in existing_members}

        new_contacts = [
            {"phone": contact.phone, "name": contact.name or str(contact.phone)}
            for contact in payload.contacts
            if str(contact.phone) not in existing_phones
        ]

        if not new_contacts:
            return {
                "message": "No new contacts to add - all contacts already exist",
                "addedContacts": [],
                "totalMembers": len(existing_members)
            }

        group.members = existing_members + new_contacts
        db.commit()

        # Clear related cache
        with cache_lock:
            cache_keys_to_clear = [
                f"groups:{tenant_id}",
                f"group:{group.id}"
            ]
            for key in cache_keys_to_clear:
                if key in custom_cache:
                    del custom_cache[key]

        return {
            "message": "Contacts added successfully",
            "addedContacts": new_contacts,
            "totalMembers": len(group.members)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error adding contacts to group: {str(e)}")
        raise HTTPException(status_code=500, detail="Error adding contacts to group")

@router.delete("/broadcast-group/delete-contact/")
async def delete_contact_from_group(
    payload: BroadcastGroupContactDelete,
    request: Request,
    db: orm.Session = Depends(get_db)
):
    """Optimized contact deletion from group"""
    try:
        tenant_id = request.headers.get("X-Tenant-Id")
        if not tenant_id:
            raise HTTPException(status_code=400, detail="Missing X-Tenant-Id header")

        # Get the group based on name and tenant ID
        group = db.query(BroadcastGroups).filter(
            BroadcastGroups.name == payload.groupName,
            BroadcastGroups.tenant_id == tenant_id
        ).first()

        if not group:
            raise HTTPException(status_code=404, detail="Broadcast group not found")

        original_members = group.members or []
        original_count = len(original_members)

        # Filter out the contact with the given phone
        updated_members = [
            member for member in original_members
            if str(member.get("phone")) != str(payload.contactPhone)
        ]

        # If no contact was removed, it means contact was not in group
        if len(updated_members) == original_count:
            raise HTTPException(status_code=404, detail="Contact not found in group")

        # Update and save
        group.members = updated_members
        db.commit()

        # Clear related cache
        with cache_lock:
            cache_keys_to_clear = [
                f"groups:{tenant_id}",
                f"group:{group.id}"
            ]
            for key in cache_keys_to_clear:
                if key in custom_cache:
                    del custom_cache[key]

        return {
            "message": "Contact deleted successfully",
            "groupName": payload.groupName,
            "remainingMembers": len(updated_members),
            "deletedContact": payload.contactPhone
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting contact from group: {str(e)}")
        raise HTTPException(status_code=500, detail="Error deleting contact from group")

@router.post("/message-statistics/")
@router.patch("/message-statistics/")
def create_or_update_message_statistics(name: str, tenant_id: str, data: dict, db: orm.Session = Depends(get_db)):
    """
    Optimized message statistics creation/update with better error handling
    """
    try:
        entry = db.query(MessageStatistics).filter_by(name=name, tenant_id=tenant_id).first()

        if entry:
            # Update existing entry
            for key, value in data.items():
                if hasattr(entry, key):
                    setattr(entry, key, value)
            db.commit()
            db.refresh(entry)
            
            # Clear related cache
            with cache_lock:
                cache_key = f"status_data:{tenant_id}"
                if cache_key in custom_cache:
                    del custom_cache[cache_key]
                    
            return {"message": "Entry updated successfully", "data": jsonable_encoder(entry)}
        else:
            # Create new entry
            new_entry = MessageStatistics(name=name, tenant_id=tenant_id, **data)
            db.add(new_entry)
            db.commit()
            db.refresh(new_entry)
            
            # Clear related cache
            with cache_lock:
                cache_key = f"status_data:{tenant_id}"
                if cache_key in custom_cache:
                    del custom_cache[cache_key]
                    
            return {"message": "Entry created successfully", "data": jsonable_encoder(new_entry)}

    except IntegrityError as e:
        db.rollback()
        logger.error(f"Integrity error in message statistics: {str(e)}")
        raise HTTPException(status_code=400, detail="Integrity error. Please check the provided data.")
    except Exception as e:
        db.rollback()
        logger.error(f"Error in message statistics operation: {str(e)}")
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")

@router.get("/prompt/fetch/")
def get_whatsapp_prompt(
    x_tenant_id: Optional[str] = Header(None),
    db: orm.Session = Depends(get_db)
):
    """Optimized prompt fetching with caching"""
    try:
        if not x_tenant_id:
            raise HTTPException(status_code=400, detail="Missing X-Tenant-ID header")

        # Check cache first
        cache_key = f"prompt:{x_tenant_id}"
        cached_prompt = get_cache(cache_key)
        if cached_prompt:
            logger.info("[CACHE HIT] Returning cached prompt")
            return cached_prompt

        # Optimized query - only fetch required field
        tenant_data = db.query(WhatsappTenantData.prompt).filter_by(tenant_id=x_tenant_id).first()

        if not tenant_data:
            raise HTTPException(status_code=404, detail="Tenant data not found")

        result = {"tenant_id": x_tenant_id, "prompt": tenant_data.prompt}
        
        # Cache the result
        set_cache(cache_key, result)
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching prompt for tenant {x_tenant_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching prompt")

@router.post("/prompt/create/")
def create_whatsapp_prompt(
    data: PromptUpdateRequest,
    x_tenant_id: Optional[str] = Header(None),
    db: orm.Session = Depends(get_db)
):
    """Optimized prompt creation"""
    try:
        if not x_tenant_id:
            raise HTTPException(status_code=400, detail="Missing X-Tenant-ID header")

        tenant_data = db.query(WhatsappTenantData).filter_by(tenant_id=x_tenant_id).first()

        if not tenant_data:
            raise HTTPException(status_code=404, detail="Tenant data not found")

        if tenant_data.prompt:
            raise HTTPException(status_code=400, detail="Prompt already exists. Use PATCH to update.")

        tenant_data.prompt = data.prompt
        db.commit()

        # Clear related cache
        with cache_lock:
            cache_key = f"prompt:{x_tenant_id}"
            if cache_key in custom_cache:
                del custom_cache[cache_key]

        return {"tenant_id": x_tenant_id, "prompt": tenant_data.prompt, "message": "Prompt added successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating prompt for tenant {x_tenant_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error creating prompt")

@router.patch("/prompt/edit/")
def update_whatsapp_prompt(
    data: PromptUpdateRequest,
    x_tenant_id: Optional[str] = Header(None),
    db: orm.Session = Depends(get_db)
):
    """Optimized prompt updating"""
    try:
        if not x_tenant_id:
            raise HTTPException(status_code=400, detail="Missing X-Tenant-ID header")

        tenant_data = db.query(WhatsappTenantData).filter_by(tenant_id=x_tenant_id).first()

        if not tenant_data:
            raise HTTPException(status_code=404, detail="Tenant data not found")

        tenant_data.prompt = data.prompt
        db.commit()

        # Clear related cache
        with cache_lock:
            cache_key = f"prompt:{x_tenant_id}"
            if cache_key in custom_cache:
                del custom_cache[cache_key]

        return {"tenant_id": x_tenant_id, "prompt": tenant_data.prompt, "message": "Prompt updated successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating prompt for tenant {x_tenant_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error updating prompt")

@router.delete("/prompt/delete/")
def delete_whatsapp_prompt(
    x_tenant_id: Optional[str] = Header(None),
    db: orm.Session = Depends(get_db)
):
    """Optimized prompt deletion"""
    try:
        if not x_tenant_id:
            raise HTTPException(status_code=400, detail="Missing X-Tenant-ID header")

        tenant_data = db.query(WhatsappTenantData).filter_by(tenant_id=x_tenant_id).first()

        if not tenant_data:
            raise HTTPException(status_code=404, detail="Tenant data not found")

        if not tenant_data.prompt:
            raise HTTPException(status_code=404, detail="No prompt to delete")

        tenant_data.prompt = None
        db.commit()

        # Clear related cache
        with cache_lock:
            cache_key = f"prompt:{x_tenant_id}"
            if cache_key in custom_cache:
                del custom_cache[cache_key]

        return {"tenant_id": x_tenant_id, "message": "Prompt deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting prompt for tenant {x_tenant_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error deleting prompt")

@router.get("/tenants/ids")
def get_all_tenant_ids(db: orm.Session = Depends(get_db)):
    """Optimized tenant ID fetching with caching"""
    try:
        # Check cache first
        cache_key = "all_tenant_ids"
        cached_ids = get_cache(cache_key)
        if cached_ids:
            logger.info("[CACHE HIT] Returning cached tenant IDs")
            return cached_ids

        # Optimized query - only fetch IDs
        tenant_ids = db.query(Tenant.id).all()
        result = {"tenant_ids": [tenant_id[0] for tenant_id in tenant_ids]}
        
        # Cache the result with shorter TTL since this might change frequently
        set_cache(cache_key, result)  # 1 minute cache
        
        return result
        
    except Exception as e:
        logger.error(f"Error fetching tenant IDs: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching tenant IDs")