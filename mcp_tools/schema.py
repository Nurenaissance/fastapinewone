from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, validator
from datetime import datetime
from uuid import UUID
from enum import Enum


class HTTPMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"


class AuthType(str, Enum):
    NONE = "none"
    BEARER = "bearer"
    API_KEY = "api_key"
    BASIC = "basic"


class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    ERROR = "error"


class TriggerType(str, Enum):
    KEYWORD = "keyword"
    LLM = "llm"


# ============================================================================
# Tool Definition Schemas
# ============================================================================

class MCPToolCreate(BaseModel):
    """Schema for creating a new MCP tool definition."""
    name: str = Field(..., min_length=1, max_length=100, description="Tool name (e.g., 'check_order_status')")
    description: str = Field(..., min_length=10, description="Description for LLM context")
    endpoint_url: str = Field(..., min_length=10, description="API endpoint URL (supports ${var} templates)")
    http_method: HTTPMethod = Field(default=HTTPMethod.GET)

    auth_type: AuthType = Field(default=AuthType.NONE)
    auth_config: Optional[Dict[str, Any]] = Field(default=None, description="Auth config (token, header, etc.)")

    parameters: Optional[Dict[str, Any]] = Field(default=None, description="JSON Schema for parameters")
    headers: Optional[Dict[str, str]] = Field(default=None, description="Custom HTTP headers")
    request_body_template: Optional[str] = Field(default=None, description="Jinja2 template for request body")

    trigger_keywords: Optional[List[str]] = Field(default=[], description="Keywords for fast matching")
    trigger_intents: Optional[List[str]] = Field(default=[], description="Intents for LLM fallback")

    response_template: Optional[str] = Field(default=None, description="Jinja2 template for success response")
    error_template: Optional[str] = Field(default="Sorry, I couldn't complete that action. Please try again.",
                                          description="Error message template")

    cache_ttl_seconds: Optional[int] = Field(default=0, ge=0, le=86400, description="Cache TTL (0 = no cache)")
    timeout_seconds: int = Field(default=10, ge=1, le=60, description="Request timeout in seconds")
    retry_count: int = Field(default=1, ge=0, le=3, description="Number of retry attempts")

    is_active: bool = Field(default=True)
    priority: int = Field(default=0, ge=0, le=100, description="Priority (higher = checked first)")

    @validator('trigger_keywords', 'trigger_intents', pre=True)
    def ensure_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [kw.strip().lower() for kw in v.split(',') if kw.strip()]
        return [str(kw).strip().lower() for kw in v if kw]

    @validator('name')
    def validate_name(cls, v):
        # Ensure name is URL-safe and lowercase
        import re
        if not re.match(r'^[a-z][a-z0-9_]*$', v.lower()):
            raise ValueError('Name must start with letter and contain only letters, numbers, underscores')
        return v.lower()

    class Config:
        json_schema_extra = {
            "example": {
                "name": "check_order_status",
                "description": "Check the delivery status of a customer order using order ID",
                "endpoint_url": "https://api.example.com/orders/${order_id}/status",
                "http_method": "GET",
                "auth_type": "bearer",
                "auth_config": {"token": "your-api-token"},
                "parameters": {
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string", "description": "The order ID to check"}
                    },
                    "required": ["order_id"]
                },
                "trigger_keywords": ["order", "tracking", "delivery", "where is my"],
                "response_template": "Your order {{order_id}} is {{status}}. Expected delivery: {{eta}}",
                "cache_ttl_seconds": 30,
                "timeout_seconds": 10,
                "priority": 10
            }
        }


class MCPToolUpdate(BaseModel):
    """Schema for updating an existing MCP tool definition."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, min_length=10)
    endpoint_url: Optional[str] = Field(None, min_length=10)
    http_method: Optional[HTTPMethod] = None

    auth_type: Optional[AuthType] = None
    auth_config: Optional[Dict[str, Any]] = None

    parameters: Optional[Dict[str, Any]] = None
    headers: Optional[Dict[str, str]] = None
    request_body_template: Optional[str] = None

    trigger_keywords: Optional[List[str]] = None
    trigger_intents: Optional[List[str]] = None

    response_template: Optional[str] = None
    error_template: Optional[str] = None

    cache_ttl_seconds: Optional[int] = Field(None, ge=0, le=86400)
    timeout_seconds: Optional[int] = Field(None, ge=1, le=60)
    retry_count: Optional[int] = Field(None, ge=0, le=3)

    is_active: Optional[bool] = None
    priority: Optional[int] = Field(None, ge=0, le=100)

    @validator('trigger_keywords', 'trigger_intents', pre=True)
    def ensure_list(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            return [kw.strip().lower() for kw in v.split(',') if kw.strip()]
        return [str(kw).strip().lower() for kw in v if kw]


class MCPToolResponse(BaseModel):
    """Response schema for MCP tool definition."""
    id: UUID
    tenant_id: str
    name: str
    description: str
    endpoint_url: str
    http_method: str
    auth_type: str
    parameters: Optional[Dict[str, Any]]
    headers: Optional[Dict[str, str]]
    trigger_keywords: Optional[List[str]]
    trigger_intents: Optional[List[str]]
    response_template: Optional[str]
    error_template: Optional[str]
    cache_ttl_seconds: Optional[int]
    timeout_seconds: int
    retry_count: int
    is_active: bool
    priority: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MCPToolListResponse(BaseModel):
    """Response schema for listing MCP tools."""
    tools: List[MCPToolResponse]
    total: int
    page: int
    page_size: int


# ============================================================================
# Tool Execution Schemas
# ============================================================================

class MCPToolTestRequest(BaseModel):
    """Schema for testing a tool execution."""
    params: Optional[Dict[str, Any]] = Field(default={}, description="Test parameters")
    message_text: Optional[str] = Field(default="Test message", description="Simulated user message")


class MCPToolTestResponse(BaseModel):
    """Response schema for tool test execution."""
    success: bool
    tool_name: str
    request_url: str
    request_params: Optional[Dict[str, Any]]
    response_data: Optional[Dict[str, Any]]
    formatted_message: Optional[str]
    error: Optional[str]
    duration_ms: int


class MCPToolExecutionResponse(BaseModel):
    """Response schema for tool execution log."""
    id: UUID
    tool_id: UUID
    tool_name: Optional[str] = None
    tenant_id: str
    contact_phone: str
    message_text: Optional[str]
    trigger_type: str
    request_params: Optional[Dict[str, Any]]
    request_url: Optional[str]
    response_data: Optional[Dict[str, Any]]
    response_message: Optional[str]
    status: str
    error_message: Optional[str]
    duration_ms: Optional[int]
    from_cache: bool
    created_at: datetime

    class Config:
        from_attributes = True


class MCPToolExecutionListResponse(BaseModel):
    """Response schema for listing tool executions."""
    executions: List[MCPToolExecutionResponse]
    total: int
    page: int
    page_size: int


# ============================================================================
# Node.js Integration Schemas (for API responses to Node.js)
# ============================================================================

class MCPToolForNodeJS(BaseModel):
    """Simplified tool schema for Node.js consumption."""
    id: str
    name: str
    description: str
    endpoint_url: str
    http_method: str
    auth_type: str
    auth_config: Optional[Dict[str, Any]]
    parameters: Optional[Dict[str, Any]]
    headers: Optional[Dict[str, str]]
    request_body_template: Optional[str]
    trigger_keywords: List[str]
    trigger_intents: List[str]
    response_template: Optional[str]
    error_template: str
    cache_ttl_seconds: int
    timeout_seconds: int
    retry_count: int
    priority: int

    class Config:
        from_attributes = True


class MCPToolsForNodeJSResponse(BaseModel):
    """Response containing all active tools for a tenant (for Node.js caching)."""
    tenant_id: str
    tools: List[MCPToolForNodeJS]
    cache_version: str  # MD5 hash for cache invalidation
    fetched_at: datetime
