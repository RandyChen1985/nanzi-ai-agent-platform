from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import String, cast, desc, select, func
from typing import Optional, List
import time
import logging

from app.core.orm import get_db_session as get_db
from app.core.dependencies import require_api_key
from app.models.chatbi_example import ChatBIExample
from app.models.user import User
from app.models.agent import AIAgent
from app.services.chatbi_example_service import ExampleService
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)

router = APIRouter()


def _chatbi_example_user_join():
    """构造兼容 MySQL/ PostgreSQL 的案例用户关联条件。

    经验表的历史结构将 user_id 保存为字符串，而平台用户主键在 PostgreSQL
    中是 BIGINT。在 MySQL 环境下使用 func.concat(User.id, '') 避开
    utf8mb4_0900_ai_ci 与 utf8mb4_unicode_ci 的 1267 排序规则碰撞。
    """
    from app.core.config import settings
    if settings.DATABASE_TYPE == "mysql":
        return func.concat(User.id, "") == ChatBIExample.user_id
    return cast(User.id, String) == ChatBIExample.user_id

class AuditRequest(BaseModel):
    id: int
    status: str # "approved", "rejected", "deprecated"

class UpdateExampleRequest(BaseModel):
    user_query: Optional[str] = None
    refined_query: Optional[str] = None
    context_summary: Optional[str] = None
    sql_text: Optional[str] = None
    sql_metadata: Optional[dict] = None
    category: Optional[str] = None

class ExampleSearchTestRequest(BaseModel):
    """案例集检索测试请求体（只读、不落库）。"""
    query: str
    metadata_provider: Optional[str] = "default"  # default / local / ragflow
    top_k: Optional[int] = None
    similarity_threshold: Optional[float] = None
    vector_weight: Optional[float] = None

@router.post("/{id}/enhance")
async def trigger_manual_enhance(
    id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_api_key)
):
    """
    手动触发 AI 智能增强（意图还原、背景总结）。
    """
    stmt = select(ChatBIExample).where(ChatBIExample.id == id)
    result = await db.execute(stmt)
    example = result.scalars().first()

    if not example:
        raise HTTPException(status_code=404, detail="Example not found.")

    # 设为 pending 并触发后台任务
    example.enhance_status = "pending"
    await db.commit()

    background_tasks.add_task(ExampleService._enhance_example_with_llm, id)
    return {"code": 200, "message": "智能增强任务已启动。"}

@router.put("/{id}")
async def update_example(
    id: int,
    request: UpdateExampleRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_api_key)
):
    """
    更新 ChatBI 经验内容（支持手动微调提问、意图和上下文）。
    """
    stmt = select(ChatBIExample).where(ChatBIExample.id == id)
    result = await db.execute(stmt)
    example = result.scalars().first()

    if not example:
        raise HTTPException(status_code=404, detail="Example not found.")

    if request.user_query is not None:
        example.user_query = request.user_query
    if request.refined_query is not None:
        example.refined_query = request.refined_query
    if request.context_summary is not None:
        example.context_summary = request.context_summary
    if request.sql_text is not None:
        example.sql_text = request.sql_text
    if request.sql_metadata is not None:
        example.sql_metadata = request.sql_metadata
    if request.category is not None:
        example.category = request.category
    # 修改内容后，重置同步状态，提醒需要重新同步
    if example.rag_sync_status == "synced":
        example.rag_sync_status = "pending"

    await db.commit()
    return {"code": 200, "message": "更新成功。"}

@router.get("")
async def list_examples(
    id: Optional[int] = None,
    agent_id: Optional[str] = None,
    dataset_id: Optional[int] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_api_key)
):
    """
    获取 ChatBI 经验库列表（含用户与智能体信息）。
    """
    # 采用外连接获取用户信息和智能体信息
    stmt = select(
        ChatBIExample, 
        User.real_name.label("user_real_name"),
        User.user_name.label("user_account_name"),
        AIAgent.display_name.label("agent_display_name"),
        AIAgent
    ).outerjoin(
        User, _chatbi_example_user_join()
    ).outerjoin(
        AIAgent, AIAgent.id == ChatBIExample.agent_id
    )

    if id:
        stmt = stmt.filter(ChatBIExample.id == id)
    if agent_id:
        stmt = stmt.filter(ChatBIExample.agent_id == agent_id)
    if dataset_id:
        stmt = stmt.filter(ChatBIExample.dataset_id == dataset_id)
    if status:
        stmt = stmt.filter(ChatBIExample.status == status)
    if category:
        stmt = stmt.filter(ChatBIExample.category == category)
    if search and search.strip():
        s = search.strip()
        if s.isdigit():
            stmt = stmt.filter(
                or_(
                    ChatBIExample.id == int(s),
                    ChatBIExample.dataset_id == int(s),
                    ChatBIExample.user_query.like(f"%{s}%"),
                    ChatBIExample.agent_id.like(f"%{s}%")
                )
            )
        else:
            stmt = stmt.filter(
                or_(
                    ChatBIExample.user_query.like(f"%{s}%"),
                    ChatBIExample.refined_query.like(f"%{s}%"),
                    ChatBIExample.sql_text.like(f"%{s}%"),
                    ChatBIExample.agent_id.like(f"%{s}%"),
                    ChatBIExample.trace_id.like(f"%{s}%"),
                    AIAgent.display_name.like(f"%{s}%")
                )
            )

    # 获取总数
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_res = await db.execute(count_stmt)
    total = total_res.scalar_one()

    # 获取数据
    stmt = stmt.order_by(desc(ChatBIExample.created_at)).offset((page - 1) * size).limit(size)
    result = await db.execute(stmt)
    
    # 转换为字典列表，合并显示名称
    items = []
    for row in result.all():
        example = row[0]
        real_name = row[1]
        account_name = row[2]
        agent_display_name = row[3]
        agent_obj = row[4]
        
        # 将 SQLAlchemy 对象转为字典
        item_dict = {c.name: getattr(example, c.name) for c in example.__table__.columns}
        
        # 兜底：若存量数据 category 为空，从智能体属性解析回填
        if not item_dict.get("category"):
            if agent_obj:
                try:
                    from app.services.ai.agent_types import resolve_agent_type, AgentType
                    resolved = resolve_agent_type(agent_obj)
                    if resolved == AgentType.CHATBI:
                        item_dict["category"] = "data_query"
                    elif resolved == AgentType.KNOWLEDGE_BASE:
                        item_dict["category"] = "knowledge"
                    else:
                        item_dict["category"] = "general"
                except Exception:
                    item_dict["category"] = "data_query" if example.sql_text else "general"
            else:
                item_dict["category"] = "data_query" if example.sql_text else "general"

        # 优先级：真实姓名 > 账号名 > ID > 系统
        display_name = real_name or account_name
        if not display_name:
            display_name = f"ID:{example.user_id}" if example.user_id else "系统"
            
        item_dict["user_real_name"] = display_name
        item_dict["agent_display_name"] = agent_display_name or f"ID:{example.agent_id}"
        items.append(item_dict)

    return {
        "code": 200,
        "data": {
            "total": total,
            "items": items,
            "page": page,
            "size": size
        }
    }

@router.post("/sync-all")
async def sync_all_examples(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_api_key)
):
    """
    一键同步所有审核通过 (approved) 的案例。

    - 本地模式 (metadata_provider=local)：仅同步到本地 Redis 向量索引，不连接 RAGFlow；
    - RAGFlow 模式：同步到 RAGFlow（并联动本地 Redis 向量）。
    """
    stmt = select(ChatBIExample).where(ChatBIExample.status == "approved")
    result = await db.execute(stmt)
    examples = result.scalars().all()

    if not examples:
        return {"code": 200, "message": "当前没有状态为'已通过'的案例需要同步。"}

    metadata_provider = await ConfigService.get("metadata_provider", default="local")
    is_local_mode = str(metadata_provider or "").strip().lower() == "local"

    target = (ExampleService.sync_to_local_redis
              if is_local_mode else ExampleService.sync_to_ragflow)
    for ex in examples:
        background_tasks.add_task(target, ex.id)

    mode_label = "本地 Redis 向量索引" if is_local_mode else "RAGFlow"
    return {"code": 200, "message": f"已成功触发 {len(examples)} 条已通过案例到{mode_label}的同步任务。"}

@router.post("/audit")
async def audit_example(
    request: AuditRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_api_key)
):
    """
    审核 ChatBI 经验。
    """
    if request.status not in ["approved", "rejected", "deprecated"]:
        raise HTTPException(status_code=400, detail="Invalid status.")

    try:
        success = await ExampleService.audit_example(db, request.id, request.status)
        if not success:
            raise HTTPException(status_code=404, detail="该案例记录不存在或已被删除，请刷新列表。")
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    return {"code": 200, "message": "审核操作成功。"}

@router.post("/sync/{example_id}")
async def sync_example(
    example_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_api_key)
):
    """
    手动触发单条案例同步。

    - 本地模式 (metadata_provider=local)：仅同步到本地 Redis 向量索引，不连接 RAGFlow；
    - RAGFlow 模式：同步到 RAGFlow（并联动本地 Redis 向量）。
    """
    stmt = select(ChatBIExample).where(ChatBIExample.id == example_id)
    result = await db.execute(stmt)
    example = result.scalars().first()

    if not example:
        raise HTTPException(status_code=404, detail="Example not found.")

    if example.status not in ["approved", "deprecated"]:
        raise HTTPException(
            status_code=400, 
            detail=f"只有审核通过或已废弃的记录允许同步。当前状态: {example.status}"
        )

    metadata_provider = await ConfigService.get("metadata_provider", default="local")
    is_local_mode = str(metadata_provider or "").strip().lower() == "local"

    target = (ExampleService.sync_to_local_redis
              if is_local_mode else ExampleService.sync_to_ragflow)
    background_tasks.add_task(target, example_id)
    mode_label = "本地 Redis 向量索引" if is_local_mode else "RAGFlow"
    return {"code": 200, "message": f"已开启异步同步到{mode_label}的任务。"}

@router.post("/search-test")
async def search_test_examples(
    request: ExampleSearchTestRequest,
    _=Depends(require_api_key)
):
    """
    案例集检索测试（只读模拟）：以与真实运行完全一致的链路在经验库中检索相似案例，
    支持临时覆盖检索模式 / Top K / 相似度阈值 / 向量权重，返回命中结果与过程日志，
    便于管理员验证样例集命中情况与调参效果。不落库、不产生副作用。
    """
    query = (request.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="检索问题不能为空。")

    start_ts = time.time()

    provider_override = None
    if request.metadata_provider in ("local", "ragflow"):
        provider_override = request.metadata_provider

    stats: dict = {}
    logs: List[str] = []
    items: List[dict] = []

    def _mode_label(mode: Optional[str]) -> str:
        return "local · Redis 向量检索" if mode == "local" else ("ragflow · RAGFlow" if mode == "ragflow" else "未知")

    try:
        items = await ExampleService.search_examples(
            query,
            dataset_id=None,
            top_k=request.top_k,
            history=None,
            stats_dump=stats,
            provider_override=provider_override,
            threshold_override=request.similarity_threshold,
            vector_weight_override=request.vector_weight,
        )
    except Exception as exc:  # 检索链路内部多数已兜底，此处兜住意外异常
        logs.append(f"[ERROR] 检索过程发生异常: {exc}")
        logger.exception("[ExampleSearchTest] search failed")

    elapsed_ms = round((time.time() - start_ts) * 1000)
    mode = stats.get("mode")
    logs.extend([
        f"[MODE] 检索模式: {_mode_label(mode)}"
        + (f"（来自手动覆盖）" if provider_override else "（跟随系统配置）"),
    ])
    if stats.get("query"):
        rewritten = stats.get("rewritten_query")
        if rewritten and rewritten != stats.get("query"):
            logs.append(f"[QUERY] 检索词: 「{rewritten}」（原问题改写而来）")
        else:
            logs.append(f"[QUERY] 检索词: 「{stats.get('query')}」")
    else:
        logs.append(f"[QUERY] 检索词: 「{query}」")
    logs.append(f"[QUERY] 未进行意图改写（测试无对话历史）")
    if stats.get("top_k") is not None:
        logs.append(f"[PARAM] top_k={stats.get('top_k')}")
    if stats.get("similarity_threshold") is not None:
        logs.append(f"[PARAM] 相似度阈值={stats.get('similarity_threshold'):.2f}")
    if stats.get("vector_similarity_weight") is not None:
        logs.append(f"[PARAM] 向量权重={stats.get('vector_similarity_weight'):.2f}")
    if stats.get("vector_recalled") is not None:
        logs.append(f"[RETRIEVE] 向量/原始召回 {stats.get('vector_recalled')} 条")
    if stats.get("valid_sql_after_filter") is not None:
        logs.append(f"[FILTER] 阈值与可用 SQL 过滤后有效 {stats.get('valid_sql_after_filter')} 条")
    if "mysql_fallback_hits" in stats:
        keywords = stats.get("mysql_keywords") or []
        kw_text = "、".join(keywords) if keywords else "（无）"
        logs.append(f"[FALLBACK] 关键词兜底 {stats.get('mysql_fallback_hits')} 条〔{kw_text}〕")

    logs.append(f"[TIME] 检索耗时 {elapsed_ms}ms")

    found = len(items) > 0
    if found:
        logs.append(f"[HIT] 命中 {len(items)} 条相似案例")
    else:
        logs.append("[MISS] 未找到足够相似的优质 SQL 案例")

    return {
        "code": 200,
        "data": {
            "found": found,
            "provider": mode or "unknown",
            "count": len(items),
            "items": items,
            "logs": logs,
            "elapsed_ms": elapsed_ms,
        },
    }
