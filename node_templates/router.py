from fastapi import APIRouter, Request, Depends, HTTPException, Header, Body
from sqlalchemy import orm, and_
from config.database import get_db
from .models import NodeTemplate
from models import Tenant
from typing import Optional

router = APIRouter()

def get_tenant_id_from_request(request: Request) -> str:
    """Helper function to extract and validate tenant_id"""
    tenant_id = request.headers.get("X-Tenant-Id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID is missing in headers")
    
    # Handle demo tenant
    if tenant_id == "demo":
        tenant_id = 'ai'
    
    return tenant_id

def validate_tenant_exists(tenant_id: str, db: orm.Session) -> None:
    """Helper function to validate tenant exists - only when necessary"""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

@router.get('/node-templates/')
def read_nodetemps(request: Request, db: orm.Session = Depends(get_db)):
    try:
        tenant_id = get_tenant_id_from_request(request)
        
        # Only validate tenant if needed for business logic
        # Consider removing if tenant validation isn't critical for this endpoint
        validate_tenant_exists(tenant_id, db)

        node_temps = (db.query(NodeTemplate)
                     .filter(NodeTemplate.tenant_id == tenant_id)
                     .all())

        if not node_temps:
            raise HTTPException(status_code=404, detail="No node templates found for this tenant")

        return node_temps
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")

@router.get("/node-templates/{node_template_id}/")
def get_node_temps(
    node_template_id: int, 
    request: Request,
    db: orm.Session = Depends(get_db)
):
    try:
        tenant_id = get_tenant_id_from_request(request)
        
        # More efficient query with tenant_id filter for security
        node_temp = (db.query(NodeTemplate)
                    .filter(
                        NodeTemplate.id == node_template_id,
                        NodeTemplate.tenant_id == tenant_id
                    )
                    .first())

        if not node_temp:
            raise HTTPException(status_code=404, detail="Node template not found")

        return node_temp
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")

@router.post("/flows/{id}")
def update_trigger_flow(
    id: int,
    request: Request,  # Moved before parameters with defaults
    data: dict = Body(...),
    db: orm.Session = Depends(get_db),
):
    try:
        tenant_id = get_tenant_id_from_request(request)

        node_template = (db.query(NodeTemplate)
                        .filter(
                            NodeTemplate.id == id,
                            NodeTemplate.tenant_id == tenant_id
                        )
                        .first())

        if not node_template:
            raise HTTPException(status_code=404, detail="NodeTemplate not found")

        # Only update if trigger value is provided and different
        new_trigger = data.get("trigger")
        if new_trigger is not None and new_trigger != node_template.trigger:
            node_template.trigger = new_trigger
            db.commit()
            db.refresh(node_template)

        return {
            "message": f"{node_template.name} updated successfully", 
            "data": {
                "id": node_template.id,
                "name": node_template.name,
                "trigger": node_template.trigger
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")

@router.get("/flows/")
def get_flows_with_trigger(
    request: Request,
    db: orm.Session = Depends(get_db),
):
    try:
        tenant_id = get_tenant_id_from_request(request)

        # More efficient query - only select needed columns
        node_templates = (db.query(
                            NodeTemplate.id, 
                            NodeTemplate.name, 
                            NodeTemplate.trigger
                        )
                        .filter(
                            NodeTemplate.tenant_id == tenant_id,
                            NodeTemplate.trigger.isnot(None),
                            NodeTemplate.trigger != ""
                        )
                        .all())

        if not node_templates:
            raise HTTPException(status_code=404, detail="No flows with triggers found")

        # Convert to list of dictionaries
        return [
            {
                "id": nt.id, 
                "name": nt.name, 
                "trigger": nt.trigger
            } for nt in node_templates
        ]
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")

@router.delete("/flows-delete/{node_template_id}/")
def delete_trigger_only(
    node_template_id: int,
    request: Request,
    db: orm.Session = Depends(get_db)
):
    try:
        tenant_id = get_tenant_id_from_request(request)

        node_template = (db.query(NodeTemplate)
                        .filter(
                            NodeTemplate.id == node_template_id,
                            NodeTemplate.tenant_id == tenant_id
                        )
                        .first())

        if not node_template:
            raise HTTPException(status_code=404, detail="Flow not found")

        # Only update if trigger actually exists
        if node_template.trigger is not None:
            node_template.trigger = None
            db.commit()
            return {"message": f"Trigger for '{node_template.name}' cleared successfully"}
        else:
            return {"message": f"No trigger found for '{node_template.name}'"}
            
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")

# Optional: Bulk operations for better performance
@router.post("/flows/bulk-update/")
def bulk_update_triggers(
    request: Request,  # Moved before parameters with defaults
    updates: list[dict] = Body(...),  # [{"id": 1, "trigger": "value"}, ...]
    db: orm.Session = Depends(get_db)
):
    """Bulk update triggers for multiple flows"""
    try:
        tenant_id = get_tenant_id_from_request(request)
        
        if not updates:
            raise HTTPException(status_code=400, detail="No updates provided")
        
        updated_count = 0
        for update in updates:
            flow_id = update.get("id")
            trigger_value = update.get("trigger")
            
            if flow_id is None:
                continue
                
            result = (db.query(NodeTemplate)
                     .filter(
                         NodeTemplate.id == flow_id,
                         NodeTemplate.tenant_id == tenant_id
                     )
                     .update({"trigger": trigger_value}))
            
            updated_count += result
        
        db.commit()
        
        return {
            "message": f"Successfully updated {updated_count} flows",
            "updated_count": updated_count
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")

@router.delete("/flows/bulk-delete-triggers/")
def bulk_clear_triggers(
    request: Request,  # Moved before parameters with defaults
    flow_ids: list[int] = Body(...),
    db: orm.Session = Depends(get_db)
):
    """Bulk clear triggers for multiple flows"""
    try:
        tenant_id = get_tenant_id_from_request(request)
        
        if not flow_ids:
            raise HTTPException(status_code=400, detail="No flow IDs provided")
        
        updated_count = (db.query(NodeTemplate)
                        .filter(
                            NodeTemplate.id.in_(flow_ids),
                            NodeTemplate.tenant_id == tenant_id
                        )
                        .update({"trigger": None}, synchronize_session=False))
        
        db.commit()
        
        return {
            "message": f"Successfully cleared triggers for {updated_count} flows",
            "cleared_count": updated_count
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")