"""MCP 工具定义变更：探测、待办列表、应用与忽略。

单独成文件而不是继续堆进 ``mcp.py``（那边已经 2788 行），但它挂的是同一个
``/mcp`` 前缀，所以前端看到的路由仍是 ``/api/portal/mcp/changes``。

可见性口径与「审计日志」页签一致：

- 管理员看全平台（含所有个人服务）——「定义变更」页签只对管理员展示；
- 非管理员只能看到**自己**的 personal 服务，因为服务内抽屉（卡片徽标点进去那个）
  对他们同样可见，不能因为抽屉背后的列表接口要求管理员而白屏。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.portal.endpoints.mcp import (
    _clear_runtime_tool_cache,
    _ensure_server_control_access,
    _get_user_id,
    _mcp_user_context,
)
from app.core.dependencies import require_api_key, require_permission
from app.core.orm import get_db_session
from app.models.mcp import McpServer, McpToolDefinitionChange
from app.services.mcp.definition_probe import (
    DETECTED_MANUAL,
    DETECTED_PAGE,
    PROBE_CHANGED,
    apply_changes,
    dismiss_changes,
    list_changes,
    probe_server,
    probe_servers,
)
from app.services.mcp.management_rate_limit import enforce_mcp_management_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

#: 一次页面探测最多覆盖多少个服务。前端按「需要探测的服务」分批调用，
#: 批量接口自身再兜一层，避免一次请求把连接池与远端同时打满。
MAX_PROBE_BATCH = 10


class ChangeIdsRequest(BaseModel):
    #: 变更记录 ID 列表
    change_ids: List[str] = Field(default_factory=list)
    #: 应用/忽略说明（可选，仅用于审计语义，当前不落库）
    note: Optional[str] = None


class ProbeRequest(BaseModel):
    #: 待探测的服务 ID 列表（最多 MAX_PROBE_BATCH 个）
    server_ids: List[str] = Field(default_factory=list)
    #: 触发来源：page（进页面惰性探测）/ manual（人点按钮）
    detected_by: str = DETECTED_PAGE


async def _visible_server_ids(db: AsyncSession, user: Dict) -> Optional[set]:
    """返回当前用户可见的服务 ID 集合；``None`` 表示不受限（管理员）。"""
    if user.get("role") == "admin":
        return None
    user_id = _get_user_id(user)
    if user_id is None:
        return set()
    rows = (
        await db.execute(
            select(McpServer.id).where(
                McpServer.scope == "personal", McpServer.user_id == user_id
            )
        )
    ).scalars().all()
    return {str(row) for row in rows}


def _ensure_changes_visible(server_ids: set, visible: Optional[set]) -> None:
    if visible is None:
        return
    if not server_ids or not server_ids.issubset(visible):
        raise HTTPException(status_code=403, detail="无权查看或处理该 MCP 服务的定义变更")


@router.get("/changes")
async def list_mcp_definition_changes(
    status: str = Query("pending", description="pending / applied / dismissed / all"),
    server_id: Optional[str] = Query(None),
    kind: Optional[List[str]] = Query(None, description="按 change_kind 过滤，可重复"),
    days: int = Query(7, ge=1, le=365, description="只看最近 N 天"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    """定义变更列表（跨服务平铺，供「定义变更」页签与服务内抽屉共用）。"""
    visible = await _visible_server_ids(db, user)
    if server_id:
        _ensure_changes_visible({str(server_id)}, visible)

    since = datetime.now() - timedelta(days=int(days))
    payload = await list_changes(
        status=status,
        server_id=server_id,
        change_kinds=kind,
        since=since,
        limit=limit,
        offset=offset,
        # 归属裁剪交给 SQL：查完再在内存里删，total 会变成「本页剩下几条」。
        visible_server_ids=None if visible is None else sorted(visible),
    )

    items = payload["items"]
    total = payload["total"]

    # 状态分段上的三个数字：给前端在「待确认 / 已应用 / 已忽略」之间切换用。
    #
    # 刻意**不带 status 过滤**（否则点进「已应用」后另外两段永远是 0，人就不知道
    # 还有多少待办），但**要带上 server_id / kind / days**——切到某个服务和某类变更
    # 之后，分段数字必须跟着收窄，否则点过去列表是空的，数字却是全局的。
    # 这些「统计卡用的数字」必须由服务端在 SQL 里算，不能拿 items 去数：
    # items 受 limit 截断（默认 200），待办一多就少算；而且切到「已应用」分段后
    # items 里没有 pending 行，卡片会直接显示 0，标题却写着「待确认」。
    scope_conditions = [McpToolDefinitionChange.detected_at >= since]
    if server_id:
        scope_conditions.append(McpToolDefinitionChange.server_id == str(server_id))
    if kind:
        scope_conditions.append(McpToolDefinitionChange.change_kind.in_(list(kind)))
    if visible is not None:
        scope_conditions.append(
            McpToolDefinitionChange.server_id.in_(visible or {""})
        )

    counts = {"pending": 0, "applied": 0, "dismissed": 0, "breaking_pending": 0}

    counts_stmt = (
        select(McpToolDefinitionChange.status, func.count())
        .where(*scope_conditions)
        .group_by(McpToolDefinitionChange.status)
    )
    for row_status, row_count in (await db.execute(counts_stmt)).all():
        if row_status in counts:
            counts[row_status] = int(row_count or 0)

    # 破坏性待确认：与第一张卡同口径（不受状态分段影响），一眼能看出「得先处理几件」。
    counts["breaking_pending"] = int(
        (
            await db.execute(
                select(func.count())
                .select_from(McpToolDefinitionChange)
                .where(
                    *scope_conditions,
                    McpToolDefinitionChange.status == "pending",
                    McpToolDefinitionChange.severity == "breaking",
                )
            )
        ).scalar()
        or 0
    )

    return {"items": items, "total": total, "counts": counts}


@router.post(
    "/servers/{server_id}/probe",
    dependencies=[Depends(require_permission("element", "element:mcp_management:sync"))],
)
async def probe_mcp_server(
    server_id: str,
    detected_by: str = Query(DETECTED_MANUAL, description="manual / page"),
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    """对单个服务做一次定义探测（只读，不改变任何已生效的工具定义）。"""
    server = (
        await db.execute(select(McpServer).where(McpServer.id == server_id))
    ).scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    _ensure_server_control_access(server, user)
    if user.get("role") != "admin":
        await enforce_mcp_management_rate_limit(_get_user_id(user), "probe")

    result = await probe_server(
        server_id,
        user_info=_mcp_user_context(user),
        detected_by=detected_by if detected_by in (DETECTED_MANUAL, DETECTED_PAGE) else DETECTED_MANUAL,
    )
    return result


@router.post(
    "/probe",
    dependencies=[Depends(require_permission("element", "element:mcp_management:sync"))],
)
async def probe_mcp_servers_batch(
    payload: ProbeRequest,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    """批量探测（进页面惰性探测 / 管理员批量刷新）。

    只探测「该用户有控制权」的服务：管理员拿到的平台服务全探，个人服务仅本人。
    """
    requested = [str(sid) for sid in payload.server_ids if sid][:MAX_PROBE_BATCH]
    if not requested:
        return {"results": [], "skipped": []}

    rows = (
        await db.execute(select(McpServer).where(McpServer.id.in_(requested)))
    ).scalars().all()
    allowed: List[McpServer] = []
    skipped: List[Dict[str, str]] = []
    for server in rows:
        try:
            _ensure_server_control_access(server, user)
        except HTTPException:
            skipped.append({"server_id": server.id, "reason": "no_access"})
            continue
        # 「需用户签名」的服务在后台无法探测，但这里是页面内探测：user_info 就是当前
        # 操作者本人，所以照常探测——这正是这条链路存在的意义。
        allowed.append(server)

    if user.get("role") != "admin":
        await enforce_mcp_management_rate_limit(_get_user_id(user), "probe")

    results = await probe_servers(
        [server.id for server in allowed],
        user_info=_mcp_user_context(user),
        detected_by=DETECTED_PAGE if payload.detected_by == DETECTED_PAGE else DETECTED_MANUAL,
    )
    changed = [item for item in results if item.get("status") == PROBE_CHANGED]
    return {
        "results": results,
        "skipped": skipped,
        "changed_count": len(changed),
        "total_new_changes": sum(int(item.get("new_changes") or 0) for item in results),
    }


@router.post(
    "/changes/apply",
    dependencies=[Depends(require_permission("element", "element:mcp_management:sync"))],
)
async def apply_mcp_definition_changes(
    payload: ChangeIdsRequest,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    """应用所选变更。

    粒度是「按服务整批同步远端」而不是「只写勾选的那几条」——``sync_tools()`` 本来就是
    整批覆盖语义，硬做局部应用会多出一条写路径，最终和凭据槽清理对不上。
    """
    if not payload.change_ids:
        raise HTTPException(status_code=400, detail="请选择要应用的变更")

    visible = await _visible_server_ids(db, user)
    targets = (
        await db.execute(
            select(McpToolDefinitionChange.server_id).where(
                McpToolDefinitionChange.id.in_([str(cid) for cid in payload.change_ids])
            )
        )
    ).scalars().all()
    _ensure_changes_visible({str(sid) for sid in targets}, visible)

    if user.get("role") != "admin":
        await enforce_mcp_management_rate_limit(_get_user_id(user), "sync")

    result = await apply_changes(
        payload.change_ids,
        user_id=_get_user_id(user),
        user_info=_mcp_user_context(user),
    )
    if result.get("applied"):
        _clear_runtime_tool_cache()
    return result


@router.post(
    "/changes/dismiss",
    dependencies=[Depends(require_permission("element", "element:mcp_management:sync"))],
)
async def dismiss_mcp_definition_changes(
    payload: ChangeIdsRequest,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_api_key),
):
    """忽略所选变更：本地定义保持原样，且不再用同一个差异反复提醒。"""
    if not payload.change_ids:
        raise HTTPException(status_code=400, detail="请选择要忽略的变更")

    visible = await _visible_server_ids(db, user)
    targets = (
        await db.execute(
            select(McpToolDefinitionChange.server_id).where(
                McpToolDefinitionChange.id.in_([str(cid) for cid in payload.change_ids])
            )
        )
    ).scalars().all()
    _ensure_changes_visible({str(sid) for sid in targets}, visible)

    return await dismiss_changes(payload.change_ids, user_id=_get_user_id(user))
