"""凭据中心门户端点。

**路由顺序有讲究**：``/secrets/usage``、``/secrets/picker``、``/secrets/policy``、
``/secrets/prefs``、``/secrets/bindings``、``/secrets/admin/*`` 这些**静态前缀必须
注册在 ``/secrets/{secret_id}`` 之前**。FastAPI 按注册顺序匹配，反了的话
``GET /secrets/usage`` 会被当成 ``secret_id="usage"`` 吞掉——本项目在
``/skills/gallery`` 上已经踩过一次。

**没有一条路径返回凭据明文**：
``credential`` 类型连创建者和管理员都读不到，唯一的出口 ``GET /{id}/value``
只对 ``variable``（租户号、接口地址这类）开放。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_admin, require_api_key
from app.core.orm import get_db_session
from app.models.secret import SecretUserPref
from app.services.secrets import center
from app.services.secrets.injection import denial_text
from app.services.secrets.center import SecretPermissionError, SecretValidationError
from app.services.secrets.policy import (
    ALL_SCENES,
    SCOPE_SYSTEM,
    SCOPE_USER,
    TYPE_CREDENTIAL,
    TYPE_VARIABLE,
)

logger = logging.getLogger(__name__)
router = APIRouter()

#: 凭证类的统一掩码。前端不要自己拼，统一取这个字段。
MASK = "••••••••••••"


# ── 请求 / 响应模型 ────────────────────────────────────────────────────


class CreateSecretRequest(BaseModel):
    secret_key: str = Field(..., description="同一 owner 内唯一的标识，如 jira.prod.token")
    name: str = ""
    value: str = Field(..., description="凭据值；落库即加密，此后任何接口都不再返回")
    secret_type: str = TYPE_CREDENTIAL
    allowed_scenarios: List[str] = Field(default_factory=list)
    allowed_hosts: List[str] = Field(default_factory=list)
    category: Optional[str] = None
    remark: Optional[str] = None
    expires_at: Optional[datetime] = None


class UpdateSecretRequest(BaseModel):
    name: Optional[str] = None
    allowed_scenarios: Optional[List[str]] = None
    allowed_hosts: Optional[List[str]] = None
    category: Optional[str] = None
    remark: Optional[str] = None
    expires_at: Optional[datetime] = None


class RotateRequest(BaseModel):
    value: str
    expires_at: Optional[datetime] = None
    overlap_days: int = Field(default=7, ge=0, le=30)


class BindRequest(BaseModel):
    secret_id: str
    consumer_type: str
    consumer_id: str
    slot: str


class UnbindRequest(BaseModel):
    consumer_type: str
    consumer_id: str
    slot: str


class PrefsRequest(BaseModel):
    allow_script_env: bool


class PolicyRequest(BaseModel):
    allow_script_env: Optional[bool] = None
    allow_external_host: Optional[bool] = None
    max_inject_per_call: Optional[int] = Field(default=None, ge=1, le=20)


class StatusRequest(BaseModel):
    status: str = Field(..., description="active / disabled")


class GrantRequest(BaseModel):
    grantee_type: str = Field(..., description="user / dept")
    grantee_id: str
    expires_at: Optional[datetime] = None


def _iso(value: datetime | None) -> str | None:
    """统一成带 UTC 标记的 ISO 8601。

    库里所有 datetime 都是 ``datetime.utcnow()`` 写的 **naive UTC**。原来输出
    ``"%Y-%m-%d %H:%M:%S"`` 把时区信息抹掉了，前端 ``new Date("2026-11-09T11:47:14")``
    会按**本地时区**解析——UTC+8 下每个时间都早 8 小时。用户 19:47 用过的凭据
    显示成「今天 11:47」，看着像另一件事。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def serialize_secret(row) -> Dict[str, Any]:
    """对外视图。**永不包含** ``value_encrypted`` 或解密后的值。"""
    is_variable = row.secret_type == TYPE_VARIABLE
    return {
        "id": row.id,
        "owner_scope": row.owner_scope,
        "owner_id": row.owner_id or None,
        "secret_key": row.secret_key,
        "name": row.name,
        "secret_type": row.secret_type,
        "category": row.category,
        "remark": row.remark,
        "status": row.status,
        "allowed_scenarios": row.allowed_scenarios or [],
        "allowed_hosts": row.allowed_hosts or [],
        "has_value": bool(row.value_encrypted),
        # 变量类展示前 8 位；凭证类永远是掩码。
        "value_preview": row.value_preview if is_variable else None,
        "value_masked": (row.value_preview or "") if is_variable else MASK,
        "readable": is_variable,
        "expires_at": _iso(row.expires_at),
        "last_rotated_at": _iso(row.last_rotated_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        # 是否处于轮换双写窗口：前端可以提示「旧值仍在生效」
        "rotating_until": _iso(row.previous_expires_at),
    }


async def _usage_stats(db: AsyncSession, secret_ids: list[str]) -> Dict[str, Dict[str, Any]]:
    """按凭据聚合「绑了几个」「最近用过没有」「近 7 天成功/失败」。

    列表页一次要展示几十行，逐行查会变成 N+1；这里两张表各一次 GROUP BY。
    聚合失败直接返回空 dict，让列表照常显示——统计信息缺失不值得让整个页面报错。
    """
    from datetime import datetime, timedelta

    from sqlalchemy import and_, case, func, select

    from app.models.secret import SecretBinding, SecretUsageLog

    if not secret_ids:
        return {}

    stats: Dict[str, Dict[str, Any]] = {
        sid: {"binding_count": 0, "last_used_at": None, "usage_7d": 0, "failed_7d": 0}
        for sid in secret_ids
    }
    try:
        binding_rows = (
            await db.execute(
                select(SecretBinding.secret_id, func.count(SecretBinding.id))
                .where(SecretBinding.secret_id.in_(secret_ids))
                .group_by(SecretBinding.secret_id)
            )
        ).all()
        for secret_id, count in binding_rows:
            stats[secret_id]["binding_count"] = int(count or 0)

        # 库里一律是 naive UTC（模型 default=datetime.utcnow），窗口也必须按 UTC
        # 算，否则「近 7 天」在 UTC+8 下整体偏移 8 小时。
        since = datetime.utcnow() - timedelta(days=7)
        in_window = SecretUsageLog.created_at >= since
        usage_rows = (
            await db.execute(
                select(
                    SecretUsageLog.secret_id,
                    # 「最近一次」是全量口径：两个月没用过也是“用过”。
                    # 只把下面两个计数扣在 7 天窗口内。
                    func.max(SecretUsageLog.created_at),
                    func.sum(case((in_window, 1), else_=0)),
                    func.sum(
                        case(
                            (and_(in_window, SecretUsageLog.result != "success"), 1),
                            else_=0,
                        )
                    ),
                )
                .where(SecretUsageLog.secret_id.in_(secret_ids))
                .group_by(SecretUsageLog.secret_id)
            )
        ).all()
        for secret_id, last_used, total, failed in usage_rows:
            stats[secret_id]["last_used_at"] = _iso(last_used)
            stats[secret_id]["usage_7d"] = int(total or 0)
            stats[secret_id]["failed_7d"] = int(failed or 0)
    except Exception:  # noqa: BLE001 - 统计失败不影响列表
        # 这里曾经是 debug 级别。结果 ``func.case`` 这个非法写法在构造语句时就抛
        # TypeError，异常被静默吞掉，统计永远停在初始值，界面上所有凭据都显示
        # 「从未使用 / 0 次」——而使用记录里明明有几十条。用 warning 打出来，
        # 下次同样的坏写法至少能看见。
        logger.warning(
            "[Secrets] 聚合使用统计失败：列表仍可显示，但「最近使用」会不准确",
            exc_info=True,
        )
    return stats


def _user_ctx(user_info: Dict[str, Any]) -> tuple[str, str]:
    return str(user_info.get("user_id") or ""), str(user_info.get("dept_code") or "")


def _fail(exc: SecretValidationError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# ══════════════════════════════════════════════════════════════════════
# 静态前缀：必须全部排在 /{secret_id} 之前
# ══════════════════════════════════════════════════════════════════════


@router.get("/usage", summary="凭据使用记录")
async def list_usage(
    secret_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _ = _user_ctx(user_info)
    is_admin = str(user_info.get("role") or "") == "admin"
    rows = await center.list_usage(
        db,
        user_id=None if is_admin else user_id,
        secret_id=secret_id,
        limit=limit,
    )
    return {
        "code": 0,
        "data": [
            {
                "id": r.id,
                "secret_id": r.secret_id,
                "secret_key": r.secret_key_snapshot,
                "owner_scope": r.owner_scope,
                "user_id": r.user_id,
                "scene": r.scene,
                "consumer_type": r.consumer_type,
                "consumer_id": r.consumer_id,
                "slot": r.slot,
                "target_host": r.target_host,
                "result": r.result,
                "trace_id": r.trace_id,
                "created_at": _iso(r.created_at),
            }
            for r in rows
        ],
    }


@router.get("/audit", summary="凭据变更记录（谁在什么时候改了什么）")
async def list_audit(
    secret_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """与 ``/usage`` 分开：一个记「谁用了」，一个记「谁改了」。

    普通用户只看自己动过的；管理员不带 ``secret_id`` 时看全局。
    """
    user_id, _ = _user_ctx(user_info)
    is_admin = str(user_info.get("role") or "") == "admin"
    rows = await center.list_audit(
        db,
        secret_id=secret_id,
        actor_id=None if is_admin else user_id,
        limit=limit,
    )
    return {
        "code": 0,
        "data": [
            {
                "id": r.id,
                "secret_id": r.secret_id,
                "secret_key": r.secret_key_snapshot,
                "owner_scope": r.owner_scope,
                "actor_id": r.actor_id,
                "actor_name": r.actor_name,
                "action": r.action,
                "detail": r.detail,
                "created_at": _iso(r.created_at),
            }
            for r in rows
        ],
    }


@router.get("/picker", summary="凭据选择器数据（供绑定入口使用）")
async def picker(
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """返回「我能选的凭据」，按来源分组，**不含任何值**。

    系统级只返回被授权的那几条——未授权的连名字都不出现。
    """
    user_id, dept_code = _user_ctx(user_info)
    rows = await center.list_visible_secrets(db, user_id=user_id, dept_code=dept_code)

    grouped: Dict[str, List[Dict[str, Any]]] = {"mine": [], "shared": []}
    for row in rows:
        is_system = row.owner_scope == SCOPE_SYSTEM
        grouped["shared" if is_system else "mine"].append(
            {
                "id": row.id,
                "secret_key": row.secret_key,
                "name": row.name,
                "secret_type": row.secret_type,
                "category": row.category,
                "status": row.status,
                "allowed_scenarios": row.allowed_scenarios or [],
                "value_masked": (row.value_preview or "") if row.secret_type == TYPE_VARIABLE else MASK,
            }
        )

    return {
        "code": 0,
        "data": {
            "groups": grouped,
            "counts": {"mine": len(grouped["mine"]), "shared": len(grouped["shared"])},
            "scenes": sorted(ALL_SCENES),
        },
    }


@router.get("/policy", summary="平台凭据注入策略")
async def get_policy(
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    from app.services.config_service import ConfigService

    configs = await ConfigService.get_all_from_db()

    def _raw(key: str):
        meta = configs.get(key) if isinstance(configs, dict) else None
        return meta.get("value") if isinstance(meta, dict) else meta

    def _flag(key: str, default: bool) -> bool:
        raw = _raw(key)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    try:
        max_inject = int(_raw("secret_policy_max_inject_per_call") or 3)
    except (TypeError, ValueError):
        max_inject = 3

    return {
        "code": 0,
        "data": {
            "allow_script_env": _flag("secret_policy_allow_script_env", False),
            "allow_external_host": _flag("secret_policy_allow_external_host", True),
            "max_inject_per_call": max_inject,
            "readonly": str(user_info.get("role") or "") != "admin",
        },
    }


@router.patch("/policy", summary="修改平台凭据注入策略（管理员）")
async def patch_policy(
    payload: PolicyRequest,
    admin: Dict[str, Any] = Depends(require_admin),
):
    from app.services.config_service import ConfigService

    updates: Dict[str, str] = {}
    if payload.allow_script_env is not None:
        updates["secret_policy_allow_script_env"] = "1" if payload.allow_script_env else "0"
    if payload.allow_external_host is not None:
        updates["secret_policy_allow_external_host"] = "1" if payload.allow_external_host else "0"
    if payload.max_inject_per_call is not None:
        updates["secret_policy_max_inject_per_call"] = str(payload.max_inject_per_call)
    if not updates:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")

    # update_config_value 自己开 session 并在成功后清掉 60s 的整表缓存，
    # 所以这里不要再传 db，也不要再手动清缓存（策略变更必须立刻生效——
    # 关掉脚本注入不能等一分钟）。
    for key, value in updates.items():
        await ConfigService.update_config_value(
            key,
            value,
            changed_by=str(admin.get("user_id") or "admin"),
            change_reason="凭据注入策略调整",
        )
    logger.warning("[SecretPolicy] 管理员 %s 修改凭据策略: %s", admin.get("user_id"), updates)
    return {"code": 0, "message": "已更新", "data": updates}


@router.get("/prefs", summary="我的凭据注入偏好")
async def get_prefs(
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _ = _user_ctx(user_info)
    pref = await db.get(SecretUserPref, user_id)
    return {
        "code": 0,
        "data": {
            "allow_script_env": bool(pref and pref.allow_script_env),
            "updated_at": _iso(pref.updated_at) if pref else None,
        },
    }


@router.patch("/prefs", summary="修改我的凭据注入偏好")
async def patch_prefs(
    payload: PrefsRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _ = _user_ctx(user_info)
    pref = await db.get(SecretUserPref, user_id)
    if pref is None:
        pref = SecretUserPref(user_id=user_id, allow_script_env=payload.allow_script_env)
        db.add(pref)
    else:
        pref.allow_script_env = payload.allow_script_env
    await db.commit()
    return {"code": 0, "message": "已保存", "data": {"allow_script_env": payload.allow_script_env}}


@router.get("/bindings", summary="绑定情况：按消费者查，或按凭据查")
async def get_bindings(
    consumer_type: str | None = Query(None),
    consumer_id: str | None = Query(None),
    secret_id: str | None = Query(None),
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """两个方向共用一条路由。

    按消费者查（``consumer_type`` + ``consumer_id``）：配置页要知道「这个槽位绑了谁」。
    按凭据查（``secret_id``）：用户要知道「我这条凭据被绑到哪了」——否则只能去每个
    工具、每个 MCP 服务里翻，翻不完。

    两个方向都只返回 ``bound_by_user_id IS NULL OR = 我`` 的记录：别人的个人绑定
    与我无关，返回了反而是泄露。
    """
    from sqlalchemy import select

    from app.models.secret import SecretBinding, UserSecret

    if not secret_id and not (consumer_type and consumer_id):
        raise HTTPException(status_code=400, detail="请提供 secret_id，或 consumer_type + consumer_id")

    stmt = (
        select(SecretBinding, UserSecret)
        .join(UserSecret, UserSecret.id == SecretBinding.secret_id)
    )
    if secret_id:
        stmt = stmt.where(SecretBinding.secret_id == secret_id)
    else:
        stmt = stmt.where(
            SecretBinding.consumer_type == consumer_type,
            SecretBinding.consumer_id == consumer_id,
        )
    rows = (await db.execute(stmt)).all()
    user_id, _ = _user_ctx(user_info)
    return {
        "code": 0,
        "data": [
            {
                "id": binding.id,
                "slot": binding.slot,
                "secret_id": binding.secret_id,
                "secret_key": secret.secret_key,
                "secret_name": secret.name,
                "owner_scope": secret.owner_scope,
                "consumer_type": binding.consumer_type,
                "consumer_id": binding.consumer_id,
                # 别人做的个人绑定不该被看见
                "bound_by_me": binding.bound_by_user_id == user_id,
            }
            for binding, secret in rows
            if binding.bound_by_user_id in (None, user_id)
        ],
    }


class CheckBindingRequest(BaseModel):
    consumer_type: str
    consumer_id: str
    slot: str
    scene: str = "mcp"
    target_host: str | None = None


@router.post("/check-binding", summary="现在这个槽位能取到凭据吗（只校验，不出站）")
async def check_binding(
    payload: CheckBindingRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """把「调用时才会发现」的问题提前到配置时。

    刻意**不发任何出站请求**：只走一遍解析链路，回答「此刻这里取得到值吗、取不到
    是被哪道闸门拦的」。真实探测会把平台变成一个可以打任意内网地址的跳板，
    而 90% 的配置错误（过期、停用、场景不符、域名不在白名单、没授权）在这里就暴露了。

    返回值里**没有明文**，也没有脱敏值——只说能取到还是不能。
    """
    from app.services.secrets.resolver import secret_resolver

    user_id, dept_code = _user_ctx(user_info)
    result = await secret_resolver.resolve(
        user_id=user_id,
        scene=payload.scene,
        consumer_type=payload.consumer_type,
        consumer_id=payload.consumer_id,
        slot=payload.slot,
        target_host=payload.target_host,
        dept_code=dept_code,
    )
    return {
        "code": 0,
        "data": {
            "ok": result.ok,
            "denial": result.denial,
            "reason": None if result.ok else denial_text(result.denial),
            "secret_key": result.secret_key,
        },
    }


@router.get("/platform-targets", summary="我可以给哪些平台资源绑自己的凭据")
async def list_platform_credential_targets(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    kind: str = Query("all", pattern="^(all|mcp|tool)$"),
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """「我的 MCP → 平台服务」的完整数据源：平台 MCP 服务 + 平台 API 工具。

    普通用户看不到平台侧的任何管理界面（MCP 列表和工具列表都是管理员接口），
    所以「每个人用自己的 Token」这件事，用户没有任何入口。这条接口补上它，
    而且只给够用的字段：名称 + 我能绑的槽位 + 我已绑的状态。
    """
    from sqlalchemy import select

    from app.models.mcp import McpServer
    from app.models.secret import SecretBinding, UserSecret
    from app.models.tool import SysApiTool
    from app.services.ai.tools.system_params import credential_param_names
    from app.services.ai.tools.tool_credentials import is_credential_placeholder

    user_id, _ = _user_ctx(user_info)

    # 分页：工具数量可能上百，一次全量返回既慢又白渲染。两类各自分页，
    # ``kind`` 让「我的 MCP」只取 MCP 那部分时不必白拉一遍工具。
    from sqlalchemy import func

    offset = (page - 1) * size
    servers: list[Any] = []
    server_total = 0
    tool_total = 0
    tools: list[Any] = []

    if kind in ("all", "mcp"):
        server_total = int(
            (await db.execute(
                select(func.count()).select_from(McpServer).where(McpServer.scope == "global")
            )).scalar() or 0
        )
        servers = list((await db.execute(
            select(McpServer)
            .where(McpServer.scope == "global")
            .order_by(McpServer.server_name)
            .offset(offset)
            .limit(size)
        )).scalars().all())

    if kind in ("all", "tool"):
        tool_total = int(
            (await db.execute(
                select(func.count()).select_from(SysApiTool).where(SysApiTool.is_active.is_(True))
            )).scalar() or 0
        )
        tools = list((await db.execute(
            select(SysApiTool)
            .where(SysApiTool.is_active.is_(True))
            .order_by(SysApiTool.name)
            .offset(offset)
            .limit(size)
        )).scalars().all())

    rows = (
        await db.execute(
            select(SecretBinding, UserSecret).join(UserSecret, UserSecret.id == SecretBinding.secret_id)
        )
    ).all()
    bindings: Dict[str, Dict[str, Any]] = {}
    for binding, secret in rows:
        # 别人的个人绑定与我无关；系统级绑定（bound_by_user_id 为空）要显示
        if binding.bound_by_user_id not in (None, user_id):
            continue
        bindings[f"{binding.consumer_type}:{binding.consumer_id}:{binding.slot}"] = {
            "secret_id": secret.id,
            "secret_key": secret.secret_key,
            "secret_name": secret.name,
            "owner_scope": secret.owner_scope,
            "bound_by_me": binding.bound_by_user_id == user_id,
        }

    def _binding(consumer_type: str, consumer_id: str, slot: str):
        return bindings.get(f"{consumer_type}:{consumer_id}:{slot}")

    from app.services.mcp.mcp_auth_policy import resolve_mcp_auth_headers

    mcp_items = []
    for server in servers:
        # 可绑槽位 = Authorization + 服务上已配置的其他认证头。
        # 只给 Authorization 的话，管理员给某个服务配了 X-API-Key 之后，用户
        # 在面板里根本看不到那个槽位，也就绑不了自己的值。
        try:
            header_names = [str(name) for name in resolve_mcp_auth_headers(server)]
        except Exception:
            # 解密失败不该让整页打不开；至少 Authorization 还能绑
            logger.warning("MCP 服务 %s 的认证头解析失败", server.id)
            header_names = []
        slots = ["header:Authorization"] + [
            f"header:{name}" for name in header_names if name.lower() != "authorization"
        ]
        mcp_items.append({
            "id": server.id,
            "server_name": server.server_name,
            "remark": getattr(server, "remark", None),
            # 字段叫 fixed_token_encrypted：只判有没有配，不解密
            "has_fixed_token": bool(getattr(server, "fixed_token_encrypted", None)),
            "bindable_slots": slots,
            "bindings": {slot: _binding("mcp_server", str(server.id), slot) for slot in slots},
            # 兼容旧字段：绑定面板最早只认单个 Authorization
            "binding": _binding("mcp_server", str(server.id), "header:Authorization"),
        })

    tool_items = []
    for tool in tools:
        try:
            headers = json.loads(tool.headers or "{}")
        except Exception:
            headers = {}
        if not isinstance(headers, dict):
            headers = {}
        # 只把「留空」或「还是占位符」的头列为可绑：手填了真实值的头，
        # 注入层是**只补空位**，绑了也不会生效，列出来就是骗人。
        bindable = [
            name
            for name, value in headers.items()
            if not str(value or "").strip() or is_credential_placeholder(value)
        ]
        # 参数位：参数定义里标了 source=credential 的，按 HTTP 方法落到 query / body。
        # 这些槽位和 header 一样能被个人绑定，漏掉它们的话用户在面板里看不到、
        # 也就绑不了——工具会一直报「凭据未填」。
        param_prefix = "query" if str(tool.method or "GET").upper() == "GET" else "body"
        try:
            param_names = credential_param_names(tool.parameter_schema)
        except Exception:
            # 一条工具的参数定义是坏 JSON，不该让整个「平台服务」页打不开
            logger.warning("工具 %s 的参数定义无法解析，跳过参数位", tool.id)
            param_names = []
        param_slots = [f"{param_prefix}:{name}" for name in param_names]
        slots = [f"header:{name}" for name in bindable] + param_slots
        tool_items.append({
            "id": tool.id,
            "name": tool.name,
            "description": tool.description,
            "bindable_slots": slots,
            "bindings": {
                slot: _binding("api_tool", str(tool.id), slot)
                for slot in slots
            },
        })

    return {
        "code": 0,
        "data": {
            "mcp_servers": mcp_items,
            "api_tools": tool_items,
            "page": page,
            "size": size,
            "total": {"mcp_servers": server_total, "api_tools": tool_total},
            "has_more": {
                "mcp_servers": offset + len(mcp_items) < server_total,
                "api_tools": offset + len(tool_items) < tool_total,
            },
        },
    }


@router.get("/mcp-targets", summary="我可以给哪些平台 MCP 服务绑自己的凭据")
async def list_mcp_credential_targets(
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """只取 MCP 那部分的窄接口，``/platform-targets`` 的旧形态。

    保留它是因为「我的 MCP」页只想显示 MCP，拿一份包含 API 工具的响应再筛一遍
    没有意义；两份实现的绑定查询都收敛在 ``/platform-targets`` 里，这里只做投影。
    """
    payload = await list_platform_credential_targets(
        page=1, size=200, kind="mcp", user_info=user_info, db=db
    )
    return {"code": 0, "data": payload["data"]["mcp_servers"]}


@router.post("/bindings", summary="把一个槽位绑到凭据上")
async def create_binding(
    payload: BindRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, dept_code = _user_ctx(user_info)
    try:
        binding = await center.bind_secret(
            db,
            secret_id=payload.secret_id,
            consumer_type=payload.consumer_type,
            consumer_id=payload.consumer_id,
            slot=payload.slot,
            bound_by_user_id=user_id,
            dept_code=dept_code,
        )
    except SecretPermissionError:
        raise HTTPException(status_code=404, detail="凭据不存在")
    except SecretValidationError as exc:
        raise _fail(exc)
    return {"code": 0, "message": "已绑定", "data": {"id": binding.id, "slot": binding.slot}}


@router.delete("/bindings", summary="解绑一个槽位")
async def delete_binding(
    payload: UnbindRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _ = _user_ctx(user_info)
    removed = await center.unbind_slot(
        db,
        consumer_type=payload.consumer_type,
        consumer_id=payload.consumer_id,
        slot=payload.slot,
        bound_by_user_id=user_id,
    )
    return {"code": 0, "message": "已解绑", "data": {"removed": removed}}


# ── 管理员：系统级凭据 ─────────────────────────────────────────────────


@router.post("/admin", summary="创建系统级凭据（管理员）")
async def admin_create(
    payload: CreateSecretRequest,
    admin: Dict[str, Any] = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        row = await center.create_secret(
            db,
            owner_scope=SCOPE_SYSTEM,
            owner_id="",
            secret_key=payload.secret_key,
            name=payload.name,
            value=payload.value,
            secret_type=payload.secret_type,
            allowed_scenarios=payload.allowed_scenarios,
            allowed_hosts=payload.allowed_hosts,
            category=payload.category,
            remark=payload.remark,
            expires_at=payload.expires_at,
            created_by=str(admin.get("user_id") or ""),
        )
    except SecretValidationError as exc:
        raise _fail(exc)
    return {"code": 0, "message": "已创建", "data": serialize_secret(row)}


@router.get("/admin", summary="系统级凭据列表（管理员）")
async def admin_list(
    admin: Dict[str, Any] = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    from sqlalchemy import select

    from app.models.secret import SecretBinding, SecretGrant, UserSecret

    rows = (
        (
            await db.execute(
                select(UserSecret)
                .where(UserSecret.owner_scope == SCOPE_SYSTEM)
                .order_by(UserSecret.secret_key)
            )
        )
        .scalars()
        .all()
    )
    grants = (await db.execute(select(SecretGrant))).scalars().all()
    by_secret: Dict[str, List[SecretGrant]] = {}
    for grant in grants:
        by_secret.setdefault(grant.secret_id, []).append(grant)

    # 管理员要能判断这条凭据「到底在跑还是摆着」：绑到哪了、用过没有。
    # 和个人列表共用同一套聚合，避免列表页逐行回查变成 N+1。
    secret_ids = [row.id for row in rows]
    stats = await _usage_stats(db, secret_ids)
    bindings = (
        (
            await db.execute(
                select(SecretBinding).where(SecretBinding.secret_id.in_(secret_ids))
            )
        )
        .scalars()
        .all()
        if secret_ids
        else []
    )
    by_binding: Dict[str, List[SecretBinding]] = {}
    for binding in bindings:
        by_binding.setdefault(binding.secret_id, []).append(binding)

    return {
        "code": 0,
        "data": [
            {
                **serialize_secret(row),
                **stats.get(row.id, {}),
                "bindings": [
                    {
                        "id": b.id,
                        "slot": b.slot,
                        "consumer_type": b.consumer_type,
                        "consumer_id": b.consumer_id,
                        "bound_by_user_id": b.bound_by_user_id,
                    }
                    for b in by_binding.get(row.id, [])
                ],
                "grants": [
                    {
                        "id": g.id,
                        "grantee_type": g.grantee_type,
                        "grantee_id": g.grantee_id,
                        "expires_at": _iso(g.expires_at),
                    }
                    for g in by_secret.get(row.id, [])
                ],
                "grant_count": len(by_secret.get(row.id, [])),
            }
            for row in rows
        ],
    }


@router.post("/admin/{secret_id}/grants", summary="授权系统级凭据（管理员）")
async def admin_grant(
    secret_id: str,
    payload: GrantRequest,
    admin: Dict[str, Any] = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        grant = await center.grant_secret(
            db,
            secret_id=secret_id,
            grantee_type=payload.grantee_type,
            grantee_id=payload.grantee_id,
            granted_by=str(admin.get("user_id") or ""),
            expires_at=payload.expires_at,
        )
    except SecretValidationError as exc:
        raise _fail(exc)
    return {"code": 0, "message": "已授权", "data": {"id": grant.id}}


@router.delete("/admin/{secret_id}/grants", summary="撤销授权（管理员）")
async def admin_revoke(
    secret_id: str,
    grantee_type: str = Query(...),
    grantee_id: str = Query(...),
    admin: Dict[str, Any] = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    removed = await center.revoke_grant(
        db, secret_id=secret_id, grantee_type=grantee_type, grantee_id=grantee_id
    )
    return {"code": 0, "message": "已撤销", "data": {"removed": removed}}


@router.post("/admin/{secret_id}/disable", summary="强制停用系统级凭据（管理员）")
async def admin_disable(
    secret_id: str,
    admin: Dict[str, Any] = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    from app.models.secret import UserSecret

    row = await db.get(UserSecret, secret_id)
    if row is None:
        raise HTTPException(status_code=404, detail="凭据不存在")
    if row.owner_scope != SCOPE_SYSTEM:
        # 管理员只能强制停用系统级凭据；个人凭据属于用户自己的资产，
        # 但「疑似泄漏」时管理员仍可通过滥用告警要求用户自行停用。
        raise HTTPException(status_code=403, detail="只能强制停用系统级凭据")
    try:
        row = await center.set_secret_status(
            db, secret_id=secret_id, status_value="disabled", actor_id=None
        )
    except SecretValidationError as exc:
        raise _fail(exc)
    return {"code": 0, "message": "已停用", "data": serialize_secret(row)}


@router.post("/admin/{secret_id}/status", summary="停用 / 恢复系统级凭据（管理员）")
async def admin_set_status(
    secret_id: str,
    payload: StatusRequest,
    admin: Dict[str, Any] = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """系统级凭据的状态开关。

    只有 ``disable`` 是不够的：误停用一条全体共用的凭据之后，平台里没有任何
    路径能把它恢复，只能重建——而重建意味着所有绑定都要重新做一遍。停用必须
    可逆，否则管理员不敢用它止血，最后反而不敢停。
    """
    from app.models.secret import UserSecret

    row = await db.get(UserSecret, secret_id)
    if row is None:
        raise HTTPException(status_code=404, detail="凭据不存在")
    if row.owner_scope != SCOPE_SYSTEM:
        raise HTTPException(status_code=403, detail="只能操作系统级凭据")
    if payload.status not in {"active", "disabled"}:
        raise HTTPException(status_code=400, detail="status 只能是 active 或 disabled")
    try:
        row = await center.set_secret_status(
            db, secret_id=secret_id, status_value=payload.status, actor_id=None
        )
    except SecretValidationError as exc:
        raise _fail(exc)
    return {"code": 0, "message": "已保存", "data": serialize_secret(row)}


# ══════════════════════════════════════════════════════════════════════
# 列表 / 创建 / 详情：/{secret_id} 必须最后注册
# ══════════════════════════════════════════════════════════════════════


@router.get("", summary="我可见的凭据列表")
async def list_secrets(
    scope: str = Query(default="all", pattern="^(all|mine|shared)$"),
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, dept_code = _user_ctx(user_info)
    rows = await center.list_visible_secrets(
        db, user_id=user_id, dept_code=dept_code, include_system=scope != "mine"
    )
    if scope == "shared":
        rows = [row for row in rows if row.owner_scope == SCOPE_SYSTEM]
    elif scope == "mine":
        rows = [row for row in rows if row.owner_scope == SCOPE_USER]

    items = [serialize_secret(row) for row in rows]
    stats = await _usage_stats(db, [row.id for row in rows])
    for item in items:
        item.update(stats.get(item["id"], {}))

    return {
        "code": 0,
        "data": {
            "items": items,
            "total": len(rows),
        },
    }


@router.post("", summary="创建个人凭据")
async def create_secret(
    payload: CreateSecretRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _ = _user_ctx(user_info)
    try:
        row = await center.create_secret(
            db,
            owner_scope=SCOPE_USER,
            owner_id=user_id,
            secret_key=payload.secret_key,
            name=payload.name,
            value=payload.value,
            secret_type=payload.secret_type,
            allowed_scenarios=payload.allowed_scenarios,
            allowed_hosts=payload.allowed_hosts,
            category=payload.category,
            remark=payload.remark,
            expires_at=payload.expires_at,
            created_by=user_id,
        )
    except SecretValidationError as exc:
        raise _fail(exc)
    return {"code": 0, "message": "已创建", "data": serialize_secret(row)}


@router.get("/{secret_id}", summary="凭据详情（不含明文）")
async def get_secret(
    secret_id: str,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    from app.models.secret import UserSecret

    row = await db.get(UserSecret, secret_id)
    if row is None:
        raise HTTPException(status_code=404, detail="凭据不存在")

    user_id, dept_code = _user_ctx(user_info)
    visible = await center.list_visible_secrets(db, user_id=user_id, dept_code=dept_code)
    if row.id not in {item.id for item in visible}:
        # 不可见就当作不存在：不要泄露「有这么一条凭据」的事实
        raise HTTPException(status_code=404, detail="凭据不存在")

    bindings = await center.list_bindings(db, secret_id=secret_id)
    return {
        "code": 0,
        "data": {
            **serialize_secret(row),
            "bindings": [
                {
                    "id": b.id,
                    "consumer_type": b.consumer_type,
                    "consumer_id": b.consumer_id,
                    "slot": b.slot,
                    "bound_by_me": b.bound_by_user_id == user_id,
                }
                for b in bindings
            ],
        },
    }


@router.patch("/{secret_id}", summary="修改凭据元数据")
async def update_secret(
    secret_id: str,
    payload: UpdateSecretRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _ = _user_ctx(user_info)
    try:
        row = await center.update_secret(
            db,
            secret_id=secret_id,
            actor_id=user_id,
            name=payload.name,
            allowed_scenarios=payload.allowed_scenarios,
            allowed_hosts=payload.allowed_hosts,
            category=payload.category,
            remark=payload.remark,
            expires_at=payload.expires_at,
            actor_is_admin=str(user_info.get("role") or "") == "admin",
        )
    except SecretPermissionError:
        raise HTTPException(status_code=404, detail="凭据不存在")
    except SecretValidationError as exc:
        raise _fail(exc)
    return {"code": 0, "message": "已保存", "data": serialize_secret(row)}


@router.delete("/{secret_id}", summary="删除凭据")
async def delete_secret(
    secret_id: str,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _ = _user_ctx(user_info)
    try:
        await center.delete_secret(
            db,
            secret_id=secret_id,
            actor_id=user_id,
            actor_is_admin=str(user_info.get("role") or "") == "admin",
        )
    except SecretPermissionError:
        # 看不见就是不存在：越权一律 404，别把「这条存在」告诉对方
        raise HTTPException(status_code=404, detail="凭据不存在")
    except SecretValidationError as exc:
        raise _fail(exc)
    return {"code": 0, "message": "已删除"}


@router.post("/{secret_id}/rotate", summary="轮换凭据值")
async def rotate_secret(
    secret_id: str,
    payload: RotateRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _ = _user_ctx(user_info)
    try:
        row = await center.rotate_secret(
            db,
            secret_id=secret_id,
            new_value=payload.value,
            rotated_by=user_id,
            overlap_days=payload.overlap_days,
            expires_at=payload.expires_at,
            actor_is_admin=str(user_info.get("role") or "") == "admin",
        )
    except SecretPermissionError:
        raise HTTPException(status_code=404, detail="凭据不存在")
    except SecretValidationError as exc:
        raise _fail(exc)
    return {"code": 0, "message": "已轮换", "data": serialize_secret(row)}


@router.post("/{secret_id}/status", summary="停用 / 启用我自己的凭据")
async def set_status(
    secret_id: str,
    payload: StatusRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """用户自助止血。

    凭据疑似泄露时，第一反应必须是「立刻停掉」而不是「等管理员」——从发现到
    管理员响应之间的每一次调用都是真实的泄露窗口。系统级凭据不走这里：它由
    管理员统一维护，个人停用会让其他人莫名其妙地失效。
    """
    from app.models.secret import UserSecret

    user_id, _ = _user_ctx(user_info)
    row = await db.get(UserSecret, secret_id)
    if row is None or row.owner_id != user_id or row.owner_scope != SCOPE_USER:
        # 不可见就当作不存在：不要泄露「有这么一条凭据」的事实
        raise HTTPException(status_code=404, detail="凭据不存在")
    if payload.status not in {"active", "disabled"}:
        raise HTTPException(status_code=400, detail="status 只能是 active 或 disabled")
    try:
        row = await center.set_secret_status(
            db, secret_id=secret_id, status_value=payload.status, actor_id=user_id
        )
    except SecretValidationError as exc:
        raise _fail(exc)
    return {"code": 0, "message": "已保存", "data": serialize_secret(row)}


@router.get("/{secret_id}/value", summary="读取变量类明文")
async def get_value(
    secret_id: str,
    request: Request,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """只对 ``variable`` 开放。``credential`` 在这里被拒绝——设计上就没有这个能力。"""
    user_id, dept_code = _user_ctx(user_info)
    try:
        plaintext = await center.read_variable_value(
            db,
            secret_id=secret_id,
            actor_id=user_id,
            dept_code=dept_code,
            trace_id=getattr(getattr(request, "state", None), "trace_id", None),
        )
    except SecretPermissionError:
        raise HTTPException(status_code=404, detail="凭据不存在")
    except SecretValidationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return {"code": 0, "data": {"value": plaintext}}
