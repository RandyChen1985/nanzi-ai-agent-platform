import json
import os
import time
import asyncio
import secrets
from datetime import datetime, timezone, timedelta
from typing import List, Optional, AsyncGenerator, Dict, Any, Union, Literal
from fastapi import APIRouter, HTTPException, Depends, Query, Request, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.orm import get_db_session
from app.services.ai.agent_service import agent_service
from app.services.ai.context_compaction_log_service import context_compaction_log_service
from app.services.ai.context_usage import estimate_context_usage
from app.services.ai.export_service import ExportService
from app.services.config_service import ConfigService
from app.core.context import set_debug_context
from app.core.dependencies import require_api_key
from app.schemas.response import StandardResponse, ListResponse
from app.schemas.agent import TraceLogResponse, AgentExecutionHistoryListResponse
from app.utils.fs_access import get_user_uploads_dir, open_upload_storage_file
from app.services.permission_service import PermissionService
from app.services.conversation_resource_service import ConversationResourceService
from app.services.resource_scope_normalizer import normalize_resource_scope_for_user
from app.services.ai.business_context import sanitize_injected_context
from app.services.ai.conversation_identity import MissingUserIdentityError, require_user_id
from app.services.ai.memory_service import memory_service
from app.services.ai.reusable_result import (
    build_reusable_result_client_summary,
    normalize_legacy_data_result,
)
from app.services.ai.runtime.agentscope.tool_timeout import load_agent_max_toolcall_timeout
from app.services.ai.error_response_service import sanitize_error_text
from app.utils.env import get_env
import logging


logger = logging.getLogger(__name__)

router = APIRouter()

SSE_RESPONSE_HEADERS = {
    # Prevent browser/proxy transformations from coalescing small narration deltas.
    "Cache-Control": "no-cache, no-transform",
    # Nginx otherwise buffers text/event-stream responses until its buffer fills.
    "X-Accel-Buffering": "no",
}
public_router = APIRouter()


def _duplicate_chat_request_response(claim: Any) -> StreamingResponse:
    """以 SSE 事件告知前端请求已被幂等键接收，避免重复创建 producer。"""
    payload = {
        "type": "duplicate_request",
        "status": "duplicate_request",
        "trace_id": getattr(claim, "trace_id", None),
        "content": "相同发送请求已提交，正在等待原任务完成，请勿重复操作。",
    }

    async def sse_generator() -> AsyncGenerator[str, None]:
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers=SSE_RESPONSE_HEADERS,
    )


def _require_chat_user_id(user_info: Optional[Dict[str, Any]]) -> str:
    """聊天会话相关接口统一使用稳定用户 ID，缺失时返回 401。"""
    try:
        return require_user_id(user_info)
    except MissingUserIdentityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _require_numeric_chat_user_id(user_info: Optional[Dict[str, Any]]) -> int:
    """需要整型主键的聊天接口也必须先通过稳定身份校验。"""
    stable_user_id = _require_chat_user_id(user_info)
    try:
        return int(stable_user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="当前用户身份格式无效") from exc


async def _conversation_belongs_to_user(
    db: AsyncSession,
    user_id: str,
    conversation_id: str,
) -> bool:
    """判断会话是否归属当前用户，用于避免读取他人会话结果。

    新创建的空会话尚无执行历史，但仍可能已被该用户通过「设置活跃会话」声明归属；
    同时该用户在该会话下也可能已有 Redis 消息历史。因此只要三者任一成立即视为归属，
    避免空会话被误判为不存在而返回 404。读取侧本身按“用户+会话”Redis 前缀隔离，
    因此该判定不会造成跨用户数据泄露。
    """
    from sqlalchemy import select
    from app.models.audit import AgentExecutionHistory

    statement = (
        select(AgentExecutionHistory.id)
        .where(
            AgentExecutionHistory.conversation_id == conversation_id,
            AgentExecutionHistory.user_id == user_id,
        )
        .limit(1)
    )
    result = await db.execute(statement)
    if result.scalar_one_or_none() is not None:
        return True

    # 空会话回退：该用户该会话已有 Redis 历史，或该会话正是该用户的活跃会话。
    try:
        if await memory_service.history_exists(user_id, conversation_id):
            return True
        active = await memory_service.get_active_conversation(user_id)
        if active and str(active) == conversation_id:
            return True
    except Exception:
        # Redis 不可用时不做额外判定，保持原有 DB 判定结果（即未命中则拒绝）。
        pass
    return False


@public_router.get("/generated-files/{artifact_id}")
async def download_generated_file(artifact_id: str, token: str):
    from app.services.ai.tools.generated_file_service import resolve_for_download, resolve_workspace_artifact

    # DB 优先：工作区产物先经 ai_artifacts 校验归属（storage_path 在工作区内 + token 匹配）
    artifact = await resolve_workspace_artifact(artifact_id, token)
    # manifest 回退：兼容旧版 publish() 生成的文件链接
    if artifact is None:
        artifact = resolve_for_download(artifact_id, token)
    if artifact is None:
        raise HTTPException(status_code=404, detail="文件不存在或已过期")
    return FileResponse(
        artifact.path,
        media_type=artifact.mime_type,
        filename=artifact.filename,
    )


class ArtifactListItem(BaseModel):
    id: str
    filename: str
    artifact_type: str
    mime_type: Optional[str] = None
    size: int
    conversation_id: Optional[str] = None
    trace_id: Optional[str] = None
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    download_url: str


@router.get(
    "/artifacts",
    response_model=StandardResponse[ListResponse[ArtifactListItem]],
    summary="我的 AI 产物列表",
    description="列出当前用户在 ai_artifacts 中登记的 AI 生成/导出产物（Word/Excel/导出等）。"
    "由于 ai_artifacts 只存 token 哈希，为每条记录新签发一个下载 token 并回写哈希与过期时间，"
    "保证返回的 download_url 可用。",
)
async def list_artifacts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    artifact_type: Optional[str] = Query(None, description="按产物类型过滤：word / excel / export"),
    conversation_id: Optional[str] = Query(None, description="按会话过滤产物"),
    trace_id: Optional[str] = Query(None, description="按 trace_id 过滤（用于查看某条 AI 消息产生的产物）"),
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    from app.models.artifact import AiArtifact
    from app.services.ai.tools.generated_file_service import (
        DEFAULT_TTL,
        _token_hash,
        build_download_url,
        get_download_url_prefix,
    )
    from sqlalchemy import func, select

    user_id = _require_numeric_chat_user_id(user_info)

    filters = [AiArtifact.owner_user_id == user_id]
    if artifact_type:
        filters.append(AiArtifact.artifact_type == artifact_type)
    if conversation_id:
        filters.append(AiArtifact.conversation_id == conversation_id)
    if trace_id:
        filters.append(AiArtifact.trace_id == trace_id)
    total = await db.scalar(
        select(func.count())
        .select_from(AiArtifact)
        .where(*filters)
    )
    total = int(total or 0)

    stmt = (
        select(AiArtifact)
        .where(*filters)
        .order_by(AiArtifact.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.scalars(stmt)).all()

    now = datetime.now(timezone.utc)
    public_base_url = await get_download_url_prefix()
    items: List[ArtifactListItem] = []
    # 数据库只存 token 哈希，无法还原旧 token。这里对新列出的每条记录新签发下载 token
    # 并回写 token_hash/expires_at，保证前端拿到的 download_url 可直接下载。
    for row in rows:
        token = secrets.token_urlsafe(32)
        new_expires_at = now + DEFAULT_TTL
        row.token_hash = _token_hash(token)
        row.expires_at = new_expires_at
        items.append(
            ArtifactListItem(
                id=row.id,
                filename=row.filename,
                artifact_type=row.artifact_type,
                mime_type=row.mime_type,
                size=int(row.size or 0),
                conversation_id=row.conversation_id,
                trace_id=row.trace_id,
                created_at=row.created_at,
                expires_at=new_expires_at,
                download_url=build_download_url(
                    row.id,
                    token,
                    public_base_url=public_base_url,
                ),
            )
        )
    await db.commit()

    return StandardResponse(
        data=ListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )
    )


class ArtifactCountsByTrace(BaseModel):
    """某会话内各 AI 消息 trace_id → 产物数量 的轻量映射（用于按钮显示与角标）。"""
    counts: Dict[str, int]


@router.get(
    "/artifacts/counts",
    response_model=StandardResponse[ArtifactCountsByTrace],
    summary="会话内产物数量统计",
    description="按 conversation_id 一次返回当前用户该会话内各 trace_id 的产物数量，"
    "仅统计 trace_id 非空的产物；不含文件元信息、不签发下载 token，供前端判断某条 AI 消息是否真有产物及显示数量角标。",
)
async def count_artifacts_by_trace(
    conversation_id: str = Query(..., description="会话 id"),
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    from app.models.artifact import AiArtifact
    from sqlalchemy import func, select

    user_id = _require_numeric_chat_user_id(user_info)

    rows = (
        await db.execute(
            select(AiArtifact.trace_id, func.count())
            .where(
                AiArtifact.owner_user_id == user_id,
                AiArtifact.conversation_id == conversation_id,
                AiArtifact.trace_id.isnot(None),
            )
            .group_by(AiArtifact.trace_id)
        )
    ).all()

    counts: Dict[str, int] = {}
    for trace_id, cnt in rows:
        if trace_id:
            counts[trace_id] = int(cnt)
    return StandardResponse(data=ArtifactCountsByTrace(counts=counts))


class ReusableResultListItem(BaseModel):
    result_id: str
    trace_id: Optional[str] = None
    result_type: str
    origin_type: str
    origin_name: str
    source_type: str
    status: str
    text_excerpt: str
    structured_preview: Optional[Dict[str, Any]] = None
    created_at: Optional[Any] = None
    expires_at: Optional[Any] = None
    is_current: bool = False


def _should_enrich_history_reusable_metadata(user_info: Dict[str, Any]) -> bool:
    """仅为普通用户读取其自身 Redis 会话，避免管理员串用 Redis 身份。"""
    return (user_info or {}).get("role") != "admin"


def _history_reusable_metadata_window(page: int, page_size: int) -> Dict[str, int]:
    """按 DB 历史页换算 Redis 的 user/assistant 双消息读取窗口。"""
    safe_page = max(1, int(page or 1))
    safe_page_size = max(1, int(page_size or 20))
    window = safe_page_size * 2
    return {"limit": window, "offset": (safe_page - 1) * window}


@router.get(
    "/reusable-results",
    response_model=StandardResponse[ListResponse[ReusableResultListItem]],
    summary="当前会话可复用结果列表",
    description="列出当前用户当前会话中仍可复用的结果摘要，不返回完整 payload、工具参数或凭证。",
)
async def list_reusable_results(
    conversation_id: str = Query(..., min_length=1, description="会话 id"),
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = _require_chat_user_id(user_info)
    if not await _conversation_belongs_to_user(db, user_id, conversation_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        current, stack, legacy = await asyncio.gather(
            memory_service.get_reusable_result(user_id, conversation_id),
            memory_service.get_reusable_result_stack(user_id, conversation_id),
            memory_service.get_last_data_result(user_id, conversation_id),
        )
    except Exception as exc:
        logger.warning("[ChatAPI] Failed to load reusable result list: %s", exc)
        return StandardResponse(
            data=ListResponse(items=[], total=0, page=1, page_size=10)
        )

    items: List[ReusableResultListItem] = []
    seen: set[str] = set()
    candidates = [(current, True)] + [
        (item, False) for item in reversed(stack or [])
    ]
    for payload, is_current in candidates:
        summary = build_reusable_result_client_summary(
            payload,
            is_current=is_current,
        )
        result_id = str(summary.get("result_id") or "") if summary else ""
        if not summary or not result_id or result_id in seen:
            continue
        seen.add(result_id)
        items.append(ReusableResultListItem(**summary))
        if len(items) >= 10:
            break
    if not items:
        summary = build_reusable_result_client_summary(
            normalize_legacy_data_result(legacy),
            is_current=True,
        )
        result_id = str(summary.get("result_id") or "") if summary else ""
        if summary and result_id:
            items.append(ReusableResultListItem(**summary))

    return StandardResponse(
        data=ListResponse(
            items=items,
            total=len(items),
            page=1,
            page_size=10,
        )
    )

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


class ChatBIQuickContext(BaseModel):
    """快捷按钮的内部路由提示；不进入用户消息和模型可见正文。"""

    source: Literal["chatbi_result"]
    result_id: Optional[str] = Field(default=None, max_length=128)
    requires_fresh_data: bool = True


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
    metadata_dataset_ids: Optional[List[str]] = Field(
        default=None,
        description="本轮结构化指定的 MetaDataset ID 列表（优先于会话挂载；仍需通过权限校验）",
    )
    debug_options: Optional[Dict[str, Any]] = None
    permission_options: Optional[Dict[str, Any]] = None
    grounding_action: Optional[Dict[str, Any]] = Field(
        default=None,
        description="事实取证卡片触发的结构化动作，仅影响当前轮",
    )
    client_request_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="客户端一次发送意图的幂等 ID；网络重试应复用同一 ID",
    )
    reusable_result_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="用户明确选择用于下一轮分析的会话结果 ID",
    )
    quick_context: Optional[ChatBIQuickContext] = Field(
        default=None,
        description="快捷结果追问的内部路由元数据，不展示给用户",
    )


def validate_chat_completion_messages(
    messages: List[ChatMessage],
    conversation_id: Optional[str] = None,
) -> None:
    """校验完成请求的当前轮边界，避免把历史 assistant 消息当成本轮问题。"""
    if not messages:
        raise HTTPException(status_code=400, detail="Messages list cannot be empty")

    latest = messages[-1]
    if latest.role != "user":
        raise HTTPException(
            status_code=400,
            detail="最后一条消息必须是当前用户消息",
        )
    if not latest.content.strip():
        raise HTTPException(status_code=400, detail="当前用户消息不能为空")


class ConversationResourceScopeRequest(BaseModel):
    project_name: str = ""
    datasets: List[Dict[str, Any]] = Field(default_factory=list)
    knowledge_bases: List[Dict[str, Any]] = Field(default_factory=list)
    skills: List[Dict[str, Any]] = Field(default_factory=list)
    mcp_tools: List[Dict[str, Any]] = Field(default_factory=list)


async def _normalize_conversation_resource_scope(
    db: AsyncSession,
    user_info: Dict[str, Any],
    raw_scope: Dict[str, Any],
) -> Dict[str, Any]:
    """把客户端提交的资源 token 收敛为当前用户可见目录中的可信快照。"""
    return await normalize_resource_scope_for_user(db, user_info, raw_scope)


class ChatCompletionResponse(BaseModel):
    content: str
    intent: str
    confidence: Optional[float] = None
    reasoning: Optional[str] = None
    model: Optional[str] = None
    trace_id: Optional[str] = None


class ChatCancelRequest(BaseModel):
    conversation_id: str = Field(..., description="要取消本轮生成并释放运行锁的会话 ID")
    trace_id: Optional[str] = Field(default=None, description="可选，用于日志关联")


class ChatCancelResponse(BaseModel):
    success: bool
    lane_released: bool
    session_locks_released: int = 0
    run_cancelled: bool = False
    canvas_stopped: int = 0


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
    is_fallback: bool = Field(..., description="标记当前是否是降级到兜底模板的数据")
    has_datasets: bool = Field(default=True, description="当前用户是否有可用数据集")
    from_cache: bool = Field(default=False, description="本次结果是否来自缓存")
    llm_generation_failed: bool = Field(
        default=False,
        description="本次生成是否因 LLM 调用失败而降级到兜底模板",
    )
    llm_error_message: Optional[str] = Field(
        default=None,
        description="LLM 生成失败时的简要错误信息（供前端提示）",
    )


class DatasetMenuClickRequest(BaseModel):
    query: str = Field(..., description="用户点击的完整 quick 问题")
    label: Optional[str] = Field(default=None, description="按钮短标签")
    group_id: Optional[str] = Field(default=None, description="业务场景卡片 ID")
    dataset_menu_hash: Optional[str] = Field(
        default=None,
        description="已废弃，仅保留兼容；点击统计按 user_id 存储",
    )


class DatasetGroupRefreshRequest(BaseModel):
    group_title: str = Field(..., description="业务场景卡片标题")
    tables: List[str] = Field(..., description="关联的数据表术语列表")
    dataset_menu_hash: Optional[str] = Field(default=None, description="当前数据目录 hash，用于短期去重隔离")
    group_id: Optional[str] = Field(default=None, description="业务场景卡片 ID，用于短期去重隔离")
    exclude_questions: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="当前页面已展示的问题列表，后端刷新时应避免重复或相似",
    )
    purpose: str = Field(
        default="questions",
        description="刷新目标：questions=推荐问题，followups=继续追问",
    )


class DatasetTableColumnInfo(BaseModel):
    name: str = Field(..., description="物理字段名")
    term: str = Field(default="", description="业务字段名")
    type: str = Field(default="", description="字段类型")
    description: str = Field(default="", description="字段描述")


class DatasetTableRecommendRequest(BaseModel):
    table: str = Field(..., description="数据表业务术语名")
    physical_table_name: Optional[str] = Field(default=None, description="物理表名")
    dataset_name: Optional[str] = Field(default=None, description="所属数据集名称")
    columns: List[DatasetTableColumnInfo] = Field(default_factory=list, description="门户已下发的字段定义")


class DatasetGroupQuestion(BaseModel):
    label: str = Field(..., description="问题短标签")
    query: str = Field(..., description="点击触发的完整查询指令")
    type: str = Field(default="dynamic", description="类型，固定为 dynamic")


class DatasetGroupRefreshResponse(BaseModel):
    questions: List[DatasetGroupQuestion] = Field(..., description="重新生成的推荐问题列表")
    refresh_disabled_reason: Optional[str] = Field(
        default=None,
        description="未返回新问题时的可读原因，例如短期内已无更多不同问题",
    )



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
    description="基于当前用户授权的 {dataset_menu} 目录，由 LLM 生成我的数据门户与 quick 追问建议，供 /dataset_portal 系统指令使用。",
)
async def get_dataset_menu_navigation(
    refresh: bool = False,
    db: AsyncSession = Depends(get_db_session),
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    from app.services.dataset_navigation_service import DatasetNavigationService

    user_id = _require_numeric_chat_user_id(user_info)
    is_admin = user_info.get("role") == "admin"
    payload = await DatasetNavigationService.build_navigation_for_user(
        db,
        user_id=user_id,
        is_admin=is_admin,
        force_refresh=refresh,
    )
    return StandardResponse(data=DatasetNavigationResponse(**payload))


@router.post(
    "/cancel",
    response_model=StandardResponse[ChatCancelResponse],
    summary="取消当前会话运行并释放锁",
    description="用户在前端终止生成时调用：取消本轮生成任务、停止同会话代码画布进程，并释放会话运行锁。",
)
async def cancel_chat_completion(
    request: ChatCancelRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    from app.services.ai.runtime.conversation_run_cancel import cancel_conversation_run

    conversation_id = (request.conversation_id or "").strip()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id is required")

    lane_user_id = _require_chat_user_id(user_info)
    result = await cancel_conversation_run(
        user_id=lane_user_id,
        conversation_id=conversation_id,
        trace_id=request.trace_id,
    )
    return StandardResponse(data=ChatCancelResponse(**result))


@router.post(
    "/dataset-menu/click",
    response_model=StandardResponse[Dict[str, bool]],
    summary="记录我的数据门户点击偏好",
    description="记录用户在 /dataset_portal 中点击的 quick 问题，用于同一数据目录下的个性化排序。",
)
async def record_dataset_menu_question_click(
    request: DatasetMenuClickRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    from app.services.dataset_navigation_service import DatasetNavigationService

    user_id = _require_numeric_chat_user_id(user_info)
    is_admin = user_info.get("role") == "admin"
    await DatasetNavigationService.record_question_click(
        user_id=user_id,
        is_admin=is_admin,
        query=request.query,
        label=request.label,
        group_id=request.group_id,
    )
    return StandardResponse(data={"success": True})


@router.post(
    "/dataset-menu/click/clear",
    response_model=StandardResponse[Dict[str, bool]],
    summary="从我的常问中移除问题",
    description="清除用户在数据门户中对某条 quick 问题的点击记录，用于「我常问」个性化列表的删除清理。",
)
async def clear_dataset_menu_question_click(
    request: DatasetMenuClickRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    from app.services.dataset_navigation_service import DatasetNavigationService

    user_id = _require_numeric_chat_user_id(user_info)
    is_admin = user_info.get("role") == "admin"
    cleared = await DatasetNavigationService.clear_question_click(
        user_id=user_id,
        is_admin=is_admin,
        query=request.query,
    )
    return StandardResponse(data={"success": cleared})


@router.post(
    "/dataset-menu/refresh-group-questions",
    response_model=StandardResponse[DatasetGroupRefreshResponse],
    summary="局部刷新当前数据门户场景卡片下的推荐问题",
    description="针对单个数据门户场景卡片，调用大模型在线实时生成 3 个推荐问题并返回。",
)
async def refresh_group_questions(
    request: DatasetGroupRefreshRequest,
    db: AsyncSession = Depends(get_db_session),
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    from app.services.dataset_navigation_service import DatasetNavigationService

    purpose = str(request.purpose or "questions").strip().lower()
    user_id = _require_numeric_chat_user_id(user_info)
    is_admin = user_info.get("role") == "admin"
    if purpose == "followups":
        questions = await DatasetNavigationService.refresh_group_followups(
            db,
            group_title=request.group_title,
            tables=request.tables,
            user_id=user_id,
            is_admin=is_admin,
            group_id=request.group_id or "",
            exclude_questions=request.exclude_questions,
        )
    else:
        questions = await DatasetNavigationService.refresh_group_questions(
            db,
            group_title=request.group_title,
            tables=request.tables,
            user_id=user_id,
            is_admin=is_admin,
            group_id=request.group_id or "",
            exclude_questions=request.exclude_questions,
        )
    reason = None if questions else "暂无更多不同问题，稍后再试"
    return StandardResponse(
        data=DatasetGroupRefreshResponse(
            questions=questions,
            refresh_disabled_reason=reason,
        )
    )


@router.post(
    "/dataset-menu/recommend-table-questions",
    response_model=StandardResponse[DatasetGroupRefreshResponse],
    summary="基于单表字段定义生成推荐提问",
    description="数据门户表级「推荐提问」专用接口：仅依据字段元数据生成 quick 按钮，不触发 ChatBI 查数。",
)
async def recommend_table_questions(
    request: DatasetTableRecommendRequest,
    db: AsyncSession = Depends(get_db_session),
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    from app.services.dataset_navigation_service import DatasetNavigationService

    questions = await DatasetNavigationService.recommend_table_questions(
        db,
        table=request.table,
        physical_table_name=request.physical_table_name or "",
        dataset_name=request.dataset_name or "",
        columns=[col.model_dump() for col in request.columns],
    )
    return StandardResponse(data=DatasetGroupRefreshResponse(questions=questions))


class FileAttachment(BaseModel):
    """会话消息中的附件条目。字段与前端 `ChatFile` 保持一致，
    同时 `extra="allow"` 保留如 skillMeta/memoryMeta 等可选附带元数据。"""
    model_config = {"extra": "allow"}

    type: Optional[str] = None
    url: Optional[str] = None
    filename: Optional[str] = None
    size: Optional[int] = None
    ext: Optional[str] = None


class ProcessTimelineItem(BaseModel):
    """流程时间线条目。`process_timeline` 高度异构（log/text/todo 三种 kind），
    这里仅收敛最常见的跨 kind 字段，`extra="allow"` 保留各自专属字段
    （如 log 的 execution_time_ms/subagent、text 的 sourceId/sourceLabel 等）。"""
    model_config = {"extra": "allow"}

    kind: Optional[str] = None
    # log kind 的 id 可以是 string 或 number（前端契约 `string | number`）
    id: Optional[Union[str, int]] = None
    title: Optional[str] = None
    status: Optional[str] = None
    textKind: Optional[str] = None
    content: Optional[str] = None
    details: Optional[str] = None
    isExpanded: Optional[bool] = False
    pending: Optional[bool] = False
    children: Optional[List["ProcessTimelineItem"]] = None
    # todo kind 专属字段（content/status 二元的待办列表）+ 计数
    todos: Optional[List[Dict[str, Any]]] = None
    counts: Optional[Dict[str, Any]] = None


class ConversationMessage(BaseModel):
    """结构化收敛的会话消息体。涵盖 user/assistant 各类已知字段，
    同时 `extra="allow"` 容忍历史遗留的未声明字段，避免解析失败。"""
    model_config = {"extra": "allow"}

    role: Optional[str] = None
    content: Optional[str] = None
    timestamp: Optional[datetime] = None
    trace_id: Optional[str] = None

    # token 用量 / 结构化输出标记
    prompt_tokens: Optional[int] = 0
    completion_tokens: Optional[int] = 0
    total_tokens: Optional[int] = 0
    has_data_output: Optional[bool] = False

    # assistant 消息专属元数据
    agent_name: Optional[str] = None
    agent_type: Optional[str] = None
    agent_display_name: Optional[str] = None
    reasoning_content: Optional[str] = None
    process_timeline: Optional[List[ProcessTimelineItem]] = None
    feedback: Optional[Any] = None
    files: Optional[List[FileAttachment]] = None


class ConversationHistoryResponse(BaseModel):

    conversation_id: str = Field(..., description="会话ID")
    messages: List[ConversationMessage] = Field(..., description="消息列表")


class ConversationRunStatusResponse(BaseModel):
    active: bool = False
    trace_id: Optional[str] = None
    ttl_seconds: Optional[int] = None


@router.get(
    "/conversation/{conversation_id}/run-status",
    response_model=StandardResponse[ConversationRunStatusResponse],
    summary="获取会话当前运行状态",
    description="读取当前用户会话的 Redis 运行锁状态，用于断线恢复期间阻止重复发送。",
)
async def get_conversation_run_status(
    conversation_id: str,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    from app.services.ai.runtime.session_run_lane import conversation_run_lane

    user_id = _require_chat_user_id(user_info)
    status = await conversation_run_lane.get_status(
        user_id=user_id,
        conversation_id=conversation_id,
    )
    return StandardResponse(data=ConversationRunStatusResponse(**status))

@router.get("/conversation/{conversation_id}",
    response_model=StandardResponse[ConversationHistoryResponse],
    summary="获取会话历史",
    description="从服务端内存 (Redis) 获取指定会话的历史记录，若缓存失效则自动从数据库持久化记录中回退恢复。"
)
def _merge_latest_audit_assistant(
    history: list[dict[str, Any]],
    audit_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """仅补齐 Redis 尾部 user 对应的缺失 assistant，避免重复整段历史。"""
    if not history or history[-1].get("role") != "user":
        return history
    latest_content = str(history[-1].get("content") or "")
    existing_trace_ids = {
        str(message.get("trace_id"))
        for message in history
        if message.get("trace_id")
    }
    for item in audit_messages:
        assistant = item.get("assistant")
        if (
            str(item.get("query") or "") == latest_content
            and isinstance(assistant, dict)
            and assistant.get("trace_id")
            and str(assistant["trace_id"]) not in existing_trace_ids
        ):
            return [*history, assistant]
    return history


async def get_conversation_history(
    conversation_id: str,
    limit: Optional[int] = 50,
    offset: int = 0,
    request: Request = None,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Retrieve conversation history from server-side memory (Redis),
    with automatic database audit log recovery fallback.
    """
    user_id = _require_chat_user_id(user_info)
    
    from app.services.ai.memory_service import memory_service
    
    history = await memory_service.get_history(user_id, conversation_id, limit=limit, offset=offset)

    # Redis 可能已写入当前 user，但 assistant 仍在取消/收尾过程中；此时不能因为
    # history 非空就跳过数据库恢复，否则刷新会话会停在最后一条 user 消息。
    if history and history[-1].get("role") == "user":
        from app.models.audit import AgentExecutionHistory
        from sqlalchemy import select

        latest_user_content = str(history[-1].get("content") or "")
        stmt = (
            select(AgentExecutionHistory)
            .where(
                AgentExecutionHistory.conversation_id == conversation_id,
                AgentExecutionHistory.user_id == user_id,
                AgentExecutionHistory.query == latest_user_content,
            )
            .order_by(AgentExecutionHistory.created_at.desc())
            .limit(5)
        )
        db_res = await db.execute(stmt)
        audit_messages = []
        for record in db_res.scalars().all():
            if not (record.summary or record.process_timeline or record.reasoning_content):
                continue
            audit_messages.append({
                "query": record.query,
                "assistant": {
                    "role": "assistant",
                    "content": record.summary or "",
                    "reasoning_content": record.reasoning_content,
                    "process_timeline": record.process_timeline,
                    "timestamp": record.created_at.isoformat() if record.created_at else None,
                    "trace_id": record.trace_id,
                    "status": record.status,
                    "has_data_output": bool(getattr(record, "has_data_output", 0) or False),
                },
            })
        history = _merge_latest_audit_assistant(history, audit_messages)

    # Enrich Redis-backed assistant messages so the frontend can keep the same
    # message actions available after a page reload. agent_type / agent_display_name
    # are now persisted at write time; we only fall back to a full agent lookup
    # for legacy entries that lack them.
    agent_type_by_name: Dict[str, str] = {}
    needs_agent_lookup = False
    for message in history[:50]:
        if message.get("role") == "assistant" and message.get("agent_name") and not message.get("agent_type"):
            needs_agent_lookup = True
            break
    if needs_agent_lookup:
        try:
            from app.services.ai.agent_manager import AgentManagerService
            all_agents = await AgentManagerService.list_agents(db, user=user_info)
            for agent in all_agents:
                agent_type = getattr(agent, "agent_type", None) or "GENERAL"
                agent_type = getattr(agent_type, "value", agent_type)
                agent_type_by_name[str(agent.name)] = str(agent_type)
        except Exception:
            pass

    for message in history:
        if message.get("role") == "assistant" and message.get("agent_name") and not message.get("agent_type"):
            agent_type = agent_type_by_name.get(str(message["agent_name"]))
            if agent_type:
                message["agent_type"] = agent_type
    
    # Fallback to DB audit logs if Redis cache is empty/expired
    if not history:
        from app.models.audit import AgentExecutionHistory
        from sqlalchemy import select
        
        stmt = select(AgentExecutionHistory).where(
            AgentExecutionHistory.conversation_id == conversation_id,
            AgentExecutionHistory.user_id == user_id,
        )
            
        stmt = stmt.order_by(AgentExecutionHistory.created_at.asc())
        
        db_res = await db.execute(stmt)
        records = db_res.scalars().all()
        
        # Dynamically fetch active agents map for rich display names
        agent_map = {}
        agent_type_by_id = {}
        try:
            from app.services.ai.agent_manager import AgentManagerService
            all_agents = await AgentManagerService.list_agents(db, user=user_info)
            agent_map = {str(a.id): (a.name, a.display_name) for a in all_agents}
            for a in all_agents:
                agent_type = getattr(a, "agent_type", None) or "GENERAL"
                agent_type = getattr(agent_type, "value", agent_type)
                agent_type_by_id[str(a.id)] = str(agent_type)
        except Exception:
            pass
            
        fallback_history = []
        for r in records:
            agent_name = None
            agent_display_name = None
            if r.agent_id in agent_map:
                agent_name = agent_map[r.agent_id][0]
                agent_display_name = agent_map[r.agent_id][1]
                
            # Each record has a query (user message) and a summary (assistant reply)
            if r.query:
                fallback_history.append({
                    "role": "user",
                    "content": r.query,
                    "timestamp": r.created_at.isoformat() if r.created_at else None
                })
            if r.summary or r.process_timeline or r.reasoning_content:
                fallback_history.append({
                    "role": "assistant",
                    "content": r.summary or "",
                    "reasoning_content": r.reasoning_content,
                    "process_timeline": r.process_timeline,
                    "timestamp": r.created_at.isoformat() if r.created_at else None,
                    "agent_name": agent_name,
                    "agent_display_name": agent_display_name,
                    "agent_type": agent_type_by_id.get(str(r.agent_id)) or "GENERAL",
                    "trace_id": r.trace_id,
                    "feedback": r.feedback,
                    "prompt_tokens": int(r.prompt_tokens or 0),
                    "completion_tokens": int(r.completion_tokens or 0),
                    "total_tokens": int(r.total_tokens or 0),
                    "has_data_output": bool(getattr(r, "has_data_output", 0) or False)
                })
        history = fallback_history
        
    return StandardResponse(data=ConversationHistoryResponse(
        conversation_id=conversation_id,
        messages=history
    ))


@router.get(
    "/conversation/{conversation_id}/context-usage",
    response_model=StandardResponse[Dict[str, Any]],
    summary="获取会话上下文使用情况",
    description="按 session_status 的同一估算口径返回当前会话上下文使用量，不触发模型调用。",
)
async def get_conversation_context_usage(
    conversation_id: str,
    model_id: Optional[str] = Query(None, description="当前输入框选中的模型 ID"),
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = _require_chat_user_id(user_info)

    runtime_model_info: Dict[str, Any] = {}
    if model_id and db is not None:
        from sqlalchemy import select

        from app.models.ai_model import AIModel

        result = await db.execute(
            select(AIModel).where(
                AIModel.model_id == model_id,
                AIModel.is_active.is_(True),
            )
        )
        model = result.scalar_one_or_none()
        if model is not None:
            runtime_model_info = {
                "source": "runtime_override",
                "effective_model_id": model.model_id,
                "context_size": model.context_size,
                "max_output_tokens": model.max_output_tokens,
            }

    usage = await estimate_context_usage(
        user_id=user_id,
        conversation_id=conversation_id,
        runtime_model_info=runtime_model_info,
        empty_history_is_zero=True,
    )
    try:
        from app.services.config_service import resolve_effective_sandbox_policy

        sandbox_policy = resolve_effective_sandbox_policy(
            await ConfigService.get("sandbox_policy", "local"),
        ).strip().lower()
    except Exception as exc:
        logger.warning("读取 sandbox_policy 失败: %s", exc)
        sandbox_policy = None
    return StandardResponse(
        data={
            **usage,
            "sandbox_policy": sandbox_policy,
            "sandbox_runtime_env": get_env(),
        }
    )


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

    user_id = _require_chat_user_id(user_info)

    result = await SessionSummaryService.finalize_session(user_id, conversation_id)
    return StandardResponse(
        data=ConversationFinalizeResponse(
            finalized=bool(result.get("finalized")),
            conversation_id=result.get("conversation_id") or conversation_id,
            reason=result.get("reason"),
        )
    )


class ContextBreakdown(BaseModel):
    """一次模型请求的上下文组成估算。"""

    system_prompt_tokens: int = Field(0, description="系统提示词估算 Token")
    tools_tokens: int = Field(0, description="工具 schema 估算 Token")
    conversation_tokens: int = Field(0, description="对话消息估算 Token")
    total_tokens: int = Field(0, description="三项合计的估算 Token")
    estimated: bool = Field(True, description="是否为平台估算值")
    source: str = Field("agentscope_count_tokens", description="Token 估算来源")


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
    context_size: Optional[int] = Field(None, description="模型物理上下文窗口大小(Token)")
    context_budget: Optional[int] = Field(None, description="平台侧上下文 Token 预算上限(agent_context_max_tokens，实际截断水位线)")
    physical_window: Optional[int] = Field(None, description="最终模型物理上下文窗口大小(Token)")
    history_budget: Optional[int] = Field(None, description="历史消息可用 Token 预算")
    completion_reserve_tokens: Optional[int] = Field(None, description="单次输出预留 Token 上限")
    request_input_budget: Optional[int] = Field(None, description="扣除输出后的单次请求输入预算")
    prompt_overhead_reservation_tokens: Optional[int] = Field(None, description="系统提示和工具开销预留 Token")
    effective_completion_limit: Optional[int] = Field(None, description="边界请求实际采用的输出上限")
    overhead_reservation_tokens: Optional[int] = Field(None, description="历史之外的总预留 Token")
    message_roles: Optional[Dict[str, int]] = Field(default_factory=dict, description="各角色消息条数统计")
    contains_compaction: bool = Field(False, description="是否包含早前对话的裁剪摘录")
    context_breakdown: Optional[ContextBreakdown] = Field(
        None,
        description="系统提示词、工具 schema 和对话消息的 Token 组成估算",
    )


class ModelCallStatsResponse(BaseModel):
    stats: List[ModelCallStatDetail] = Field(..., description="大模型调用指标列表")


class ContextCompactionRecord(BaseModel):
    event_id: str
    conversation_id: str
    event_type: str
    source: str
    stage: str
    occurred_at: str
    title: str = "上下文已压缩"
    status: str = "success"
    preview: str = ""
    trace_id: Optional[str] = None
    agent_name: Optional[str] = None
    model_name: Optional[str] = None
    dropped: Optional[int] = None
    kept: Optional[int] = None
    origin: Optional[str] = None
    token_used: Optional[int] = None
    token_budget: Optional[int] = None
    history_budget: Optional[int] = None
    physical_window: Optional[int] = None
    completion_reserve_tokens: Optional[int] = None
    request_input_budget: Optional[int] = None
    overhead_reservation_tokens: Optional[int] = None
    prompt_overhead_reservation_tokens: Optional[int] = None
    summary_chars: Optional[int] = None
    saved_tokens: Optional[int] = None
    saved_percent: Optional[float] = None


class ContextCompactionsResponse(BaseModel):
    records: List[ContextCompactionRecord] = Field(default_factory=list)
    count: int = 0
    retention_seconds: int = context_compaction_log_service.TTL_SECONDS


class ManualContextCompactionRequest(BaseModel):
    retain_ratio: float = Field(0.5, description="保留最近历史比例，仅支持 0.25、0.5、0.75")
    mode: Literal["fast", "smart"] = Field("fast", description="快速压缩或调用模型进行智能摘要")


@router.get(
    "/conversation/{conversation_id}/context_compactions",
    response_model=StandardResponse[ContextCompactionsResponse],
    summary="获取会话上下文压缩时间线",
    description="读取当前用户当前会话最近七天的上下文压缩结构化记录。",
)
async def get_context_compactions(
    conversation_id: str,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    raw_user_id = _require_chat_user_id(user_info)

    raw_records = await context_compaction_log_service.list_records(
        raw_user_id, conversation_id
    )
    records: List[ContextCompactionRecord] = []
    for raw_record in raw_records:
        try:
            records.append(ContextCompactionRecord.model_validate(raw_record))
        except Exception:
            logger.warning(
                "Skip invalid context compaction API record conversation=%s",
                conversation_id,
            )

    return StandardResponse(
        data=ContextCompactionsResponse(
            records=records,
            count=len(records),
        )
    )


@router.post(
    "/conversation/{conversation_id}/context_compactions/manual",
    response_model=StandardResponse[Dict[str, Any]],
    summary="手动压缩会话上下文",
    description="由当前用户手动触发一次上下文压缩；保留原始历史，仅更新可供后续请求使用的摘要。",
)
async def manual_context_compaction(
    conversation_id: str,
    request: ManualContextCompactionRequest = ManualContextCompactionRequest(),
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    user_id = _require_chat_user_id(user_info)
    try:
        result = await agent_service.manual_compact_conversation(
            user_id, conversation_id, retain_ratio=request.retain_ratio, mode=request.mode
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StandardResponse(data=result)


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
    uid = _require_chat_user_id(user_info)

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
    chat_user_id = _require_chat_user_id(user_info)
    # Initialize Request Context for Debugging
    effective_debug_options = dict(completion_request.debug_options or {})
    # 资源范围只能由服务端会话快照决定，禁止客户端通过 debug_options 注入范围。
    effective_debug_options.pop("resource_scope", None)
    if "injected_context" in effective_debug_options:
        effective_debug_options["injected_context"] = sanitize_injected_context(
            effective_debug_options["injected_context"]
        )
    if completion_request.grounding_action:
        effective_debug_options["grounding_action"] = dict(
            completion_request.grounding_action
        )
    if effective_debug_options:
        set_debug_context(effective_debug_options)
    else:
        set_debug_context({}) # Clear/Default

    validate_chat_completion_messages(
        completion_request.messages,
        completion_request.conversation_id,
    )

    # 会话资源范围以服务端 Redis 为准，客户端只用于立即刷新 UI，不能伪造范围。
    conversation_scope = {
        "project_name": "",
        "datasets": [],
        "knowledge_bases": [],
        "skills": [],
        "mcp_tools": [],
    }
    if completion_request.conversation_id:
        conversation_scope = await ConversationResourceService.get(
            chat_user_id,
            completion_request.conversation_id,
        )
    if any(
        conversation_scope.get(key)
        for key in ("datasets", "knowledge_bases", "skills", "mcp_tools")
    ):
        effective_debug_options["resource_scope"] = conversation_scope
    scoped_kb_ids = [str(item.get("id")) for item in conversation_scope.get("knowledge_bases", []) if item.get("id")]
    effective_knowledge_dataset_ids = scoped_kb_ids or completion_request.knowledge_dataset_ids
    from app.services.ai.metadata_dataset_scope import (
        merge_request_metadata_dataset_ids,
        resolve_effective_metadata_dataset_ids,
    )

    history = [msg.model_dump() for msg in completion_request.messages]

    session_dataset_ids = [
        str(item.get("id"))
        for item in conversation_scope.get("datasets", []) or []
        if item.get("id")
    ]
    request_metadata_dataset_ids = merge_request_metadata_dataset_ids(
        request_ids=completion_request.metadata_dataset_ids,
        messages=history,
    )
    effective_metadata_dataset_ids = resolve_effective_metadata_dataset_ids(
        request_ids=request_metadata_dataset_ids,
        session_ids=session_dataset_ids,
    )
    effective_debug_options["metadata_dataset_scope"] = {
        "source": "turn" if request_metadata_dataset_ids else ("session" if session_dataset_ids else "user"),
        "request_ids": list(request_metadata_dataset_ids or []),
        "session": list(conversation_scope.get("datasets", []) or []),
    }
    
    # --- Orchestration / Routing Logic ---
    # DEPRECATED here: Moved to AgentService/ContextManager for better trace and CoT logging.
    # We now let agent_service handle the routing if agent_id is missing.
    
    # Extract API Key for Context Propagation (Tool Authorization)
    api_key_str = request.headers.get("X-API-Key")
    if not api_key_str:
        auth = request.headers.get("Authorization")
        if auth:
            if auth.startswith("Bearer "):
                api_key_str = auth.split(" ")[1]
            else:
                api_key_str = auth

    request_claim = None
    if completion_request.client_request_id and completion_request.conversation_id:
        from app.services.ai.runtime.chat_request_idempotency import chat_request_idempotency

        request_claim = await chat_request_idempotency.claim(
            user_id=chat_user_id,
            conversation_id=completion_request.conversation_id,
            client_request_id=completion_request.client_request_id,
        )
        if request_claim is not None and not request_claim.acquired:
            if completion_request.stream:
                return _duplicate_chat_request_response(request_claim)
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "duplicate_chat_request",
                    "status": request_claim.status,
                    "trace_id": request_claim.trace_id,
                    "message": "相同发送请求已提交，请勿重复操作。",
                },
            )

    authorized_resource_scope: Dict[str, Any] = {"status": "unavailable"}
    try:
        from app.services.ai.accessible_resource_catalog import fetch_accessible_resource_counts

        raw_numeric_user_id = user_info.get("user_id") or user_info.get("id")
        numeric_user_id = int(raw_numeric_user_id) if raw_numeric_user_id is not None else None
        authorized_resource_scope = await fetch_accessible_resource_counts(
            db,
            user_id=numeric_user_id,
            user_name=user_info.get("user_name") or user_info.get("username"),
            is_admin=user_info.get("role") == "admin",
        )
    except Exception as exc:  # 目录统计只用于可观测性，不能阻断聊天请求
        logger.warning("Failed to load authorized resource counts for trace: %s", exc)

    agent_max_toolcall_timeout_seconds = await load_agent_max_toolcall_timeout()
    request_observability = {
        "authenticated": True,
        "parameters_validated": True,
        "idempotency_status": (
            "已通过"
            if request_claim is not None
            else "未启用（无客户端请求 ID 或未绑定会话）"
        ),
        "resource_scope": {
            key: len(conversation_scope.get(key, []) or [])
            for key in ("datasets", "knowledge_bases", "skills", "mcp_tools")
        },
        "turn_resource_scope": {
            "datasets": len({
                str(item).strip()
                for item in (effective_metadata_dataset_ids or [])
                if str(item).strip()
            }),
            "knowledge_bases": len({
                str(item).strip()
                for item in (effective_knowledge_dataset_ids or [])
                if str(item).strip()
            }),
        },
        "authorized_resource_scope": authorized_resource_scope,
        "agent_max_toolcall_timeout": int(agent_max_toolcall_timeout_seconds),
    }

    # Convert Pydantic models to dicts for the service
    quick_context = (
        completion_request.quick_context.model_dump(exclude_none=True)
        if completion_request.quick_context
        else None
    )
    if completion_request.stream:
        lane_user_id = chat_user_id
        conversation_id = completion_request.conversation_id
        claim_trace_id: Optional[str] = None
        claim_status = "completed"

        async def _release_locks_on_client_abort() -> None:
            from app.services.ai.runtime.conversation_run_cancel import release_conversation_run_locks

            await release_conversation_run_locks(
                user_id=lane_user_id,
                conversation_id=conversation_id,
            )

        client_disconnected_event = asyncio.Event()
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        async def _producer_task() -> None:
            nonlocal claim_trace_id, claim_status
            terminal_enqueued = False
            try:
                async for chunk in agent_service.chat_completion_stream(
                    history,
                    agent_id=completion_request.agent_id,
                    version_id=completion_request.version_id,
                    conversation_id=completion_request.conversation_id,
                    user_info=user_info,
                    api_key=api_key_str,
                    enable_multi_agent=completion_request.enable_multi_agent,
                    debug_options=effective_debug_options,
                    permission_options=completion_request.permission_options,
                    knowledge_dataset_ids=effective_knowledge_dataset_ids,
                    metadata_dataset_ids=effective_metadata_dataset_ids,
                    reusable_result_id=completion_request.reusable_result_id,
                    quick_context=quick_context,
                    request_observability=request_observability,
                ):
                    if isinstance(chunk, dict):
                        if chunk.get("trace_id"):
                            claim_trace_id = str(chunk["trace_id"])
                        if chunk.get("type") == "error" or chunk.get("status") == "error":
                            claim_status = "failed"
                    if not client_disconnected_event.is_set():
                        await queue.put(("chunk", chunk))
                    # run_status 表示模型输出已完成。提前结束 SSE 响应，producer
                    # 仍继续消费生成器，以便在后台完成 Redis/摘要持久化和会话收尾。
                    if (
                        isinstance(chunk, dict)
                        and chunk.get("type") == "run_status"
                        and not terminal_enqueued
                        and not client_disconnected_event.is_set()
                    ):
                        await queue.put(("done", None))
                        terminal_enqueued = True
                if not terminal_enqueued and not client_disconnected_event.is_set():
                    await queue.put(("done", None))
                if request_claim is not None:
                    from app.services.ai.runtime.chat_request_idempotency import chat_request_idempotency

                    await chat_request_idempotency.finish(
                        request_claim,
                        status=claim_status,
                        trace_id=claim_trace_id,
                    )
            except asyncio.CancelledError:
                if request_claim is not None:
                    from app.services.ai.runtime.chat_request_idempotency import chat_request_idempotency

                    await chat_request_idempotency.finish(
                        request_claim,
                        status="failed",
                        trace_id=claim_trace_id,
                    )
                from app.core.cancellation import spawn_detached

                spawn_detached(
                    _release_locks_on_client_abort(),
                    name=f"release-locks-producer-{conversation_id or 'unknown'}",
                )
                raise
            except Exception as exc:
                logger.error(
                    "[ChatAPI] Background producer task encountered error: %s",
                    exc,
                    exc_info=True,
                )
                if request_claim is not None:
                    from app.services.ai.runtime.chat_request_idempotency import chat_request_idempotency

                    await chat_request_idempotency.finish(
                        request_claim,
                        status="failed",
                        trace_id=claim_trace_id,
                    )
                if not client_disconnected_event.is_set():
                    await queue.put(("error", exc))

        producer_task = asyncio.create_task(
            _producer_task(),
            name=f"chat-producer-{conversation_id or 'unknown'}",
        )

        async def sse_generator() -> AsyncGenerator[str, None]:
            client_disconnected = False
            last_keepalive = 0.0
            try:
                run_config_payload = json.dumps(
                    {
                        "type": "run_config",
                        "agent_max_toolcall_timeout": int(agent_max_toolcall_timeout_seconds),
                    },
                    ensure_ascii=False,
                )
                yield f"data: {run_config_payload}\n\n"
                while True:
                    if not client_disconnected and await request.is_disconnected():
                        client_disconnected = True
                        client_disconnected_event.set()
                        logger.info(
                            "[ChatAPI] Client disconnected (mobile background or connection dropped) "
                            "for conversation=%s; background producer continues persistence.",
                            conversation_id,
                        )
                        break

                    try:
                        tag, payload = await asyncio.wait_for(queue.get(), timeout=0.25)
                    except asyncio.TimeoutError:
                        if producer_task.done() and queue.empty():
                            break
                        # 长时间静默（纯推理/工具执行、无事件输出）时周期性下发 keepalive
                        # 数据帧：既能防止代理/中间层超时断流，也能让前端读循环 resetStallTimer，
                        # 避免在真实 2s 静默窗口内误弹“AI 还在思考”Stall 提示。
                        now = time.monotonic()
                        if now - last_keepalive >= 1.0:
                            yield "data: {\"type\":\"keepalive\"}\n\n"
                            last_keepalive = now
                        continue

                    if tag == "chunk":
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    elif tag == "done":
                        yield "data: [DONE]\n\n"
                        break
                    elif tag == "error":
                        # producer 异常路径：必须向客户端发出 error 帧并补 [DONE]，
                        # 否则连接被静默关闭，前端 isChatStreamDone 永不置真、用户只看到"回答中断"
                        # 却没有任何错误原因。error 帧 shape 与链路内错误帧保持一致（type=error, content）。
                        try:
                            err_text = sanitize_error_text(payload) if payload is not None else "未知错误"
                        except Exception:  # 兜底：即便序列化失败也绝不静默关闭
                            err_text = "服务处理出错，请稍后重试"
                        yield (
                            "data: "
                            + json.dumps(
                                {"type": "error", "content": err_text},
                                ensure_ascii=False,
                            )
                            + "\n\n"
                        )
                        yield "data: [DONE]\n\n"
                        break
            except asyncio.CancelledError:
                client_disconnected_event.set()
                logger.info(
                    "[ChatAPI] SSE streaming cancelled by client for conversation=%s; "
                    "background producer task continues to finish message persistence.",
                    conversation_id,
                )
            finally:
                client_disconnected_event.set()
                # 客户端断开连接时，保持 producer_task 在后台独立运行完成消息落库
                pass

        return StreamingResponse(
            sse_generator(),
            media_type="text/event-stream",
            headers=SSE_RESPONSE_HEADERS,
        )
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

        try:
            result = await agent_service.chat_completion(
                history,
                agent_id=completion_request.agent_id,
                version_id=completion_request.version_id,
                conversation_id=completion_request.conversation_id,
                user_info=user_info,
                api_key=api_key_str,
                enable_multi_agent=completion_request.enable_multi_agent,
                debug_options=effective_debug_options,
                permission_options=completion_request.permission_options,
                knowledge_dataset_ids=effective_knowledge_dataset_ids,
                metadata_dataset_ids=effective_metadata_dataset_ids,
                reusable_result_id=completion_request.reusable_result_id,
                quick_context=quick_context,
                request_observability=request_observability,
            )
        except Exception:
            if request_claim is not None:
                from app.services.ai.runtime.chat_request_idempotency import chat_request_idempotency

                await chat_request_idempotency.finish(request_claim, status="failed")
            raise
        if request_claim is not None:
            from app.services.ai.runtime.chat_request_idempotency import chat_request_idempotency

            trace_id = result.get("trace_id") if isinstance(result, dict) else None
            await chat_request_idempotency.finish(
                request_claim,
                status="completed",
                trace_id=trace_id,
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

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers=SSE_RESPONSE_HEADERS,
    )


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

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers=SSE_RESPONSE_HEADERS,
    )


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

    # 1. User scope is resolved before grouping so one user's latest turn
    # cannot hide or select another user's conversation row.
    user_info = getattr(request.state, "user", None) if request else None
    if not user_info:
        raise HTTPException(status_code=401, detail="缺少用户身份")
    is_admin = user_info.get("role") == "admin"
    history_user_id = None if is_admin else _require_chat_user_id(user_info)
    scope_filters = []
    if history_user_id is not None:
        scope_filters.append(AgentExecutionHistory.user_id == history_user_id)
    elif username:
        scope_filters.append(AgentExecutionHistory.username == username)

    # 1. Base Query
    if group_by_conversation:
        # Aggregation Logic: Get latest record AND total count per conversation
        subquery = (
            select(
                func.max(AgentExecutionHistory.id).label("max_id"),
                func.count(AgentExecutionHistory.id).label("turn_count")
            )
            .where(*scope_filters)
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
    if scope_filters:
        query = query.where(*scope_filters)

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

    # /history is DB-backed, while reusable-result metadata is also persisted in
    # Redis history. Merge it by assistant trace_id so a refreshed conversation
    # can restore both the data badge and the generated/reused relation.
    reusable_metadata_by_trace: Dict[str, Dict[str, Any]] = {}
    if conversation_id and _should_enrich_history_reusable_metadata(user_info):
        try:
            redis_window = _history_reusable_metadata_window(page, page_size)
            redis_history = await memory_service.get_history(
                _require_chat_user_id(user_info),
                conversation_id,
                **redis_window,
            )
            for message in redis_history:
                if message.get("role") != "assistant" or not message.get("trace_id"):
                    continue
                metadata: Dict[str, Any] = {}
                # 旧 Redis 消息可能没有该字段；不要用默认 false 覆盖数据库里
                # 已经保存的 has_data_output=true。
                if message.get("has_data_output"):
                    metadata["has_data_output"] = True
                if message.get("reusable_result_id"):
                    metadata["reusable_result_id"] = str(message["reusable_result_id"])
                if message.get("reusable_result_status"):
                    metadata["reusable_result_status"] = str(message["reusable_result_status"])
                reusable_metadata_by_trace[str(message["trace_id"])] = metadata
        except Exception as exc:
            logger.debug("[History API] Failed to enrich output metadata from Redis: %s", exc)

    items = []
    if group_by_conversation:
        rows = result.all()
        if (user_info or {}).get("role") == "admin":
            from app.models.user import User
            usernames = {row_obj.username for row_obj, _ in rows if row_obj.username}
            owner_result = await db.execute(select(User.user_name, User.id).where(User.user_name.in_(usernames))) if usernames else None
            owner_map = {str(row.user_name): row.id for row in owner_result.all()} if owner_result else {}
            scopes = await ConversationResourceService.get_many_for_owners(
                [(owner_map.get(row_obj.username), row_obj.conversation_id) for row_obj, _ in rows if row_obj.conversation_id and owner_map.get(row_obj.username) is not None]
            )
            scope_key = lambda item: (str(owner_map.get(item.username)), item.conversation_id)
        else:
            scopes = await ConversationResourceService.get_many(
                history_user_id,
                [row_obj.conversation_id for row_obj, _ in rows if row_obj.conversation_id],
            )
            scope_key = lambda item: item.conversation_id
        for row_obj, turn_count in rows:
            item = AgentExecutionHistoryResponse.from_orm(row_obj)
            item = item.model_copy(update=reusable_metadata_by_trace.get(str(item.trace_id), {}))
            item.turn_count = turn_count
            if item.agent_id in agent_map:
                item.agent_name = agent_map[item.agent_id][0]
                item.agent_display_name = agent_map[item.agent_id][1]
            if item.conversation_id:
                scope = scopes.get(scope_key(row_obj), {})
                item.project_name = scope.get("project_name") or None
            items.append(item)
    else:
        rows = result.scalars().all()
        if (user_info or {}).get("role") == "admin":
            from app.models.user import User
            usernames = {row.username for row in rows if row.username}
            owner_result = await db.execute(select(User.user_name, User.id).where(User.user_name.in_(usernames))) if usernames else None
            owner_map = {str(row.user_name): row.id for row in owner_result.all()} if owner_result else {}
            scopes = await ConversationResourceService.get_many_for_owners(
                [(owner_map.get(row.username), row.conversation_id) for row in rows if row.conversation_id and owner_map.get(row.username) is not None]
            )
            scope_key = lambda item: (str(owner_map.get(item.username)), item.conversation_id)
        else:
            scopes = await ConversationResourceService.get_many(
                history_user_id,
                [row.conversation_id for row in rows if row.conversation_id],
            )
            scope_key = lambda item: item.conversation_id
        for row in rows:
            item = AgentExecutionHistoryResponse.from_orm(row)
            item = item.model_copy(update=reusable_metadata_by_trace.get(str(item.trace_id), {}))
            if item.agent_id in agent_map:
                item.agent_name = agent_map[item.agent_id][0]
                item.agent_display_name = agent_map[item.agent_id][1]
            if item.conversation_id:
                scope = scopes.get(scope_key(row), {})
                item.project_name = scope.get("project_name") or None
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

    user_info = getattr(request.state, "user", None) if request else None
    current_user_id = _require_chat_user_id(user_info)
    is_admin = user_info.get("role") == "admin"

    # 1. Find Record
    stmt = select(AgentExecutionHistory).where(AgentExecutionHistory.trace_id == trace_id)
    result = await db.execute(stmt)
    history = result.scalar_one_or_none()
    
    if not history:
        raise HTTPException(status_code=404, detail="History not found")

    # 2. Permission Check
    # Only allow if admin OR the persisted stable user ID owns the record.
    if not is_admin and str(history.user_id or "") != current_user_id:
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
    current_user_id = _require_chat_user_id(user_info)
    is_admin = user_info.get("role") == "admin"

    # 2. 同时取出会话归属人；Redis key 的 user_id 必须使用目标用户，而不是当前管理员。
    stmt = select(
        AgentExecutionHistory.conversation_id,
        AgentExecutionHistory.trace_id,
        AgentExecutionHistory.username,
        AgentExecutionHistory.user_id,
    ).where(AgentExecutionHistory.conversation_id.in_(payload.conversation_ids))
    if user_info and not is_admin:
        stmt = stmt.where(AgentExecutionHistory.user_id == current_user_id)
    
    result = await db.execute(stmt)
    history_rows = result.all()
    trace_ids = [row.trace_id for row in history_rows if row.trace_id]

    # 3. 执行批量级联删除
    if trace_ids:
        await db.execute(delete(AgentExecutionTrace).where(AgentExecutionTrace.trace_id.in_(trace_ids)))
    
    delete_history_stmt = delete(AgentExecutionHistory).where(AgentExecutionHistory.conversation_id.in_(payload.conversation_ids))
    if user_info and not is_admin:
        delete_history_stmt = delete_history_stmt.where(AgentExecutionHistory.user_id == current_user_id)
        
    await db.execute(delete_history_stmt)
    
    await db.commit()

    # 数据库历史删除后同步清理会话 Redis，避免项目资源范围和记忆残留。
    from app.services.ai.memory_service import memory_service
    for conversation_id in payload.conversation_ids:
        matching_owners = {
            str(row.user_id).strip()
            for row in history_rows
            if row.conversation_id == conversation_id and row.user_id
        }
        matching_owners.discard(None)
        if not matching_owners and not is_admin:
            matching_owners.add(current_user_id)
        for owner_id in matching_owners:
            await memory_service.clear_history(owner_id, conversation_id)

    return StandardResponse(data={"success": True})


class TruncateHistoryRequest(BaseModel):
    conversation_id: str = Field(..., description="会话ID")
    keep_count: int = Field(..., ge=0, description="保留前 N 条消息数量")


@router.post(
    "/history/truncate",
    response_model=StandardResponse[Dict[str, Any]],
    summary="截断会话历史记录",
    description="当用户编辑历史消息重发时，将服务端该会话的记忆与数据库记录截断至指定条数。",
)
async def truncate_history_endpoint(
    payload: TruncateHistoryRequest,
    request: Request,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    from app.models.audit import AgentExecutionHistory, AgentExecutionTrace
    from app.services.ai.memory_service import memory_service
    from sqlalchemy import delete, select

    user_id = _require_chat_user_id(user_info)

    # 1. 截断 Redis 记忆
    history_truncated = await memory_service.truncate_history(
        user_id=str(user_id),
        conversation_id=payload.conversation_id,
        keep_count=payload.keep_count,
    )
    if not history_truncated:
        raise HTTPException(
            status_code=503,
            detail="会话历史暂时无法同步，请稍后重试",
        )

    # 2. 如果保留数量对应的轮次之后的 DB 历史也可以同步裁剪
    keep_turns = payload.keep_count // 2
    stmt = (
        select(AgentExecutionHistory)
        .where(
            AgentExecutionHistory.conversation_id == payload.conversation_id,
            AgentExecutionHistory.user_id == user_id,
        )
        .order_by(AgentExecutionHistory.created_at.asc())
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()
    if len(rows) > keep_turns:
        to_delete = rows[keep_turns:]
        trace_ids = [r.trace_id for r in to_delete if r.trace_id]
        if trace_ids:
            await db.execute(
                delete(AgentExecutionTrace).where(AgentExecutionTrace.trace_id.in_(trace_ids))
            )
        del_ids = [r.id for r in to_delete]
        await db.execute(
            delete(AgentExecutionHistory).where(AgentExecutionHistory.id.in_(del_ids))
        )
        await db.commit()

    return StandardResponse(data={"success": True, "keep_count": payload.keep_count})

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
    from app.services.ai.audit_payload import bound_audit_payload
    
    # 1. Fetch High-Level History within the current user's stable identity scope.
    current_user_id = _require_chat_user_id(user_info)
    is_admin = user_info.get("role") == "admin"
    history_stmt = select(AgentExecutionHistory).where(AgentExecutionHistory.trace_id == trace_id)
    if not is_admin:
        history_stmt = history_stmt.where(AgentExecutionHistory.user_id == current_user_id)
    history_res = await db.execute(history_stmt)
    history_item = history_res.scalar_one_or_none()
    if not history_item:
        raise HTTPException(status_code=404, detail="History not found")

    # Permission Check: only admin or owner can view trace logs
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
            tool_input=bound_audit_payload(row.tool_input),
            tool_output=bound_audit_payload(row.tool_output),
            execution_time_ms=row.execution_time_ms,
            status=row.status,
            error_message=row.error_message,
            timestamp=row.created_at,
            span_id=getattr(row, "span_id", None),
            parent_span_id=getattr(row, "parent_span_id", None),
            meta_info=getattr(row, "meta_info", None),
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
    current_user_id = _require_chat_user_id(user_info)
    is_admin = user_info.get("role") == "admin"
    history_stmt = select(AgentExecutionHistory).where(AgentExecutionHistory.trace_id == trace_id)
    if not is_admin:
        history_stmt = history_stmt.where(AgentExecutionHistory.user_id == current_user_id)
    history_res = await db.execute(history_stmt)
    history_item = history_res.scalar_one_or_none()
    if not history_item:
        raise HTTPException(status_code=404, detail="History not found")

    data = await ExportService.get_trace_data(trace_id)
    if not data:
        raise HTTPException(status_code=404, detail="No exportable data found for this trace.")

    filename = f"export_{trace_id}"
    is_xlsx = format.lower() == "xlsx"
    if is_xlsx:
        content = ExportService.json_to_excel(data)
        ext, media_type = "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        content = ExportService.json_to_csv(data)
        ext, media_type = "csv", "text/csv"
    out_name = f"{filename}.{ext}"

    # 导出产物落盘到用户工作区目录 {root}/{user_key}/export/，并登记到 ai_artifacts
    # （只存元信息，具体内容留在工作区），返回带鉴权 token 的 download_url。
    from pathlib import Path
    from app.services.ai.runtime.agentscope.workspace import (
        resolve_workspace_root,
        resolve_workspace_user_key,
    )
    from app.services.ai.tools.generated_file_service import register_artifact

    workspace_root = await resolve_workspace_root()
    user_key = resolve_workspace_user_key(
        user_id=history_item.user_id,
        user_name=history_item.username,
    )
    export_dir = Path(workspace_root) / user_key / "export"
    export_dir.mkdir(parents=True, exist_ok=True)
    out_path = export_dir / out_name
    if isinstance(content, bytes):
        out_path.write_bytes(content)
    else:
        out_path.write_bytes(content.encode("utf-8"))

    # owner_user_id：优先取鉴权用户 id，回退 history.user_id（register_artifact 内部会 int() 转换）
    owner_user_id = history_item.user_id
    artifact = await register_artifact(
        source_path=out_path,
        filename=out_name,
        owner_user_id=owner_user_id,
        artifact_type="export",
        conversation_id=history_item.conversation_id,
        trace_id=trace_id,
    )
    return {
        "filename": artifact.filename,
        "mime_type": media_type,
        "size": artifact.size,
        "download_url": artifact.download_url,
        "expires_at": artifact.expires_at.isoformat(),
    }


class UploadResponse(BaseModel):
    url: str = Field(..., description="文件在服务器上的绝对路径（供 AI 与鉴权预览使用）")
    filename: str = Field(..., description="原始文件名")
    size: int = Field(..., description="文件字节大小")
    ext: str = Field(..., description="文件后缀名")

@router.post("/upload",
    response_model=StandardResponse[UploadResponse],
    summary="会话附件上传",
    description="支持会话过程中附件的上传、自动清洗和安全托管（最大限制 20MB，阻断敏感危险后缀）。文件保存至本人 agent_workspaces/uploads 目录。",
)
async def upload_chat_file(
    file: UploadFile = File(...),
    user_info: Dict[str, Any] = Depends(require_api_key)
):
    """
    Upload a session attachment into the current user's private workspace uploads folder.
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
    upload_dir = get_user_uploads_dir(user_info)
    if not upload_dir:
        raise HTTPException(status_code=403, detail="无法解析用户工作目录，上传失败。")
    os.makedirs(upload_dir, exist_ok=True)
    
    try:
        file_path, handle = open_upload_storage_file(upload_dir, file.filename)
        with handle as f:
            f.write(contents)
    except Exception as e:
        logger.error(f"Failed to save uploaded file: {e}")
        raise HTTPException(status_code=500, detail="保存文件失败，请稍后重试。")
        
    return StandardResponse(data=UploadResponse(
        url=file_path,
        filename=file.filename or os.path.basename(file_path),
        size=len(contents),
        ext=ext.replace(".", "")
    ))


class ActiveConversationRequest(BaseModel):
    conversation_id: str = Field(..., description="会话 ID")


@router.get("/active", summary="获取当前活跃的会话 ID")
async def get_active_conversation(
    user_info: dict = Depends(require_api_key),
    instance_id: Optional[str] = Query(default=None, max_length=128),
):
    from app.services.ai.memory_service import memory_service
    stable_user_id = _require_chat_user_id(user_info)
    user_id: Any = int(stable_user_id) if stable_user_id.isdigit() else stable_user_id
    conv_id = await memory_service.get_active_conversation(user_id, instance_id=instance_id)
    return StandardResponse(data={"conversation_id": conv_id})


@router.post("/active", summary="设置当前活跃的会话 ID")
async def set_active_conversation(
    body: ActiveConversationRequest,
    user_info: dict = Depends(require_api_key),
    instance_id: Optional[str] = Query(default=None, max_length=128),
):
    from app.services.ai.memory_service import memory_service
    stable_user_id = _require_chat_user_id(user_info)
    user_id: Any = int(stable_user_id) if stable_user_id.isdigit() else stable_user_id
    await memory_service.set_active_conversation(
        user_id,
        body.conversation_id,
        instance_id=instance_id,
    )
    return StandardResponse(data={"status": "success"})


@router.get("/conversation/{conversation_id}/resource-scope", summary="获取会话资源范围")
async def get_conversation_resource_scope(
    conversation_id: str,
    user_info: dict = Depends(require_api_key),
):
    user_id = _require_chat_user_id(user_info)
    scope = await ConversationResourceService.get(user_id, conversation_id)
    return StandardResponse(data=scope)


@router.put("/conversation/{conversation_id}/resource-scope", summary="更新会话资源范围")
async def update_conversation_resource_scope(
    conversation_id: str,
    body: ConversationResourceScopeRequest,
    user_info: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = _require_chat_user_id(user_info)
    normalized = await _normalize_conversation_resource_scope(db, user_info, body.model_dump())
    scope = await ConversationResourceService.replace(user_id, conversation_id, normalized)
    return StandardResponse(data=scope)
