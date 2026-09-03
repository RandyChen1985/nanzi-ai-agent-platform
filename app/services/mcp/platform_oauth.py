"""NanZi Platform MCP 的 OAuth2 核心服务与入站身份模型。

这里使用不可逆摘要保存 Client Secret、授权码和 Token；Bearer Token 本身是
高熵随机值，服务端通过数据库状态支持撤销和即时禁用 Client。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from mcp.server.auth.provider import AccessToken
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm import AsyncSessionLocal
from app.models.platform_mcp import (
    McpOAuthAccessToken,
    McpOAuthAuthorizationCode,
    McpOAuthClient,
    McpOAuthGrant,
    McpOAuthRefreshToken,
)
from app.models.user import User


MCP_RESOURCE = "mcp:nanzi-platform"
MCP_RESOURCE_URI_SUFFIX = "/mcp/platform"
DEFAULT_SCOPES = (
    "agent:list",
    "agent:invoke",
    "conversation:continue",
    "knowledge:search",
    "metadata:read",
    "metadata:search",
    "metadata:metrics:read",
)
SUPPORTED_GRANT_TYPES = ("authorization_code", "refresh_token")
ACCESS_TOKEN_TTL_SECONDS = 3600
MIN_ACCESS_TOKEN_TTL_SECONDS = 300
MAX_ACCESS_TOKEN_TTL_SECONDS = 30 * 24 * 3600
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 3600
AUTHORIZATION_CODE_TTL_SECONDS = 300


def utcnow() -> datetime:
    """返回与现有 ORM DateTime 字段兼容的 naive UTC 时间。"""
    return datetime.utcnow()


def hash_secret(value: str) -> str:
    """对随机凭证做稳定 SHA-256 摘要；原文不会写入数据库。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_client_id_secret() -> tuple[str, str]:
    return f"mcp_{secrets.token_urlsafe(18)}", f"mcp_secret_{secrets.token_urlsafe(36)}"


def build_pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_pkce(verifier: str, challenge: str, method: str) -> bool:
    if method != "S256" or not verifier or not challenge:
        return False
    try:
        expected = build_pkce_challenge(verifier)
    except (UnicodeEncodeError, ValueError):
        return False
    return hmac.compare_digest(expected, challenge)


def normalize_scopes(value: str | Sequence[str] | None) -> list[str]:
    if value is None:
        return []
    values = value.split() if isinstance(value, str) else value
    result: list[str] = []
    for item in values:
        scope = str(item).strip()
        if scope and scope not in result:
            result.append(scope)
    return result


def resolve_access_token_ttl(value: int | None) -> int:
    """解析 Access Token 有效期，避免服务台生成永不过期的用户凭证。"""
    if value is None:
        return ACCESS_TOKEN_TTL_SECONDS
    if isinstance(value, bool):
        raise ValueError("access token ttl must be an integer")
    try:
        ttl = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("access token ttl must be an integer") from exc
    if not MIN_ACCESS_TOKEN_TTL_SECONDS <= ttl <= MAX_ACCESS_TOKEN_TTL_SECONDS:
        raise ValueError(
            "access token ttl must be between "
            f"{MIN_ACCESS_TOKEN_TTL_SECONDS} and {MAX_ACCESS_TOKEN_TTL_SECONDS} seconds"
        )
    return ttl


def filter_requested_scopes(
    requested: str | Sequence[str] | None,
    allowed: Iterable[str],
) -> list[str]:
    allowed_set = {str(item).strip() for item in allowed if str(item).strip()}
    return [scope for scope in normalize_scopes(requested) if scope in allowed_set]


def redirect_uri_allowed(redirect_uri: str, registered: Iterable[str]) -> bool:
    """OAuth 回调地址必须精确匹配，禁止通配符和前缀匹配。"""
    return bool(redirect_uri and redirect_uri in {str(item) for item in registered})


def intersect_authorized_ids(
    user_allowed: Iterable[str] | None,
    requested: Iterable[str] | None = None,
) -> list[str]:
    """计算当前用户授权资源与请求范围的交集。"""
    def _clean(values: Iterable[str] | None) -> set[str] | None:
        if values is None:
            return None
        return {str(value).strip() for value in values if str(value).strip()}

    user_set, requested_set = map(_clean, (user_allowed, requested))
    result = user_set
    if requested_set is not None:
        result = requested_set if result is None else result & requested_set
    return sorted(result or set())


@dataclass(frozen=True)
class McpPrincipal:
    """一次 Platform MCP 调用的可信身份，不接受工具参数覆盖。"""

    client_id: str
    user_id: str | None
    scopes: tuple[str, ...]
    resource: str
    auth_type: str
    jti: str | None = None
    user_name: str | None = None
    claims: dict[str, Any] = field(default_factory=dict)
    expires_at: datetime | None = None

    @property
    def is_user_delegated(self) -> bool:
        return self.auth_type == "user_delegated" and self.user_id is not None

    def is_expired(self, now: datetime | None = None) -> bool:
        return bool(self.expires_at and self.expires_at <= (now or utcnow()))


class PlatformAccessToken(AccessToken):
    """扩展 FastMCP AccessToken，携带 subject 与 claims。"""

    subject: str | None = None
    claims: dict[str, Any] = Field(default_factory=dict)


def access_token_to_principal(token: AccessToken) -> McpPrincipal:
    claims = dict(getattr(token, "claims", None) or {})
    subject = getattr(token, "subject", None) or claims.get("user_id")
    exp_dt = None
    if token.expires_at:
        try:
            exp_dt = datetime.fromtimestamp(token.expires_at, tz=timezone.utc).replace(tzinfo=None)
        except Exception:
            exp_dt = None
    return McpPrincipal(
        client_id=token.client_id,
        user_id=str(subject) if subject is not None else None,
        scopes=tuple(token.scopes),
        resource=token.resource or MCP_RESOURCE,
        auth_type="user_delegated",
        user_name=claims.get("user_name"),
        claims=claims,
        expires_at=exp_dt,
    )


class PlatformMcpOAuthService:
    """OAuth Client、授权码和 opaque Bearer Token 的数据库服务。"""

    @staticmethod
    async def get_client(db: AsyncSession, client_id: str) -> McpOAuthClient | None:
        return (
            await db.execute(
                select(McpOAuthClient).where(McpOAuthClient.client_id == client_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    def verify_client_secret(client: McpOAuthClient, client_secret: str | None) -> bool:
        stored = str(client.client_secret_hash or "")
        if client.client_type != "confidential" or not stored or not client_secret:
            return False
        return hmac.compare_digest(stored, hash_secret(client_secret))

    @staticmethod
    async def create_client(
        db: AsyncSession,
        *,
        client_name: str,
        redirect_uris: Sequence[str],
        allowed_scopes: Sequence[str],
        allowed_grant_types: Sequence[str] = ("authorization_code",),
        created_by: str | None = None,
        is_shared: bool = False,
    ) -> tuple[McpOAuthClient, str, str]:
        if not client_name.strip():
            raise ValueError("client_name is required")
        if "authorization_code" not in allowed_grant_types:
            raise ValueError("authorization_code is required for a user-bound Client")
        if "authorization_code" in allowed_grant_types and not redirect_uris:
            raise ValueError("at least one redirect_uri is required for authorization_code")
        if any("*" in uri for uri in redirect_uris):
            raise ValueError("redirect_uri does not support wildcard")
        invalid_grants = set(allowed_grant_types) - set(SUPPORTED_GRANT_TYPES)
        if invalid_grants:
            raise ValueError(f"unsupported grant type: {sorted(invalid_grants)}")

        client_id, client_secret = generate_client_id_secret()
        client = McpOAuthClient(
            id=str(uuid.uuid4()),
            client_id=client_id,
            client_name=client_name.strip(),
            client_type="confidential",
            client_secret_hash=hash_secret(client_secret),
            redirect_uris=list(redirect_uris),
            allowed_grant_types=list(allowed_grant_types),
            allowed_scopes=filter_requested_scopes(allowed_scopes, DEFAULT_SCOPES),
            is_shared=bool(is_shared),
            created_by=created_by,
        )
        db.add(client)
        await db.flush()
        return client, client_id, client_secret

    @staticmethod
    async def issue_tokens(
        db: AsyncSession,
        *,
        client: McpOAuthClient,
        scopes: Sequence[str],
        user_id: str,
        grant_id: str | None = None,
        issue_refresh_token: bool = False,
        resource: str = MCP_RESOURCE,
        access_token_ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        if not str(user_id).strip():
            raise ValueError("user_id is required for a user-bound Access Token")
        now = utcnow()
        access_token_ttl = resolve_access_token_ttl(access_token_ttl_seconds)
        access_token = secrets.token_urlsafe(48)
        access_expires = now + timedelta(seconds=access_token_ttl)
        token_row = McpOAuthAccessToken(
            id=str(uuid.uuid4()),
            jti=str(uuid.uuid4()),
            token_hash=hash_secret(access_token),
            client_id=client.client_id,
            user_id=str(user_id),
            grant_id=grant_id,
            resource=resource,
            scopes=list(scopes),
            scope_version=int(getattr(client, "scope_version", 1) or 1),
            issued_at=now,
            expires_at=access_expires,
        )
        db.add(token_row)

        refresh_token = None
        refresh_expires = None
        if issue_refresh_token and grant_id:
            refresh_token = secrets.token_urlsafe(48)
            refresh_expires = now + timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS)
            db.add(
                McpOAuthRefreshToken(
                    id=str(uuid.uuid4()),
                    token_hash=hash_secret(refresh_token),
                    grant_id=grant_id,
                    client_id=client.client_id,
                    user_id=str(user_id),
                    issued_at=now,
                    expires_at=refresh_expires,
                )
            )

        await db.flush()
        response: dict[str, Any] = {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": access_token_ttl,
            "scope": " ".join(scopes),
        }
        if refresh_token:
            response.update(
                refresh_token=refresh_token,
                refresh_token_expires_in=REFRESH_TOKEN_TTL_SECONDS,
            )
        return response

    @staticmethod
    async def create_authorization_code(
        db: AsyncSession,
        *,
        client: McpOAuthClient,
        user_id: str,
        redirect_uri: str,
        scopes: Sequence[str],
        code_challenge: str,
        code_challenge_method: str = "S256",
        resource: str = MCP_RESOURCE,
    ) -> str:
        if not redirect_uri_allowed(redirect_uri, client.redirect_uris or []):
            raise ValueError("redirect_uri does not match registered URI")
        if code_challenge_method != "S256" or not code_challenge:
            raise ValueError("PKCE S256 is required")

        raw_code = secrets.token_urlsafe(48)
        db.add(
            McpOAuthAuthorizationCode(
                id=str(uuid.uuid4()),
                code_hash=hash_secret(raw_code),
                client_id=client.client_id,
                user_id=str(user_id),
                redirect_uri=redirect_uri,
                resource=resource,
                scopes=list(scopes),
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                expires_at=utcnow() + timedelta(seconds=AUTHORIZATION_CODE_TTL_SECONDS),
            )
        )
        await db.flush()
        return raw_code

    @staticmethod
    async def _get_or_create_grant(
        db: AsyncSession,
        *,
        client_id: str,
        user_id: str,
        scopes: Sequence[str],
        resource: str,
    ) -> McpOAuthGrant:
        grant = (
            await db.execute(
                select(McpOAuthGrant).where(
                    McpOAuthGrant.client_id == client_id,
                    McpOAuthGrant.user_id == str(user_id),
                    McpOAuthGrant.resource == resource,
                )
            )
        ).scalar_one_or_none()
        now = utcnow()
        if grant is None:
            grant = McpOAuthGrant(
                id=str(uuid.uuid4()),
                client_id=client_id,
                user_id=str(user_id),
                scopes=list(scopes),
                resource=resource,
                status="active",
                consented_at=now,
            )
            db.add(grant)
        else:
            grant.scopes = list(scopes)
            grant.status = "active"
            grant.revoked_at = None
            grant.consented_at = now
            grant.updated_at = now
        await db.flush()
        return grant

    @staticmethod
    async def exchange_authorization_code(
        db: AsyncSession,
        *,
        client: McpOAuthClient,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        resource: str = MCP_RESOURCE,
    ) -> dict[str, Any]:
        row = (
            await db.execute(
                select(McpOAuthAuthorizationCode)
                .where(McpOAuthAuthorizationCode.code_hash == hash_secret(code))
                .with_for_update()
            )
        ).scalar_one_or_none()
        now = utcnow()
        if (
            row is None
            or row.client_id != client.client_id
            or row.consumed_at is not None
            or row.expires_at <= now
            or row.redirect_uri != redirect_uri
            or row.resource != resource
            or not verify_pkce(code_verifier, row.code_challenge, row.code_challenge_method)
        ):
            raise ValueError("invalid authorization code")
        row.consumed_at = now
        grant = await PlatformMcpOAuthService._get_or_create_grant(
            db,
            client_id=client.client_id,
            user_id=row.user_id,
            scopes=normalize_scopes(row.scopes),
            resource=resource,
        )
        return await PlatformMcpOAuthService.issue_tokens(
            db,
            client=client,
            scopes=normalize_scopes(row.scopes),
            user_id=row.user_id,
            grant_id=grant.id,
            issue_refresh_token=True,
            resource=resource,
        )

    @staticmethod
    async def exchange_refresh_token(
        db: AsyncSession,
        *,
        client: McpOAuthClient,
        raw_refresh_token: str,
        scopes: Sequence[str] | None = None,
        resource: str = MCP_RESOURCE,
    ) -> dict[str, Any]:
        row = (
            await db.execute(
                select(McpOAuthRefreshToken)
                .where(McpOAuthRefreshToken.token_hash == hash_secret(raw_refresh_token))
                .with_for_update()
            )
        ).scalar_one_or_none()
        now = utcnow()
        if (
            row is None
            or row.client_id != client.client_id
            or row.used_at is not None
            or row.revoked_at is not None
            or row.expires_at <= now
        ):
            raise ValueError("invalid refresh token")
        grant = await db.get(McpOAuthGrant, row.grant_id)
        if grant is None or grant.status != "active" or grant.resource != resource:
            raise ValueError("authorization grant is not active")
        requested = normalize_scopes(scopes)
        grant_scopes = normalize_scopes(grant.scopes)
        effective_scopes = requested or grant_scopes
        if set(effective_scopes) - set(grant_scopes):
            raise ValueError("requested scope exceeds grant")
        row.used_at = now
        row.revoked_at = now
        return await PlatformMcpOAuthService.issue_tokens(
            db,
            client=client,
            scopes=effective_scopes,
            user_id=row.user_id,
            grant_id=grant.id,
            issue_refresh_token=True,
            resource=resource,
        )

    @staticmethod
    async def load_access_token(db: AsyncSession, token: str) -> AccessToken | None:
        token_hash = hash_secret(token)
        row = (
            await db.execute(
                select(McpOAuthAccessToken).where(McpOAuthAccessToken.token_hash == token_hash)
            )
        ).scalar_one_or_none()
        now = utcnow()
        if row is None or row.revoked_at is not None or row.expires_at <= now:
            return None
        # 兼容历史表结构中的 nullable user_id，但不再接受没有用户身份的运行时 Token。
        if not row.user_id:
            return None

        client = await PlatformMcpOAuthService.get_client(db, row.client_id)
        if client is None or client.status != "active":
            return None

        if row.grant_id:
            grant = await db.get(McpOAuthGrant, row.grant_id)
            if grant is None or grant.status != "active":
                return None

        try:
            user = await db.get(User, int(row.user_id))
        except (TypeError, ValueError):
            user = None
        if user is None or user.status != 1:
            return None
        user_name = user.user_name

        exp_dt = row.expires_at
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        expires_at_ts = int(exp_dt.timestamp())

        return PlatformAccessToken(
            token=token,
            client_id=row.client_id,
            scopes=normalize_scopes(row.scopes),
            expires_at=expires_at_ts,
            resource=row.resource,
            subject=row.user_id,
            claims={"jti": row.jti, "user_name": user_name},
        )


class PlatformMcpTokenVerifier:
    """FastMCP Resource Server 使用的 Bearer TokenVerifier。"""

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token or len(token) > 512:
            return None
        async with AsyncSessionLocal() as db:
            return await PlatformMcpOAuthService.load_access_token(db, token)


__all__ = [
    "ACCESS_TOKEN_TTL_SECONDS",
    "MAX_ACCESS_TOKEN_TTL_SECONDS",
    "MIN_ACCESS_TOKEN_TTL_SECONDS",
    "DEFAULT_SCOPES",
    "MCP_RESOURCE",
    "McpPrincipal",
    "PlatformAccessToken",
    "PlatformMcpOAuthService",
    "PlatformMcpTokenVerifier",
    "access_token_to_principal",
    "build_pkce_challenge",
    "filter_requested_scopes",
    "generate_client_id_secret",
    "hash_secret",
    "intersect_authorized_ids",
    "normalize_scopes",
    "redirect_uri_allowed",
    "resolve_access_token_ttl",
    "utcnow",
    "verify_pkce",
]
