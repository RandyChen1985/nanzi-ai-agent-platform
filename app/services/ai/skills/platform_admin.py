"""平台技能管理权限（``element:skills:admin``）的同步读缓存。

## 为什么需要这一层

「装进平台全局技能库」的调用点有三处：

* ``create_skills(scope="global")``：模型逐字写 SKILL.md；
* ``import_skill_from_sandbox(scope="global")``：整目录收录第三方技能包；
* ``GET /skills/gallery``：决定要不要下发 ``install_prompt_global``。

前两处是**同步工具函数**，没法 ``await PermissionService.check_permission``；而三处必须
同一口径——页面入口按 ``element:skills:admin`` 判定、工具里只认 ``role == "admin"``，
会出现「按钮看得见、点了必然失败」或者反过来的漏权。

做法与「禁用公共技能」（``app/services/ai/skills/policy.py``）同源：会话启动时异步预热
一次，工具侧同步读进程内缓存。**但失败方向相反**：那条是 fail-open（未命中按放行，
避免把文件判定变成会抛异常的路径），这里是 **fail-closed**——它对应的是写平台库，
宁可少放行也不能多放行，所以未命中一律按无权限处理。

缓存只是「最近一次异步查询结果」的副本，TTL 很短；真正的权威判定仍在
``PermissionService`` 与 HTTP 层的 ``require_permission("element", "element:skills:admin")``。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Optional

from app.services.ai.skills.policy import resolve_user_id_from_user_info

logger = logging.getLogger(__name__)

PLATFORM_SKILL_ADMIN_RESOURCE_TYPE = "element"
PLATFORM_SKILL_ADMIN_PERMISSION = "element:skills:admin"

# 同步读缓存的绝对有效期（秒）。每个会话启动都会异步刷新一遍，实际陈旧窗口远小于此值。
CACHE_TTL_SECONDS = 60.0
_CACHE_MAX_ENTRIES = 512

# key = 规范化后的 user_id 字符串；value = (写入时刻 monotonic, 是否可管理平台技能)
_cache: "OrderedDict[str, tuple[float, bool]]" = OrderedDict()
_cache_lock = threading.RLock()


def _now() -> float:
    """时钟入口单独抽出，便于测试把条目推到过期。"""
    return time.monotonic()


def _normalize_user_id(user_id: Any) -> Optional[str]:
    if user_id is None:
        return None
    key = str(user_id).strip()
    return key or None


def _has_admin_role(user_info: Optional[Dict[str, Any]]) -> bool:
    """与 ``create_skills`` 原来的判定保持一致：admin 直接放行，不查库也不看缓存。"""
    role = str((user_info or {}).get("role") or "").strip().lower()
    return bool(role) and (role == "admin" or "admin" in role)


def clear_platform_skill_admin_cache() -> None:
    """清空缓存。权限变更（改角色/撤授权）后可主动调用，不清也会在 TTL 后自然失效。"""
    with _cache_lock:
        _cache.clear()


def remember_platform_skill_admin(user_id: Any, allowed: bool) -> None:
    """把一次异步查询的结果写进同步读缓存（覆盖同 key 旧值，超容量按插入序淘汰）。"""
    key = _normalize_user_id(user_id)
    if key is None:
        return
    with _cache_lock:
        _cache[key] = (_now(), bool(allowed))
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)


def _cached_allowed(user_id: Any) -> Optional[bool]:
    key = _normalize_user_id(user_id)
    if key is None:
        return None
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        written_at, allowed = entry
        if _now() - written_at > CACHE_TTL_SECONDS:
            _cache.pop(key, None)
            return None
        return allowed


def can_manage_platform_skills(user_info: Optional[Dict[str, Any]] = None) -> bool:
    """同步判定当前用户能否创建/收录平台全局技能。

    身份缺失、缓存未命中、缓存为 False → 一律 False（fail-closed）。
    """
    if not isinstance(user_info, dict) or not user_info:
        return False
    if _has_admin_role(user_info):
        return True
    return _cached_allowed(resolve_user_id_from_user_info(user_info)) is True


async def prime_platform_skill_admin(
    user_info: Optional[Dict[str, Any]],
    db: Any = None,
) -> bool:
    """异步查一次 ``element:skills:admin`` 并写入同步缓存，返回判定结果。

    查询失败不写缓存：异常不等于「无权限」，让下一次会话再试，而不是把一个坏结果
    钉住整个 TTL。
    """
    if not isinstance(user_info, dict) or not user_info:
        return False
    if _has_admin_role(user_info):
        return True

    user_id = resolve_user_id_from_user_info(user_info)
    if user_id is None:
        return False

    async def _query(session: Any) -> bool:
        from app.services.permission_service import PermissionService

        return await PermissionService(session).check_permission(
            int(user_id),
            PLATFORM_SKILL_ADMIN_RESOURCE_TYPE,
            PLATFORM_SKILL_ADMIN_PERMISSION,
        )

    try:
        if db is not None:
            allowed = await _query(db)
        else:
            # 与 ``prime_forbidden_skill_cache`` 同一手法：调用方没有现成 session 时自建，
            # 避免把「会话启停路径」的 db 依赖一路传下去。
            from app.core.orm import AsyncSessionLocal

            async with AsyncSessionLocal() as session:
                allowed = await _query(session)
    except Exception as exc:  # noqa: BLE001 - 权限查询失败不能打断会话，也不能放行
        logger.warning("[Skills] 查询平台技能管理权限失败，按无权限处理：%s", exc)
        return False

    remember_platform_skill_admin(user_id, allowed)
    return bool(allowed)
