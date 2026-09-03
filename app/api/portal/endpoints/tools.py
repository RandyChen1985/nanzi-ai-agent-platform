from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import uuid
import json

from app.core.dependencies import require_admin, require_permission, require_api_key
from app.core.orm import get_db_session
from app.models.tool import SysApiTool
from app.models.mcp import McpToolCache
from app.schemas.tool import SysApiToolCreate, SysApiToolUpdate, SysApiToolResponse

router = APIRouter()

@router.get("/mcp", response_model=List[Dict[str, Any]])
async def list_published_mcp_tools(
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    """列出已发布的 MCP 工具：包含平台公共 MCP 工具，以及当前登录用户自己创建的个人私有 MCP 工具。"""
    from sqlalchemy.orm import joinedload
    from sqlalchemy import or_, and_
    from app.models.mcp import McpServer
    
    current_user_id = user.get("user_id") if user.get("user_id") is not None else user.get("id")
    try:
        current_user_id = int(current_user_id) if current_user_id is not None else None
    except Exception:
        pass
    
    # 1. 平台公共 MCP 服务 (scope == 'global' 或历史 NULL)
    global_cond = or_(McpServer.scope == "global", McpServer.scope.is_(None))
    # 2. 当前登录用户的个人私有 MCP 服务 (scope == 'personal' 且 user_id == current_user_id)
    personal_cond = and_(McpServer.scope == "personal", McpServer.user_id == current_user_id)

    stmt = (
        select(McpToolCache)
        .join(McpToolCache.server)
        .options(joinedload(McpToolCache.server))
        .where(
            McpToolCache.is_published == True,
            McpToolCache.is_available == True,
            McpServer.enabled_status == 1,
            or_(global_cond, personal_cond)
        )
    )
    result = await db.execute(stmt)
    tools = result.scalars().all()
    
    return [
        {
            "id": t.id,
            "name": t.tool_name,
            "description": t.tool_description,
            "server_name": t.server.server_name if t.server else "Unknown",
            "server_remark": (t.server.remark if t.server else None) or None,
            "scope": t.server.scope if (t.server and t.server.scope) else "global",
            "parameter_schema": json.loads(t.parameter_schema or "{}")
        } for t in tools
    ]

@router.get("", response_model=List[SysApiToolResponse])
async def list_tools(
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    """列出系统 HTTP/API 工具（任意持有有效 API Key 的登录用户可读，供智能体配置勾选）。"""
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
