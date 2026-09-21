from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from app.core.avatar_assets import (
    AGENT_AVATAR_DIR,
    AGENT_AVATAR_URL,
    MAX_AVATAR_BYTES,
    PENDING_AVATAR_PREFIX,
    purge_stale_pending_avatars,
    resolve_avatar_extension,
    sanitize_asset_key,
    save_avatar_file,
)
from app.core.orm import get_db_session
from app.core.dependencies import require_admin, get_current_user, require_permission
from typing import Dict, Any
from app.services.ai.agent_manager import AgentManagerService
from app.schemas.agent import (
    AIAgentResponse, AIAgentBase,
    AIAgentReorderRequest,
    AIAgentVersionResponse, AIAgentVersionBase,
    AgentExecutionHistoryResponse,
    AgentOnboardingCreateRequest,
    AgentOnboardingResponse,
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


@router.get("/{agent_id}/embed-access", response_model=AIAgentResponse)
async def get_embed_agent_access(
    agent_id: str,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """
    EmbedChat URL 深链校验：支持按 id 或 name 解析。
    - 404：智能体不存在或已停用
    - 403：智能体存在但当前用户无权使用
    """
    from sqlalchemy import or_, select
    from app.models.agent import AIAgent

    key = str(agent_id or "").strip()
    if not key:
        raise HTTPException(
            status_code=404,
            detail={"code": "AGENT_NOT_FOUND", "message": "智能体不存在或已停用", "agent_key": agent_id},
        )

    stmt = select(AIAgent).where(or_(AIAgent.id == key, AIAgent.name == key)).limit(1)
    agent = (await session.execute(stmt)).scalar_one_or_none()
    if not agent or not agent.is_enabled:
        raise HTTPException(
            status_code=404,
            detail={"code": "AGENT_NOT_FOUND", "message": "智能体不存在或已停用", "agent_key": agent_id},
        )

    can_use = await AgentManagerService._user_can_execute_agent(session, agent, user)
    if not can_use:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "AGENT_FORBIDDEN",
                "message": "无权使用该智能体",
                "agent_key": agent_id,
                "agent_id": agent.id,
                "agent_name": agent.name,
                "display_name": agent.display_name,
            },
        )
    return agent

@router.get("/", response_model=List[AIAgentResponse], include_in_schema=False)
@router.get("", response_model=List[AIAgentResponse])
async def list_agents(session: AsyncSession = Depends(get_db_session), user: Dict[str, Any] = Depends(require_permission("menu", "menu:agent_management"))):
    """获取所有智能体列表 (基于权限过滤)"""
    return await AgentManagerService.list_agents(session, user=user)


@router.get("/toolcall-timeout")
async def get_agent_toolcall_timeout(
    user: Dict[str, Any] = Depends(require_permission("menu", "menu:agent_management")),
):
    """返回智能体管理页需要展示的全局工具调用超时。"""
    from app.services.ai.runtime.agentscope.tool_timeout import load_agent_max_toolcall_timeout

    timeout_seconds = await load_agent_max_toolcall_timeout()
    return {"seconds": int(timeout_seconds)}


# 与 `ai_agents.name` 的 String(100) 对齐：预检就拦住超长名称，避免落库报错
AGENT_NAME_MAX_LENGTH = 100


def _name_availability_payload(
    name: str, available: bool, reason: Optional[str], message: Optional[str]
) -> Dict[str, Any]:
    return {
        "code": 0,
        "data": {
            "name": name,
            "available": available,
            "reason": reason,
            "message": message,
        },
    }


@router.get(
    "/name-availability",
    dependencies=[Depends(require_permission("menu", "menu:agent_management"))],
    summary="预检智能体物理标识符是否可用（创建/改名前调用）",
)
async def check_agent_name_availability(
    name: str = "",
    exclude_agent_id: Optional[str] = None,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """物理标识符在 `ai_agents.name` 上全局唯一，撞名时创建接口返回 400。

    这里提供轻量预检，让前端在输入时就能提示「已被占用」，而不是提交后才失败。
    判定与创建路径共用 `find_agent_name_conflict`，不会出现两套口径。

    权限用页面级 `menu:agent_management`（而非创建的 element 权限）：这是只读检查，
    可见信息不多于 `GET /agents` 列表，且改自己名字的用户同样需要它。
    """
    normalized = AgentManagerService.normalize_agent_name(name)
    if not normalized:
        return _name_availability_payload(normalized, False, "empty", "物理标识符不能为空")
    if len(normalized) > AGENT_NAME_MAX_LENGTH:
        return _name_availability_payload(
            normalized,
            False,
            "too_long",
            f"物理标识符最多 {AGENT_NAME_MAX_LENGTH} 个字符",
        )

    conflict = await AgentManagerService.find_agent_name_conflict(
        session, normalized, exclude_agent_id=exclude_agent_id
    )
    if conflict:
        is_admin, username = AgentManagerService._resolve_actor(user)
        owned = bool(username) and conflict.created_by == username
        detail = (
            "该标识符已被你自己创建的智能体占用，可在列表中直接继续配置"
            if owned
            else "该标识符已被占用（全局唯一，可能由其他成员创建），请换一个"
        )
        return _name_availability_payload(normalized, False, "taken", detail)

    return _name_availability_payload(normalized, True, None, None)


@router.post("/reorder")
async def reorder_agents(
    data: AIAgentReorderRequest,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(require_admin),
):
    """批量更新智能体排序权重（值越大越靠前）"""
    success = await AgentManagerService.reorder_agents(session, data.items, user=user)
    if not success:
        raise HTTPException(status_code=403, detail="排序失败：主智能体不可参与排序")
    return {"status": "success"}

@router.post("/", response_model=AIAgentResponse, dependencies=[Depends(require_permission("element", "element:agent:create"))])
async def create_agent(data: AIAgentBase, session: AsyncSession = Depends(get_db_session), user: Dict[str, Any] = Depends(get_current_user)):
    """创建新智能体"""
    try:
        return await AgentManagerService.create_agent(session, data, user=user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/onboarding",
    response_model=AgentOnboardingResponse,
    dependencies=[Depends(require_permission("element", "element:agent:create"))],
)
async def create_agent_onboarding(
    data: AgentOnboardingCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    result = await AgentManagerService.create_agent_onboarding(
        session,
        data,
        onboarding_key=data.onboarding_key,
        user=user,
    )
    return AgentOnboardingResponse(
        agent=result.agent,
        version=result.version,
        onboarding_step=result.agent.onboarding_step,
        template_fallback=result.template_fallback,
    )

@router.put("/{agent_id}", response_model=AIAgentResponse, dependencies=[Depends(require_permission("element", "element:agent:edit"))])
async def update_agent(
    agent_id: str, 
    data: AIAgentBase, 
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """更新智能体元数据"""
    try:
        agent = await AgentManagerService.update_agent(session, agent_id, data, user=user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not agent:
        raise HTTPException(status_code=403, detail="Forbidden: You can only edit your own agents")
    return agent


@router.post(
    "/avatar/upload",
    dependencies=[Depends(require_permission("element", "element:agent:create"))],
    summary="上传待绑定智能体头像（新建流程尚无 agent_id 时使用）",
)
async def upload_pending_agent_avatar(file: UploadFile = File(...)):
    """新建智能体时先上传头像：此时还没有 agent_id，无法走按智能体隔离的清理。

    文件以 `pending_` 前缀落盘，保存时 URL 直接写进 `avatar_url`，因此不需要搬动文件。
    放弃创建会留下孤儿文件，故每次上传顺带回收超过 24h 的 `pending_*`。
    """
    ext = resolve_avatar_extension(file.content_type, file.filename)
    if not ext:
        raise HTTPException(
            status_code=400, detail="仅支持 PNG、JPEG、WebP、SVG、GIF 格式图片"
        )

    data = await file.read()
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=400, detail="头像图片不能超过 2MB")

    purge_stale_pending_avatars(AGENT_AVATAR_DIR)
    avatar_url = save_avatar_file(
        data,
        ext,
        directory=AGENT_AVATAR_DIR,
        public_prefix=AGENT_AVATAR_URL,
        prefix=PENDING_AVATAR_PREFIX,
        # 刻意不做通配清理：pending 文件彼此无主，删不掉「自己上一张」，
        # 只能靠 TTL 回收，绝不能碰 {agent_id}_* 的正式头像。
        cleanup_glob=None,
    )
    return {
        "code": 0,
        "data": {"avatar_url": avatar_url},
        "message": "智能体头像上传成功",
    }


@router.post(
    "/{agent_id}/avatar/upload",
    dependencies=[Depends(require_permission("element", "element:agent:edit"))],
    summary="上传智能体头像图片（落盘并返回短路径 URL，不直接写库）",
)
async def upload_agent_avatar(
    agent_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """上传智能体专属头像。

    只负责落盘并返回 URL，`ai_agents.avatar_url` 仍由 `PUT /{agent_id}` 统一持久化：
    智能体编辑是「表单 + 保存」语义，上传即写库会让「不保存也生效」，破坏撤销预期。
    权限判定与 `PUT /{agent_id}` 共用 `can_edit_agent_meta`，避免权限漂移。
    """
    agent = await AgentManagerService.get_agent_for_edit(session, agent_id, user)
    if not agent:
        raise HTTPException(status_code=403, detail="Forbidden: You can only edit your own agents")

    ext = resolve_avatar_extension(file.content_type, file.filename)
    if not ext:
        raise HTTPException(
            status_code=400, detail="仅支持 PNG、JPEG、WebP、SVG、GIF 格式图片"
        )

    data = await file.read()
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=400, detail="头像图片不能超过 2MB")

    asset_key = sanitize_asset_key(agent.id)
    avatar_url = save_avatar_file(
        data,
        ext,
        directory=AGENT_AVATAR_DIR,
        public_prefix=AGENT_AVATAR_URL,
        prefix=asset_key,
        # 只清理该智能体自己的历史头像。这里**绝不能**用跨智能体的通配：
        # 全局头像上传会清空 data/branding/avatars/，智能体头像在独立子目录，
        # 双方各自只删自己的，才不会互相误删。
        cleanup_glob=f"{asset_key}_*",
    )
    return {
        "code": 0,
        "data": {"avatar_url": avatar_url},
        "message": "智能体头像上传成功",
    }


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
    from app.services.ai.agent_manager import AgentNotReadyError

    try:
        success = await AgentManagerService.publish_version(session, agent_id, version_id, user=user)
    except AgentNotReadyError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "AGENT_NOT_READY", "missing": list(exc.missing)},
        ) from exc
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


@router.get("/{agent_id}/welcome-cards")
async def get_runtime_welcome_cards(
    agent_id: str,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """获取欢迎页卡片：人工模式读固定配置，自动模式读 5 分钟推荐缓存。"""
    config = await AgentManagerService.get_active_agent_config(session, agent_id=agent_id)
    if not config:
        raise HTTPException(status_code=404, detail="Active configuration not found for this agent")
    from app.services.ai.welcome_card_service import get_runtime_welcome_cards as load_cards

    return {"cards": await load_cards(config)}

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
        raw_uid = user.get('user_id') or user.get('id')
        try:
            target_uid = int(raw_uid) if raw_uid is not None else None
        except (TypeError, ValueError):
            target_uid = None
        if target_uid is not None:
            stmt = stmt.where(AgentExecutionHistory.user_id == target_uid)
        else:
            stmt = stmt.where(AgentExecutionHistory.username == user.get('user_name'))

    stmt = stmt.order_by(desc(AgentExecutionHistory.created_at)).limit(limit)
    
    result = await session.execute(stmt)
    rows = result.scalars().all()
    
    return rows
