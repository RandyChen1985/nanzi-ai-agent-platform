from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import uuid
import json

from app.core.dependencies import require_admin, require_permission
from app.core.orm import get_db_session
from app.models.tool import SysApiTool
from app.models.mcp import McpToolCache
from app.schemas.tool import SysApiToolCreate, SysApiToolUpdate, SysApiToolResponse

router = APIRouter()

@router.get("/mcp", response_model=List[Dict[str, Any]])
async def list_published_mcp_tools(
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("menu", "menu:system:config"))
):
    """List all MCP tools that are marked as published"""
    # ... (rest of function)
    from sqlalchemy.orm import joinedload
    from app.models.mcp import McpServer
    
    stmt = select(McpToolCache).options(joinedload(McpToolCache.server)).where(McpToolCache.is_published == True)
    result = await db.execute(stmt)
    tools = result.scalars().all()
    
    return [
        {
            "id": t.id,
            "name": t.tool_name,
            "description": t.tool_description,
            "server_name": t.server.server_name if t.server else "Unknown",
            "parameter_schema": json.loads(t.parameter_schema or "{}")
        } for t in tools
    ]

@router.get("", response_model=List[SysApiToolResponse])
async def list_tools(
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("menu", "menu:system:config"))
):
    """List all configured API tools"""
    query = select(SysApiTool).order_by(SysApiTool.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()

@router.post("", response_model=SysApiToolResponse)
async def create_tool(
    tool_in: SysApiToolCreate,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """Create a new API tool"""
    # ... (rest of function)
    # Check if name exists
    existing = await db.execute(select(SysApiTool).where(SysApiTool.name == tool_in.name))
    if existing.scalars().first():
        raise HTTPException(status_code=400, detail="Tool name already exists")
    
    # Dump Dicts to JSON strings for DB
    data = tool_in.model_dump()
    if data.get("headers") is not None:
        data["headers"] = json.dumps(data["headers"], ensure_ascii=False)
    if data.get("parameter_schema") is not None:
        data["parameter_schema"] = json.dumps(data["parameter_schema"], ensure_ascii=False)
        
    new_tool = SysApiTool(
        id=str(uuid.uuid4()),
        **data
    )
    db.add(new_tool)
    await db.commit()
    await db.refresh(new_tool)
    return new_tool

@router.put("/{tool_id}", response_model=SysApiToolResponse)
async def update_tool(
    tool_id: str,
    tool_in: SysApiToolUpdate,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """Update an API tool"""
    # ... (rest of function)
    result = await db.execute(select(SysApiTool).where(SysApiTool.id == tool_id))
    tool = result.scalars().first()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    
    update_data = tool_in.model_dump(exclude_unset=True)
    
    # Dump Dicts to JSON strings for DB
    if "headers" in update_data:
         val = update_data["headers"]
         update_data["headers"] = json.dumps(val, ensure_ascii=False) if val is not None else None
         
    if "parameter_schema" in update_data:
        val = update_data["parameter_schema"]
        update_data["parameter_schema"] = json.dumps(val, ensure_ascii=False) if val is not None else None

    for field, value in update_data.items():
        setattr(tool, field, value)
        
    await db.commit()
    await db.refresh(tool)
    return tool

@router.delete("/{tool_id}")
async def delete_tool(
    tool_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """Delete an API tool"""
    result = await db.execute(select(SysApiTool).where(SysApiTool.id == tool_id))
    tool = result.scalars().first()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    
    await db.delete(tool)
    await db.commit()
    return {"status": "success", "message": "Tool deleted"}
