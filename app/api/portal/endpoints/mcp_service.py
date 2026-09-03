"""MCP 服务台管理 API。

此模块管理 NanZi 作为 MCP Server 的入站能力，不修改 MCP 工具集的出站配置。
"""

from __future__ import annotations

import csv
import io
import time
from datetime import datetime, timedelta
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.models.platform_mcp import (
    McpInboundAuditLog,
    McpOAuthAccessToken,
    McpOAuthClient,
    McpOAuthGrant,
    McpOAuthRefreshToken,
    McpOAuthSecurityAuditLog,
    McpPlatformConfig,
)
from app.models.user import User
from app.services.mcp.platform_mcp import (
    PLATFORM_MCP_METHODS,
    PLATFORM_MCP_NAME,
    get_method_definition,
    is_platform_mcp_capability_enabled,
    platform_mcp,
    platform_mcp_resource_url,
)
from app.services.mcp.platform_config import PlatformMcpConfigService
from app.services.mcp.platform_oauth import (
    DEFAULT_SCOPES,
    PlatformMcpOAuthService,
    generate_client_id_secret,
    hash_secret,
    normalize_scopes,
)
from app.services.permission_service import PermissionService
from app.services.mcp.security_audit import write_security_audit


router = APIRouter()


def require_mcp_service_permission(element: str):
    """服务台统一权限依赖：菜单权限 + 具体元素权限。"""

    async def _check(
        user: dict = Depends(require_api_key),
        db: AsyncSession = Depends(get_db_session),
    ) -> dict:
        if user.get("role") == "admin":
            return user
        service = PermissionService(db)
        user_id = int(user["user_id"])
        if not await service.check_permission(user_id, "menu", "menu:mcp_service"):
            raise HTTPException(status_code=403, detail="Permission required: menu:mcp_service")
        if element and not await service.check_permission(user_id, "element", element):
            raise HTTPException(status_code=403, detail=f"Permission required: {element}")
        return user

    return _check


def _current_user_id(user: dict) -> str:
    """返回当前登录用户的稳定 ID，Client 所有权不使用可变的用户名。"""
    current_user_id = str(user.get("user_id") or "").strip()
    if not current_user_id:
        raise HTTPException(status_code=401, detail="当前登录用户身份无效")
    return current_user_id


async def _get_owned_client(
    db: AsyncSession,
    client_id: str,
    user: dict,
    allow_shared: bool = False,
) -> McpOAuthClient | None:
    """普通用户按所有权查询 Client，若 allow_shared=True 则允许查询全员共享 Client；管理员可在服务台管理全局 Client。"""
    current_user_id = _current_user_id(user)
    if user.get("role") == "admin":
        return (
            await db.execute(
                select(McpOAuthClient).where(McpOAuthClient.client_id == client_id)
            )
        ).scalar_one_or_none()
    conditions = [McpOAuthClient.client_id == client_id]
    if allow_shared:
        conditions.append(
            (McpOAuthClient.created_by == current_user_id) | (McpOAuthClient.is_shared.is_(True))
        )
    else:
        conditions.append(McpOAuthClient.created_by == current_user_id)
    return (
        await db.execute(
            select(McpOAuthClient).where(*conditions)
        )
    ).scalar_one_or_none()


async def _require_element_permission(
    user: dict,
    db: AsyncSession,
    element: str,
) -> None:
    """在同一个管理请求中校验额外的元素权限。"""
    if user.get("role") == "admin":
        return
    if not await PermissionService(db).check_permission(
        int(user["user_id"]),
        "element",
        element,
    ):
        raise HTTPException(status_code=403, detail=f"Permission required: {element}")


class McpServiceConfigUpdate(BaseModel):
    platform_enabled: bool | None = None
    agent_enabled: bool | None = None
    conversation_enabled: bool | None = None
    knowledge_enabled: bool | None = None
    metadata_enabled: bool | None = None
    rate_limit_client_per_minute: int | None = Field(default=None, ge=0, le=100000)
    rate_limit_user_per_minute: int | None = Field(default=None, ge=0, le=100000)


class McpOAuthClientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_name: str = Field(..., min_length=1, max_length=200)
    redirect_uris: list[str] = Field(default_factory=list)
    allowed_grant_types: list[str] = Field(default_factory=lambda: ["authorization_code"])
    allowed_scopes: list[str] = Field(default_factory=list)
    is_shared: bool = False

    @field_validator("redirect_uris")
    @classmethod
    def validate_redirect_uris(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if any("*" in value for value in cleaned):
            raise ValueError("redirect_uris 必须是不含通配符的精确地址")
        return list(dict.fromkeys(cleaned)) or ["https://localhost/oauth/callback"]

    @field_validator("allowed_grant_types")
    @classmethod
    def validate_grant_types(cls, values: list[str]) -> list[str]:
        allowed = {"authorization_code", "refresh_token"}
        cleaned = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not cleaned or set(cleaned) - allowed:
            raise ValueError("allowed_grant_types 包含不支持的值")
        return cleaned

    @field_validator("allowed_scopes")
    @classmethod
    def validate_allowed_scopes(cls, values: list[str]) -> list[str]:
        cleaned = normalize_scopes(values)
        invalid = set(cleaned) - set(DEFAULT_SCOPES)
        if invalid:
            raise ValueError(f"allowed_scopes 包含不支持的 Scope: {sorted(invalid)}")
        return cleaned

    @model_validator(mode="after")
    def validate_grant_requirements(self) -> "McpOAuthClientCreate":
        grants = set(self.allowed_grant_types)
        if "authorization_code" not in grants:
            raise ValueError("Client 必须启用 authorization_code，所有 Token 都必须绑定用户")
        if "authorization_code" in grants and not self.redirect_uris:
            raise ValueError("authorization_code 模式必须配置至少一个 redirect_uri")
        if "refresh_token" in grants and "authorization_code" not in grants:
            raise ValueError("refresh_token 必须和 authorization_code 一起启用")
        return self


class McpAccessTokenCreate(BaseModel):
    """服务台为当前登录用户签发个人 MCP Access Token 的请求。"""

    scopes: list[str] = Field(default_factory=list)
    expires_in: int = Field(default=3600, ge=300, le=2592000)

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, values: list[str]) -> list[str]:
        cleaned = normalize_scopes(values)
        invalid = set(cleaned) - set(DEFAULT_SCOPES)
        if invalid:
            raise ValueError(f"scopes 包含不支持的 Scope: {sorted(invalid)}")
        if not cleaned:
            raise ValueError("至少选择一个 Scope")
        return cleaned


class McpAccessTokenDeleteBatch(BaseModel):
    token_ids: list[str] = Field(min_length=1, max_length=100)


class McpOAuthClientUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_name: str | None = Field(default=None, min_length=1, max_length=200)
    redirect_uris: list[str] | None = None
    allowed_grant_types: list[str] | None = None
    allowed_scopes: list[str] | None = None
    status: str | None = None
    is_shared: bool | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        if value is not None and value not in {"active", "disabled"}:
            raise ValueError("status 只能是 active 或 disabled")
        return value

    @field_validator("redirect_uris")
    @classmethod
    def validate_update_redirect_uris(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        cleaned = [value.strip() for value in values if value.strip()]
        if any("*" in value for value in cleaned):
            raise ValueError("redirect_uris 必须是不含通配符的精确地址")
        return list(dict.fromkeys(cleaned))

    @field_validator("allowed_grant_types")
    @classmethod
    def validate_update_grant_types(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        allowed = {"authorization_code", "refresh_token"}
        cleaned = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not cleaned or set(cleaned) - allowed:
            raise ValueError("allowed_grant_types 包含不支持的值")
        return cleaned

    @field_validator("allowed_scopes")
    @classmethod
    def validate_update_scopes(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        cleaned = normalize_scopes(values)
        invalid = set(cleaned) - set(DEFAULT_SCOPES)
        if invalid:
            raise ValueError(f"allowed_scopes 包含不支持的 Scope: {sorted(invalid)}")
        return cleaned


def _serialize_client(
    client: McpOAuthClient,
    *,
    needs_token_regeneration: bool = False,
    owner_user_name: str | None = None,
    owner_real_name: str | None = None,
    last_token_issued_at: datetime | None = None,
    last_token_issue_method: str | None = None,
    active_token_count: int = 0,
    token_total_count: int = 0,
    expiring_token_count: int = 0,
    expired_token_count: int = 0,
    revoked_token_count: int = 0,
    latest_token_expires_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "id": client.id,
        "client_id": client.client_id,
        "client_name": client.client_name,
        "client_type": client.client_type,
        "redirect_uris": list(client.redirect_uris or []),
        "allowed_grant_types": list(client.allowed_grant_types or []),
        "allowed_scopes": list(client.allowed_scopes or []),
        "scope_version": int(client.scope_version or 1),
        "is_shared": bool(client.is_shared),
        "needs_token_regeneration": needs_token_regeneration,
        "status": client.status,
        "created_by": client.created_by,
        "owner_user_name": owner_user_name,
        "owner_real_name": owner_real_name,
        "has_issued_token": last_token_issued_at is not None,
        "last_token_issued_at": last_token_issued_at.isoformat() if last_token_issued_at else None,
        "last_token_issue_method": last_token_issue_method,
        "active_token_count": active_token_count,
        "token_total_count": token_total_count,
        "expiring_token_count": expiring_token_count,
        "expired_token_count": expired_token_count,
        "revoked_token_count": revoked_token_count,
        "latest_token_expires_at": latest_token_expires_at.isoformat() if latest_token_expires_at else None,
        "created_at": client.created_at.isoformat() if client.created_at else None,
        "updated_at": client.updated_at.isoformat() if client.updated_at else None,
        "disabled_at": client.disabled_at.isoformat() if client.disabled_at else None,
        "client_secret": None,
    }


def _serialize_config(config: McpPlatformConfig | None) -> dict[str, Any]:
    return {
        **PlatformMcpConfigService.to_dict(config),
        "mcp_endpoint": platform_mcp_resource_url(),
    }


def _serialize_audit_log(log: McpInboundAuditLog) -> dict[str, Any]:
    """返回审计页面可展示字段，明确不暴露任何凭证或原始请求头。"""
    return {
        "id": log.id,
        "request_id": log.request_id,
        "client_request_id": log.client_request_id,
        "client_id": log.client_id,
        "user_id": log.user_id,
        "auth_type": log.auth_type,
        "method_name": log.method_name,
        "agent_id": log.agent_id,
        "conversation_id": log.conversation_id,
        "dataset_id": log.dataset_id,
        "scopes": list(log.scopes or []),
        "status_code": log.status_code,
        "result_status": log.result_status,
        "error_code": log.error_code,
        "latency_ms": log.latency_ms,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


def _serialize_security_audit(log: McpOAuthSecurityAuditLog) -> dict[str, Any]:
    return {
        "id": log.id,
        "event_type": log.event_type,
        "request_id": log.request_id,
        "client_id": log.client_id,
        "user_id": log.user_id,
        "actor_user_id": log.actor_user_id,
        "result_status": log.result_status,
        "error_code": log.error_code,
        "details": log.details or {},
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


def _serialize_token(
    token: McpOAuthAccessToken,
    *,
    user_name: str | None = None,
    real_name: str | None = None,
) -> dict[str, Any]:
    return {
        "id": token.id,
        "client_id": token.client_id,
        "user_id": token.user_id,
        "user_name": user_name,
        "real_name": real_name,
        "scopes": list(token.scopes or []),
        "scope_version": int(token.scope_version or 1),
        "issue_method": "oauth_authorization" if token.grant_id else "manual_user_token",
        "issued_at": token.issued_at.isoformat() if token.issued_at else None,
        "expires_at": token.expires_at.isoformat() if token.expires_at else None,
        "revoked_at": token.revoked_at.isoformat() if token.revoked_at else None,
        "status": "revoked" if token.revoked_at else ("expired" if token.expires_at <= datetime.utcnow() else "active"),
    }


def _serialize_grant(grant: McpOAuthGrant, client_name: str | None = None) -> dict[str, Any]:
    return {
        "id": grant.id,
        "client_id": grant.client_id,
        "client_name": client_name or grant.client_id,
        "user_id": grant.user_id,
        "scopes": list(grant.scopes or []),
        "resource": grant.resource,
        "status": grant.status,
        "consented_at": grant.consented_at.isoformat() if grant.consented_at else None,
        "last_used_at": grant.last_used_at.isoformat() if grant.last_used_at else None,
        "revoked_at": grant.revoked_at.isoformat() if grant.revoked_at else None,
        "created_at": grant.created_at.isoformat() if grant.created_at else None,
    }


@router.get("/overview")
async def get_overview(
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:overview:read")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    current_user_id = _current_user_id(user)
    clients = list(
        (
            await db.execute(
                select(McpOAuthClient)
                .where(
                    McpOAuthClient.status != "deleted",
                    *([] if user.get("role") == "admin" else [McpOAuthClient.created_by == current_user_id]),
                )
            )
        )
        .scalars()
        .all()
    )
    config = await PlatformMcpConfigService.get(db)
    return {
        "service_name": PLATFORM_MCP_NAME,
        "mcp_endpoint": platform_mcp_resource_url(),
        "resource": platform_mcp_resource_url(),
        "authorization_server": str(settings.APP_PUBLIC_URL or "http://localhost:8001").rstrip("/"),
        "protected_resource_metadata": f"{str(settings.APP_PUBLIC_URL or 'http://localhost:8001').rstrip('/')}/.well-known/oauth-protected-resource/mcp/platform",
        "authorization_server_metadata": f"{str(settings.APP_PUBLIC_URL or 'http://localhost:8001').rstrip('/')}/.well-known/oauth-authorization-server",
        "jwks_url": None,
        "platform_enabled": bool(config.platform_enabled) if config else False,
        "client_count": len(clients),
        "active_client_count": sum(client.status == "active" for client in clients),
        "published_method_count": sum(item.implemented for item in PLATFORM_MCP_METHODS),
    }


@router.get("/config")
async def get_config(
    _user: dict = Depends(require_mcp_service_permission("element:mcp_service:config:read")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    return _serialize_config(await PlatformMcpConfigService.get(db))


@router.get("/audit/summary")
async def audit_summary(
    range: Literal["24h", "7d", "30d"] = Query(default="24h"),
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:audit:read")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """返回当前用户可见范围内的 MCP 审计调用汇总。"""
    if user.get("role") == "admin":
        filters = []
    else:
        filters = [McpInboundAuditLog.user_id == _current_user_id(user)]

    period_hours = {"24h": 24, "7d": 7 * 24, "30d": 30 * 24}[range]
    filters.append(McpInboundAuditLog.created_at >= datetime.utcnow() - timedelta(hours=period_hours))
    total_calls = int(
        await db.scalar(select(func.count()).select_from(McpInboundAuditLog).where(*filters)) or 0
    )
    completed_calls = int(
        await db.scalar(
            select(func.count())
            .select_from(McpInboundAuditLog)
            .where(*filters, McpInboundAuditLog.result_status == "completed")
        )
        or 0
    )
    failed_or_denied = int(
        await db.scalar(
            select(func.count())
            .select_from(McpInboundAuditLog)
            .where(*filters, McpInboundAuditLog.result_status.in_(["failed", "denied"]))
        )
        or 0
    )
    failed_calls = int(
        await db.scalar(
            select(func.count()).select_from(McpInboundAuditLog)
            .where(*filters, McpInboundAuditLog.result_status == "failed")
        ) or 0
    )
    denied_calls = int(
        await db.scalar(
            select(func.count()).select_from(McpInboundAuditLog)
            .where(*filters, McpInboundAuditLog.result_status == "denied")
        ) or 0
    )
    average_latency = await db.scalar(
        select(func.avg(McpInboundAuditLog.latency_ms)).where(
            *filters, McpInboundAuditLog.latency_ms.is_not(None)
        )
    )
    latency_count = int(
        await db.scalar(
            select(func.count())
            .select_from(McpInboundAuditLog)
            .where(*filters, McpInboundAuditLog.latency_ms.is_not(None))
        )
        or 0
    )
    p95_latency = None
    if latency_count:
        p95_offset = max(0, (latency_count * 95 + 99) // 100 - 1)
        p95_latency = await db.scalar(
            select(McpInboundAuditLog.latency_ms)
            .where(*filters, McpInboundAuditLog.latency_ms.is_not(None))
            .order_by(McpInboundAuditLog.latency_ms.asc())
            .offset(p95_offset)
            .limit(1)
        )
    return {
        "range": range,
        "total_calls": total_calls,
        "success_rate": round(completed_calls / total_calls * 100, 2) if total_calls else 0,
        "failed_or_denied": failed_or_denied,
        "failed_calls": failed_calls,
        "denied_calls": denied_calls,
        "average_latency_ms": round(float(average_latency), 2) if average_latency is not None else None,
        "p95_latency_ms": int(p95_latency) if p95_latency is not None else None,
    }


@router.get("/audit")
async def list_audit(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    client_id: str | None = Query(default=None, max_length=128),
    user_id: str | None = Query(default=None, max_length=64),
    auth_type: str | None = Query(default=None, pattern="^user_delegated$"),
    method_name: str | None = Query(default=None, max_length=128),
    agent_id: str | None = Query(default=None, max_length=128),
    dataset_id: str | None = Query(default=None, max_length=128),
    request_id: str | None = Query(default=None, max_length=128),
    result_status: str | None = Query(default=None, pattern="^(completed|failed|denied)$"),
    status_code: int | None = Query(default=None, ge=100, le=599),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:audit:read")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """分页查询 Platform MCP 入站调用审计，不返回 Token 或原始 Header。"""
    if start_at and end_at and start_at > end_at:
        raise HTTPException(status_code=400, detail="start_at 不能晚于 end_at")

    if user.get("role") == "admin":
        # 管理员可以查看所有用户的入站调用审计；具体元素权限仍由统一依赖校验。
        filters = []
    else:
        # 审计归属以实际调用身份为准，而不是以 Client 创建人或请求筛选参数为准。
        current_user_id = _current_user_id(user)
        filters = [McpInboundAuditLog.user_id == current_user_id]
    exact_filters = {
        "client_id": client_id,
        "user_id": user_id,
        "auth_type": auth_type,
        "method_name": method_name,
        "agent_id": agent_id,
        "dataset_id": dataset_id,
        "request_id": request_id,
        "result_status": result_status,
    }
    for field_name, value in exact_filters.items():
        if value:
            filters.append(getattr(McpInboundAuditLog, field_name) == value.strip())
    if status_code is not None:
        filters.append(McpInboundAuditLog.status_code == status_code)
    if start_at is not None:
        filters.append(McpInboundAuditLog.created_at >= start_at)
    if end_at is not None:
        filters.append(McpInboundAuditLog.created_at <= end_at)

    total = int(
        await db.scalar(
            select(func.count()).select_from(McpInboundAuditLog).where(*filters)
        )
        or 0
    )
    offset = (page - 1) * page_size
    rows = (
        await db.execute(
            select(McpInboundAuditLog)
            .where(*filters)
            .order_by(McpInboundAuditLog.created_at.desc(), McpInboundAuditLog.id.desc())
            .offset(offset)
            .limit(page_size)
        )
    ).scalars().all()
    return {
        "items": [_serialize_audit_log(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": offset + len(rows) < total,
    }


@router.get("/audit/export")
async def export_audit(
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    client_id: str | None = Query(default=None, max_length=128),
    user_id: str | None = Query(default=None, max_length=64),
    auth_type: str | None = Query(default=None, pattern="^user_delegated$"),
    method_name: str | None = Query(default=None, max_length=128),
    agent_id: str | None = Query(default=None, max_length=128),
    dataset_id: str | None = Query(default=None, max_length=128),
    request_id: str | None = Query(default=None, max_length=128),
    result_status: str | None = Query(default=None, pattern="^(completed|failed|denied)$"),
    status_code: int | None = Query(default=None, ge=100, le=599),
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:audit:read")),
    db: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    """导出当前可见范围内的脱敏调用审计 CSV。"""
    if start_at and end_at and start_at > end_at:
        raise HTTPException(status_code=400, detail="start_at 不能晚于 end_at")
    filters = []
    if user.get("role") != "admin":
        filters.append(McpInboundAuditLog.user_id == _current_user_id(user))
    exact_filters = {
        "client_id": client_id,
        "user_id": user_id,
        "auth_type": auth_type,
        "method_name": method_name,
        "agent_id": agent_id,
        "dataset_id": dataset_id,
        "request_id": request_id,
        "result_status": result_status,
    }
    for field_name, value in exact_filters.items():
        if value:
            filters.append(getattr(McpInboundAuditLog, field_name) == value.strip())
    if status_code is not None:
        filters.append(McpInboundAuditLog.status_code == status_code)
    if start_at:
        filters.append(McpInboundAuditLog.created_at >= start_at)
    if end_at:
        filters.append(McpInboundAuditLog.created_at <= end_at)
    rows = (
        await db.execute(
            select(McpInboundAuditLog)
            .where(*filters)
            .order_by(McpInboundAuditLog.created_at.desc())
            .limit(10000)
        )
    ).scalars().all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["时间", "请求 ID", "Client ID", "用户 ID", "方法", "认证方式", "状态", "状态码", "耗时(ms)", "错误码"])
    for row in rows:
        writer.writerow([
            row.created_at.isoformat() if row.created_at else "",
            row.request_id,
            row.client_id,
            row.user_id or "",
            row.method_name,
            row.auth_type,
            row.result_status,
            row.status_code,
            row.latency_ms if row.latency_ms is not None else "",
            row.error_code or "",
        ])
    content = "\ufeff" + output.getvalue()
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=mcp-audit.csv"},
    )


@router.get("/audit/security")
async def list_security_audit(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    event_type: str | None = Query(default=None, max_length=64),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:audit:read")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    if start_at and end_at and start_at > end_at:
        raise HTTPException(status_code=400, detail="start_at 不能晚于 end_at")
    filters = []
    if user.get("role") != "admin":
        filters.append(McpOAuthSecurityAuditLog.user_id == _current_user_id(user))
    if event_type:
        filters.append(McpOAuthSecurityAuditLog.event_type == event_type.strip())
    if start_at:
        filters.append(McpOAuthSecurityAuditLog.created_at >= start_at)
    if end_at:
        filters.append(McpOAuthSecurityAuditLog.created_at <= end_at)
    total = int(await db.scalar(
        select(func.count()).select_from(McpOAuthSecurityAuditLog).where(*filters)
    ) or 0)
    offset = (page - 1) * page_size
    rows = (await db.execute(
        select(McpOAuthSecurityAuditLog)
        .where(*filters)
        .order_by(McpOAuthSecurityAuditLog.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )).scalars().all()
    return {
        "items": [_serialize_security_audit(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": offset + len(rows) < total,
    }


@router.get("/audit/trend")
async def audit_trend(
    range: Literal["24h", "7d", "30d"] = Query(default="24h"),
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:audit:read")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    period_hours = {"24h": 24, "7d": 7 * 24, "30d": 30 * 24}[range]
    filters = [McpInboundAuditLog.created_at >= datetime.utcnow() - timedelta(hours=period_hours)]
    if user.get("role") != "admin":
        filters.append(McpInboundAuditLog.user_id == _current_user_id(user))
    rows = (await db.execute(
        select(McpInboundAuditLog.created_at, McpInboundAuditLog.result_status)
        .where(*filters)
        .order_by(McpInboundAuditLog.created_at.asc())
    )).all()
    use_hour = range == "24h"
    buckets: dict[str, dict[str, int]] = {}
    for created_at, result_status in rows:
        bucket = (
            created_at.replace(minute=0, second=0, microsecond=0)
            if use_hour
            else created_at.replace(hour=0, minute=0, second=0, microsecond=0)
        ).isoformat()
        item = buckets.setdefault(bucket, {"total": 0, "completed": 0, "failed": 0, "denied": 0})
        item["total"] += 1
        if result_status in item:
            item[result_status] += 1
    return {
        "range": range,
        "bucket": "hour" if use_hour else "day",
        "items": [{"at": at, **values} for at, values in sorted(buckets.items())],
    }


@router.get("/audit/security/alerts")
async def security_audit_alerts(
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:audit:read")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """返回最近安全异常的轻量告警摘要，避免引入额外告警表。"""
    filters = [
        McpOAuthSecurityAuditLog.created_at >= datetime.utcnow() - timedelta(minutes=15),
        McpOAuthSecurityAuditLog.result_status.in_(["failed", "denied"]),
    ]
    if user.get("role") != "admin":
        filters.append(McpOAuthSecurityAuditLog.user_id == _current_user_id(user))
    recent_count = int(await db.scalar(select(func.count()).select_from(McpOAuthSecurityAuditLog).where(*filters)) or 0)
    rate_limited_count = int(
        await db.scalar(
            select(func.count()).select_from(McpOAuthSecurityAuditLog)
            .where(*filters, McpOAuthSecurityAuditLog.event_type == "mcp_rate_limited")
        ) or 0
    )
    return {
        "window_minutes": 15,
        "recent_failure_count": recent_count,
        "rate_limited_count": rate_limited_count,
        "alert": recent_count >= 5 or rate_limited_count >= 3,
        "message": "最近 15 分钟安全异常较多，请检查 OAuth 审计日志。" if recent_count >= 5 else None,
    }


@router.patch("/config")
async def update_config(
    payload: McpServiceConfigUpdate,
    user: dict = Depends(require_mcp_service_permission("")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    field_names = (
        "platform_enabled",
        "agent_enabled",
        "conversation_enabled",
        "knowledge_enabled",
        "metadata_enabled",
        "rate_limit_client_per_minute",
        "rate_limit_user_per_minute",
    )
    if payload.platform_enabled is not None or any(
        getattr(payload, field_name) is not None
        for field_name in ("rate_limit_client_per_minute", "rate_limit_user_per_minute")
    ):
        await _require_element_permission(user, db, "element:mcp_service:config:edit")
    if any(
        getattr(payload, field_name) is not None
        for field_name in (
            "agent_enabled",
            "conversation_enabled",
            "knowledge_enabled",
            "metadata_enabled",
        )
    ):
        await _require_element_permission(user, db, "element:mcp_service:capability:manage")
    config = await PlatformMcpConfigService.get_or_create(db)
    for field_name in field_names:
        value = getattr(payload, field_name)
        if value is not None:
            setattr(config, field_name, value)
    config.updated_by = str(user.get("user_name") or user.get("user_id"))
    config.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(config)
    if user.get("role") == "admin" or await PermissionService(db).check_permission(
        int(user["user_id"]),
        "element",
        "element:mcp_service:config:read",
    ):
        return _serialize_config(config)
    return {
        "updated_fields": [
            field_name
            for field_name in field_names
            if getattr(payload, field_name) is not None
        ],
        "mcp_endpoint": platform_mcp_resource_url(),
    }


@router.get("/clients")
async def list_clients(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None, pattern="^(active|disabled)$"),
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:client:read")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    current_user_id = _current_user_id(user)
    filters = [McpOAuthClient.status != "deleted"]
    if user.get("role") != "admin":
        filters.append(
            (McpOAuthClient.created_by == current_user_id) | (McpOAuthClient.is_shared.is_(True))
        )
    if search and search.strip():
        keyword = f"%{search.strip()}%"
        filters.append(
            McpOAuthClient.client_name.ilike(keyword)
            | McpOAuthClient.client_id.ilike(keyword)
            | McpOAuthClient.created_by.ilike(keyword)
        )
    if status:
        filters.append(McpOAuthClient.status == status)
    total = int(await db.scalar(select(func.count()).select_from(McpOAuthClient).where(*filters)) or 0)
    rows = (
        await db.execute(
            select(McpOAuthClient)
            .where(*filters)
            .order_by(McpOAuthClient.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    client_ids = [row.client_id for row in rows]
    latest_tokens: dict[str, McpOAuthAccessToken] = {}
    active_token_counts: dict[str, int] = {}
    token_total_counts: dict[str, int] = {}
    expiring_token_counts: dict[str, int] = {}
    expired_token_counts: dict[str, int] = {}
    revoked_token_counts: dict[str, int] = {}
    latest_token_expiries: dict[str, datetime] = {}
    if client_ids:
        owned_client_ids = [
            row.client_id for row in rows if str(row.created_by or "") == current_user_id
        ]
        visible_token_filters = [McpOAuthAccessToken.client_id.in_(client_ids)]
        if user.get("role") != "admin":
            shared_client_ids = [client_id for client_id in client_ids if client_id not in owned_client_ids]
            visible_token_filters.append(
                or_(
                    McpOAuthAccessToken.client_id.in_(owned_client_ids),
                    and_(
                        McpOAuthAccessToken.client_id.in_(shared_client_ids),
                        McpOAuthAccessToken.user_id == current_user_id,
                    ),
                )
            )
        all_token_rows = (
            await db.execute(
                select(McpOAuthAccessToken)
                .where(*visible_token_filters)
                .order_by(McpOAuthAccessToken.issued_at.desc(), McpOAuthAccessToken.created_at.desc())
            )
        ).scalars().all()
        latest_tokens = {
            row.client_id: row
            for row in reversed(all_token_rows)
        }
        now = datetime.utcnow()
        for token in all_token_rows:
            token_total_counts[token.client_id] = token_total_counts.get(token.client_id, 0) + 1
            if token.revoked_at is not None:
                revoked_token_counts[token.client_id] = revoked_token_counts.get(token.client_id, 0) + 1
            elif token.expires_at <= now:
                expired_token_counts[token.client_id] = expired_token_counts.get(token.client_id, 0) + 1
            else:
                active_token_counts[token.client_id] = active_token_counts.get(token.client_id, 0) + 1
                if token.expires_at <= now + timedelta(hours=24):
                    expiring_token_counts[token.client_id] = expiring_token_counts.get(token.client_id, 0) + 1
            if token.expires_at and token.client_id not in latest_token_expiries:
                latest_token_expiries[token.client_id] = token.expires_at
    owner_ids = {int(row.created_by) for row in rows if str(row.created_by or "").isdigit()}
    owner_rows = (
        await db.execute(select(User).where(User.id.in_(owner_ids)))
    ).scalars().all() if owner_ids else []
    owners = {str(owner.id): owner for owner in owner_rows}
    items = [
        _serialize_client(
            row,
            # Scope、Secret、停用/启用等安全变更都会撤销旧 Token。只要该
            # Client 曾经签发过 Token 且当前没有有效 Token，就需要重新生成；
            # 停用期间不提示，重新启用后再提示；新建但尚未签发 Token
            # 的 Client 不显示重复提示。
            needs_token_regeneration=(
                row.status == "active"
                and
                latest_tokens.get(row.client_id) is not None
                and active_token_counts.get(row.client_id, 0) == 0
            ),
            owner_user_name=owners.get(str(row.created_by)).user_name if owners.get(str(row.created_by)) else None,
            owner_real_name=owners.get(str(row.created_by)).real_name if owners.get(str(row.created_by)) else None,
            last_token_issued_at=latest_tokens.get(row.client_id).issued_at if latest_tokens.get(row.client_id) else None,
            last_token_issue_method=(
                "oauth_authorization" if latest_tokens.get(row.client_id).grant_id else "manual_user_token"
            ) if latest_tokens.get(row.client_id) else None,
            active_token_count=active_token_counts.get(row.client_id, 0),
            token_total_count=token_total_counts.get(row.client_id, 0),
            expiring_token_count=expiring_token_counts.get(row.client_id, 0),
            expired_token_count=expired_token_counts.get(row.client_id, 0),
            revoked_token_count=revoked_token_counts.get(row.client_id, 0),
            latest_token_expires_at=latest_token_expiries.get(row.client_id),
        )
        for row in rows
    ]
    return {"items": items, "page": page, "page_size": page_size, "total": total, "has_more": page * page_size < total}


@router.get("/clients/{client_id}/tokens")
async def list_client_tokens(
    client_id: str,
    include_revoked: bool = Query(default=True),
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:client:read")),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    client = await _get_owned_client(db, client_id, user, allow_shared=True)
    if client is None:
        raise HTTPException(status_code=404, detail="OAuth Client 不存在")
    filters = [McpOAuthAccessToken.client_id == client_id]
    current_user_id = _current_user_id(user)
    if user.get("role") != "admin" and client.created_by != current_user_id:
        filters.append(McpOAuthAccessToken.user_id == current_user_id)
    if not include_revoked:
        filters.extend((McpOAuthAccessToken.revoked_at.is_(None), McpOAuthAccessToken.expires_at > datetime.utcnow()))
    rows = (
        await db.execute(
            select(McpOAuthAccessToken)
            .where(*filters)
            .order_by(McpOAuthAccessToken.issued_at.desc())
        )
    ).scalars().all()
    user_ids = {int(row.user_id) for row in rows if str(row.user_id or "").isdigit()}
    user_rows = (
        await db.execute(select(User).where(User.id.in_(user_ids)))
    ).scalars().all() if user_ids else []
    users = {str(u.id): u for u in user_rows}
    return [
        _serialize_token(
            row,
            user_name=users.get(str(row.user_id)).user_name if users.get(str(row.user_id)) else None,
            real_name=users.get(str(row.user_id)).real_name if users.get(str(row.user_id)) else None,
        )
        for row in rows
    ]


@router.delete("/clients/{client_id}/tokens/{token_id}")
async def delete_client_token(
    client_id: str,
    token_id: str,
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:client:token_issue")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """物理删除指定 Access Token，不影响 OAuth Grant 或 Refresh Token。"""
    client = await _get_owned_client(db, client_id, user, allow_shared=True)
    if client is None:
        raise HTTPException(status_code=404, detail="OAuth Client 不存在")
    token = (
        await db.execute(
            select(McpOAuthAccessToken).where(
                McpOAuthAccessToken.id == token_id,
                McpOAuthAccessToken.client_id == client_id,
            )
        )
    ).scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=404, detail="Access Token 不存在")
    current_user_id = _current_user_id(user)
    if user.get("role") != "admin" and client.created_by != current_user_id and token.user_id != current_user_id:
        raise HTTPException(status_code=403, detail="无权删除其他用户的 Access Token")

    now = datetime.utcnow()
    token_status = "revoked" if token.revoked_at else ("expired" if token.expires_at <= now else "active")
    await db.execute(
        delete(McpOAuthAccessToken).where(
            McpOAuthAccessToken.id == token_id,
            McpOAuthAccessToken.client_id == client_id,
        )
    )
    await db.commit()
    await write_security_audit(
        db,
        event_type="oauth_access_token_deleted",
        client_id=client_id,
        user_id=token.user_id,
        actor_user_id=current_user_id,
        details={"token_id": token_id, "token_status": token_status},
    )
    try:
        await db.commit()
    except Exception:
        await db.rollback()
    return {"token_id": token_id, "status": "deleted"}


@router.post("/clients/{client_id}/tokens/delete")
async def delete_client_tokens(
    client_id: str,
    payload: McpAccessTokenDeleteBatch,
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:client:token_issue")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """批量物理删除选中的 Access Token，不影响 OAuth Grant 或 Refresh Token。"""
    client = await _get_owned_client(db, client_id, user, allow_shared=True)
    if client is None:
        raise HTTPException(status_code=404, detail="OAuth Client 不存在")
    token_ids = list(dict.fromkeys(payload.token_ids))
    filters = [
        McpOAuthAccessToken.client_id == client_id,
        McpOAuthAccessToken.id.in_(token_ids),
    ]
    current_user_id = _current_user_id(user)
    if user.get("role") != "admin" and client.created_by != current_user_id:
        filters.append(McpOAuthAccessToken.user_id == current_user_id)
    tokens = (await db.execute(select(McpOAuthAccessToken).where(*filters))).scalars().all()
    if not tokens:
        return {"client_id": client_id, "status": "deleted", "deleted_count": 0}

    now = datetime.utcnow()
    await db.execute(
        delete(McpOAuthAccessToken).where(
            McpOAuthAccessToken.client_id == client_id,
            McpOAuthAccessToken.id.in_([token.id for token in tokens]),
        )
    )
    await db.commit()
    await write_security_audit(
        db,
        event_type="oauth_access_token_deleted",
        client_id=client_id,
        user_id=None,
        actor_user_id=current_user_id,
        details={
            "token_ids": [token.id for token in tokens],
            "deleted_count": len(tokens),
            "token_statuses": {
                token.id: "revoked" if token.revoked_at else ("expired" if token.expires_at <= now else "active")
                for token in tokens
            },
        },
    )
    try:
        await db.commit()
    except Exception:
        await db.rollback()
    return {"client_id": client_id, "status": "deleted", "deleted_count": len(tokens)}


@router.post("/clients/{client_id}/tokens/{token_id}/revoke")
async def revoke_client_token(
    client_id: str,
    token_id: str,
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:client:token_issue")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    client = await _get_owned_client(db, client_id, user, allow_shared=True)
    if client is None:
        raise HTTPException(status_code=404, detail="OAuth Client 不存在")
    token = (
        await db.execute(
            select(McpOAuthAccessToken).where(
                McpOAuthAccessToken.id == token_id,
                McpOAuthAccessToken.client_id == client_id,
            )
        )
    ).scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=404, detail="Access Token 不存在")
    current_user_id = _current_user_id(user)
    if user.get("role") != "admin" and client.created_by != current_user_id and token.user_id != current_user_id:
        raise HTTPException(status_code=403, detail="无权撤销其他用户的 Access Token")
    if token.revoked_at is None:
        token.revoked_at = datetime.utcnow()
        await db.commit()
        await write_security_audit(
            db,
            event_type="oauth_access_token_revoked_by_admin",
            client_id=client_id,
            user_id=token.user_id,
            actor_user_id=current_user_id,
        )
        await db.commit()
    return {"token_id": token.id, "status": "revoked"}


@router.post("/clients/{client_id}/tokens/revoke-all")
async def revoke_all_client_tokens(
    client_id: str,
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:client:token_issue")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """一键撤销指定 Client 下全部有效 Access Token 与 Refresh Token。"""
    client = await _get_owned_client(db, client_id, user, allow_shared=False)
    if client is None:
        raise HTTPException(status_code=404, detail="OAuth Client 不存在")
    now = datetime.utcnow()
    access_result = await db.execute(
        update(McpOAuthAccessToken)
        .where(
            McpOAuthAccessToken.client_id == client_id,
            McpOAuthAccessToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    refresh_result = await db.execute(
        update(McpOAuthRefreshToken)
        .where(
            McpOAuthRefreshToken.client_id == client_id,
            McpOAuthRefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    await db.commit()
    await write_security_audit(
        db,
        event_type="oauth_all_tokens_revoked",
        client_id=client_id,
        actor_user_id=_current_user_id(user),
        user_id=client.created_by,
        details={
            "revoked_access_tokens": access_result.rowcount,
            "revoked_refresh_tokens": refresh_result.rowcount,
        },
    )
    await db.commit()
    return {
        "client_id": client_id,
        "status": "all_revoked",
        "revoked_count": access_result.rowcount,
    }


@router.get("/grants")
async def list_grants(
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:grant:read")),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """查询外部应用授权关系列表。管理员可查看全局，普通用户仅查看自身的授权。"""
    current_user_id = _current_user_id(user)
    filters = []
    if user.get("role") != "admin":
        filters.append(McpOAuthGrant.user_id == current_user_id)
    grants = (
        await db.execute(
            select(McpOAuthGrant)
            .where(*filters)
            .order_by(McpOAuthGrant.consented_at.desc(), McpOAuthGrant.created_at.desc())
        )
    ).scalars().all()
    client_ids = [g.client_id for g in grants]
    client_map: dict[str, str] = {}
    if client_ids:
        client_rows = (
            await db.execute(
                select(McpOAuthClient.client_id, McpOAuthClient.client_name)
                .where(McpOAuthClient.client_id.in_(client_ids))
            )
        ).all()
        client_map = {row[0]: row[1] for row in client_rows}
    return [_serialize_grant(g, client_name=client_map.get(g.client_id)) for g in grants]


@router.post("/grants/{grant_id}/revoke")
async def revoke_grant(
    grant_id: str,
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:grant:revoke")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """一键撤销外部应用的用户授权关系，并同时级联废除该 Grant 下的所有 Access/Refresh Token。"""
    current_user_id = _current_user_id(user)
    grant = (
        await db.execute(select(McpOAuthGrant).where(McpOAuthGrant.id == grant_id))
    ).scalar_one_or_none()
    if grant is None:
        raise HTTPException(status_code=404, detail="OAuth 授权关系不存在")
    if user.get("role") != "admin" and grant.user_id != current_user_id:
        raise HTTPException(status_code=403, detail="无权操作其他用户的授权关系")
    if grant.status == "active":
        now = datetime.utcnow()
        grant.status = "revoked"
        grant.revoked_at = now
        await db.execute(
            update(McpOAuthAccessToken)
            .where(McpOAuthAccessToken.grant_id == grant_id)
            .values(revoked_at=now)
        )
        await db.execute(
            update(McpOAuthRefreshToken)
            .where(McpOAuthRefreshToken.grant_id == grant_id)
            .values(revoked_at=now)
        )
        await db.commit()
        await write_security_audit(
            db,
            event_type="oauth_grant_revoked",
            client_id=grant.client_id,
            user_id=grant.user_id,
            actor_user_id=current_user_id,
        )
        await db.commit()
    return {"grant_id": grant.id, "status": "revoked"}


@router.post("/clients", status_code=201)
async def create_client(
    payload: McpOAuthClientCreate,
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:client:manage")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    current_user_id = _current_user_id(user)
    try:
        client, client_id, client_secret = await PlatformMcpOAuthService.create_client(
            db,
            client_name=payload.client_name,
            redirect_uris=payload.redirect_uris,
            allowed_scopes=payload.allowed_scopes,
            allowed_grant_types=payload.allowed_grant_types,
            created_by=current_user_id,
            is_shared=payload.is_shared,
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_security_audit(
        db,
        event_type="client_created",
        client_id=client.client_id,
        actor_user_id=current_user_id,
        user_id=current_user_id,
        details={"scope_count": len(payload.allowed_scopes)},
    )
    await db.commit()
    response = _serialize_client(client)
    response["client_id"] = client_id
    response["client_secret"] = client_secret
    response["secret_notice"] = "client_secret 只显示本次，请立即安全保存。"
    return response


@router.patch("/clients/{client_id}")
async def update_client(
    client_id: str,
    payload: McpOAuthClientUpdate,
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:client:manage")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    client = await _get_owned_client(db, client_id, user)
    if client is None:
        raise HTTPException(status_code=404, detail="OAuth Client 不存在")
    if client.status == "deleted":
        raise HTTPException(status_code=400, detail="OAuth Client 已删除，不能继续编辑")
    changes = payload.model_dump(exclude_unset=True)
    if "redirect_uris" in changes:
        uris = changes["redirect_uris"] or []
        changes["redirect_uris"] = list(uris)
    if "allowed_scopes" in changes:
        invalid = set(normalize_scopes(changes["allowed_scopes"])) - set(DEFAULT_SCOPES)
        if invalid:
            raise HTTPException(status_code=400, detail=f"Scope 不支持: {sorted(invalid)}")
        changes["allowed_scopes"] = normalize_scopes(changes["allowed_scopes"])
    scope_changed = (
        "allowed_scopes" in changes
        and normalize_scopes(changes["allowed_scopes"])
        != normalize_scopes(client.allowed_scopes)
    )
    grant_types_changed = (
        "allowed_grant_types" in changes
        and normalize_scopes(changes["allowed_grant_types"])
        != normalize_scopes(client.allowed_grant_types)
    )
    status_changed = "status" in changes and changes["status"] != client.status
    redirect_uris_changed = (
        "redirect_uris" in changes
        and set(changes["redirect_uris"]) != set(client.redirect_uris or [])
    )
    effective_grants = set(
        normalize_scopes(changes.get("allowed_grant_types", client.allowed_grant_types))
    )
    redirect_config_changed = "allowed_grant_types" in changes or "redirect_uris" in changes
    if redirect_config_changed:
        effective_redirect_uris = changes.get("redirect_uris", client.redirect_uris) or []
        if "authorization_code" not in effective_grants:
            raise HTTPException(
                status_code=400,
                detail="Client 必须启用 authorization_code，所有 Token 都必须绑定用户",
            )
        if "authorization_code" in effective_grants and not effective_redirect_uris:
            raise HTTPException(status_code=400, detail="authorization_code 模式必须配置至少一个 redirect_uri")
        if "refresh_token" in effective_grants and "authorization_code" not in effective_grants:
            raise HTTPException(status_code=400, detail="refresh_token 必须和 authorization_code 一起启用")
    if changes.get("status") == "disabled":
        client.disabled_at = datetime.utcnow()
    elif changes.get("status") == "active":
        client.disabled_at = None
    if scope_changed:
        client.scope_version = int(client.scope_version or 1) + 1

    # Client 的授权范围、授权模式或状态发生安全性变化时，旧 Token 和用户
    # 授权关系不能继续沿用旧权限；恢复调用必须重新授权。
    security_changed = scope_changed or grant_types_changed or redirect_uris_changed or status_changed
    if security_changed:
        revoked_at = datetime.utcnow()
        await db.execute(
            update(McpOAuthAccessToken)
            .where(McpOAuthAccessToken.client_id == client_id)
            .values(revoked_at=revoked_at)
        )
        await db.execute(
            update(McpOAuthRefreshToken)
            .where(McpOAuthRefreshToken.client_id == client_id)
            .values(revoked_at=revoked_at)
        )
        await db.execute(
            update(McpOAuthGrant)
            .where(
                McpOAuthGrant.client_id == client_id,
                McpOAuthGrant.status == "active",
            )
            .values(status="revoked", revoked_at=revoked_at)
        )
    for key, value in changes.items():
        setattr(client, key, value)
    await db.commit()
    await write_security_audit(
        db,
        event_type="client_updated",
        client_id=client.client_id,
        actor_user_id=_current_user_id(user),
        user_id=client.created_by,
        details={"changed_fields": sorted(changes)},
    )
    await db.commit()
    return _serialize_client(client)


@router.delete("/clients/{client_id}")
async def delete_client(
    client_id: str,
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:client:manage")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """软删除 Client，并立即撤销其凭证与授权关系。"""
    client = await _get_owned_client(db, client_id, user)
    if client is None:
        raise HTTPException(status_code=404, detail="OAuth Client 不存在")
    if client.status == "deleted":
        raise HTTPException(status_code=400, detail="OAuth Client 已删除")

    deleted_at = datetime.utcnow()
    client.status = "deleted"
    client.disabled_at = deleted_at
    await db.execute(
        update(McpOAuthAccessToken)
        .where(McpOAuthAccessToken.client_id == client_id)
        .values(revoked_at=deleted_at)
    )
    await db.execute(
        update(McpOAuthRefreshToken)
        .where(McpOAuthRefreshToken.client_id == client_id)
        .values(revoked_at=deleted_at)
    )
    await db.execute(
        update(McpOAuthGrant)
        .where(
            McpOAuthGrant.client_id == client_id,
            McpOAuthGrant.status == "active",
        )
        .values(status="revoked", revoked_at=deleted_at)
    )
    await db.commit()
    await write_security_audit(
        db,
        event_type="client_deleted",
        client_id=client.client_id,
        actor_user_id=_current_user_id(user),
        user_id=client.created_by,
    )
    await db.commit()
    return {"client_id": client.client_id, "status": "deleted"}


@router.post("/clients/{client_id}/secret")
async def reset_client_secret(
    client_id: str,
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:client:secret_reset")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    client = await _get_owned_client(db, client_id, user)
    if client is None:
        raise HTTPException(status_code=404, detail="OAuth Client 不存在")
    if client.status == "deleted":
        raise HTTPException(status_code=400, detail="OAuth Client 已删除，不能重置 Secret")
    _, secret = generate_client_id_secret()
    client.client_secret_hash = hash_secret(secret)
    await db.execute(
        update(McpOAuthAccessToken)
        .where(McpOAuthAccessToken.client_id == client_id)
        .values(revoked_at=datetime.utcnow())
    )
    await db.execute(
        update(McpOAuthRefreshToken)
        .where(McpOAuthRefreshToken.client_id == client_id)
        .values(revoked_at=datetime.utcnow())
    )
    await db.commit()
    await write_security_audit(
        db,
        event_type="client_secret_reset",
        client_id=client.client_id,
        actor_user_id=_current_user_id(user),
        user_id=client.created_by,
    )
    await db.commit()
    return {"client_id": client.client_id, "client_secret": secret, "secret_notice": "旧 Secret 已立即失效。"}


@router.post("/clients/{client_id}/user-access-token")
async def create_current_user_access_token(
    client_id: str,
    payload: McpAccessTokenCreate,
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:client:token_issue")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """为当前登录用户签发一个短期个人 Token，不允许指定或代发其他用户身份。"""
    client = await _get_owned_client(db, client_id, user, allow_shared=True)
    if client is None:
        raise HTTPException(status_code=404, detail="OAuth Client 不存在")
    if client.status != "active":
        raise HTTPException(status_code=400, detail="OAuth Client 已停用，不能生成用户 Token")

    requested_scopes = normalize_scopes(payload.scopes)
    allowed_scopes = set(normalize_scopes(client.allowed_scopes))
    if set(requested_scopes) - allowed_scopes:
        raise HTTPException(status_code=400, detail="请求的 Scope 超出当前 Client 允许范围")

    current_user_id = _current_user_id(user)

    try:
        result = await PlatformMcpOAuthService.issue_tokens(
            db,
            client=client,
            scopes=requested_scopes,
            user_id=current_user_id,
            grant_id=None,
            issue_refresh_token=False,
            resource=platform_mcp_resource_url(),
            access_token_ttl_seconds=payload.expires_in,
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result.update(
        {
            "client_id": client.client_id,
            "user_id": current_user_id,
            "user_name": user.get("real_name") or user.get("user_name"),
            "auth_type": "user_delegated",
            "notice": (
                "这是代表当前登录用户的个人 MCP Access Token，只显示本次；"
                "请使用 Authorization: Bearer <access_token> 调用。"
            ),
        }
    )
    return result


@router.get("/methods")
async def list_methods(
    _user: dict = Depends(require_mcp_service_permission("element:mcp_service:capability:read")),
) -> list[dict[str, Any]]:
    return [
        {
            "name": item.name,
            "scope": item.scope,
            "capability_group": item.capability_group,
            "requires_user": item.requires_user,
            "implemented": item.implemented,
            "description": item.description,
            "enabled": await is_platform_mcp_capability_enabled(item.capability_group),
        }
        for item in PLATFORM_MCP_METHODS
    ]


class McpPlaygroundTestRequest(BaseModel):
    method_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    token: str | None = None


@router.post("/playground/test")
async def test_playground_method(
    payload: McpPlaygroundTestRequest,
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:capability:read")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """MCP 在线探针测试端点：在管理端安全发起只读 JSON-RPC 测试，捕获所有错误并以业务结构返回，不导致页面 401 登出。"""
    method_def = get_method_definition(payload.method_name)
    if not method_def:
        return {
            "status": "failed",
            "status_code": 404,
            "error": f"未知的 MCP 方法: {payload.method_name}",
            "latency_ms": 0,
        }

    token_to_use = (payload.token or "").strip()

    if not token_to_use:
        return {
            "status": "failed",
            "status_code": 400,
            "error": "请提供有效的 Bearer Access Token。你可以在「外部 Client」列表中点击「生成 MCP Access Token」，复制后粘贴至此处进行在线调试。",
            "latency_ms": 0,
        }

    start = time.time()
    mcp_app = platform_mcp.streamable_http_app()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_app),
            base_url="http://localhost:8001",
            headers={
                "Authorization": f"Bearer {token_to_use}",
                "Accept": "application/json, text/event-stream",
            },
            timeout=30.0,
        ) as http_client:
            resp = await http_client.post(
                "/platform",
                json={
                    "jsonrpc": "2.0",
                    "id": int(time.time() * 1000),
                    "method": "tools/call",
                    "params": {
                        "name": payload.method_name,
                        "arguments": payload.arguments,
                    },
                },
            )
            latency_ms = int((time.time() - start) * 1000)
            try:
                rpc_data = resp.json()
            except Exception:
                rpc_data = {"raw_text": resp.text}

            is_success = resp.status_code == 200 and not (
                isinstance(rpc_data, dict) and rpc_data.get("error")
            )
            return {
                "status": "success" if is_success else "failed",
                "status_code": resp.status_code,
                "latency_ms": latency_ms,
                "token_masked": token_to_use[:8] + "..." + token_to_use[-6:] if len(token_to_use) > 14 else "***",
                "response": rpc_data,
            }
    except Exception as exc:
        latency_ms = int((time.time() - start) * 1000)
        return {
            "status": "failed",
            "status_code": 500,
            "latency_ms": latency_ms,
            "error": f"探针调用异常: {exc}",
        }


__all__ = ["router"]
