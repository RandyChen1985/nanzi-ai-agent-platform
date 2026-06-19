from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update, func
import uuid
import json
import time
import logging

from app.core.orm import get_db_session
from app.core.dependencies import require_admin, require_permission
from app.models.mcp import McpServer, McpToolCache
from app.models.agent import AIAgentVersion
from app.services.ai.tools.mcp_client import McpClientService, McpSseSession
from app.services.ai.tools.mcp_factory import McpToolFactory
from pydantic import BaseModel, Field, ConfigDict

logger = logging.getLogger(__name__)
router = APIRouter()

class McpServerBase(BaseModel):
    server_name: str
    sse_url: str
    auth_headers: Optional[str] = "{}"
    enabled_status: Optional[int] = 1

class McpServerResponse(McpServerBase):
    id: str
    last_sync_at: Optional[Any] = None
    tool_count: int = 0
    published_tool_count: int = 0

    model_config = ConfigDict(from_attributes=True)

class McpToolResponse(BaseModel):
    id: str
    server_id: str
    tool_name: str
    tool_description: Optional[str]
    parameter_schema: str
    is_published: bool

    model_config = ConfigDict(from_attributes=True)

class McpToolResponseWithUsage(McpToolResponse):
    usage_count: int = 0

    model_config = ConfigDict(from_attributes=True)

@router.post("/verify")
async def verify_mcp_server(
    data: McpServerBase,
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """Test connection and return discovered tools without saving"""
    temp_id = f"verify_{uuid.uuid4().hex[:8]}"
    # ... (rest of function)
    auth_headers = {}
    if data.auth_headers:
        try: auth_headers = json.loads(data.auth_headers)
        except: pass
            
    McpClientService._sessions[temp_id] = McpSseSession(temp_id, data.sse_url, auth_headers)
    
    try:
        tools = await McpClientService.list_remote_tools(temp_id)
        if temp_id in McpClientService._sessions:
            await McpClientService._sessions[temp_id].close()
            del McpClientService._sessions[temp_id]
            
        return {
            "status": "success",
            "tools": [
                {"name": t.name if hasattr(t, 'name') else t.get('name'), 
                 "description": t.description if hasattr(t, 'description') else t.get('description')} 
                for t in tools
            ]
        }
    except Exception as e:
        if temp_id in McpClientService._sessions:
            await McpClientService._sessions[temp_id].close()
            del McpClientService._sessions[temp_id]
        raise HTTPException(status_code=400, detail=f"连接失败: {str(e)}")

@router.get("/servers", response_model=List[McpServerResponse])
async def list_mcp_servers(
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("menu", "menu:system:config"))
):
    """List all configured MCP servers with detailed tool counts"""
    # ... (rest of function)
    stmt = select(McpServer)
    result = await db.execute(stmt)
    servers = result.scalars().all()
    
    res = []
    for s in servers:
        # Total count
        count_stmt = select(func.count(McpToolCache.id)).where(McpToolCache.server_id == s.id)
        total_count = (await db.execute(count_stmt)).scalar() or 0
        
        # Published count
        pub_stmt = select(func.count(McpToolCache.id)).where(
            McpToolCache.server_id == s.id,
            McpToolCache.is_published == True
        )
        pub_count = (await db.execute(pub_stmt)).scalar() or 0
        
        item = McpServerResponse.model_validate(s)
        item.tool_count = total_count
        item.published_tool_count = pub_count
        res.append(item)
    return res

@router.post("/servers", response_model=McpServerResponse)
async def create_mcp_server(
    data: McpServerBase,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    # Check for duplicates
    # ... (rest of function)
    exist_stmt = select(McpServer).where(McpServer.sse_url == data.sse_url)
    existing = (await db.execute(exist_stmt)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail=f"该服务地址已存在 (已命名为: {existing.server_name})")

    server_id = str(uuid.uuid4())
    new_server = McpServer(id=server_id, **data.model_dump())
    db.add(new_server)
    await db.commit()
    
    # Auto-sync tools immediately after creation
    try:
        await McpClientService.sync_tools(server_id)
    except Exception as e:
        logger.warning(f"Initial sync failed for new server {server_id}: {e}")
        
    return {**data.model_dump(), "id": server_id, "tool_count": 0, "published_tool_count": 0}

@router.put("/servers/{server_id}", response_model=McpServerResponse)
async def update_mcp_server(
    server_id: str,
    data: McpServerBase,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    # ... (rest of function)
    stmt = select(McpServer).where(McpServer.id == server_id)
    server = (await db.execute(stmt)).scalar_one_or_none()
    if not server: raise HTTPException(status_code=404, detail="Server not found")
    
    # Check if new SSE URL is being used by another server
    if data.sse_url != server.sse_url:
        exist_stmt = select(McpServer).where(McpServer.sse_url == data.sse_url, McpServer.id != server_id)
        existing = (await db.execute(exist_stmt)).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=400, detail=f"新地址已被其他服务占用: {existing.server_name}")

    server.server_name = data.server_name
    server.sse_url = data.sse_url
    server.auth_headers = data.auth_headers
    server.enabled_status = data.enabled_status
    await db.commit()
    
    # Auto-sync tools after update to capture any changes
    try:
        await McpClientService.sync_tools(server_id)
    except Exception as e:
        logger.warning(f"Sync failed during update for server {server_id}: {e}")
    
    # Return with updated counts
    count_stmt = select(func.count(McpToolCache.id)).where(McpToolCache.server_id == server_id)
    total = (await db.execute(count_stmt)).scalar() or 0
    pub_stmt = select(func.count(McpToolCache.id)).where(McpToolCache.server_id == server_id, McpToolCache.is_published == True)
    pub = (await db.execute(pub_stmt)).scalar() or 0
    
    return {**data.model_dump(), "id": server_id, "tool_count": total, "published_tool_count": pub}

@router.delete("/servers/{server_id}")
async def delete_mcp_server(
    server_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    # ... (rest of function)
    # 1. Cascade delete associated tools first
    await db.execute(delete(McpToolCache).where(McpToolCache.server_id == server_id))
    
    # 2. Delete the server itself
    await db.execute(delete(McpServer).where(McpServer.id == server_id))
    
    await db.commit()
    return {"message": "Server and associated tools deleted"}

@router.post("/servers/{server_id}/sync")
async def sync_mcp_tools(
    server_id: str,
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    # ... (rest of function)
    try:
        await McpClientService.sync_tools(server_id)
        return {"status": "success", "message": "Tools synchronized successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/servers/{server_id}/tools", response_model=List[McpToolResponseWithUsage])
async def list_mcp_server_tools(
    server_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("menu", "menu:system:config"))
):
    # ... (rest of function)
    stmt = select(McpToolCache).where(McpToolCache.server_id == server_id)
    tools = (await db.execute(stmt)).scalars().all()
    
    v_stmt = select(AIAgentVersion.tools)
    all_versions_tools = (await db.execute(v_stmt)).scalars().all()
    
    usage_map = {}
    for tool_config in all_versions_tools:
        if not tool_config: continue
        actual_list = tool_config
        if isinstance(tool_config, str):
            try: actual_list = json.loads(tool_config)
            except: continue
        if not isinstance(actual_list, list): continue
        for t in actual_list:
            t_name = t if isinstance(t, str) else (t.get("name") if isinstance(t, dict) else None)
            if t_name: usage_map[t_name] = usage_map.get(t_name, 0) + 1

    res = []
    for t in tools:
        item = McpToolResponseWithUsage.model_validate(t)
        item.usage_count = usage_map.get(t.tool_name, 0)
        res.append(item)
    return res

class ToolExecutionRequest(BaseModel):
    arguments: Dict[str, Any]

@router.post("/tools/{tool_id}/execute")
async def execute_mcp_tool(
    tool_id: str,
    req: ToolExecutionRequest,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    # ... (rest of function)
    stmt = select(McpToolCache).where(McpToolCache.id == tool_id)
    tool = (await db.execute(stmt)).scalar_one_or_none()
    if not tool: raise HTTPException(status_code=404, detail="Tool not found")
    try:
        lc_tool = McpToolFactory.create_tool(tool)
        result = await lc_tool.ainvoke(req.arguments)
        return {"status": "success", "result": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.put("/tools/{tool_id}/publish")
async def toggle_tool_publish(
    tool_id: str,
    published: bool,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
) -> Dict:
    stmt = update(McpToolCache).where(McpToolCache.id == tool_id).values(is_published=published)
    await db.execute(stmt)
    await db.commit()
    return {"status": "success", "is_published": published}
