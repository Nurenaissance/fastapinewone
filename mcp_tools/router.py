from fastapi import APIRouter, Request, Depends, HTTPException, Query
from sqlalchemy import orm, func
from sqlalchemy.exc import IntegrityError
from config.database import get_db
from .models import MCPToolDefinition, MCPToolExecution
from .schema import (
    MCPToolCreate, MCPToolUpdate, MCPToolResponse, MCPToolListResponse,
    MCPToolTestRequest, MCPToolTestResponse,
    MCPToolExecutionResponse, MCPToolExecutionListResponse,
    MCPToolForNodeJS, MCPToolsForNodeJSResponse
)
from typing import Optional
from datetime import datetime
from uuid import UUID
import hashlib
import json
import httpx
import re
import logging

router = APIRouter(prefix="/mcp-tools", tags=["MCP Tools"])
logger = logging.getLogger(__name__)


def get_tenant_id(request: Request) -> Optional[str]:
    """Get tenant_id from headers (supports both X-Tenant-Id and X-Tenant-ID)."""
    return request.headers.get("X-Tenant-Id") or request.headers.get("X-Tenant-ID")


def interpolate_url(url: str, params: dict) -> str:
    """Replace ${var} placeholders in URL with actual values."""
    result = url
    for key, value in params.items():
        result = result.replace(f"${{{key}}}", str(value))
    return result


def render_template(template: str, data: dict) -> str:
    """Simple Jinja2-style template rendering with {{var}} placeholders."""
    if not template:
        return json.dumps(data) if data else ""

    result = template
    # Handle nested data access like {{response.status}}
    for key, value in flatten_dict(data).items():
        result = result.replace(f"{{{{{key}}}}}", str(value) if value is not None else "")

    # Clean up any remaining placeholders
    result = re.sub(r'\{\{[^}]+\}\}', '', result)
    return result.strip()


def flatten_dict(d: dict, parent_key: str = '', sep: str = '.') -> dict:
    """Flatten nested dictionary for template rendering."""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
        # Also add without parent prefix for direct access
        items.append((k, v))
    return dict(items)


def compute_cache_version(tools: list) -> str:
    """Compute MD5 hash of tools for cache invalidation."""
    content = json.dumps([{
        'id': str(t.id),
        'name': t.name,
        'updated_at': t.updated_at.isoformat() if t.updated_at else ''
    } for t in tools], sort_keys=True)
    return hashlib.md5(content.encode()).hexdigest()[:12]


# ============================================================================
# CRUD Endpoints
# ============================================================================

@router.get("/tenant/{tenant_id}", response_model=MCPToolsForNodeJSResponse)
def get_tools_for_nodejs(
    tenant_id: str,
    db: orm.Session = Depends(get_db)
):
    """
    Get all active tools for a tenant (optimized for Node.js caching).
    This endpoint is called by the Node.js webhook server.
    """
    tools = (
        db.query(MCPToolDefinition)
        .filter(
            MCPToolDefinition.tenant_id == tenant_id,
            MCPToolDefinition.is_active == True
        )
        .order_by(MCPToolDefinition.priority.desc(), MCPToolDefinition.name)
        .all()
    )

    return MCPToolsForNodeJSResponse(
        tenant_id=tenant_id,
        tools=[
            MCPToolForNodeJS(
                id=str(tool.id),
                name=tool.name,
                description=tool.description,
                endpoint_url=tool.endpoint_url,
                http_method=tool.http_method,
                auth_type=tool.auth_type,
                auth_config=tool.auth_config,
                parameters=tool.parameters,
                headers=tool.headers,
                request_body_template=tool.request_body_template,
                trigger_keywords=tool.trigger_keywords or [],
                trigger_intents=tool.trigger_intents or [],
                response_template=tool.response_template,
                error_template=tool.error_template or "Sorry, I couldn't complete that action.",
                cache_ttl_seconds=tool.cache_ttl_seconds or 0,
                timeout_seconds=tool.timeout_seconds,
                retry_count=tool.retry_count,
                priority=tool.priority
            )
            for tool in tools
        ],
        cache_version=compute_cache_version(tools),
        fetched_at=datetime.utcnow()
    )


@router.get("", response_model=MCPToolListResponse)
def list_tools(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    db: orm.Session = Depends(get_db)
):
    """List all MCP tools for the tenant with pagination."""
    tenant_id = get_tenant_id(request)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID missing in headers")

    query = db.query(MCPToolDefinition).filter(MCPToolDefinition.tenant_id == tenant_id)

    if is_active is not None:
        query = query.filter(MCPToolDefinition.is_active == is_active)

    if search:
        search_filter = f"%{search.lower()}%"
        query = query.filter(
            (MCPToolDefinition.name.ilike(search_filter)) |
            (MCPToolDefinition.description.ilike(search_filter))
        )

    total = query.count()
    offset = (page - 1) * page_size

    tools = (
        query
        .order_by(MCPToolDefinition.priority.desc(), MCPToolDefinition.name)
        .offset(offset)
        .limit(page_size)
        .all()
    )

    return MCPToolListResponse(
        tools=[MCPToolResponse.model_validate(t) for t in tools],
        total=total,
        page=page,
        page_size=page_size
    )


@router.get("/{tool_id}", response_model=MCPToolResponse)
def get_tool(
    tool_id: UUID,
    request: Request,
    db: orm.Session = Depends(get_db)
):
    """Get a specific MCP tool by ID."""
    tenant_id = get_tenant_id(request)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID missing in headers")

    tool = (
        db.query(MCPToolDefinition)
        .filter(
            MCPToolDefinition.id == tool_id,
            MCPToolDefinition.tenant_id == tenant_id
        )
        .first()
    )

    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")

    return MCPToolResponse.model_validate(tool)


@router.post("", response_model=MCPToolResponse, status_code=201)
async def create_tool(
    tool_data: MCPToolCreate,
    request: Request,
    db: orm.Session = Depends(get_db)
):
    """Create a new MCP tool definition."""
    tenant_id = get_tenant_id(request)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID missing in headers")

    # Check for duplicate name
    existing = (
        db.query(MCPToolDefinition)
        .filter(
            MCPToolDefinition.tenant_id == tenant_id,
            MCPToolDefinition.name == tool_data.name
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Tool with name '{tool_data.name}' already exists for this tenant"
        )

    try:
        new_tool = MCPToolDefinition(
            tenant_id=tenant_id,
            name=tool_data.name,
            description=tool_data.description,
            endpoint_url=tool_data.endpoint_url,
            http_method=tool_data.http_method.value,
            auth_type=tool_data.auth_type.value,
            auth_config=tool_data.auth_config,
            parameters=tool_data.parameters,
            headers=tool_data.headers,
            request_body_template=tool_data.request_body_template,
            trigger_keywords=tool_data.trigger_keywords,
            trigger_intents=tool_data.trigger_intents,
            response_template=tool_data.response_template,
            error_template=tool_data.error_template,
            cache_ttl_seconds=tool_data.cache_ttl_seconds,
            timeout_seconds=tool_data.timeout_seconds,
            retry_count=tool_data.retry_count,
            is_active=tool_data.is_active,
            priority=tool_data.priority
        )

        db.add(new_tool)
        db.commit()
        db.refresh(new_tool)

        logger.info(f"Created MCP tool '{new_tool.name}' for tenant {tenant_id}")
        return MCPToolResponse.model_validate(new_tool)

    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Database integrity error: {str(e)}")
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating MCP tool: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating tool: {str(e)}")


@router.put("/{tool_id}", response_model=MCPToolResponse)
async def update_tool(
    tool_id: UUID,
    tool_data: MCPToolUpdate,
    request: Request,
    db: orm.Session = Depends(get_db)
):
    """Update an existing MCP tool definition."""
    tenant_id = get_tenant_id(request)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID missing in headers")

    tool = (
        db.query(MCPToolDefinition)
        .filter(
            MCPToolDefinition.id == tool_id,
            MCPToolDefinition.tenant_id == tenant_id
        )
        .first()
    )

    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")

    try:
        # Update only provided fields
        update_data = tool_data.model_dump(exclude_unset=True)

        for field, value in update_data.items():
            if value is not None:
                # Handle enum conversion
                if field in ['http_method', 'auth_type'] and hasattr(value, 'value'):
                    value = value.value
                setattr(tool, field, value)

        tool.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(tool)

        logger.info(f"Updated MCP tool '{tool.name}' for tenant {tenant_id}")
        return MCPToolResponse.model_validate(tool)

    except Exception as e:
        db.rollback()
        logger.error(f"Error updating MCP tool: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating tool: {str(e)}")


@router.delete("/{tool_id}", status_code=204)
async def delete_tool(
    tool_id: UUID,
    request: Request,
    db: orm.Session = Depends(get_db)
):
    """Delete an MCP tool definition."""
    tenant_id = get_tenant_id(request)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID missing in headers")

    deleted_count = (
        db.query(MCPToolDefinition)
        .filter(
            MCPToolDefinition.id == tool_id,
            MCPToolDefinition.tenant_id == tenant_id
        )
        .delete()
    )

    if deleted_count == 0:
        raise HTTPException(status_code=404, detail="Tool not found")

    db.commit()
    logger.info(f"Deleted MCP tool {tool_id} for tenant {tenant_id}")
    return None


@router.patch("/{tool_id}/toggle", response_model=MCPToolResponse)
async def toggle_tool_active(
    tool_id: UUID,
    request: Request,
    db: orm.Session = Depends(get_db)
):
    """Toggle a tool's active status."""
    tenant_id = get_tenant_id(request)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID missing in headers")

    tool = (
        db.query(MCPToolDefinition)
        .filter(
            MCPToolDefinition.id == tool_id,
            MCPToolDefinition.tenant_id == tenant_id
        )
        .first()
    )

    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")

    tool.is_active = not tool.is_active
    tool.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(tool)

    return MCPToolResponse.model_validate(tool)


# ============================================================================
# Tool Testing Endpoint
# ============================================================================

@router.post("/{tool_id}/test", response_model=MCPToolTestResponse)
async def test_tool(
    tool_id: UUID,
    test_data: MCPToolTestRequest,
    request: Request,
    db: orm.Session = Depends(get_db)
):
    """Test a tool execution with sample parameters."""
    tenant_id = get_tenant_id(request)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID missing in headers")

    tool = (
        db.query(MCPToolDefinition)
        .filter(
            MCPToolDefinition.id == tool_id,
            MCPToolDefinition.tenant_id == tenant_id
        )
        .first()
    )

    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")

    start_time = datetime.utcnow()
    params = test_data.params or {}

    try:
        # Build URL
        final_url = interpolate_url(tool.endpoint_url, params)

        # Build headers
        headers = dict(tool.headers or {})
        if tool.auth_type == "bearer" and tool.auth_config:
            token = tool.auth_config.get("token", "")
            headers["Authorization"] = f"Bearer {token}"
        elif tool.auth_type == "api_key" and tool.auth_config:
            header_name = tool.auth_config.get("header", "X-API-Key")
            headers[header_name] = tool.auth_config.get("key", "")

        # Execute request
        async with httpx.AsyncClient(timeout=tool.timeout_seconds) as client:
            if tool.http_method == "GET":
                response = await client.get(final_url, headers=headers, params=params)
            elif tool.http_method == "POST":
                body = params
                if tool.request_body_template:
                    body = json.loads(render_template(tool.request_body_template, params))
                response = await client.post(final_url, headers=headers, json=body)
            elif tool.http_method == "PUT":
                response = await client.put(final_url, headers=headers, json=params)
            elif tool.http_method == "DELETE":
                response = await client.delete(final_url, headers=headers)
            else:
                response = await client.request(tool.http_method, final_url, headers=headers, json=params)

        response_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {"text": response.text}
        duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        # Format response message
        formatted_message = render_template(tool.response_template, {**params, **response_data}) if tool.response_template else None

        return MCPToolTestResponse(
            success=response.is_success,
            tool_name=tool.name,
            request_url=final_url,
            request_params=params,
            response_data=response_data,
            formatted_message=formatted_message,
            error=None if response.is_success else f"HTTP {response.status_code}",
            duration_ms=duration_ms
        )

    except httpx.TimeoutException:
        duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        return MCPToolTestResponse(
            success=False,
            tool_name=tool.name,
            request_url=interpolate_url(tool.endpoint_url, params),
            request_params=params,
            response_data=None,
            formatted_message=None,
            error=f"Request timed out after {tool.timeout_seconds} seconds",
            duration_ms=duration_ms
        )
    except Exception as e:
        duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        return MCPToolTestResponse(
            success=False,
            tool_name=tool.name,
            request_url=interpolate_url(tool.endpoint_url, params),
            request_params=params,
            response_data=None,
            formatted_message=None,
            error=str(e),
            duration_ms=duration_ms
        )


# ============================================================================
# Execution Logs Endpoint
# ============================================================================

@router.get("/executions/tenant/{tenant_id}", response_model=MCPToolExecutionListResponse)
def get_executions(
    tenant_id: str,
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    tool_id: Optional[UUID] = None,
    status: Optional[str] = None,
    contact_phone: Optional[str] = None,
    db: orm.Session = Depends(get_db)
):
    """Get tool execution logs for a tenant."""
    # Verify tenant access
    header_tenant_id = get_tenant_id(request)
    if header_tenant_id and header_tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Tenant ID mismatch")

    query = db.query(MCPToolExecution).filter(MCPToolExecution.tenant_id == tenant_id)

    if tool_id:
        query = query.filter(MCPToolExecution.tool_id == tool_id)
    if status:
        query = query.filter(MCPToolExecution.status == status)
    if contact_phone:
        query = query.filter(MCPToolExecution.contact_phone == contact_phone)

    total = query.count()
    offset = (page - 1) * page_size

    executions = (
        query
        .order_by(MCPToolExecution.created_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    # Enrich with tool names
    tool_ids = list(set(e.tool_id for e in executions))
    tools = db.query(MCPToolDefinition).filter(MCPToolDefinition.id.in_(tool_ids)).all()
    tool_names = {str(t.id): t.name for t in tools}

    return MCPToolExecutionListResponse(
        executions=[
            MCPToolExecutionResponse(
                id=e.id,
                tool_id=e.tool_id,
                tool_name=tool_names.get(str(e.tool_id)),
                tenant_id=e.tenant_id,
                contact_phone=e.contact_phone,
                message_text=e.message_text,
                trigger_type=e.trigger_type,
                request_params=e.request_params,
                request_url=e.request_url,
                response_data=e.response_data,
                response_message=e.response_message,
                status=e.status,
                error_message=e.error_message,
                duration_ms=e.duration_ms,
                from_cache=e.from_cache,
                created_at=e.created_at
            )
            for e in executions
        ],
        total=total,
        page=page,
        page_size=page_size
    )


# ============================================================================
# Log Execution (called by Node.js)
# ============================================================================

@router.post("/executions", status_code=201)
async def log_execution(
    request: Request,
    db: orm.Session = Depends(get_db)
):
    """Log a tool execution (called by Node.js webhook server)."""
    try:
        body = await request.json()

        execution = MCPToolExecution(
            tool_id=body.get('tool_id'),
            tenant_id=body.get('tenant_id'),
            contact_phone=body.get('contact_phone'),
            message_text=body.get('message_text'),
            trigger_type=body.get('trigger_type', 'keyword'),
            request_params=body.get('request_params'),
            request_url=body.get('request_url'),
            response_data=body.get('response_data'),
            response_message=body.get('response_message'),
            status=body.get('status', 'success'),
            error_message=body.get('error_message'),
            duration_ms=body.get('duration_ms'),
            from_cache=body.get('from_cache', False)
        )

        db.add(execution)
        db.commit()

        return {"id": str(execution.id), "status": "logged"}

    except Exception as e:
        db.rollback()
        logger.error(f"Error logging MCP execution: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error logging execution: {str(e)}")
