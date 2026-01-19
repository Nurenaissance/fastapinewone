from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, JSON, Float
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from config.database import Base
from datetime import datetime
import uuid


class MCPToolDefinition(Base):
    """
    MCP Tool Definition - stores configuration for client API tools
    that can be called based on chat context for customer support and nurturing.
    """
    __tablename__ = "mcp_tool_definitions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(String(50), ForeignKey("tenant_tenant.id"), nullable=False, index=True)

    # Tool identity
    name = Column(String(100), nullable=False)  # e.g., "book_appointment", "check_order_status"
    description = Column(Text, nullable=False)  # For LLM context - describe what the tool does

    # API endpoint configuration
    endpoint_url = Column(String(500), nullable=False)  # Support URL templates with ${var}
    http_method = Column(String(10), nullable=False, default="GET")  # GET/POST/PUT/DELETE

    # Authentication
    auth_type = Column(String(20), nullable=False, default="none")  # none/bearer/api_key/basic
    auth_config = Column(JSON, nullable=True)  # Encrypted auth config: {"token": "...", "header": "..."}

    # Request/Response configuration
    parameters = Column(JSON, nullable=True)  # JSON Schema for parameters
    headers = Column(JSON, nullable=True)  # Custom headers to send
    request_body_template = Column(Text, nullable=True)  # Jinja2 template for request body

    # Trigger configuration (speed-first approach)
    trigger_keywords = Column(JSON, nullable=True)  # ["order", "tracking", "delivery"] for fast matching
    trigger_intents = Column(JSON, nullable=True)  # ["check_status", "track_order"] for LLM fallback

    # Response formatting
    response_template = Column(Text, nullable=True)  # Jinja2 template: "Your order {{order_id}} is {{status}}"
    error_template = Column(Text, nullable=True)  # Error message template

    # Performance settings
    cache_ttl_seconds = Column(Integer, nullable=True, default=0)  # 0 = no cache
    timeout_seconds = Column(Integer, nullable=False, default=10)
    retry_count = Column(Integer, nullable=False, default=1)

    # Status and priority
    is_active = Column(Boolean, default=True, nullable=False)
    priority = Column(Integer, nullable=False, default=0)  # Higher = checked first

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    created_by = Column(String(100), nullable=True)

    # Relationships
    executions = relationship("MCPToolExecution", back_populates="tool", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<MCPToolDefinition(name={self.name}, tenant={self.tenant_id})>"


class MCPToolExecution(Base):
    """
    MCP Tool Execution Log - tracks all tool executions for analytics and debugging.
    """
    __tablename__ = "mcp_tool_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tool_id = Column(UUID(as_uuid=True), ForeignKey("mcp_tool_definitions.id"), nullable=False, index=True)
    tenant_id = Column(String(50), nullable=False, index=True)

    # Execution context
    contact_phone = Column(String(20), nullable=False, index=True)
    message_text = Column(Text, nullable=True)  # Original user message
    trigger_type = Column(String(20), nullable=False)  # "keyword" or "llm"

    # Request/Response
    request_params = Column(JSON, nullable=True)  # Parameters sent to API
    request_url = Column(String(500), nullable=True)  # Final resolved URL
    response_data = Column(JSON, nullable=True)  # Raw API response
    response_message = Column(Text, nullable=True)  # Formatted message sent to user

    # Status and performance
    status = Column(String(20), nullable=False)  # success/failed/timeout/error
    error_message = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    from_cache = Column(Boolean, default=False, nullable=False)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    tool = relationship("MCPToolDefinition", back_populates="executions")

    def __repr__(self):
        return f"<MCPToolExecution(tool={self.tool_id}, status={self.status})>"
