"""NanZi Platform MCP 的 OAuth2 授权端点与标准发现文档。"""

from __future__ import annotations

import base64
import html
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Cookie, Depends, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.orm import get_db_session
from app.models.platform_mcp import (
    McpOAuthAccessToken,
    McpOAuthClient,
    McpOAuthRefreshToken,
)
from app.services.auth_service import AuthService
from app.services.mcp.platform_mcp import (
    platform_mcp_resource_url,
)
from app.services.mcp.platform_oauth import (
    DEFAULT_SCOPES,
    PlatformMcpOAuthService,
    hash_secret,
    normalize_scopes,
    redirect_uri_allowed,
)
from app.services.mcp.security_audit import write_security_audit


router = APIRouter()


def _oauth_response(payload: dict[str, Any], status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        payload,
        status_code=status_code,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _oauth_error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    return _oauth_response(
        {"error": error, "error_description": description},
        status_code,
    )


async def _record_oauth_failure(
    db: AsyncSession,
    *,
    error_code: str,
    client_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """记录 OAuth 失败但不保存 Secret、Token 或原始请求头。"""
    try:
        await write_security_audit(
            db,
            event_type="oauth_request_failed",
            client_id=client_id,
            user_id=user_id,
            result_status="failed",
            error_code=error_code,
        )
        await db.commit()
    except Exception:
        await db.rollback()


def _base_url() -> str:
    return str(settings.APP_PUBLIC_URL or "http://localhost:8001").rstrip("/")


def _form_from_body(body: bytes) -> dict[str, str]:
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items() if values}


async def _read_form(request: Request) -> dict[str, str]:
    return _form_from_body(await request.body())


def _basic_credentials(request: Request) -> tuple[str | None, str | None]:
    value = request.headers.get("Authorization", "")
    if not value.lower().startswith("basic "):
        return None, None
    try:
        decoded = base64.b64decode(value[6:].strip()).decode("utf-8")
        client_id, separator, client_secret = decoded.partition(":")
        if not separator:
            return None, None
        return client_id, client_secret
    except (ValueError, UnicodeDecodeError):
        return None, None


async def _authenticate_client(
    request: Request,
    form: dict[str, str],
    db: AsyncSession,
) -> McpOAuthClient | None:
    basic_id, basic_secret = _basic_credentials(request)
    client_id = basic_id or form.get("client_id")
    client_secret = basic_secret if basic_id else form.get("client_secret")
    if not client_id or not client_secret:
        return None
    client = await PlatformMcpOAuthService.get_client(db, client_id)
    if client is None or client.status != "active":
        return None
    if not PlatformMcpOAuthService.verify_client_secret(client, client_secret):
        return None
    return client


def _platform_resource(value: str | None) -> str:
    """在 OAuth 边界只接受 Platform MCP 的规范资源 URI。"""
    resource = platform_mcp_resource_url()
    if value and value != resource:
        raise ValueError("resource does not match Platform MCP")
    return resource


@router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
async def oauth_authorization_server_metadata() -> dict[str, Any]:
    base = _base_url()
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
        "scopes_supported": list(DEFAULT_SCOPES),
    }


@router.get(
    "/.well-known/oauth-protected-resource/mcp/platform",
    include_in_schema=False,
)
@router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
async def oauth_protected_resource_metadata() -> dict[str, Any]:
    return {
        "resource": platform_mcp_resource_url(),
        "authorization_servers": [_base_url()],
        "scopes_supported": list(DEFAULT_SCOPES),
        "bearer_methods_supported": ["header"],
    }


@router.get("/oauth/authorize", response_class=HTMLResponse, include_in_schema=False)
async def authorize_get(
    request: Request,
    response_type: str = "",
    client_id: str = "",
    redirect_uri: str = "",
    scope: str = "",
    state: str | None = None,
    code_challenge: str = "",
    code_challenge_method: str = "",
    resource: str | None = None,
    admin_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db_session),
):
    if response_type != "code" or not client_id or not redirect_uri:
        await _record_oauth_failure(db, error_code="invalid_request", client_id=client_id or None)
        return _oauth_error("invalid_request", "response_type、client_id、redirect_uri 是必填项")
    client = await PlatformMcpOAuthService.get_client(db, client_id)
    if client is None or client.status != "active":
        await _record_oauth_failure(db, error_code="unauthorized_client", client_id=client_id)
        return _oauth_error("unauthorized_client", "OAuth Client 不存在或已禁用", 401)
    if "authorization_code" not in normalize_scopes(client.allowed_grant_types):
        return _oauth_error("unauthorized_client", "OAuth Client 未启用 authorization_code", 403)
    if not redirect_uri_allowed(redirect_uri, client.redirect_uris or []):
        await _record_oauth_failure(db, error_code="invalid_redirect_uri", client_id=client_id)
        return _oauth_error("invalid_request", "redirect_uri 未注册")
    requested_scopes = normalize_scopes(scope)
    allowed_scopes = normalize_scopes(client.allowed_scopes)
    if set(requested_scopes) - set(allowed_scopes):
        await _record_oauth_failure(db, error_code="invalid_scope", client_id=client_id)
        return _oauth_error("invalid_scope", "请求的 Scope 超出 Client 配置")
    if code_challenge_method != "S256" or not code_challenge:
        await _record_oauth_failure(db, error_code="invalid_pkce", client_id=client_id)
        return _oauth_error("invalid_request", "必须使用 PKCE S256")
    try:
        resource_value = _platform_resource(resource)
    except ValueError as exc:
        await _record_oauth_failure(db, error_code="invalid_target", client_id=client_id)
        return _oauth_error("invalid_target", str(exc))

    user = await AuthService.verify_api_key(admin_token, db) if admin_token else None
    if user is None:
        next_url = request.url.path
        if request.url.query:
            next_url = f"{next_url}?{request.url.query}"
        return RedirectResponse(
            url=f"/login?{urlencode({'next': next_url})}",
            status_code=status.HTTP_302_FOUND,
        )

    fields = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(requested_scopes or allowed_scopes),
        "state": state or "",
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "resource": resource_value,
    }
    hidden = "".join(
        f'<input type="hidden" name="{html.escape(key)}" value="{html.escape(value)}">'
        for key, value in fields.items()
    )
    safe_name = html.escape(str(client.client_name))
    safe_user = html.escape(str(user.get("real_name") or user.get("user_name") or user.get("user_id")))
    return HTMLResponse(
        f"""<!doctype html><html lang=zh-CN><meta charset=utf-8>
<title>NanZi 授权</title><body style="font-family: sans-serif;max-width:560px;margin:48px auto">
<h1>授权访问 NanZi Platform MCP</h1>
<p><strong>{safe_name}</strong> 正在请求以 <strong>{safe_user}</strong> 的身份访问 NanZi。</p>
<p>请求范围：<code>{html.escape(fields['scope'])}</code></p>
<form method="post" action="/oauth/authorize">{hidden}
<button name="approve" value="true" type="submit">同意并继续</button>
<button name="approve" value="false" type="submit">拒绝</button>
</form></body></html>""",
    )


@router.post("/oauth/authorize", include_in_schema=False)
async def authorize_post(
    request: Request,
    admin_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db_session),
):
    form = await _read_form(request)
    required = ("client_id", "redirect_uri", "code_challenge", "code_challenge_method")
    if any(not form.get(key) for key in required):
        return _oauth_error("invalid_request", "授权确认参数不完整")
    client = await PlatformMcpOAuthService.get_client(db, form["client_id"])
    user = await AuthService.verify_api_key(admin_token, db) if admin_token else None
    if client is None or client.status != "active" or user is None:
        return _oauth_error("access_denied", "当前登录状态无效", 403)
    if "authorization_code" not in normalize_scopes(client.allowed_grant_types):
        return _oauth_error("unauthorized_client", "OAuth Client 未启用 authorization_code", 403)
    if not redirect_uri_allowed(form["redirect_uri"], client.redirect_uris or []):
        return _oauth_error("invalid_request", "redirect_uri 未注册")
    try:
        resource_value = _platform_resource(form.get("resource"))
    except ValueError as exc:
        return _oauth_error("invalid_target", str(exc))
    if form.get("approve") != "true":
        await write_security_audit(
            db,
            event_type="oauth_authorization_denied",
            client_id=client.client_id,
            user_id=str(user["user_id"]),
            result_status="denied",
        )
        await db.commit()
        params = {"error": "access_denied"}
        if form.get("state"):
            params["state"] = form["state"]
        return RedirectResponse(
            f"{form['redirect_uri']}?{urlencode(params)}",
            status_code=status.HTTP_302_FOUND,
        )

    scopes = normalize_scopes(form.get("scope"))
    allowed = normalize_scopes(client.allowed_scopes)
    if not scopes or set(scopes) - set(allowed):
        return _oauth_error("invalid_scope", "请求的 Scope 不合法")
    try:
        grant = await PlatformMcpOAuthService._get_or_create_grant(
            db,
            client_id=client.client_id,
            user_id=str(user["user_id"]),
            scopes=scopes,
            resource=resource_value,
        )
        code = await PlatformMcpOAuthService.create_authorization_code(
            db,
            client=client,
            user_id=str(user["user_id"]),
            redirect_uri=form["redirect_uri"],
            scopes=scopes,
            code_challenge=form["code_challenge"],
            code_challenge_method=form["code_challenge_method"],
            resource=grant.resource,
        )
        await write_security_audit(
            db,
            event_type="oauth_authorization_approved",
            client_id=client.client_id,
            user_id=str(user["user_id"]),
            details={"scope_count": len(scopes)},
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        await write_security_audit(
            db,
            event_type="oauth_authorization_failed",
            client_id=client.client_id,
            user_id=str(user["user_id"]),
            result_status="failed",
            error_code="invalid_request",
        )
        await db.commit()
        return _oauth_error("invalid_request", str(exc))

    params = {"code": code}
    if form.get("state"):
        params["state"] = form["state"]
    return RedirectResponse(
        f"{form['redirect_uri']}?{urlencode(params)}",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/oauth/token", include_in_schema=False)
async def token(request: Request, db: AsyncSession = Depends(get_db_session)):
    form = await _read_form(request)
    client = await _authenticate_client(request, form, db)
    if client is None:
        await _record_oauth_failure(db, error_code="invalid_client", client_id=form.get("client_id"))
        return _oauth_error("invalid_client", "Client 认证失败", 401)
    grant_type = form.get("grant_type", "")
    if grant_type not in normalize_scopes(client.allowed_grant_types):
        await _record_oauth_failure(db, error_code="unauthorized_client", client_id=client.client_id)
        return _oauth_error("unauthorized_client", "Client 未启用该 grant_type", 403)
    try:
        resource_value = _platform_resource(form.get("resource"))
    except ValueError as exc:
        await _record_oauth_failure(db, error_code="invalid_target", client_id=client.client_id)
        return _oauth_error("invalid_target", str(exc))
    try:
        if grant_type == "authorization_code":
            result = await PlatformMcpOAuthService.exchange_authorization_code(
                db,
                client=client,
                code=form.get("code", ""),
                redirect_uri=form.get("redirect_uri", ""),
                code_verifier=form.get("code_verifier", ""),
                resource=resource_value,
            )
        elif grant_type == "refresh_token":
            result = await PlatformMcpOAuthService.exchange_refresh_token(
                db,
                client=client,
                raw_refresh_token=form.get("refresh_token", ""),
                scopes=form.get("scope"),
                resource=resource_value,
            )
        else:
            return _oauth_error("unsupported_grant_type", "不支持的 grant_type")
        await write_security_audit(
            db,
            event_type=f"oauth_token_{grant_type}",
            client_id=client.client_id,
            user_id=str(result.get("user_id") or "") or None,
        )
        await db.commit()
        return _oauth_response(result, 200)
    except ValueError as exc:
        await db.rollback()
        await write_security_audit(
            db,
            event_type=f"oauth_token_{grant_type}",
            client_id=client.client_id,
            result_status="failed",
            error_code="invalid_grant",
        )
        await db.commit()
        return _oauth_error("invalid_grant", str(exc))


@router.post("/oauth/revoke", include_in_schema=False)
async def revoke(request: Request, db: AsyncSession = Depends(get_db_session)):
    form = await _read_form(request)
    client = await _authenticate_client(request, form, db)
    if client is None:
        await _record_oauth_failure(db, error_code="invalid_client", client_id=form.get("client_id"))
        return _oauth_error("invalid_client", "Client 认证失败", 401)
    token_hash = hash_secret(form.get("token", ""))
    now = datetime.utcnow()
    access = (
        await db.execute(
            select(McpOAuthAccessToken).where(
                McpOAuthAccessToken.token_hash == token_hash,
                McpOAuthAccessToken.client_id == client.client_id,
            )
        )
    ).scalar_one_or_none()
    refresh = (
        await db.execute(
            select(McpOAuthRefreshToken).where(
                McpOAuthRefreshToken.token_hash == token_hash,
                McpOAuthRefreshToken.client_id == client.client_id,
            )
        )
    ).scalar_one_or_none()
    if access is not None:
        access.revoked_at = now
    if refresh is not None:
        refresh.revoked_at = now
    await write_security_audit(
        db,
        event_type="oauth_token_revoked",
        client_id=client.client_id,
        user_id=(access.user_id if access is not None else (refresh.user_id if refresh is not None else None)),
        result_status="completed" if access is not None or refresh is not None else "not_found",
    )
    await db.commit()
    return Response(status_code=200)


__all__ = ["router"]
