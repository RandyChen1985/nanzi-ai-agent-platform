from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from app.core.orm import get_db_session
from app.core.dependencies import require_admin, get_current_user, require_permission
from typing import Dict, Any
from app.services.ai.agent_manager import AgentManagerService
from app.schemas.agent import (
    AIAgentResponse, AIAgentBase,
    AIAgentReorderRequest,
    AIAgentVersionResponse, AIAgentVersionBase,
    AgentExecutionHistoryResponse
)

router = APIRouter()

@router.get("/allowed", response_model=List[AIAgentResponse])
async def list_allowed_agents(
    keyword: str = None,
    session: AsyncSession = Depends(get_db_session), 
    user: Dict[str, Any] = Depends(get_current_user)
):
    """获取当前用户有权限使用的智能体列表 (用于 @提及)"""
    return await AgentManagerService.list_allowed_agents(session, user=user, keyword=keyword)

@router.get("/", response_model=List[AIAgentResponse], include_in_schema=False)
@router.get("", response_model=List[AIAgentResponse])
async def list_agents(session: AsyncSession = Depends(get_db_session), user: Dict[str, Any] = Depends(require_permission("menu", "menu:agent_management"))):
    """获取所有智能体列表 (基于权限过滤)"""
    return await AgentManagerService.list_agents(session, user=user)

@router.post("/reorder")
async def reorder_agents(
    data: AIAgentReorderRequest,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(require_admin),
):
    """批量更新智能体排序权重（值越大越靠前）"""
    success = await AgentManagerService.reorder_agents(session, data.items, user=user)
    if not success:
        raise HTTPException(status_code=403, detail="Forbidden: Only admins can reorder agents")
    return {"status": "success"}

@router.post("/", response_model=AIAgentResponse, dependencies=[Depends(require_permission("element", "element:agent:create"))])
async def create_agent(data: AIAgentBase, session: AsyncSession = Depends(get_db_session), user: Dict[str, Any] = Depends(get_current_user)):
    """创建新智能体"""
    return await AgentManagerService.create_agent(session, data, user=user)

@router.put("/{agent_id}", response_model=AIAgentResponse, dependencies=[Depends(require_permission("element", "element:agent:edit"))])
async def update_agent(
    agent_id: str, 
    data: AIAgentBase, 
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """更新智能体元数据"""
    agent = await AgentManagerService.update_agent(session, agent_id, data, user=user)
    if not agent:
        raise HTTPException(status_code=403, detail="Forbidden: You can only edit your own agents")
    return agent

@router.delete("/{agent_id}", dependencies=[Depends(require_permission("element", "element:agent:delete"))])
async def delete_agent(
    agent_id: str, 
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """删除智能体 (系统内置智能体不可删除)"""
    success = await AgentManagerService.delete_agent(session, agent_id, user=user)
    if not success:
        raise HTTPException(status_code=403, detail="Forbidden: Cannot delete system agents or agents you do not own")
    return {"status": "success"}

@router.get("/{agent_id}/versions", response_model=List[AIAgentVersionResponse])
async def list_agent_versions(agent_id: str, session: AsyncSession = Depends(get_db_session), user: Dict[str, Any] = Depends(get_current_user)):
    """获取智能体的所有版本记录"""
    return await AgentManagerService.get_agent_versions(session, agent_id, user=user)

@router.post("/{agent_id}/versions", response_model=AIAgentVersionResponse)
async def create_agent_version(agent_id: str, data: AIAgentVersionBase, session: AsyncSession = Depends(get_db_session), user: Dict[str, Any] = Depends(get_current_user)):
    """为智能体创建新版本 (默认 DRAFT)"""
    try:
        version = await AgentManagerService.create_agent_version(session, agent_id, data, user=user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not version:
        raise HTTPException(status_code=403, detail="Forbidden: You can only add versions to your own agents")
    return version

@router.put("/{agent_id}/versions/{version_id}", response_model=AIAgentVersionResponse)
async def update_agent_version(agent_id: str, version_id: str, data: AIAgentVersionBase, session: AsyncSession = Depends(get_db_session), user: Dict[str, Any] = Depends(get_current_user)):
    """更新现有的草稿版本 (仅限 DRAFT 状态)"""
    try:
        version = await AgentManagerService.update_agent_version(session, agent_id, version_id, data, user=user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not version:
        raise HTTPException(status_code=403, detail="Forbidden: Version not found, not a DRAFT, or missing permissions")
    return version

@router.post("/{agent_id}/versions/{version_id}/publish")
async def publish_agent_version(agent_id: str, version_id: str, session: AsyncSession = Depends(get_db_session), user: Dict[str, Any] = Depends(get_current_user)):
    """发布特定版本（将该版本设为 PUBLISHED，原发布版本设为 ARCHIVED）"""
    success = await AgentManagerService.publish_version(session, agent_id, version_id, user=user)
    if not success:
        raise HTTPException(status_code=403, detail="Forbidden: Failed to publish version (check ownership)")
    return {"status": "success"}
    return {"status": "success"}

@router.delete("/{agent_id}/versions/{version_id}")
async def delete_agent_version(agent_id: str, version_id: str, session: AsyncSession = Depends(get_db_session), user: Dict[str, Any] = Depends(get_current_user)):
    """删除指定版本 (仅限 DRAFT 或 ARCHIVED)"""
    success = await AgentManagerService.delete_version(session, agent_id, version_id, user=user)
    if not success:
        raise HTTPException(status_code=403, detail="Forbidden: Version not found, currently active, or missing permissions")
    return {"status": "success"}

@router.get("/{agent_id}/active-config")
async def get_active_config(
    agent_id: str, 
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """获取智能体当前的活跃版本配置 (用于调试预览)"""
    config = await AgentManagerService.get_active_agent_config(session, agent_id=agent_id)
    if not config:
        raise HTTPException(status_code=404, detail="Active configuration not found for this agent")
    return config

@router.get("/{agent_id}/executions", response_model=List[AgentExecutionHistoryResponse])
async def list_agent_executions(
    agent_id: str, 
    limit: int = 50, 
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """
    获取智能体的历史对话记录
    """
    from app.models.audit import AgentExecutionHistory
    from sqlalchemy import select, desc
    
    # Simple query: Filter by agent_id, order by time desc
    stmt = (
        select(AgentExecutionHistory)
        .where(AgentExecutionHistory.agent_id == agent_id)
    )
    
    # Filter by user if not admin
    if user.get('role') != 'admin':
        # Assuming user dict has 'id' or 'user_name' to match against AgentExecutionHistory
        # If user_id is in the user dict, use it. Otherwise fall back to user_name?
        # Standardize on user_id if possible. 
        # Checking schema: AgentExecutionHistory has user_id and username.
        stmt = stmt.where(AgentExecutionHistory.user_id == user.get('id'))

    stmt = stmt.order_by(desc(AgentExecutionHistory.created_at)).limit(limit)
    
    result = await session.execute(stmt)
    rows = result.scalars().all()
    
    return rows
