from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Literal, Union
from datetime import datetime

class BroadcastGroupMember(BaseModel):
    name: Optional[str] = None
    phone: int  

class BroadcastGroupCreate(BaseModel):
    id: Optional[str] = None
    name: str
    members: Optional[List[BroadcastGroupMember]] = []
    auto_rules: Optional[Dict[str, Any]] = None  # Will be defined after AutoRules class

class BroadcastGroupResponse(BaseModel):
    id: str
    name: str
    members: Optional[List[dict]] = []
    auto_rules: Optional[Dict[str, Any]] = None

    class Config:
        orm_mode = True     
class BroadcastGroupAddContacts(BaseModel):
    groupName: str
    contacts: List[BroadcastGroupMember]

class BroadcastGroupContactDelete(BaseModel):
    groupName: str
    contactPhone: int

# Auto-rules schemas for dynamic group membership
class DateRangeValue(BaseModel):
    start: Optional[datetime] = None
    end: Optional[datetime] = None

class RuleCondition(BaseModel):
    type: Literal["date", "text", "engagement", "custom_field"]
    field: str  # e.g., "createdOn", "name", "last_seen", "customField.status"
    operator: Literal[
        "equals", "not_equals", "contains", "starts_with",
        "ends_with", "greater_than", "less_than", "in_range"
    ]
    value: Union[str, int, float, datetime, DateRangeValue, Dict[str, Any]]  # Flexible value type

class AutoRules(BaseModel):
    enabled: bool = True
    logic: Literal["AND"] = "AND"  # Future: support "OR"
    conditions: List[RuleCondition] = []

class BroadcastGroupUpdateRules(BaseModel):
    auto_rules: AutoRules

class RuleTestRequest(BaseModel):
    rules: AutoRules
    sample_contact_id: Optional[int] = None  # Test against specific contact

class PromptUpdateRequest(BaseModel):
    prompt: str


class WhatsappTenantDataSchema(BaseModel):
    business_phone_number_id: Optional[int]
    flow_data: Optional[List[Dict[str, str]]]  # List of dictionaries where keys and values are strings
    adj_list: Optional[List[List[int]]]  # List of lists of integers
    access_token: str
    updated_at: Optional[datetime]
    business_account_id: int
    start: Optional[int]
    fallback_count: Optional[int]
    fallback_message: Optional[str]
    flow_name: Optional[str]
    tenant_id: str
    spreadsheet_link: Optional[str]
    id: int
    language: Optional[str]
    prompt:Optional[str]

    class Config:
        orm_mode = True
        from_attributes = True  # Allows from_orm to work correctly