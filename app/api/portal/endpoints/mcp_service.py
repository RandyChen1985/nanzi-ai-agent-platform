"""MCP 服务台管理 API。

此模块管理 NanZi 作为 MCP Server 的入站能力，不修改 MCP 工具集的出站配置。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select, update
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
    McpPlatformConfig,
)
from app.services.mcp.platform_mcp import (
    PLATFORM_MCP_METHODS,
    PLATFORM_MCP_NAME,
    is_platform_mcp_capability_enabled,
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
) -> McpOAuthClient | None:
    """只按当前用户查询 Client；管理员同样不能越过所有权边界。"""
    current_user_id = _current_user_id(user)
    return (
        await db.execute(
            select(McpOAuthClient).where(
                McpOAuthClient.client_id == client_id,
                McpOAuthClient.created_by == current_user_id,
            )
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


class McpOAuthClientCreate(BaseModel):
    client_name: str = Field(..., min_length=1, max_length=200)
    redirect_uris: list[str] = Field(default_factory=list)
    allowed_grant_types: list[str] = Field(default_factory=lambda: ["authorization_code"])
    allowed_scopes: list[str] = Field(default_factory=list)
    allowed_agent_ids: list[str] | None = None
    allowed_knowledge_base_ids: list[str] | None = None
    allowed_metadata_dataset_ids: list[str] | None = None

    @field_validator("redirect_uris")
    @classmethod
    def validate_redirect_uris(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if any("*" in value for value in cleaned):
            raise ValueError("redirect_uris 必须是不含通配符的精确地址")
        return list(dict.fromkeys(cleaned))

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
    expires_in: int = Field(default=3600, ge=300, le=604800)

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


class McpOAuthClientUpdate(BaseModel):
    client_name: str | None = Field(default=None, min_length=1, max_length=200)
    redirect_uris: list[str] | None = None
    allowed_grant_types: list[str] | None = None
    allowed_scopes: list[str] | None = None
    allowed_agent_ids: list[str] | None = None
    allowed_knowledge_base_ids: list[str] | None = None
    allowed_metadata_dataset_ids: list[str] | None = None
    status: str | None = None

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


def _serialize_client(client: McpOAuthClient) -> dict[str, Any]:
    return {
        "id": client.id,
        "client_id": client.client_id,
        "client_name": client.client_name,
        "client_type": client.client_type,
        "redirect_uris": list(client.redirect_uris or []),
        "allowed_grant_types": list(client.allowed_grant_types or []),
        "allowed_scopes": list(client.allowed_scopes or []),
        "allowed_agent_ids": client.allowed_agent_ids,
        "allowed_knowledge_base_ids": client.allowed_knowledge_base_ids,
        "allowed_metadata_dataset_ids": client.allowed_metadata_dataset_ids,
        "status": client.status,
        "created_by": client.created_by,
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


@router.get("/overview")
async def get_overview(
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:overview:read")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    current_user_id = _current_user_id(user)
    clients = list(
        (
            await db.execute(
                select(McpOAuthClient).where(
                    McpOAuthClient.status != "deleted",
                    McpOAuthClient.created_by == current_user_id,
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

    current_user_id = _current_user_id(user)
    owner_client_ids = select(McpOAuthClient.client_id).where(
        McpOAuthClient.created_by == current_user_id
    )
    filters = [McpInboundAuditLog.client_id.in_(owner_client_ids)]
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
    )
    if payload.platform_enabled is not None:
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
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:client:read")),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    current_user_id = _current_user_id(user)
    rows = (
        await db.execute(
            select(McpOAuthClient)
            .where(
                McpOAuthClient.status != "deleted",
                McpOAuthClient.created_by == current_user_id,
            )
            .order_by(McpOAuthClient.created_at.desc())
        )
    ).scalars().all()
    return [_serialize_client(row) for row in rows]


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
            allowed_agent_ids=payload.allowed_agent_ids,
            allowed_knowledge_base_ids=payload.allowed_knowledge_base_ids,
            allowed_metadata_dataset_ids=payload.allowed_metadata_dataset_ids,
            created_by=current_user_id,
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    effective_grants = set(
        normalize_scopes(changes.get("allowed_grant_types", client.allowed_grant_types))
    )
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

    # Client 的授权范围、授权模式或状态发生安全性变化时，旧 Token 和用户
    # 授权关系不能继续沿用旧权限；恢复调用必须重新授权。
    if any(key in changes for key in ("allowed_scopes", "allowed_grant_types", "status")):
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
    return {"client_id": client.client_id, "client_secret": secret, "secret_notice": "旧 Secret 已立即失效。"}


@router.post("/clients/{client_id}/user-access-token")
async def create_current_user_access_token(
    client_id: str,
    payload: McpAccessTokenCreate,
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:client:token_issue")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """为当前登录用户签发一个短期个人 Token，不允许指定或代发其他用户身份。"""
    client = await _get_owned_client(db, client_id, user)
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


__all__ = ["router"]
