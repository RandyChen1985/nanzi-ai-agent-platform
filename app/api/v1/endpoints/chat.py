import json
import os
import time
import uuid
import re
from typing import List, Optional, AsyncGenerator, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Request, UploadFile, File
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.orm import get_db_session
from app.services.ai.agent_service import agent_service
from app.services.ai.export_service import ExportService
from app.core.context import set_debug_context
from app.core.dependencies import require_api_key
from app.schemas.agent import TraceLogResponse, AgentExecutionHistoryListResponse
from app.services.permission_service import PermissionService
import logging


logger = logging.getLogger(__name__)

router = APIRouter()

class SkillMeta(BaseModel):
    id: Optional[str] = Field(default=None, description="技能 ID")
    name: Optional[str] = Field(default=None, description="SKILL.md Frontmatter name")
    description: Optional[str] = Field(default=None, description="SKILL.md Frontmatter description")


class ChatFile(BaseModel):
    type: Optional[str] = Field(default=None, description="附件类型，如 skill 表示技能工作流")
    url: str = Field(..., description="附件可访问静态 URL")
    filename: str = Field(..., description="附件原始文件名")
    size: int = Field(..., description="文件字节大小")
    ext: str = Field(..., description="文件后缀名")
    skillMeta: Optional[SkillMeta] = Field(default=None, description="技能 Frontmatter 元数据（type=skill 时）")
    skill_meta: Optional[SkillMeta] = Field(default=None, description="skillMeta 蛇形命名别名")

class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str
    files: Optional[List[ChatFile]] = Field(default=None, description="单条消息挂载的附件")


class ChatCompletionRequest(BaseModel):
    messages: List[ChatMessage]
    stream: bool = False
    model: Optional[str] = None
    agent_id: Optional[str] = None
    version_id: Optional[str] = None
    conversation_id: Optional[str] = None  # 服务端对话记忆 ID
    enable_multi_agent: bool = True        # 是否启用多智能体协同
    knowledge_dataset_ids: Optional[List[str]] = Field(
        default=None,
        description="本轮结构化指定的 RAGFlow 知识库 dataset ID 列表（优先于消息内文本提示）",
    )
    debug_options: Optional[Dict[str, Any]] = None
    permission_options: Optional[Dict[str, Any]] = None

class ChatCompletionResponse(BaseModel):
    content: str
    intent: str
    confidence: Optional[float] = None
    reasoning: Optional[str] = None
    model: Optional[str] = None
    trace_id: Optional[str] = None


class ToolPermissionConfirmRequest(BaseModel):
    confirmed: bool = Field(..., description="是否允许执行该工具调用")


class ExternalExecutionResultItem(BaseModel):
    id: str = Field(..., description="tool_call id")
    name: str = Field(..., description="tool name")
    output: str = Field(..., description="tool execution output text")
    state: str = Field(default="success", description="tool result state")


class ExternalExecutionResumeRequest(BaseModel):
    results: list[ExternalExecutionResultItem] = Field(
        ...,
        description="外部执行工具返回结果列表",
    )


from app.schemas.response import StandardResponse

class GreetingResponse(BaseModel):
    greeting: str = Field(..., description="欢迎语内容")


class DatasetNavigationResponse(BaseModel):
    dataset_count: int = Field(..., description="当前用户可访问的数据集数量")
    dataset_menu_hash: str = Field(..., description="当前授权数据目录的内容指纹，用于判断导航是否变化")
    generated_at: str = Field(..., description="本次导航生成时间")
    groups: List[Dict[str, Any]] = Field(default_factory=list, description="按标签分组的数据集导航")
    markdown: str = Field(..., description="含 quick 按钮的 Markdown 导航内容")


class DatasetMenuClickRequest(BaseModel):
    dataset_menu_hash: str = Field(..., description="当前数据目录 hash")
    query: str = Field(..., description="用户点击的完整 quick 问题")
    label: Optional[str] = Field(default=None, description="按钮短标签")
    group_id: Optional[str] = Field(default=None, description="业务场景卡片 ID")


@router.get("/greeting", 
    response_model=StandardResponse[GreetingResponse],
    summary="获取欢迎语",
    description="获取系统动态生成的欢迎语配置。"
)
async def get_greeting():
    """
    Get a dynamically generated welcome message.
    """
    greeting = await agent_service.generate_greeting()
    # return {"greeting": greeting} -> Wrap
    return StandardResponse(data=GreetingResponse(greeting=greeting))


@router.get(
    "/dataset-menu",
    response_model=StandardResponse[DatasetNavigationResponse],
    summary="获取我的数据门户",
    description="基于当前用户授权的 {dataset_menu} 目录，由 LLM 生成我的数据门户与 quick 追问建议，供 /dataset_menu 系统指令使用。",
)
async def get_dataset_menu_navigation(
    refresh: bool = False,
    db: AsyncSession = Depends(get_db_session),
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    from app.services.dataset_navigation_service import DatasetNavigationService

    raw_user_id = user_info.get("user_id") or user_info.get("id")
    user_id = int(raw_user_id) if raw_user_id is not None else None
    is_admin = user_info.get("role") == "admin"
    payload = await DatasetNavigationService.build_navigation_for_user(
        db,
        user_id=user_id,
        is_admin=is_admin,
        force_refresh=refresh,
    )
    return StandardResponse(data=DatasetNavigationResponse(**payload))


@router.post(
    "/dataset-menu/click",
    response_model=StandardResponse[Dict[str, bool]],
    summary="记录我的数据门户点击偏好",
    description="记录用户在 /dataset_menu 中点击的 quick 问题，用于同一数据目录下的个性化排序。",
)
async def record_dataset_menu_question_click(
    request: DatasetMenuClickRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    from app.services.dataset_navigation_service import DatasetNavigationService

    raw_user_id = user_info.get("user_id") or user_info.get("id")
    user_id = int(raw_user_id) if raw_user_id is not None else None
    is_admin = user_info.get("role") == "admin"
    await DatasetNavigationService.record_question_click(
        user_id=user_id,
        is_admin=is_admin,
        dataset_menu_hash=request.dataset_menu_hash,
        query=request.query,
        label=request.label,
        group_id=request.group_id,
    )
    return StandardResponse(data={"success": True})


class ConversationHistoryResponse(BaseModel):
    conversation_id: str = Field(..., description="会话ID")
    messages: List[Dict[str, Any]] = Field(..., description="消息列表")

@router.get("/conversation/{conversation_id}",
    response_model=StandardResponse[ConversationHistoryResponse],
    summary="获取会话历史",
    description="从服务端内存 (Redis) 获取指定会话的历史记录。"
)
async def get_conversation_history(
    conversation_id: str,
    limit: Optional[int] = 50,
    offset: int = 0,
    request: Request = None,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Retrieve conversation history from server-side memory (Redis).
    """
    # 1. Permission Check (Optional: Check if user owns this conversation?)
    # For now, we rely on the UUID being hard to guess. 
    # But we now enforce user isolation via Redis keys.
    user_id = user_info.get("user_id") if user_info else None
    
    from app.services.ai.memory_service import memory_service
    
    history = await memory_service.get_history(user_id, conversation_id, limit=limit, offset=offset)
    return StandardResponse(data=ConversationHistoryResponse(
        conversation_id=conversation_id,
        messages=history
    ))


class ConversationFinalizeResponse(BaseModel):
    finalized: bool = Field(..., description="是否已触发摘要写入")
    conversation_id: Optional[str] = None
    reason: Optional[str] = Field(None, description="未写入时的原因")


@router.post(
    "/conversation/{conversation_id}/finalize",
    response_model=StandardResponse[ConversationFinalizeResponse],
    summary="结束会话并刷新记忆摘要",
    description="切换或新建会话前调用，强制合并当前会话摘要（跳过防抖）。",
)
async def finalize_conversation(
    conversation_id: str,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    from app.services.ai.session_summary_service import SessionSummaryService

    user_id = user_info.get("user_id") if user_info else None
    if not user_id:
        raise HTTPException(status_code=401, detail="无法识别当前用户")

    result = await SessionSummaryService.finalize_session(str(user_id), conversation_id)
    return StandardResponse(
        data=ConversationFinalizeResponse(
            finalized=bool(result.get("finalized")),
            conversation_id=result.get("conversation_id") or conversation_id,
            reason=result.get("reason"),
        )
    )


class ModelCallStatDetail(BaseModel):
    call_index: int = Field(..., description="调用序号")
    timestamp: str = Field(..., description="时间戳")
    conversation_id: str = Field(..., description="会话ID")
    agent_name: str = Field(..., description="智能体名称")
    model_name: str = Field(..., description="使用的模型名称")
    input_message_count: int = Field(..., description="输入消息轮数")
    has_tools_bound: bool = Field(..., description="是否绑定了工具")
    input_tokens: int = Field(..., description="输入 Token 数")
    output_tokens: int = Field(..., description="输出 Token 数")
    cache_input_tokens: int = Field(..., description="缓存命中输入 Token")
    total_tokens: int = Field(..., description="总 Token")
    has_tool_calls: bool = Field(..., description="是否触发了工具调用")
    tool_names: List[str] = Field(..., description="调用的工具名称列表")
    elapsed_ms: float = Field(..., description="调用耗时(ms)")
    trace_id: Optional[str] = Field(None, description="本次运行的 Trace ID")
    response_text: Optional[str] = Field("", description="模型输出文本")
    reasoning_content: Optional[str] = Field("", description="模型深度思考内容")
    tool_calls: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="工具调用详情")


class ModelCallStatsResponse(BaseModel):
    stats: List[ModelCallStatDetail] = Field(..., description="大模型调用指标列表")


@router.get("/conversation/{conversation_id}/model_calls",
    response_model=StandardResponse[ModelCallStatsResponse],
    summary="获取会话的大模型调用明细",
    description="从服务端的 Redis 列表中获取当前会话的大模型调用指标，支持通过 trace_id 过滤。"
)
async def get_conversation_model_calls(
    conversation_id: str,
    trace_id: Optional[str] = None,
    user_info: Dict[str, Any] = Depends(require_api_key)
):
    user_id = user_info.get("user_id") if user_info else None
    uid = str(user_id) if user_id else "anonymous"

    from app.services.ai.runtime.agentscope.middleware import STATS_KEY_SUFFIX
    from app.services.ai.memory_service import memory_service
    from app.core.redis import get_redis

    key = f"{memory_service.KEY_PREFIX}:{uid}:{conversation_id}:{STATS_KEY_SUFFIX}"
    redis = await get_redis()
    if not redis:
        return StandardResponse(data=ModelCallStatsResponse(stats=[]))

    raw_data = await redis.lrange(key, 0, -1)
    stats = []
    for item in raw_data:
        try:
            if isinstance(item, bytes):
                item = item.decode("utf-8")
            record = json.loads(item)
            if trace_id and record.get("trace_id") != trace_id:
                continue
            stats.append(record)
        except Exception:
            continue

    return StandardResponse(data=ModelCallStatsResponse(stats=stats))


@router.post("/completions",
    response_model=StandardResponse[ChatCompletionResponse],
    summary="发送对话请求",
    description="统一的对话接口，支持流式 (SSE) 和非流式响应。流式响应直接返回 `text/event-stream`，非流式返回标准 JSONWrapper。",
    responses={
        200: {"description": "成功响应 (非流式)"},
        400: {"description": "参数错误"},
        500: {"description": "内部错误"}
    }
)
async def create_chat_completion(
    completion_request: ChatCompletionRequest,
    request: Request,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Unified Chat Completion endpoint (V1).
    Supports both standard JSON response and SSE Streaming.
    """
    # Initialize Request Context for Debugging
    if completion_request.debug_options:
        set_debug_context(completion_request.debug_options)
    else:
        set_debug_context({}) # Clear/Default

    if not completion_request.messages:
        raise HTTPException(status_code=400, detail="Messages list cannot be empty")
    
    # --- Orchestration / Routing Logic ---
    # DEPRECATED here: Moved to AgentService/ContextManager for better trace and CoT logging.
    # We now let agent_service handle the routing if agent_id is missing.
    
    # Convert Pydantic models to dicts for the service
    history = [msg.dict() for msg in completion_request.messages]
    
    if completion_request.stream:
        async def sse_generator() -> AsyncGenerator[str, None]:
            # Extract user info from request state (set by require_api_key dependency)
            # FASTAPI Middleware attaches state to the raw request object
            # user_info already extracted above

            # Extract API Key for Context Propagation (Tool Authorization)
            api_key_str = request.headers.get("X-API-Key")
            if not api_key_str:
                auth = request.headers.get("Authorization")
                if auth:
                    if auth.startswith("Bearer "):
                        api_key_str = auth.split(" ")[1]
                    else:
                        api_key_str = auth
            
            async for chunk in agent_service.chat_completion_stream(
                history, 
                agent_id=completion_request.agent_id,
                version_id=completion_request.version_id,
                conversation_id=completion_request.conversation_id,
                user_info=user_info,
                api_key=api_key_str,
                enable_multi_agent=completion_request.enable_multi_agent,
                debug_options=completion_request.debug_options,
                permission_options=completion_request.permission_options,
                knowledge_dataset_ids=completion_request.knowledge_dataset_ids,
            ):
                # Format each chunk as an SSE data event
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(sse_generator(), media_type="text/event-stream")
    else:
        # Standard non-streaming response
        # Extract API Key for Context Propagation (Tool Authorization)
        api_key_str = request.headers.get("X-API-Key")
        if not api_key_str:
            auth = request.headers.get("Authorization")
            if auth:
                if auth.startswith("Bearer "):
                    api_key_str = auth.split(" ")[1]
                else:
                    api_key_str = auth

        result = await agent_service.chat_completion(
            history, 
            agent_id=completion_request.agent_id,
            version_id=completion_request.version_id,
            conversation_id=completion_request.conversation_id,
            user_info=user_info,
            api_key=api_key_str,
            enable_multi_agent=completion_request.enable_multi_agent,
            permission_options=completion_request.permission_options,
            knowledge_dataset_ids=completion_request.knowledge_dataset_ids,
        )
        return StandardResponse(data=result)


@router.post(
    "/permissions/{permission_request_id}/confirm",
    summary="确认或拒绝待执行工具调用",
    description="确认 AgentScope ASK 工具调用后继续原 Agent 运行，流式返回后续 SSE。",
)
async def confirm_tool_permission(
    permission_request_id: str,
    confirm_request: ToolPermissionConfirmRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    async def sse_generator() -> AsyncGenerator[str, None]:
        async for chunk in agent_service.resume_agentscope_permission_stream(
            permission_request_id=permission_request_id,
            confirmed=confirm_request.confirmed,
            user_info=user_info,
        ):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse_generator(), media_type="text/event-stream")


@router.post(
    "/external-executions/{external_execution_request_id}/resume",
    summary="提交外部执行工具结果并恢复 Agent",
    description="客户端执行 external tool 后，通过此接口回传结果并继续原 Agent 运行。",
)
async def resume_external_execution(
    external_execution_request_id: str,
    resume_request: ExternalExecutionResumeRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    async def sse_generator() -> AsyncGenerator[str, None]:
        async for chunk in agent_service.resume_agentscope_external_execution_stream(
            external_execution_request_id=external_execution_request_id,
            results=[item.model_dump() for item in resume_request.results],
            user_info=user_info,
        ):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse_generator(), media_type="text/event-stream")


@router.get("/history", 
    response_model=StandardResponse[AgentExecutionHistoryListResponse],
    summary="查询历史记录",
    description="支持分页、筛选查询持久化的对话历史。支持按会话聚合展示。"
)
async def get_history(
    page: int = 1,
    page_size: int = 20,
    agent_id: Optional[str] = None,
    conversation_id: Optional[str] = None, # 新增参数
    username: Optional[str] = None,
    keyword: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    group_by_conversation: bool = False,
    request: Request = None,
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get dialogue history with filtering and pagination.
    """
    from app.models.audit import AgentExecutionHistory
    from sqlalchemy import select, or_, desc, func
    from datetime import datetime
    from app.schemas.agent import AgentExecutionHistoryResponse

    # ... (date parsing logic) ...
    # Parse dates if provided
    start_dt = None
    end_dt = None
    try:
        if start_date:
            start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        if end_date:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use ISO 8601.")

    # 1. Base Query
    if group_by_conversation:
        # Aggregation Logic: Get latest record AND total count per conversation
        subquery = (
            select(
                func.max(AgentExecutionHistory.id).label("max_id"),
                func.count(AgentExecutionHistory.id).label("turn_count")
            )
            .group_by(func.coalesce(AgentExecutionHistory.conversation_id, AgentExecutionHistory.trace_id))
            .subquery()
        )
        query = (
            select(AgentExecutionHistory, subquery.c.turn_count)
            .join(subquery, AgentExecutionHistory.id == subquery.c.max_id)
        )
    else:
        query = select(AgentExecutionHistory)

    # 2. User Filter (Security)
    user_info = getattr(request.state, "user", None) if request else None
    if user_info:
        is_admin = user_info.get("role") == "admin"
        if not is_admin:
            query = query.where(AgentExecutionHistory.username == user_info.get("user_name"))
        elif username:
            query = query.where(AgentExecutionHistory.username == username)

    # 3. Apply Filters
    if agent_id:
        query = query.where(AgentExecutionHistory.agent_id == agent_id)
    if conversation_id: # 应用会话过滤
        query = query.where(AgentExecutionHistory.conversation_id == conversation_id)
    if status:
        query = query.where(AgentExecutionHistory.status == status)
    if keyword:
        search_pattern = f"%{keyword}%"
        query = query.where(or_(AgentExecutionHistory.query.like(search_pattern), AgentExecutionHistory.summary.like(search_pattern)))
    if start_dt:
        query = query.where(AgentExecutionHistory.created_at >= start_dt)
    if end_dt:
        query = query.where(AgentExecutionHistory.created_at <= end_dt)

    # 4. Get Total Count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # 5. Pagination & Ordering
    if group_by_conversation:
        query = query.order_by(desc(AgentExecutionHistory.id))
    else:
        query = query.order_by(desc(AgentExecutionHistory.id))
        
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    
    # 动态获取智能体 ID 到 (Slug 标识名, 显示名) 的映射，用以丰富前端历史列表展现
    try:
        from app.services.ai.agent_manager import AgentManagerService
        all_agents = await AgentManagerService.list_agents(db, user=user_info)
        agent_map = {str(a.id): (a.name, a.display_name) for a in all_agents}
    except Exception as e:
        logger.warning(f"[History API] Failed to fetch active agents mapping: {e}")
        agent_map = {}

    items = []
    if group_by_conversation:
        rows = result.all()
        for row_obj, turn_count in rows:
            item = AgentExecutionHistoryResponse.from_orm(row_obj)
            item.turn_count = turn_count
            if item.agent_id in agent_map:
                item.agent_name = agent_map[item.agent_id][0]
                item.agent_display_name = agent_map[item.agent_id][1]
            items.append(item)
    else:
        rows = result.scalars().all()
        for row in rows:
            item = AgentExecutionHistoryResponse.from_orm(row)
            if item.agent_id in agent_map:
                item.agent_name = agent_map[item.agent_id][0]
                item.agent_display_name = agent_map[item.agent_id][1]
            items.append(item)
    
    return StandardResponse(data=AgentExecutionHistoryListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=items
    ))

@router.delete("/history/{trace_id}",
    response_model=StandardResponse[Dict[str, bool]],
    summary="删除历史记录",
    description="删除指定的对话历史记录及关联的追踪日志。"
)
async def delete_history(
    trace_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session)
):
    """
    Delete a specific history record.
    """
    from app.models.audit import AgentExecutionHistory, AgentExecutionTrace
    from sqlalchemy import delete, select

    # 1. Find Record
    stmt = select(AgentExecutionHistory).where(AgentExecutionHistory.trace_id == trace_id)
    result = await db.execute(stmt)
    history = result.scalar_one_or_none()
    
    if not history:
        raise HTTPException(status_code=404, detail="History not found")

    # 2. Permission Check
    user_info = getattr(request.state, "user", None)
    if user_info:
        is_admin = user_info.get("role") == "admin"
        # Only allow if admin OR owner
        if not is_admin and history.username != user_info.get("user_name"):
             raise HTTPException(status_code=403, detail="Permission denied")

    # 3. Delete Traces and History
    await db.execute(delete(AgentExecutionTrace).where(AgentExecutionTrace.trace_id == trace_id))
    await db.execute(delete(AgentExecutionHistory).where(AgentExecutionHistory.trace_id == trace_id))
    
    await db.commit()
    
    return StandardResponse(data={"success": True})

class BatchDeleteHistoryRequest(BaseModel):
    conversation_ids: List[str] = Field(..., description="待批量删除的会话ID列表")

@router.post("/history/batch-delete",
    response_model=StandardResponse[Dict[str, bool]],
    summary="批量删除历史记录",
    description="根据一组会话 ID 批量删除对应的对话历史记录及关联的追踪日志。"
)
async def batch_delete_history(
    payload: BatchDeleteHistoryRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session)
):
    from app.models.audit import AgentExecutionHistory, AgentExecutionTrace
    from sqlalchemy import delete, select

    if not payload.conversation_ids:
        raise HTTPException(status_code=400, detail="conversation_ids 不能为空")

    # 1. 权限隔离：如果是非 admin 用户，只能删除属于该用户的会话
    user_info = getattr(request.state, "user", None)
    is_admin = False
    username = None
    if user_info:
        is_admin = user_info.get("role") == "admin"
        username = user_info.get("user_name")

    # 2. 查询对应的 trace_id 列表，以便级联删除 AgentExecutionTrace
    stmt = select(AgentExecutionHistory.trace_id).where(AgentExecutionHistory.conversation_id.in_(payload.conversation_ids))
    if user_info and not is_admin:
        stmt = stmt.where(AgentExecutionHistory.username == username)
    
    result = await db.execute(stmt)
    trace_ids = [row for row in result.scalars().all() if row]

    # 3. 执行批量级联删除
    if trace_ids:
        await db.execute(delete(AgentExecutionTrace).where(AgentExecutionTrace.trace_id.in_(trace_ids)))
    
    delete_history_stmt = delete(AgentExecutionHistory).where(AgentExecutionHistory.conversation_id.in_(payload.conversation_ids))
    if user_info and not is_admin:
        delete_history_stmt = delete_history_stmt.where(AgentExecutionHistory.username == username)
        
    await db.execute(delete_history_stmt)
    
    await db.commit()
    
    return StandardResponse(data={"success": True})

@router.get("/logs/{trace_id}", 
    response_model=StandardResponse[TraceLogResponse],
    summary="获取执行链路",
    description="获取单次对话的详细内部执行步骤 (Trace)。"
)
async def get_trace_logs(
    trace_id: str,
    request: Request,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get detailed execution trace for a chat turn.
    """
    from app.models.audit import AgentExecutionTrace, AgentExecutionHistory
    from sqlalchemy import select
    from app.schemas.agent import AgentExecutionStep, AgentExecutionHistoryResponse
    
    # 1. Fetch High-Level History
    history_res = await db.execute(
        select(AgentExecutionHistory).where(AgentExecutionHistory.trace_id == trace_id)
    )
    history_item = history_res.scalar_one_or_none()
    if not history_item:
        raise HTTPException(status_code=404, detail="History not found")

    # Permission Check: only admin or owner can view trace logs
    is_admin = (user_info or {}).get("role") == "admin"
    if not is_admin and history_item.username != (user_info or {}).get("user_name"):
        raise HTTPException(status_code=403, detail="Permission denied")

    # 2. Fetch Trace Steps
    trace_stmt = select(AgentExecutionTrace).where(AgentExecutionTrace.trace_id == trace_id)
    if history_item and history_item.created_at:
        from datetime import timedelta
        start_bound = history_item.created_at - timedelta(days=1)
        end_bound = history_item.created_at + timedelta(days=1)
        trace_stmt = trace_stmt.where(
            AgentExecutionTrace.created_at >= start_bound,
            AgentExecutionTrace.created_at <= end_bound
        )

    result = await db.execute(
        trace_stmt.order_by(AgentExecutionTrace.step_number)
    )
    rows = result.scalars().all()
    
    steps = []
    for row in rows:
        steps.append(AgentExecutionStep(
            step_number=row.step_number,
            event_type=row.event_type,
            agent_name=row.agent_name,
            model=getattr(row, "model", None),
            temperature=getattr(row, "temperature", None),
            tool_name=row.tool_name,
            tool_input=row.tool_input,
            tool_output=row.tool_output,
            execution_time_ms=row.execution_time_ms,
            status=row.status,
            error_message=row.error_message,
            timestamp=row.created_at
        ))
        
    return StandardResponse(data=TraceLogResponse(
        trace_id=trace_id,
        total_steps=len(steps),
        steps=steps,
        history=AgentExecutionHistoryResponse.from_orm(history_item) if history_item else None
    ))

@router.post("/agents/{agent_id}/chat",
    response_model=StandardResponse[ChatCompletionResponse],
    summary="指定智能体对话",
    description="Restful 风格的快捷接口，直接与指定智能体对话。"
)
async def create_agent_chat(
    agent_id: str, 
    completion_request: ChatCompletionRequest,
    request: Request
):
    """
    RESTful endpoint for agent-specific chat.
    Overrides agent_id in request body if provided.
    """
    completion_request.agent_id = agent_id
    return await create_chat_completion(completion_request, request)

@router.get("/export/data/{trace_id}",
    summary="导出查询数据",
    description="根据 Trace ID 导出最近一次工具调用的结构化数据 (CSV/Excel)。"
)
async def export_trace_data(
    trace_id: str,
    format: str = "xlsx",
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Export tool output data for a given trace.
    """
    # Permission Check: only admin or owner can export
    from app.models.audit import AgentExecutionHistory
    from sqlalchemy import select
    history_res = await db.execute(select(AgentExecutionHistory).where(AgentExecutionHistory.trace_id == trace_id))
    history_item = history_res.scalar_one_or_none()
    if not history_item:
        raise HTTPException(status_code=404, detail="History not found")

    is_admin = (user_info or {}).get("role") == "admin"
    if not is_admin and history_item.username != (user_info or {}).get("user_name"):
        raise HTTPException(status_code=403, detail="Permission denied")

    data = await ExportService.get_trace_data(trace_id)
    if not data:
        raise HTTPException(status_code=404, detail="No exportable data found for this trace.")
    
    filename = f"export_{trace_id}"
    
    if format.lower() == "xlsx":
        content = ExportService.json_to_excel(data)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}.xlsx"}
        )
    else:
        content = ExportService.json_to_csv(data)
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}.csv"}
        )


class UploadResponse(BaseModel):
    url: str = Field(..., description="文件可访问的静态 URL")
    filename: str = Field(..., description="原始文件名")
    size: int = Field(..., description="文件字节大小")
    ext: str = Field(..., description="文件后缀名")

@router.post("/upload",
    response_model=StandardResponse[UploadResponse],
    summary="会话附件上传",
    description="支持会话过程中附件的上传、自动清洗和安全托管（最大限制 20MB，阻断敏感危险后缀）。"
)
async def upload_chat_file(
    file: UploadFile = File(...),
    user_info: Dict[str, Any] = Depends(require_api_key)
):
    """
    Upload a session attachment with security checks and static hosting mapping.
    """
    # 1. 20MB 大小硬上限校验
    MAX_SIZE = 20 * 1024 * 1024
    contents = await file.read(MAX_SIZE + 1)
    if len(contents) > MAX_SIZE:
        raise HTTPException(status_code=400, detail="文件大小超出 20MB 限制")
    
    # 2. 安全危险后缀拦截
    ext = os.path.splitext(file.filename or "")[1].lower()
    forbidden_exts = {".exe", ".bat", ".sh", ".cmd", ".com", ".msi", ".php", ".jsp", ".asp", ".py", ".pl"}
    if ext in forbidden_exts:
        raise HTTPException(status_code=403, detail=f"禁止上传该类型文件: {ext}")
        
    # 3. 文件名清洗与混淆命名防冲突
    clean_name = re.sub(r'[^a-zA-Z0-9._-]', '_', file.filename or "")
    if not clean_name or clean_name.startswith('.'):
        clean_name = f"attachment{ext}"
        
    unique_name = f"{int(time.time())}_{uuid.uuid4().hex[:12]}_{clean_name}"
    
    # 4. 保存到持久卷规划目录 data/uploads
    upload_dir = os.path.join("data", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    
    file_path = os.path.join(upload_dir, unique_name)
    try:
        with open(file_path, "wb") as f:
            f.write(contents)
    except Exception as e:
        logger.error(f"Failed to save uploaded file: {e}")
        raise HTTPException(status_code=500, detail="保存文件失败，请稍后重试。")
        
    return StandardResponse(data=UploadResponse(
        url=f"/static/uploads/{unique_name}",
        filename=file.filename or unique_name,
        size=len(contents),
        ext=ext.replace(".", "")
    ))
