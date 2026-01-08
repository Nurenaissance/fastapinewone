"""
FlowsAPI Router - Migrated from JSON to PostgreSQL Database
SECURITY FIX: Now uses database with tenant isolation instead of insecure JSON file
"""
from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from config.database import get_db
from .models import FlowDataModel
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

# ==================== Pydantic Models ====================

class Question(BaseModel):
    """Question/Answer pair for flow data"""
    question: str
    answer: str

class FlowData(BaseModel):
    """Request model for creating flow data"""
    PAN: str
    phone: Optional[str] = None
    name: Optional[str] = None
    password: Optional[str] = None
    questions: Optional[List[Question]] = None

class UpdateFlowData(BaseModel):
    """Request model for updating flow data"""
    phone: Optional[str] = None
    name: Optional[str] = None
    password: Optional[str] = None
    questions: Optional[List[Question]] = None

class FlowDataResponse(BaseModel):
    """Response model for flow data"""
    PAN: str
    phone: Optional[str] = None
    name: Optional[str] = None
    password: Optional[str] = None
    questions: Optional[List[dict]] = None

    class Config:
        from_attributes = True

@router.post("/temp-flow-data")
def addFlowData(
    data: FlowData,
    tenant_id: str = Header(..., alias="X-Tenant-Id"),
    db: Session = Depends(get_db)
):
    """
    Add flow data to database with tenant isolation
    SECURITY: Requires X-Tenant-Id header, ensures PAN unique within tenant
    """
    try:
        # Validate tenant_id
        if not tenant_id:
            raise HTTPException(status_code=400, detail="X-Tenant-Id header is required")

        # Check if PAN already exists for this tenant
        existing = db.query(FlowDataModel).filter(
            FlowDataModel.pan == data.PAN,
            FlowDataModel.tenant_id == tenant_id
        ).first()

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Data with PAN '{data.PAN}' already exists for tenant '{tenant_id}'"
            )

        # Convert questions to dict format for JSONB storage
        questions_data = None
        if data.questions:
            questions_data = [q.dict() for q in data.questions]

        # Create new flow data record
        db_flow = FlowDataModel(
            pan=data.PAN,
            phone=data.phone,
            name=data.name,
            password=data.password,
            questions=questions_data,
            tenant_id=tenant_id
        )

        db.add(db_flow)
        db.commit()
        db.refresh(db_flow)

        logger.info(f"✅ Flow data added for PAN: {data.PAN}, tenant: {tenant_id}")

        return {
            "message": "Flow data added successfully.",
            "data": {
                "PAN": db_flow.pan,
                "phone": db_flow.phone,
                "name": db_flow.name,
                "password": db_flow.password,
                "questions": db_flow.questions
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error adding flow data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error adding flow data: {str(e)}")

@router.get("/get-flow-data")
def getFlowData(
    tenant_id: str = Header(..., alias="X-Tenant-Id"),
    db: Session = Depends(get_db)
):
    """
    Get all flow data for a tenant
    SECURITY: Only returns data for the requesting tenant
    """
    try:
        # Validate tenant_id
        if not tenant_id:
            raise HTTPException(status_code=400, detail="X-Tenant-Id header is required")

        # Query flow data for this tenant only
        flow_data = db.query(FlowDataModel).filter(
            FlowDataModel.tenant_id == tenant_id
        ).all()

        if not flow_data:
            raise HTTPException(status_code=404, detail="No flow data found for this tenant")

        # Convert to response format
        results = []
        for flow in flow_data:
            results.append({
                "PAN": flow.pan,
                "phone": flow.phone,
                "name": flow.name,
                "password": flow.password,
                "questions": flow.questions
            })

        logger.info(f"✅ Retrieved {len(results)} flow data records for tenant: {tenant_id}")
        return results

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error retrieving flow data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving flow data: {str(e)}")

@router.get("/temp-flow-data/{pan}")
def getFlowDataByPAN(
    pan: str,
    tenant_id: str = Header(..., alias="X-Tenant-Id"),
    db: Session = Depends(get_db)
):
    """
    Get flow data for a specific PAN within tenant
    SECURITY: Only returns data if PAN belongs to requesting tenant
    """
    try:
        # Validate tenant_id
        if not tenant_id:
            raise HTTPException(status_code=400, detail="X-Tenant-Id header is required")

        # Query for PAN within this tenant only
        flow_data = db.query(FlowDataModel).filter(
            FlowDataModel.pan == pan,
            FlowDataModel.tenant_id == tenant_id
        ).first()

        if not flow_data:
            raise HTTPException(
                status_code=404,
                detail=f"No data found for PAN: {pan} in tenant: {tenant_id}"
            )

        logger.info(f"✅ Retrieved flow data for PAN: {pan}, tenant: {tenant_id}")

        return {
            "PAN": flow_data.pan,
            "phone": flow_data.phone,
            "name": flow_data.name,
            "password": flow_data.password,
            "questions": flow_data.questions
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error retrieving flow data by PAN: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving flow data: {str(e)}")


@router.patch("/temp-flow-data/{pan}")
def updateFlowData(
    pan: str,
    update_data: UpdateFlowData,
    tenant_id: str = Header(..., alias="X-Tenant-Id"),
    db: Session = Depends(get_db)
):
    """
    Update flow data for a specific PAN within tenant
    SECURITY: Only updates if PAN belongs to requesting tenant
    Only updates the fields provided in the request body.
    """
    try:
        # Validate tenant_id
        if not tenant_id:
            raise HTTPException(status_code=400, detail="X-Tenant-Id header is required")

        # Find the flow data for this PAN and tenant
        flow_data = db.query(FlowDataModel).filter(
            FlowDataModel.pan == pan,
            FlowDataModel.tenant_id == tenant_id
        ).first()

        if not flow_data:
            raise HTTPException(
                status_code=404,
                detail=f"No data found for PAN: {pan} in tenant: {tenant_id}"
            )

        # Update only the provided fields
        if update_data.name is not None:
            flow_data.name = update_data.name

        if update_data.phone is not None:
            flow_data.phone = update_data.phone

        if update_data.password is not None:
            flow_data.password = update_data.password

        if update_data.questions is not None:
            # Convert questions to dict format for JSONB storage
            flow_data.questions = [q.dict() for q in update_data.questions]

        db.commit()
        db.refresh(flow_data)

        logger.info(f"✅ Updated flow data for PAN: {pan}, tenant: {tenant_id}")

        return {
            "message": "Flow data updated successfully.",
            "updated_data": {
                "PAN": flow_data.pan,
                "phone": flow_data.phone,
                "name": flow_data.name,
                "password": flow_data.password,
                "questions": flow_data.questions
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error updating flow data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating flow data: {str(e)}")