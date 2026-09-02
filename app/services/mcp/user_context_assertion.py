"""签发和校验传给自有 MCP 的短期用户身份断言。"""

from __future__ import annotations

import json
import time
import uuid
import base64
from typing import Any, Mapping

import jwt

MAX_CUSTOM_ATTRIBUTES_BYTES = 8 * 1024
DEFAULT_ISSUER = "nanzi-platform"
DEFAULT_LIFETIME_SECONDS = 60

_USER_CONTEXT_FIELDS = ("user_id", "user_name", "real_name", "dept_code", "org_path")
_SENSITIVE_KEY_NAMES = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "private_key",
    "secret",
    "session_token",
    "token",
}
_RESERVED_KEY_NAMES = {
    "agent_id",
    "agent_name",
    "agent_version_id",
    "aud",
    "custom_attributes",
    "exp",
    "iat",
    "iss",
    "jti",
    "request_id",
    "scope",
    "sub",
    "tenant_id",
    "user_context",
    "user_id",
}


def _key_is_blocked(key: str) -> bool:
    normalized = key.strip().casefold()
    return normalized in _SENSITIVE_KEY_NAMES or normalized in _RESERVED_KEY_NAMES


def _filter_custom_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _filter_custom_value(item)
            for key, item in value.items()
            if isinstance(key, str) and key.strip() and not _key_is_blocked(key)
        }
    if isinstance(value, list):
        return [_filter_custom_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _parse_extra_data(raw: Any, allowed_keys: set[str] | None = None) -> dict[str, Any]:
    if raw in (None, ""):
        return {}
    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("extra_data must be a JSON object") from exc
    if not isinstance(value, Mapping):
        raise ValueError("extra_data must be a JSON object")
    filtered = _filter_custom_value(value)
    if allowed_keys is not None:
        filtered = {
            key: item
            for key, item in filtered.items()
            if key in allowed_keys
        }
    serialized = json.dumps(filtered, ensure_ascii=False, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > MAX_CUSTOM_ATTRIBUTES_BYTES:
        raise ValueError("custom_attributes exceeds the maximum size")
    return filtered


def sanitize_user_extra_data(raw: Any) -> dict[str, Any]:
    """清洗用户扩展字段，供内部运行时和 User Assertion 共用。"""
    try:
        return _parse_extra_data(raw)
    except ValueError:
        # 扩展字段是可选上下文；格式错误不应阻断已认证用户的主请求。
        return {}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def issue_user_assertion(
    *,
    user_info: Mapping[str, Any],
    agent_info: Mapping[str, Any],
    audience: str,
    request_id: str,
    private_key: Any,
    key_id: str,
    issuer: str = DEFAULT_ISSUER,
    lifetime_seconds: int = DEFAULT_LIFETIME_SECONDS,
    now: int | None = None,
    custom_attribute_keys: set[str] | None = None,
) -> str:
    """使用 EdDSA 签发一次 MCP 调用对应的短期 UserContext JWS。"""
    if not _text(audience):
        raise ValueError("audience is required")
    if not _text(request_id):
        raise ValueError("request_id is required")
    if lifetime_seconds <= 0:
        raise ValueError("lifetime_seconds must be positive")

    user_id = _text(user_info.get("user_id") or user_info.get("id"))
    if not user_id:
        raise ValueError("authenticated user_id is required")

    user_context = {
        key: text
        for key in _USER_CONTEXT_FIELDS
        if (text := _text(user_info.get(key))) is not None
    }
    user_context["user_id"] = user_id

    agent_id = _text(agent_info.get("agent_id") or agent_info.get("id"))
    agent_version_id = _text(agent_info.get("agent_version_id") or agent_info.get("version_id"))
    if not agent_id:
        raise ValueError("agent_id is required")

    issued_at = int(time.time() if now is None else now)
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": f"nanzi:user:{user_id}",
        "user_context": user_context,
        "custom_attributes": _parse_extra_data(
            user_info.get("extra_data"),
            allowed_keys=custom_attribute_keys,
        ),
        "agent_id": agent_id,
        "request_id": str(request_id),
        "jti": str(uuid.uuid4()),
        "iat": issued_at,
        "exp": issued_at + int(lifetime_seconds),
    }
    if agent_version_id:
        claims["agent_version_id"] = agent_version_id
    agent_name = _text(agent_info.get("agent_name") or agent_info.get("name"))
    if agent_name:
        claims["agent_name"] = agent_name

    return jwt.encode(
        claims,
        private_key,
        algorithm="EdDSA",
        headers={"kid": key_id, "typ": "JWT"},
    )


def verify_user_assertion(
    token: str,
    *,
    public_key: Any,
    issuer: str = DEFAULT_ISSUER,
    audience: str,
) -> dict[str, Any]:
    """验证 MCP UserContext JWS，失败时抛出 PyJWT 的 InvalidTokenError。"""
    claims = jwt.decode(
        token,
        public_key,
        algorithms=["EdDSA"],
        issuer=issuer,
        audience=audience,
        options={
            "require": ["iss", "aud", "sub", "jti", "iat", "exp"],
        },
    )
    user_context = claims.get("user_context")
    user_id = _text(user_context.get("user_id")) if isinstance(user_context, Mapping) else None
    if not user_id:
        raise jwt.InvalidTokenError("user_context.user_id is required")
    if claims.get("sub") != f"nanzi:user:{user_id}":
        raise jwt.InvalidTokenError("user_context.user_id does not match sub")
    if not _text(claims.get("agent_id")):
        raise jwt.InvalidTokenError("agent_id is required")
    if not _text(claims.get("request_id")):
        raise jwt.InvalidTokenError("request_id is required")
    return claims


def public_jwks(*, private_key: Any, key_id: str) -> dict[str, list[dict[str, str]]]:
    """将当前 Ed25519 私钥对应的公钥发布为 MCP 可消费的 JWKS。"""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("MCP UserContext private key must be an Ed25519 key")
    normalized_key_id = _text(key_id)
    if not normalized_key_id:
        raise ValueError("key_id is required")
    raw_public_key = private_key.public_key().public_bytes(
        Encoding.Raw,
        PublicFormat.Raw,
    )
    public_key = base64.urlsafe_b64encode(raw_public_key).rstrip(b"=").decode("ascii")
    return {
        "keys": [{
            "kty": "OKP",
            "crv": "Ed25519",
            "use": "sig",
            "alg": "EdDSA",
            "kid": normalized_key_id,
            "x": public_key,
        }],
    }
