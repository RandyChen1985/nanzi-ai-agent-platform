"""个人 MCP 分享：快照、发送、接受安装、撤回与列表。

语义与技能分享（``skill_share_service``）保持一致：

* 分享即打快照：payload 里是「分享瞬间」的完整配置 + 工具清单，源服务之后被改/删都不影响
  已发出的邀请，也不存在回写源服务的路径；
* 只分享给指定的人，不做公开链接；同一个人重复分享同一服务 = 刷新同一条 PENDING 记录；
* 接受方拿到一份**独立副本**：新 server 行 + 复制的工具缓存，名字冲突自动加 ``-2`` / ``-3``。

与技能不同的是凭证：MCP 的可用性完全依赖 Token / 断言私钥，因此默认**携带**（否则对方接受
了也连不上），但调用方可以传 ``include_credentials=False`` 只分享连接配置，由接收人自己填。
无论携带与否，凭证字段在库里始终是密文（沿用 ``sys_mcp_servers`` 的加密字段）。
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp import McpServer, McpToolCache
from app.models.mcp_share import McpServerShare
from app.models.user import User
from app.services.mcp.mcp_auth_policy import without_credential_headers
from app.services.portal_notification_service import PortalNotificationService

# 与技能分享共用同一套「搜索接收人」实现（纯用户检索，与技能表无关）
from app.services.skill_share_service import search_share_targets as search_share_targets  # noqa: F401

MAX_SHARE_TARGETS = 50
MAX_MESSAGE_LENGTH = 200
SHARE_STATUSES = ("PENDING", "ACCEPTED", "REJECTED", "WITHDRAWN")
DEFAULT_LIST_LIMIT = 100
MAX_LIST_LIMIT = 500
MAX_SERVER_NAME_LENGTH = 100
PAYLOAD_VERSION = 1
# mcp-private-<用户名>- / mcp-public-<用户名>-：前端命名约定，接受分享时要按接收人重建
_SCOPE_NAME_PREFIX_RE = re.compile(r"^mcp-(?:private|public)-[^-]+-", re.IGNORECASE)


class McpShareError(Exception):
    """MCP 分享域异常基类。"""


class McpShareNotFoundError(McpShareError):
    pass


class McpShareConflictError(McpShareError):
    pass


class McpSharePermissionError(McpShareError):
    pass


def _user_id(user: dict[str, Any]) -> int:
    value = user.get("user_id") or user.get("id")
    return int(value)


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _suggested_name_from_payload(
    row: McpServerShare, receiver_user_name: str | None
) -> str | None:
    if not row.payload:
        return None
    try:
        snapshot = json.loads(row.payload)
    except json.JSONDecodeError:
        return None
    config = snapshot.get("config") or {}
    return suggested_accepted_name(
        config.get("server_name") or row.server_name,
        receiver_user_name,
        config.get("source_user_name"),
    )


def _share_payload(
    row: McpServerShare,
    *,
    source_user: User | None = None,
    target_user: User | None = None,
    receiver_user_name: str | None = None,
) -> dict[str, Any]:
    return {
        "id": row.id,
        "source_user_id": int(row.source_user_id),
        "source_user_name": getattr(source_user, "user_name", None),
        "source_real_name": getattr(source_user, "real_name", None),
        "source_server_id": row.source_server_id,
        "server_name": row.server_name,
        "remark": row.remark,
        "sse_url": row.sse_url,
        "include_credentials": bool(row.include_credentials),
        "message": row.message,
        "target_user_id": int(row.target_user_id),
        "target_user_name": getattr(target_user, "user_name", None),
        "target_real_name": getattr(target_user, "real_name", None),
        "status": row.status,
        "accepted_server_id": row.accepted_server_id,
        # 安装向导弹层预填用：已按接收人账号算好默认落地名
        "suggested_server_name": (
            _suggested_name_from_payload(row, receiver_user_name)
            if row.status == "PENDING"
            else None
        ),
        "created_at": _iso(row.created_at),
        "responded_at": _iso(row.responded_at),
    }


# ---------------------------------------------------------------------------
# 服务名：前缀属于「谁建的」，接受时要换成接收人的账号
# ---------------------------------------------------------------------------

def _sanitize_name_part(value: str | None, fallback: str = "user") -> str:
    part = re.sub(r"[^a-zA-Z0-9_-]+", "-", (value or "").strip()).strip("-")
    return part or fallback


def shared_name_suffix(server_name: str, source_user_name: str | None = None) -> str:
    """剥掉 ``mcp-private-<分享人>-`` / ``mcp-public-<分享人>-`` 前缀，只留业务别名。

    优先按分享人账号精确切；分享人账号可能含 ``-``，正则兜底只切第一个 ``-`` 之前，
    所以「知道分享人是谁」时结果更准。手动改成裸名（没有前缀）时原样返回。
    """
    name = (server_name or "").strip()
    if source_user_name:
        prefix = f"mcp-private-{source_user_name}-"
        if name.lower().startswith(prefix.lower()):
            return name[len(prefix):]
    match = _SCOPE_NAME_PREFIX_RE.match(name)
    return name[match.end():] if match else name


def suggested_accepted_name(
    server_name: str,
    target_user_name: str | None,
    source_user_name: str | None = None,
) -> str:
    """接收方落地名：``mcp-private-<接收人>-<业务别名>``。

    直接用源名会让副本看起来像分享人的资产（admin 分享出去的还叫 ``mcp-private-admin-*``），
    既误导接收人，也让「谁建的」这条约定失效。
    """
    prefix = f"mcp-private-{_sanitize_name_part(target_user_name)}-"
    suffix = shared_name_suffix(server_name, source_user_name) or "shared"
    return f"{prefix}{suffix}"[:MAX_SERVER_NAME_LENGTH]


# ---------------------------------------------------------------------------
# 快照
# ---------------------------------------------------------------------------

def build_share_config(server: McpServer, *, include_credentials: bool) -> dict[str, Any]:
    """把一条 MCP 服务配置拍成可存储的快照（敏感字段保持密文原样）。"""
    config: dict[str, Any] = {
        "server_name": server.server_name,
        "remark": server.remark,
        "sse_url": server.sse_url,
        "credential_mode": server.credential_mode or "static",
        "user_assertion_header": server.user_assertion_header or "X-Nanzi-User-Assertion",
        "enabled_status": int(server.enabled_status or 0),
    }
    if include_credentials:
        config.update(
            {
                "auth_headers": server.auth_headers,
                "fixed_token_encrypted": server.fixed_token_encrypted,
                "user_assertion_enabled": bool(server.user_assertion_enabled),
                "user_assertion_audience": server.user_assertion_audience,
                "user_assertion_key_id": server.user_assertion_key_id,
                "user_assertion_issuer": server.user_assertion_issuer,
                "user_assertion_private_key_encrypted": server.user_assertion_private_key_encrypted,
            }
        )
    else:
        # 不携带凭证：清掉 Authorization / 固定 Token / 断言私钥，断言开关一并关闭，
        # 否则对方会拿到一个「开着签名但没有私钥」的坏配置。
        config.update(
            {
                "auth_headers": without_credential_headers(server),
                "fixed_token_encrypted": None,
                "user_assertion_enabled": False,
                "user_assertion_audience": None,
                "user_assertion_key_id": None,
                "user_assertion_issuer": None,
                "user_assertion_private_key_encrypted": None,
            }
        )
    return config


def build_share_payload(
    server: McpServer,
    tools: list[McpToolCache],
    *,
    include_credentials: bool,
    source_user_name: str | None = None,
) -> dict[str, Any]:
    config = build_share_config(server, include_credentials=include_credentials)
    # 记下分享人账号：接受时要按它把 mcp-private-<分享人>- 前缀换成接收人的
    config["source_user_name"] = source_user_name
    return {
        "version": PAYLOAD_VERSION,
        "include_credentials": bool(include_credentials),
        "config": config,
        "tools": [
            {
                "tool_name": tool.tool_name,
                "tool_description": tool.tool_description,
                "parameter_schema": tool.parameter_schema,
                "is_published": bool(tool.is_published),
                "is_available": bool(tool.is_available),
            }
            for tool in tools
        ],
    }


async def _server_tools(session: AsyncSession, server_id: str) -> list[McpToolCache]:
    result = await session.execute(
        select(McpToolCache)
        .where(McpToolCache.server_id == server_id)
        .order_by(McpToolCache.tool_name.asc())
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# 分享人视角：发送 / 列表 / 撤回
# ---------------------------------------------------------------------------

async def _notify(
    session: AsyncSession,
    *,
    user_id: int,
    title: str,
    content: str,
    share_id: str,
    server_id: str,
    for_receiver: bool,
    accepted_server_id: str | None = None,
    target_tab: str = "received",
) -> None:
    await PortalNotificationService.create(
        session,
        user_id=user_id,
        title=title[:200],
        content=content,
        level="info",
        category="mcp_share" if for_receiver else "mcp_share_result",
        resource_type="mcp_share",
        resource_id=share_id,
        metadata={
            "share_id": share_id,
            "server_id": server_id,
            "accepted_server_id": accepted_server_id,
            "open": "mcp_share",
            "target_tab": target_tab,
        },
    )


async def _load_owned_server(
    session: AsyncSession, *, user: dict[str, Any], server_id: str
) -> McpServer:
    result = await session.execute(select(McpServer).where(McpServer.id == server_id))
    server = result.scalar_one_or_none()
    if server is None:
        raise McpShareNotFoundError("MCP 服务不存在")
    if server.scope != "personal":
        raise McpSharePermissionError("只能分享「我的 MCP」中的个人服务")
    if int(server.user_id or 0) != _user_id(user):
        raise McpSharePermissionError("只能分享自己创建的个人 MCP")
    return server


async def share_server(
    session: AsyncSession,
    *,
    user: dict[str, Any],
    server_id: str,
    target_user_ids: list[int],
    message: str | None = None,
    include_credentials: bool = True,
) -> dict[str, Any]:
    """把个人 MCP 分享给一批人，返回 ``{created, refreshed}``。"""
    user_id = _user_id(user)
    targets: list[int] = []
    for raw in target_user_ids or []:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value and value not in targets:
            targets.append(value)
    if not targets:
        raise McpShareConflictError("请选择接收人")
    if len(targets) > MAX_SHARE_TARGETS:
        raise McpShareConflictError(f"一次最多分享给 {MAX_SHARE_TARGETS} 人")
    if user_id in targets:
        raise McpShareConflictError("不能分享给自己")

    text = (message or "").strip()
    if len(text) > MAX_MESSAGE_LENGTH:
        raise McpShareConflictError(f"附言不能超过 {MAX_MESSAGE_LENGTH} 字")

    server = await _load_owned_server(session, user=user, server_id=server_id)

    # 接收人必须真实存在且处于启用状态：没有物理外键，脏 ID 会安静地写进分享表，
    # 还会朝一个不存在的人发通知。口径与技能分享一致（status != 1 一律视为不可接收）。
    users_result = await session.execute(select(User).where(User.id.in_(targets)))
    users = {int(row.id): row for row in users_result.scalars().all()}
    invalid = [target for target in targets if target not in users or users[target].status != 1]
    if invalid:
        raise McpShareConflictError(
            "以下接收人不存在或已停用：" + "、".join(str(item) for item in invalid)
        )

    tools = await _server_tools(session, server.id)
    payload_json = json.dumps(
        build_share_payload(
            server,
            tools,
            include_credentials=include_credentials,
            source_user_name=user.get("user_name"),
        ),
        ensure_ascii=False,
    )

    existing_result = await session.execute(
        select(McpServerShare).where(
            McpServerShare.source_user_id == user_id,
            McpServerShare.source_server_id == server.id,
            McpServerShare.target_user_id.in_(targets),
            McpServerShare.status == "PENDING",
        )
    )
    existing_map = {
        int(row.target_user_id): row for row in existing_result.scalars().all()
    }

    created: list[dict[str, Any]] = []
    refreshed: list[dict[str, Any]] = []
    for target_id in targets:
        existing = existing_map.get(target_id)
        if existing is not None:
            # 重复分享 = 刷新同一条邀请（换最新快照 + 重发通知），不堆一堆 PENDING
            existing.server_name = server.server_name
            existing.remark = server.remark
            existing.sse_url = server.sse_url
            existing.payload = payload_json
            existing.include_credentials = bool(include_credentials)
            existing.message = text or None
            existing.created_at = datetime.now()
            row = existing
        else:
            row = McpServerShare(
                id=str(uuid.uuid4()),
                source_user_id=user_id,
                source_server_id=server.id,
                server_name=server.server_name,
                remark=server.remark,
                sse_url=server.sse_url,
                payload=payload_json,
                include_credentials=bool(include_credentials),
                target_user_id=target_id,
                status="PENDING",
                message=text or None,
                created_at=datetime.now(),
            )
            session.add(row)

        await _notify(
            session,
            user_id=target_id,
            title="收到一个 MCP 分享",
            content=(
                f"{user.get('real_name') or user.get('user_name') or '同事'} 分享了 MCP 服务"
                f"「{server.server_name}」，接受后会安装到你的「我的 MCP」。"
            ),
            share_id=row.id,
            server_id=server.id,
            for_receiver=True,
        )
        item = _share_payload(row)
        (refreshed if existing is not None else created).append(item)

    await session.flush()
    return {"created": created, "refreshed": refreshed}


async def list_sent_shares(
    session: AsyncSession,
    *,
    user: dict[str, Any],
    status: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    user_id = _user_id(user)
    stmt = (
        select(McpServerShare, User)
        .outerjoin(User, User.id == McpServerShare.target_user_id)
        .where(McpServerShare.source_user_id == user_id)
        .order_by(McpServerShare.created_at.desc())
    )
    if status and status in SHARE_STATUSES:
        stmt = stmt.where(McpServerShare.status == status)
    result = await session.execute(
        stmt.offset(max(offset, 0)).limit(max(1, min(limit, MAX_LIST_LIMIT)))
    )
    rows = result.all()

    counts_result = await session.execute(
        select(McpServerShare.status, func.count())
        .where(McpServerShare.source_user_id == user_id)
        .group_by(McpServerShare.status)
    )
    status_counts = {key: 0 for key in SHARE_STATUSES}
    for raw_status, count in counts_result.all():
        if raw_status in status_counts:
            status_counts[raw_status] = int(count)

    return {
        "items": [_share_payload(row, target_user=target) for row, target in rows],
        "total": sum(status_counts.values()),
        "pending_count": status_counts["PENDING"],
        "status_counts": status_counts,
    }


async def withdraw_share(
    session: AsyncSession, *, user: dict[str, Any], share_id: str
) -> dict[str, Any]:
    user_id = _user_id(user)
    row = (
        await session.execute(select(McpServerShare).where(McpServerShare.id == share_id))
    ).scalar_one_or_none()
    if row is None:
        raise McpShareNotFoundError("分享记录不存在")
    if int(row.source_user_id) != user_id:
        raise McpSharePermissionError("只有分享人可以撤回")
    if row.status != "PENDING":
        raise McpShareConflictError("该分享已处理，无法撤回")

    row.status = "WITHDRAWN"
    row.responded_at = datetime.now()
    row.payload = None
    await session.flush()
    return _share_payload(row)


# ---------------------------------------------------------------------------
# 接收人视角：列表 / 接受 / 拒绝
# ---------------------------------------------------------------------------

async def list_received_shares(
    session: AsyncSession,
    *,
    user: dict[str, Any],
    status: str = "PENDING",
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    user_id = _user_id(user)
    stmt = (
        select(McpServerShare, User)
        .outerjoin(User, User.id == McpServerShare.source_user_id)
        .where(McpServerShare.target_user_id == user_id)
        .order_by(McpServerShare.created_at.desc())
    )
    if status != "all" and status in SHARE_STATUSES:
        stmt = stmt.where(McpServerShare.status == status)
    result = await session.execute(
        stmt.offset(max(offset, 0)).limit(max(1, min(limit, MAX_LIST_LIMIT)))
    )
    rows = result.all()

    pending_result = await session.execute(
        select(func.count()).where(
            McpServerShare.target_user_id == user_id,
            McpServerShare.status == "PENDING",
        )
    )
    return {
        "items": [
            _share_payload(
                row, source_user=source, receiver_user_name=user.get("user_name")
            )
            for row, source in rows
        ],
        "pending_count": int(pending_result.scalar() or 0),
    }


async def _server_name_taken(session: AsyncSession, name: str) -> bool:
    result = await session.execute(
        select(McpServer.id)
        .where(func.lower(McpServer.server_name) == name.lower())
        .limit(1)
    )
    return result.first() is not None


async def resolve_accepted_server_name(session: AsyncSession, base_name: str) -> str:
    """接受时给副本取一个全库唯一的名字（``-2`` / ``-3`` …）。

    ``sys_mcp_servers`` 的服务名是**全局唯一**（V108 的大小写不敏感唯一索引），
    而分享的名字通常带原主前缀；接收人已有同名服务时不能直接复用。长度按列宽截断，
    保证加后缀后仍然放得下。
    """
    candidate = (base_name or "").strip() or "mcp-shared"
    candidate = candidate[:MAX_SERVER_NAME_LENGTH]
    if not await _server_name_taken(session, candidate):
        return candidate
    for index in range(2, 100):
        suffix = f"-{index}"
        candidate = f"{(base_name or 'mcp-shared').strip()[: MAX_SERVER_NAME_LENGTH - len(suffix)]}{suffix}"
        if not await _server_name_taken(session, candidate):
            return candidate
    raise McpShareConflictError("同名 MCP 服务过多，请先清理后再接受分享")


async def accept_share(
    session: AsyncSession,
    *,
    user: dict[str, Any],
    share_id: str,
    server_name: str | None = None,
    remark: str | None = None,
    sse_url: str | None = None,
) -> dict[str, Any]:
    """接受分享并安装副本。

    ``server_name`` / ``remark`` / ``sse_url`` 是安装向导里允许用户改的那几项：
    显式传名时冲突**直接报错**（用户自己输的名字不该被悄悄加 ``-2``），
    不传才走「默认名 + 自动加后缀」。
    """
    user_id = _user_id(user)
    row = (
        await session.execute(select(McpServerShare).where(McpServerShare.id == share_id))
    ).scalar_one_or_none()
    if row is None:
        raise McpShareNotFoundError("分享记录不存在")
    if int(row.target_user_id) != user_id:
        raise McpSharePermissionError("这条分享不是发给你的")
    if row.status != "PENDING":
        raise McpShareConflictError("该分享已处理")
    if not row.payload:
        raise McpShareConflictError("分享内容已损坏，请让分享人重新分享")

    try:
        snapshot = json.loads(row.payload)
    except json.JSONDecodeError as exc:
        raise McpShareConflictError("分享内容已损坏，请让分享人重新分享") from exc
    config = snapshot.get("config") or {}
    tools = snapshot.get("tools") or []

    new_id = str(uuid.uuid4())
    if server_name is not None:
        requested = (server_name or "").strip()[:MAX_SERVER_NAME_LENGTH]
        if not requested:
            raise McpShareConflictError("服务名不能为空")
        if await _server_name_taken(session, requested):
            raise McpShareConflictError(f"服务名「{requested}」已存在，请换一个")
        final_name = requested
    else:
        shared_name = config.get("server_name") or row.server_name
        final_name = await resolve_accepted_server_name(
            session,
            suggested_accepted_name(
                shared_name,
                user.get("user_name"),
                config.get("source_user_name"),
            ),
        )

    if sse_url is not None:
        requested_url = (sse_url or "").strip()
        if not requested_url.lower().startswith(("http://", "https://")):
            raise McpShareConflictError("服务地址需以 http:// 或 https:// 开头")
    else:
        requested_url = config.get("sse_url") or row.sse_url

    if remark is not None:
        requested_remark = (remark or "").strip()[:500] or None
    else:
        requested_remark = (
            config.get("remark") if config.get("remark") is not None else row.remark
        )

    server = McpServer(
        id=new_id,
        server_name=final_name,
        remark=requested_remark,
        sse_url=requested_url,
        auth_headers=config.get("auth_headers"),
        credential_mode=config.get("credential_mode") or "static",
        fixed_token_encrypted=config.get("fixed_token_encrypted"),
        user_assertion_enabled=bool(config.get("user_assertion_enabled")),
        user_assertion_header=config.get("user_assertion_header") or "X-Nanzi-User-Assertion",
        user_assertion_audience=config.get("user_assertion_audience"),
        user_assertion_key_id=config.get("user_assertion_key_id"),
        user_assertion_issuer=config.get("user_assertion_issuer"),
        user_assertion_private_key_encrypted=config.get(
            "user_assertion_private_key_encrypted"
        ),
        enabled_status=int(config.get("enabled_status") or 0),
        scope="personal",
        user_id=user_id,
    )
    session.add(server)
    for tool in tools:
        if not tool.get("tool_name"):
            continue
        session.add(
            McpToolCache(
                id=str(uuid.uuid4()),
                server_id=new_id,
                tool_name=str(tool["tool_name"]),
                tool_description=tool.get("tool_description"),
                parameter_schema=tool.get("parameter_schema"),
                is_published=bool(tool.get("is_published")),
                is_available=bool(tool.get("is_available", True)),
            )
        )

    row.status = "ACCEPTED"
    row.accepted_server_id = new_id
    row.responded_at = datetime.now()
    row.payload = None
    await session.flush()

    await _notify(
        session,
        user_id=int(row.source_user_id),
        title="MCP 分享已被接受",
        content=f"你分享的 MCP 服务「{row.server_name}」已被接受，对方已安装到自己的「我的 MCP」。",
        share_id=row.id,
        server_id=row.source_server_id,
        for_receiver=False,
        accepted_server_id=new_id,
        target_tab="sent",
    )
    return {
        "share_id": row.id,
        "accepted_server_id": new_id,
        "server": {
            "id": new_id,
            "server_name": server.server_name,
            "sse_url": server.sse_url,
            "remark": server.remark,
            "tool_count": len([tool for tool in tools if tool.get("tool_name")]),
        },
    }


async def reject_share(
    session: AsyncSession, *, user: dict[str, Any], share_id: str
) -> dict[str, Any]:
    user_id = _user_id(user)
    row = (
        await session.execute(select(McpServerShare).where(McpServerShare.id == share_id))
    ).scalar_one_or_none()
    if row is None:
        raise McpShareNotFoundError("分享记录不存在")
    if int(row.target_user_id) != user_id:
        raise McpSharePermissionError("这条分享不是发给你的")
    if row.status != "PENDING":
        raise McpShareConflictError("该分享已处理")

    row.status = "REJECTED"
    row.responded_at = datetime.now()
    row.payload = None
    await session.flush()
    return _share_payload(row)


# ---------------------------------------------------------------------------
# 菜单角标 / 列表注解
# ---------------------------------------------------------------------------

async def share_menu_counts(session: AsyncSession, *, user: dict[str, Any]) -> dict[str, int]:
    user_id = _user_id(user)

    async def _count(*conditions: Any) -> int:
        result = await session.execute(select(func.count()).where(*conditions))
        return int(result.scalar() or 0)

    return {
        "received_pending": await _count(
            McpServerShare.target_user_id == user_id,
            McpServerShare.status == "PENDING",
        ),
        "sent_pending": await _count(
            McpServerShare.source_user_id == user_id,
            McpServerShare.status == "PENDING",
        ),
        "sent_total": await _count(McpServerShare.source_user_id == user_id),
    }


async def annotate_personal_servers(
    session: AsyncSession, *, user: dict[str, Any]
) -> dict[str, dict]:
    """给「我的 MCP」列表补充 ``shared_from`` 与 ``share_counts``。

    ``shared_from`` 只认「我接受且落地服务仍存在」的来源，避免把「我分享出去被人接受」
    误算成我的服务来源。
    """
    user_id = _user_id(user)
    accepted_result = await session.execute(
        select(McpServerShare).where(
            McpServerShare.target_user_id == user_id,
            McpServerShare.status == "ACCEPTED",
            McpServerShare.accepted_server_id.isnot(None),
        )
    )
    accepted_rows = list(accepted_result.scalars().all())

    source_ids = {int(row.source_user_id) for row in accepted_rows}
    source_users: dict[int, User] = {}
    if source_ids:
        users_result = await session.execute(select(User).where(User.id.in_(source_ids)))
        source_users = {int(item.id): item for item in users_result.scalars().all()}

    shared_from: dict[str, dict] = {}
    for row in accepted_rows:
        owner = source_users.get(int(row.source_user_id))
        shared_from[row.accepted_server_id] = {
            "share_id": row.id,
            "source_user_id": int(row.source_user_id),
            "source_user_name": getattr(owner, "user_name", None),
            "source_real_name": getattr(owner, "real_name", None),
            "shared_at": _iso(row.created_at),
        }

    counts_result = await session.execute(
        select(McpServerShare.source_server_id, func.count())
        .where(
            McpServerShare.source_user_id == user_id,
            McpServerShare.status.in_(("PENDING", "ACCEPTED")),
        )
        .group_by(McpServerShare.source_server_id)
    )
    share_counts = {server_id: int(count) for server_id, count in counts_result.all()}
    return {"shared_from": shared_from, "share_counts": share_counts}
