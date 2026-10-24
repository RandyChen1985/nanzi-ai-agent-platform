from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
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
from app.services.ai.agent_system_publication_service import AgentSystemPublicationService
from app.models.agent import AIAgent, AIAgentVersion
from app.services.mcp.tool_ref_health import find_stale_tool_refs
from app.schemas.agent import (
    AIAgentResponse, AIAgentBase,
    AIAgentReorderRequest,
    AIAgentVersionResponse, AIAgentVersionBase,
    AgentDuplicateRequest,
    AgentExecutionHistoryResponse,
    AgentOnboardingCreateRequest,
    AgentOnboardingResponse,
    AgentSystemHistoryResponse,
    AgentSystemPublishRequest,
    AgentSystemRequestCreate,
    AgentSystemRequestDetail,
    AgentSystemRequestItem,
    AgentSystemReviewRequest,
    AgentSystemUnpublishPreview,
    AgentSystemUnpublishRequest,
)

router = APIRouter()

@router.get("/allowed", response_model=List[AIAgentResponse])
async def list_allowed_agents(
    keyword: str = None,
    include_disabled: bool = False,
    session: AsyncSession = Depends(get_db_session), 
    user: Dict[str, Any] = Depends(get_current_user)
):
    """获取当前用户有权限使用的智能体列表。

    默认只返回已启用智能体（@提及、任务绑定等可选用场景）；
    ``include_disabled=true`` 额外返回已停用智能体，供聊天日志等历史数据筛选使用。
    """
    agents = list(
        await AgentManagerService.list_allowed_agents(
            session, user=user, keyword=keyword, include_disabled=include_disabled
        )
    )
    # 输入框据此决定是否展示模型切换入口：该字段属于 ai_agent_versions，这里一次性补齐，避免前端逐条回查
    return await AgentManagerService.attach_published_model_switch_flags(session, agents)


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
    # 深链锁定的智能体同样需要已发布版本的「是否允许手动切换模型」，用于决定输入框是否展示模型切换入口
    await AgentManagerService.attach_published_model_switch_flags(session, [agent])
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


@router.get(
    "/model-health",
    dependencies=[Depends(require_permission("menu", "menu:agent_management"))],
    summary="系统默认 LLM 模型的健康状态（智能体管理页顶部提示用）",
)
async def get_agent_model_health(session: AsyncSession = Depends(get_db_session)):
    """默认模型未配置 / 未注册 / 已禁用 / 缺 API Key 时返回具体原因。

    判定与智能体自身的 ``model_issue`` 同源（同一个模型表快照规则），
    区别只在于这里校验的是系统配置 ``llm_model_name`` 指向的模型。
    """
    from app.services.ai.agent_model_health import load_default_model_issue

    issue = await load_default_model_issue(session)
    return {"default_model_issue": issue.to_dict() if issue else None}


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
    "/{agent_id}/duplicate",
    response_model=AIAgentResponse,
    dependencies=[Depends(require_permission("element", "element:agent:create"))],
)
async def duplicate_agent(
    agent_id: str,
    data: AgentDuplicateRequest,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """复制一个智能体（元数据 + 已发布版本配置），副本创建后立即可用。"""
    try:
        agent = await AgentManagerService.duplicate_agent(session, agent_id, data, user=user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not agent:
        raise HTTPException(status_code=404, detail="智能体不存在")
    return agent


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
        dropped_tool_refs=list(result.dropped_tool_refs or []),
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

@router.get("/{agent_id}/versions/{version_id}/tool-health")
async def get_agent_version_tool_health(
    agent_id: str,
    version_id: str,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """列出该版本配置里运行时已失效的工具引用（MCP 服务被删/禁用、工具下线等）。

    只读诊断，用于给编辑器一个「解绑」入口：版本配置的 tools 是整体加载、整体保存的，
    失效引用既不会自己消失，也不会出现在工具选择器里，没有这个接口用户无从取消。
    判定口径与运行时一致；权限与版本编辑一致（owner 或管理员），不向他人暴露工具配置。
    """
    agent = await session.get(AIAgent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    is_admin, username = AgentManagerService._resolve_actor(user)
    if not is_admin and str(agent.created_by or "") != username:
        raise HTTPException(status_code=403, detail="Forbidden: 只能查看自己智能体的版本配置")

    version = (
        await session.execute(
            select(AIAgentVersion).where(
                AIAgentVersion.id == version_id,
                AIAgentVersion.agent_id == agent_id,
            )
        )
    ).scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    stale_tools = await find_stale_tool_refs(session, version.tools, user=user)
    return {
        "agent_id": agent_id,
        "version_id": version_id,
        "stale_count": len(stale_tools),
        "stale_tools": stale_tools,
    }


class AgentToolsHealthRequest(BaseModel):
    """待保存的工具配置。项可能是名称，也可能是带运行配置的对象。"""

    tools: List[Any] = []


@router.post("/{agent_id}/tool-health")
async def check_agent_tools_health(
    agent_id: str,
    payload: AgentToolsHealthRequest,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """诊断「还没有版本 id」的工具配置（新建、克隆）。

    克隆会把旧版本的失效引用整份复制过来，而这一刻恰恰最需要提示：工具选择器
    只渲染可用工具，克隆进来的幽灵引用既看不见也取消不了。判定逻辑与按版本查询
    完全一致，只是把 tools 从请求体里拿而不是从库里读。
    """
    agent = await session.get(AIAgent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    is_admin, username = AgentManagerService._resolve_actor(user)
    if not is_admin and str(agent.created_by or "") != username:
        raise HTTPException(status_code=403, detail="Forbidden: 只能校验自己智能体的工具配置")

    stale_tools = await find_stale_tool_refs(session, payload.tools, user=user)
    return {
        "agent_id": agent_id,
        "version_id": None,
        "stale_count": len(stale_tools),
        "stale_tools": stale_tools,
    }


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


# ---------------------------------------------------------------------------
# 系统级发布：申请 → 审核 → 上架 → 下架，以及发布历史
#
# 只有 LOCAL 引擎、且至少有一个 PUBLISHED 版本，才能被设为系统级——运行时按
# status='PUBLISHED' 取配置，取不到等于一个点了就报错的智能体（服务层统一校验）。
# 路由顺序：/system/requests/count 必须排在 /system/requests/{request_id} 之前。
# ---------------------------------------------------------------------------

_SYSTEM_PUBLISH_PERM = "element:agent:system_publish"


class AgentSystemApproveResponse(BaseModel):
    agent_id: str
    is_system: bool
    message: str = "已发布为系统级"


@router.post(
    "/{agent_id}/system/requests",
    dependencies=[Depends(require_permission("element", "element:agent:edit"))],
)
async def submit_system_request(
    agent_id: str,
    data: AgentSystemRequestCreate,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """提交「发布为系统级」申请：提交即快照，内容冻结，等管理员审核。"""
    request = await AgentSystemPublicationService.submit_request(
        session, agent_id, user, comment=data.comment
    )
    return {"request_id": request.id, "status": request.status}


class AgentVersionReleaseRequest(BaseModel):
    """申请把某个草稿版本发布上线。"""

    version_id: str
    comment: Optional[str] = None


@router.post(
    "/{agent_id}/system/release-requests",
    dependencies=[Depends(require_permission("element", "element:agent:edit"))],
)
async def submit_version_release_request(
    agent_id: str,
    data: AgentVersionReleaseRequest,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """提交「发布版本上线」申请：系统级智能体的线上基线只能由管理员变更。

    所有者改完草稿后走这条通道，管理员审核通过即发布该版本，
    不必线下沟通「你去点一下 V3」。
    """
    request = await AgentSystemPublicationService.submit_release_request(
        session, agent_id, data.version_id, user, comment=data.comment
    )
    return {"request_id": request.id, "status": request.status}


@router.delete(
    "/{agent_id}/system/requests",
    dependencies=[Depends(require_permission("element", "element:agent:edit"))],
)
async def cancel_system_request(
    agent_id: str,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """撤回待审申请，立即解冻。"""
    request = await AgentSystemPublicationService.cancel_request(session, agent_id, user)
    return {"request_id": request.id, "status": request.status}


@router.get(
    "/system/requests/count",
    dependencies=[Depends(require_permission("element", _SYSTEM_PUBLISH_PERM))],
)
async def count_system_requests(
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """待审数量，用于「发布审核」入口角标。"""
    return {"count": await AgentSystemPublicationService.pending_count(session)}


@router.get(
    "/system/requests",
    response_model=List[AgentSystemRequestItem],
    dependencies=[Depends(require_permission("element", _SYSTEM_PUBLISH_PERM))],
)
async def list_system_requests(
    status: str = "PENDING",
    page: int = 1,
    page_size: int = 20,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """待审列表（默认按等待时长升序，等得最久的排最前）。"""
    return await AgentSystemPublicationService.list_requests(
        session,
        status=status,
        limit=page_size,
        offset=max(0, (max(1, page) - 1) * page_size),
    )


@router.get(
    "/system/requests/{request_id}",
    response_model=AgentSystemRequestDetail,
    dependencies=[Depends(require_permission("element", _SYSTEM_PUBLISH_PERM))],
)
async def get_system_request(
    request_id: str,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """申请单详情：包含提交时的完整内容快照（只读）。"""
    return await AgentSystemPublicationService.request_detail(session, request_id)


@router.post(
    "/system/requests/{request_id}/approve",
    response_model=AgentSystemApproveResponse,
    dependencies=[Depends(require_permission("element", _SYSTEM_PUBLISH_PERM))],
)
async def approve_system_request(
    request_id: str,
    data: AgentSystemReviewRequest,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """通过审核：只把智能体置为系统级并记事件，不改任何版本状态。

    注意：通过后立即生效，该智能体马上进入自动委派与路由候选。
    """
    agent = await AgentSystemPublicationService.approve_request(
        session, request_id, user, comment=data.comment
    )
    return AgentSystemApproveResponse(agent_id=agent.id, is_system=bool(agent.is_system))


@router.post(
    "/system/requests/{request_id}/reject",
    dependencies=[Depends(require_permission("element", _SYSTEM_PUBLISH_PERM))],
)
async def reject_system_request(
    request_id: str,
    data: AgentSystemReviewRequest,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """驳回申请：必须写理由，申请单关闭并解冻内容。"""
    request = await AgentSystemPublicationService.reject_request(
        session, request_id, user, comment=data.comment or ""
    )
    return {"request_id": request.id, "status": request.status}


@router.post(
    "/{agent_id}/system/publish",
    response_model=AIAgentResponse,
    dependencies=[Depends(require_permission("element", _SYSTEM_PUBLISH_PERM))],
)
async def publish_system_agent(
    agent_id: str,
    data: AgentSystemPublishRequest,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """直接设为系统级（不选版本、不改版本状态）；会通知所有者并自动授权。"""
    return await AgentSystemPublicationService.publish_system(
        session,
        agent_id,
        user,
        reason=data.reason,
        notify_restore=data.notify_restore,
    )


@router.get(
    "/{agent_id}/system/unpublish-preview",
    response_model=AgentSystemUnpublishPreview,
    dependencies=[Depends(require_permission("element", _SYSTEM_PUBLISH_PERM))],
)
async def preview_system_unpublish(
    agent_id: str,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """下架前的影响范围：会影响多少「实际能用」的人，供弹窗展示。"""
    return await AgentSystemPublicationService.unpublish_preview(session, agent_id)


@router.post(
    "/{agent_id}/system/unpublish",
    dependencies=[Depends(require_permission("element", _SYSTEM_PUBLISH_PERM))],
)
async def unpublish_system_agent(
    agent_id: str,
    data: AgentSystemUnpublishRequest,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """下架：取消系统级、物归原主、按需通知已分配用户；授权保留以便重新上架。"""
    return await AgentSystemPublicationService.unpublish_system(
        session, agent_id, user, reason=data.reason, notify=data.notify
    )


@router.get(
    "/{agent_id}/system/history",
    response_model=AgentSystemHistoryResponse,
)
async def get_system_history(
    agent_id: str,
    page: int = 1,
    page_size: int = 20,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """发布历史：申请与事件合并的时间线，仅管理员与创建者可见。"""
    return await AgentSystemPublicationService.history(
        session,
        agent_id,
        user,
        limit=page_size,
        offset=max(0, (max(1, page) - 1) * page_size),
    )
