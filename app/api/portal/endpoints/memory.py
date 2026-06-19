"""记忆管理中心 API"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.dependencies import require_api_key, require_permission
from app.services.ai.embedding_client import EmbeddingClient
from app.services.ai.daily_summary_service import DailySummaryService
from app.services.ai.memory_index_service import MemoryIndexService
from app.services.ai.memory_service import memory_service, ltm_service
from app.services.ai.redis_vector_health import RedisVectorHealthService
from app.services.ai.session_summary_service import SessionSummaryService
from app.services.memory_config_service import MemoryConfigService

logger = logging.getLogger(__name__)
router = APIRouter()

MENU = ("menu", "menu:memory_management")


def _current_uid(user: Dict) -> int:
    return int(user.get("user_id") or user.get("id"))


async def _can_view_user(requester: Dict, target_user_id: int) -> bool:
    if requester.get("role") == "admin":
        return True
    if target_user_id == _current_uid(requester):
        return True
    from app.core.orm import AsyncSessionLocal
    from app.services.permission_service import PermissionService

    async with AsyncSessionLocal() as session:
        svc = PermissionService(session)
        return await svc.check_permission(
            _current_uid(requester), "element", "element:memory:view_all_users"
        )


async def _resolve_target_user_id(
    user: Dict, user_id: Optional[int] = None
) -> str:
    target = user_id if user_id is not None else _current_uid(user)
    if not await _can_view_user(user, int(target)):
        raise HTTPException(status_code=403, detail="无权查看该用户的记忆数据")
    return str(target)


async def _can_view_all_users(user: Dict) -> bool:
    if user.get("role") == "admin":
        return True
    from app.core.orm import AsyncSessionLocal
    from app.services.permission_service import PermissionService

    async with AsyncSessionLocal() as session:
        svc = PermissionService(session)
        return await svc.check_permission(
            _current_uid(user), "element", "element:memory:view_all_users"
        )


async def _resolve_target_user_id_with_username(
    user: Dict,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
) -> str:
    if user_id is not None:
        return await _resolve_target_user_id(user, user_id)
    username_q = (username or "").strip()
    if not username_q:
        return await _resolve_target_user_id(user, None)
    if await _can_view_all_users(user):
        matched = await _find_user_ids_by_username(username_q, limit=5)
        if not matched:
            raise HTTPException(status_code=404, detail=f"未找到匹配「{username_q}」的用户")
        if len(matched) > 1:
            raise HTTPException(
                status_code=400,
                detail=f"匹配到多个用户 ID：{matched}，请缩小用户名或指定用户 ID",
            )
        return await _resolve_target_user_id(user, matched[0])
    uid = _current_uid(user)
    names = await _user_display_names([uid])
    info = names.get(str(uid), {})
    uq = username_q.lower()
    if uq not in (info.get("display_name") or "").lower() and uq not in (
        info.get("user_name") or ""
    ).lower():
        raise HTTPException(status_code=403, detail="无权按该用户名检索他人记忆")
    return await _resolve_target_user_id(user, uid)


async def _user_display_names(user_ids: List[int]) -> Dict[str, Dict[str, Optional[str]]]:
    unique = sorted({int(u) for u in user_ids if u is not None})
    if not unique:
        return {}
    from sqlalchemy import select

    from app.core.orm import AsyncSessionLocal
    from app.models.user import User

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(User.id, User.user_name, User.real_name).where(User.id.in_(unique))
            )
        ).all()
    out: Dict[str, Dict[str, Optional[str]]] = {}
    for uid, uname, rname in rows:
        out[str(uid)] = {
            "user_name": uname,
            "display_name": rname or uname or str(uid),
        }
    return out


async def _find_user_ids_by_username(username: str, limit: int = 20) -> List[int]:
    from sqlalchemy import or_, select

    from app.core.orm import AsyncSessionLocal
    from app.models.user import User

    q = (username or "").strip()
    if not q:
        return []
    pattern = f"%{q}%"
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(User.id)
                .where(
                    or_(User.user_name.like(pattern), User.real_name.like(pattern))
                )
                .limit(limit)
            )
        ).all()
    return [int(r[0]) for r in rows]


def _filter_items_by_username(items: List[Dict[str, Any]], username: str) -> List[Dict[str, Any]]:
    uq = (username or "").strip().lower()
    if not uq:
        return items
    return [
        i
        for i in items
        if uq in (i.get("display_name") or "").lower()
        or uq in (i.get("user_name") or "").lower()
    ]


def _attach_user_labels(
    items: List[Dict[str, Any]],
    names: Dict[str, Dict[str, Optional[str]]],
    default_user_id: str,
) -> None:
    for item in items:
        uid = str(item.get("user_id") or default_user_id)
        info = names.get(uid, {})
        item["user_id"] = uid
        item["user_name"] = info.get("user_name")
        item["display_name"] = info.get("display_name") or info.get("user_name") or uid


class ConfigItem(BaseModel):
    key: str
    value: str
    description: Optional[str] = None
    is_secret: bool = False


class ConfigUpdateRequest(BaseModel):
    updates: List[ConfigItem]


class SearchTestRequest(BaseModel):
    user_id: Optional[int] = None
    username: Optional[str] = Field(None, description="登录名/姓名，与 user_id 二选一或优先 user_id")
    query: Optional[str] = None
    scope: str = "summary"
    conversation_id: Optional[str] = None
    limit: Optional[int] = None


async def require_memory_vector_ready() -> Dict[str, Any]:
    """记忆功能依赖 RediSearch 向量索引；未通过健康检查时拒绝写操作与数据接口。"""
    health = await RedisVectorHealthService.check()
    if not health.get("ok"):
        raise HTTPException(status_code=503, detail=health)
    return health


@router.get("/redis-vector-test")
async def redis_vector_test(
    force: bool = Query(False, description="跳过缓存立即重测"),
    user: Dict = Depends(require_permission(*MENU)),
):
    """进入记忆管理中心前的 Redis Stack / 向量索引环境检测。"""
    data = await RedisVectorHealthService.check(force=force)
    return {"status": "success", "data": data}


@router.get("/configs")
async def get_memory_configs(
    user: Dict = Depends(require_permission(*MENU)),
    _health: Dict = Depends(require_memory_vector_ready),
):
    items = await MemoryConfigService.get_all()
    for item in items:
        if item.get("is_secret") and item.get("value"):
            item["value"] = "******"
    return {"status": "success", "data": items}


@router.put("/configs")
async def update_memory_configs(
    body: ConfigUpdateRequest,
    user: Dict = Depends(require_permission("element", "element:memory:config_save")),
    _health: Dict = Depends(require_memory_vector_ready),
):
    updates = [i.model_dump() for i in body.updates]
    for u in updates:
        if u.get("is_secret") and u.get("value") == "******":
            existing = await MemoryConfigService.get(u["key"])
            if existing:
                u["value"] = existing
    await MemoryConfigService.bulk_update(updates, changed_by=user.get("user_name", "admin"))
    return {"status": "success", "message": "配置已更新"}


@router.post("/test-embedding")
async def test_embedding(
    user: Dict = Depends(require_permission("element", "element:memory:config_save")),
    _health: Dict = Depends(require_memory_vector_ready),
):
    try:
        vec = await EmbeddingClient.embed_text("记忆服务连通性测试")
        return {
            "status": "success",
            "dimensions": len(vec),
            "sample": vec[:5],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/index/status")
async def get_index_status(
    user: Dict = Depends(require_permission(*MENU)),
    _health: Dict = Depends(require_memory_vector_ready),
):
    return {"status": "success", "data": await MemoryIndexService.index_status()}


@router.post("/index/rebuild")
async def rebuild_index(
    user: Dict = Depends(require_permission("element", "element:memory:config_index")),
    _health: Dict = Depends(require_memory_vector_ready),
):
    RedisVectorHealthService.invalidate_cache()
    return {"status": "success", "data": await MemoryIndexService.rebuild_index()}


@router.get("/summaries")
async def list_summaries(
    user_id: Optional[int] = Query(None),
    conversation_id: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    username: Optional[str] = Query(None, description="按登录名/姓名模糊筛选"),
    limit: int = Query(50, ge=1, le=200),
    user: Dict = Depends(require_permission("element", "element:memory:view_data")),
    _health: Dict = Depends(require_memory_vector_ready),
):
    username_q = (username or "").strip()
    items: List[Dict[str, Any]] = []

    if username_q and user_id is None:
        if await _can_view_all_users(user):
            matched_ids = await _find_user_ids_by_username(username_q)
            if not matched_ids:
                return {"status": "success", "data": []}
            per_user = max(10, limit // max(1, len(matched_ids)))
            for mid in matched_ids[:10]:
                part = await MemoryIndexService.list_summaries(
                    str(mid),
                    keyword=keyword,
                    conversation_id=conversation_id,
                    limit=per_user,
                )
                items.extend(part)
            items.sort(key=lambda x: x.get("last_active") or 0, reverse=True)
            items = items[:limit]
            user_ids = list({int(i.get("user_id") or 0) for i in items if i.get("user_id")})
            names = await _user_display_names(user_ids)
            _attach_user_labels(items, names, str(matched_ids[0]))
            items = _filter_items_by_username(items, username_q)
            for item in items:
                cid = item.get("conversation_id")
                if not cid:
                    continue
                uid_str = str(item.get("user_id") or "")
                item["has_history"] = await memory_service.history_exists(
                    uid_str, cid
                )
                item.pop("_embedding_vec", None)
            # Filter out items without conversation_id
            items = [i for i in items if i.get("conversation_id")]
            return {"status": "success", "data": items}

    uid = await _resolve_target_user_id(user, user_id)
    items = await MemoryIndexService.list_summaries(
        uid, keyword=keyword, conversation_id=conversation_id, limit=limit
    )
    user_ids = [int(uid)]
    for item in items:
        if item.get("user_id"):
            try:
                user_ids.append(int(item["user_id"]))
            except (TypeError, ValueError):
                pass
    names = await _user_display_names(user_ids)
    _attach_user_labels(items, names, uid)
    if username_q:
        items = _filter_items_by_username(items, username_q)
    for item in items:
        cid = item.get("conversation_id")
        if not cid:
            continue
        item_uid = str(item.get("user_id") or uid)
        item["has_history"] = await memory_service.history_exists(
            item_uid, cid
        )
        item.pop("_embedding_vec", None)
    # Filter out items without conversation_id
    items = [i for i in items if i.get("conversation_id")]
    return {"status": "success", "data": items}


@router.get("/daily-summaries")
async def list_daily_summaries(
    user_id: Optional[int] = Query(None),
    username: Optional[str] = Query(None, description="按登录名/姓名筛选"),
    keyword: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user: Dict = Depends(require_permission("element", "element:memory:view_data")),
    _health: Dict = Depends(require_memory_vector_ready),
):
    uid = await _resolve_target_user_id_with_username(user, user_id, username)
    items = await DailySummaryService.list_daily_summaries(
        uid,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    names = await _user_display_names([int(uid)])
    _attach_user_labels(items, names, uid)
    for item in items:
        item.pop("_embedding_vec", None)
        item["session_count"] = await MemoryIndexService.count_session_summaries_for_day(
            uid, item.get("date") or ""
        )
    return {"status": "success", "data": items}


@router.get("/daily-summaries/{target_user_id}/{day}")
async def get_daily_summary_detail(
    target_user_id: int,
    day: str,
    user: Dict = Depends(require_permission("element", "element:memory:view_data")),
    _health: Dict = Depends(require_memory_vector_ready),
):
    if not await _can_view_user(user, target_user_id):
        raise HTTPException(status_code=403, detail="无权查看")
    uid = str(target_user_id)
    summary = await DailySummaryService.get_daily_summary(uid, day)
    if summary:
        names = await _user_display_names([int(uid)])
        _attach_user_labels([summary], names, uid)
        summary.pop("_embedding_vec", None)
    sessions = await MemoryIndexService.list_session_summaries_for_day(uid, day)
    for item in sessions:
        item.pop("_embedding_vec", None)
        item["has_history"] = await memory_service.history_exists(
            uid, item.get("conversation_id") or ""
        )
    return {
        "status": "success",
        "data": {
            "summary": summary,
            "sessions": sessions,
        },
    }


@router.post("/daily-summaries/{target_user_id}/{day}/rebuild")
async def rebuild_daily_summary(
    target_user_id: int,
    day: str,
    user: Dict = Depends(require_permission("element", "element:memory:config_index")),
    _health: Dict = Depends(require_memory_vector_ready),
):
    if not await _can_view_user(user, target_user_id):
        raise HTTPException(status_code=403, detail="无权重建")
    result = await DailySummaryService.refresh_for_date(str(target_user_id), day)
    return {"status": "success", "data": result}


@router.delete("/daily-summaries/{target_user_id}/{day}")
async def delete_daily_summary(
    target_user_id: int,
    day: str,
    user: Dict = Depends(require_permission("element", "element:memory:delete")),
    _health: Dict = Depends(require_memory_vector_ready),
):
    if not await _can_view_user(user, target_user_id):
        raise HTTPException(status_code=403, detail="无权删除")
    await DailySummaryService.delete_daily_summary(str(target_user_id), day)
    return {"status": "success", "message": "已删除每日摘要"}


@router.get("/summaries/{target_user_id}/{conversation_id}")
async def get_summary_detail(
    target_user_id: int,
    conversation_id: str,
    history_limit: int = Query(30, ge=1, le=100),
    user: Dict = Depends(require_permission("element", "element:memory:view_data")),
    _health: Dict = Depends(require_memory_vector_ready),
):
    if not await _can_view_user(user, target_user_id):
        raise HTTPException(status_code=403, detail="无权查看")
    uid = str(target_user_id)
    items = await MemoryIndexService.list_summaries(uid, conversation_id=conversation_id, limit=1)
    summary = items[0] if items else None
    if summary:
        names = await _user_display_names([int(uid)])
        _attach_user_labels([summary], names, uid)
        summary.pop("_embedding_vec", None)
        summary.setdefault("has_embedding", False)
    history = await memory_service.get_history(uid, conversation_id, limit=history_limit)
    return {
        "status": "success",
        "data": {
            "summary": summary,
            "history": history,
            "has_history": bool(history),
        },
    }


@router.delete("/summaries/{target_user_id}/{conversation_id}")
async def delete_summary(
    target_user_id: int,
    conversation_id: str,
    user: Dict = Depends(require_permission("element", "element:memory:delete")),
    _health: Dict = Depends(require_memory_vector_ready),
):
    if not await _can_view_user(user, target_user_id):
        raise HTTPException(status_code=403, detail="无权删除")
    uid = str(target_user_id)
    await MemoryIndexService.delete_summary(uid, conversation_id)
    return {"status": "success", "message": "已删除摘要"}


@router.delete("/users/{target_user_id}")
async def delete_user_memory(
    target_user_id: int,
    user: Dict = Depends(require_permission("element", "element:memory:delete")),
    _health: Dict = Depends(require_memory_vector_ready),
):
    if not await _can_view_user(user, target_user_id):
        raise HTTPException(status_code=403, detail="无权清空")
    uid = str(target_user_id)
    from app.core.redis import get_redis

    count = await MemoryIndexService.delete_all_for_user(uid)
    redis = await get_redis()
    if redis:
        pattern = f"conversation:{uid}:*:history"
        async for key in redis.scan_iter(match=pattern, count=200):
            await redis.delete(key)
    return {"status": "success", "message": f"已清空用户 {uid} 的记忆（摘要 {count} 条）"}


@router.post("/search-test")
async def search_test(
    body: SearchTestRequest,
    user: Dict = Depends(require_permission("element", "element:memory:test_search")),
    _health: Dict = Depends(require_memory_vector_ready),
):
    uid = await _resolve_target_user_id_with_username(
        user, body.user_id, body.username
    )
    limit = body.limit or await MemoryConfigService.get_int("memory_search_knn_top_k", 5)
    data = await SessionSummaryService.search_for_user(
        uid,
        query=body.query,
        scope=body.scope,
        conversation_id=body.conversation_id,
        limit=limit,
    )
    names = await _user_display_names([int(uid)])
    info = names.get(uid, {})
    data["target_user"] = {
        "user_id": uid,
        "user_name": info.get("user_name"),
        "display_name": info.get("display_name") or info.get("user_name"),
    }
    return {"status": "success", "data": data}


# =====================================================================
# 用户本人可见的隔离记忆接口（无需 menu:memory_management 权限，仅限本人操作）
# =====================================================================

class LtmUpdateRequest(BaseModel):
    key: str = Field(..., description="长期事实/偏好键名")
    value: str = Field(..., description="长期事实/偏好值")


async def _clear_all_session_memory_for_user(uid: str) -> dict[str, int]:
    from app.core.redis import get_redis

    summary_count = await MemoryIndexService.delete_all_for_user(uid)
    daily_count = await DailySummaryService.delete_all_for_user(uid)
    history_count = 0
    redis = await get_redis()
    if redis:
        pattern = f"conversation:{uid}:*:history"
        async for key in redis.scan_iter(match=pattern, count=200):
            await redis.delete(key)
            history_count += 1
    return {
        "session_summaries_deleted": summary_count,
        "daily_summaries_deleted": daily_count,
        "history_deleted": history_count,
    }


@router.get("/my/summaries")
async def list_my_summaries(
    keyword: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user: Dict = Depends(require_api_key),
    _health: Dict = Depends(require_memory_vector_ready),
):
    """拉取当前用户本人的会话记忆列表"""
    uid = str(_current_uid(current_user))
    items = await MemoryIndexService.list_summaries(
        uid, keyword=keyword, limit=limit
    )
    names = await _user_display_names([int(uid)])
    _attach_user_labels(items, names, uid)
    for item in items:
        cid = item.get("conversation_id")
        if not cid:
            continue
        item["has_history"] = await memory_service.history_exists(
            uid, cid
        )
        item.pop("_embedding_vec", None)
    # Filter out items without conversation_id
    items = [i for i in items if i.get("conversation_id")]
    return {"status": "success", "data": items}


@router.get("/my/summaries/{conversation_id}")
async def get_my_summary_detail(
    conversation_id: str,
    history_limit: int = Query(30, ge=1, le=100),
    current_user: Dict = Depends(require_api_key),
    _health: Dict = Depends(require_memory_vector_ready),
):
    """获取当前用户本人特定会话的记忆明细与历史聊天记录"""
    uid = str(_current_uid(current_user))
    items = await MemoryIndexService.list_summaries(uid, conversation_id=conversation_id, limit=1)
    summary = items[0] if items else None
    if summary:
        names = await _user_display_names([int(uid)])
        _attach_user_labels([summary], names, uid)
        summary.pop("_embedding_vec", None)
        summary.setdefault("has_embedding", False)
    history = await memory_service.get_history(uid, conversation_id, limit=history_limit)
    return {
        "status": "success",
        "data": {
            "summary": summary,
            "history": history,
            "has_history": bool(history),
        },
    }


@router.delete("/my/summaries/{conversation_id}")
async def delete_my_summary(
    conversation_id: str,
    current_user: Dict = Depends(require_api_key),
    _health: Dict = Depends(require_memory_vector_ready),
):
    """物理清除当前用户本人特定会话的记忆摘要与 Redis 历史"""
    uid = str(_current_uid(current_user))
    await memory_service.delete_session_memory(uid, conversation_id, include_summary=True)
    return {"status": "success", "message": "已清除该会话的记忆与聊天历史"}


@router.delete("/my/session-memory")
async def delete_all_my_session_memory(
    current_user: Dict = Depends(require_api_key),
    _health: Dict = Depends(require_memory_vector_ready),
):
    """物理清除当前用户本人的全部会话摘要、每日摘要与 Redis 聊天历史"""
    uid = str(_current_uid(current_user))
    stats = await _clear_all_session_memory_for_user(uid)
    total = (
        stats["session_summaries_deleted"]
        + stats["daily_summaries_deleted"]
        + stats["history_deleted"]
    )
    return {
        "status": "success",
        "message": "已清除全部会话记忆",
        "data": stats,
        "total_deleted": total,
    }


@router.get("/my/ltm")
async def get_my_ltm(
    current_user: Dict = Depends(require_api_key),
    _health: Dict = Depends(require_memory_vector_ready),
):
    """拉取当前用户本人的长期记忆事实与偏好数据"""
    uid = str(_current_uid(current_user))
    data = await ltm_service.fetch_memory(uid)
    return {"status": "success", "data": data}


@router.put("/my/ltm")
async def update_my_ltm(
    body: LtmUpdateRequest,
    current_user: Dict = Depends(require_api_key),
    _health: Dict = Depends(require_memory_vector_ready),
):
    """新增或修改当前用户本人的长期偏好键值对"""
    uid = str(_current_uid(current_user))
    key = body.key.strip()
    value = body.value.strip()
    if not key:
        raise HTTPException(status_code=400, detail="键名不能为空")
    success = await ltm_service.update_preference(uid, key, value)
    if not success:
        raise HTTPException(status_code=500, detail="保存长期偏好失败，请检查 Redis 状态")
    return {"status": "success", "message": "偏好记忆已更新"}


@router.delete("/my/ltm/{key}")
async def delete_my_ltm(
    key: str,
    current_user: Dict = Depends(require_api_key),
    _health: Dict = Depends(require_memory_vector_ready),
):
    """删除当前用户本人的某项长期记忆偏好键"""
    uid = str(_current_uid(current_user))
    success = await ltm_service.delete_preference(uid, key)
    if not success:
        raise HTTPException(status_code=500, detail="删除偏好失败，请检查 Redis 状态")
    return {"status": "success", "message": "偏好记忆已删除"}


@router.get("/my/daily-summaries")
async def list_my_daily_summaries(
    keyword: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user: Dict = Depends(require_api_key),
    _health: Dict = Depends(require_memory_vector_ready),
):
    """拉取当前用户本人的每日摘要列表"""
    uid = str(_current_uid(current_user))
    items = await DailySummaryService.list_daily_summaries(
        uid,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    names = await _user_display_names([int(uid)])
    _attach_user_labels(items, names, uid)
    for item in items:
        item.pop("_embedding_vec", None)
        item["session_count"] = await MemoryIndexService.count_session_summaries_for_day(
            uid, item.get("date") or ""
        )
    return {"status": "success", "data": items}


@router.get("/my/daily-summaries/{day}")
async def get_my_daily_summary_detail(
    day: str,
    current_user: Dict = Depends(require_api_key),
    _health: Dict = Depends(require_memory_vector_ready),
):
    """获取当前用户本人特定日期的每日摘要详情与关联会话"""
    uid = str(_current_uid(current_user))
    summary = await DailySummaryService.get_daily_summary(uid, day)
    if summary:
        names = await _user_display_names([int(uid)])
        _attach_user_labels([summary], names, uid)
        summary.pop("_embedding_vec", None)
    sessions = await MemoryIndexService.list_session_summaries_for_day(uid, day)
    for item in sessions:
        item.pop("_embedding_vec", None)
        item["has_history"] = await memory_service.history_exists(
            uid, item.get("conversation_id") or ""
        )
    return {
        "status": "success",
        "data": {
            "summary": summary,
            "sessions": sessions,
        },
    }


@router.delete("/my/daily-summaries/{day}")
async def delete_my_daily_summary(
    day: str,
    current_user: Dict = Depends(require_api_key),
    _health: Dict = Depends(require_memory_vector_ready),
):
    """删除当前用户本人特定日期的每日摘要"""
    uid = str(_current_uid(current_user))
    await DailySummaryService.delete_daily_summary(uid, day)
    return {"status": "success", "message": "已删除每日摘要"}


@router.post("/my/daily-summaries/{day}/rebuild")
async def rebuild_my_daily_summary(
    day: str,
    current_user: Dict = Depends(require_api_key),
    _health: Dict = Depends(require_memory_vector_ready),
):
    """重建当前用户本人特定日期的每日摘要"""
    uid = str(_current_uid(current_user))
    result = await DailySummaryService.refresh_for_date(uid, day)
    return {"status": "success", "data": result}


class ConsolidateRequest(BaseModel):
    user_id: Optional[int] = None


@router.post("/consolidate")
async def consolidate_memories(
    req: ConsolidateRequest,
    current_user: Dict = Depends(require_api_key),
    _health: Dict = Depends(require_memory_vector_ready),
):
    """手动触发记忆整理降噪接口（支持管理员指定用户或全部用户）"""
    # 鉴权
    is_admin = current_user.get("role") == "admin"
    if not is_admin:
        # 校验是否有 element:memory:view_all_users 权限
        has_all_perm = await _can_view_all_users(current_user)
        if not has_all_perm:
            # 普通用户，只能整理自己本人的
            if req.user_id is not None and req.user_id != _current_uid(current_user):
                raise HTTPException(status_code=403, detail="无权为其他用户执行记忆整理")
    
    from app.core.orm import AsyncSessionLocal
    from app.models.user import User
    from sqlalchemy import select
    
    target_uids = []
    if req.user_id is not None:
        target_uids = [str(req.user_id)]
    else:
        # 如果不传，默认管理员可以整理所有人
        if not is_admin:
            target_uids = [str(_current_uid(current_user))]
        else:
            async with AsyncSessionLocal() as session:
                stmt = select(User.id).where(User.status == 1)
                result = await session.execute(stmt)
                target_uids = [str(uid) for uid in result.scalars().all()]
                
    # 逐个执行
    consolidated_count = 0
    for uid in target_uids:
        try:
            await MemoryIndexService.consolidate_user_memories(uid)
            consolidated_count += 1
        except Exception as ex:
            logger.error(f"Failed to manually consolidate memory for user {uid}: {ex}")
            
    return {
        "status": "success",
        "message": f"成功触发并整理了 {consolidated_count} 个用户的历史记忆",
        "processed_users": target_uids
    }


@router.post("/my/consolidate")
async def consolidate_my_memories(
    current_user: Dict = Depends(require_api_key),
    _health: Dict = Depends(require_memory_vector_ready),
):
    """当前用户本人手动触发自己长时记忆整理"""
    uid = str(_current_uid(current_user))
    try:
        await MemoryIndexService.consolidate_user_memories(uid)
        return {"status": "success", "message": "记忆整理与合并归约成功完成"}
    except Exception as ex:
        logger.error(f"Failed to manually consolidate my memory {uid}: {ex}")
        raise HTTPException(status_code=500, detail=str(ex))



