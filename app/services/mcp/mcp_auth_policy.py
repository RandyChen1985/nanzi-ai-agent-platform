"""MCP 出站认证策略：兼容静态 Header，并按配置附加用户断言。"""

from __future__ import annotations

import json
import uuid
from typing import Any, Mapping

from app.utils.encryption import get_api_key_manager
from app.services.mcp.user_context_assertion import issue_user_assertion


DEFAULT_ASSERTION_HEADER = "X-Nanzi-User-Assertion"
MCP_AUTH_HEADERS_PREFIX = "enc:v1:"


def generate_mcp_private_key_pem() -> str:
    """为一个 MCP 实例生成独立的 Ed25519 PKCS8 PEM 私钥。"""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    return Ed25519PrivateKey.generate().private_bytes(
        Encoding.PEM,
        PrivateFormat.PKCS8,
        NoEncryption(),
    ).decode("utf-8")


def _parse_auth_headers(raw: Any) -> dict[str, str]:
    if not raw:
        return {}
    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("MCP auth_headers must be a JSON object") from exc
    if not isinstance(value, Mapping):
        raise ValueError("MCP auth_headers must be a JSON object")
    return {str(key): str(item) for key, item in value.items() if str(key).strip()}


def encrypt_mcp_auth_headers(raw: Any) -> str:
    """加密保存 MCP 静态认证头，兼容接口传入的 JSON 字符串或映射。"""
    headers = _parse_auth_headers(raw)
    payload = json.dumps(headers, ensure_ascii=False, separators=(",", ":"))
    encrypted = get_api_key_manager().encrypt_api_key(payload)
    return f"{MCP_AUTH_HEADERS_PREFIX}{encrypted}"


def _stored_auth_headers(raw: Any) -> Any:
    if isinstance(raw, str) and raw.startswith(MCP_AUTH_HEADERS_PREFIX):
        encrypted = raw[len(MCP_AUTH_HEADERS_PREFIX):]
        return get_api_key_manager().decrypt_api_key(encrypted)
    return raw


def mcp_auth_headers_configured(server: Any) -> bool:
    """只返回认证配置状态，不向 API 响应暴露认证头内容。"""
    try:
        return bool(_parse_auth_headers(_stored_auth_headers(getattr(server, "auth_headers", None))))
    except (TypeError, ValueError):
        # 历史脏值仍视为已配置，避免 UI 误导用户覆盖未知凭据。
        raw = getattr(server, "auth_headers", None)
        return bool(str(raw or "").strip() not in {"", "{}", "null"})


def resolve_mcp_auth_headers(server: Any) -> dict[str, str]:
    """解析固定 MCP 凭证，优先使用新加密字段并兼容旧 auth_headers。"""
    headers = _parse_auth_headers(_stored_auth_headers(getattr(server, "auth_headers", None)))
    encrypted_token = getattr(server, "fixed_token_encrypted", None)
    if encrypted_token:
        token = get_api_key_manager().decrypt_api_key(encrypted_token)
        headers["Authorization"] = f"Bearer {token}"
    return headers


# 认证头关键字：命中即视为「凭据」，分享时选择「不携带凭证」必须一并剔除。
# 只按名字判断是有意为之——值已经加密，按内容猜更不可靠。
_CREDENTIAL_HEADER_HINTS = (
    "authorization",
    "api-key",
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "passwd",
    "cookie",
    "signature",
    "credential",
    "auth",
)


def looks_like_credential_header(name: Any) -> bool:
    """判断一个请求头名是否属于凭据（``X-Api-Key``、``X-Auth-Token`` 等）。

    ``Accept`` / ``Content-Type`` / ``X-Tenant-Id`` 这类协议头不会被命中，因此
    「不携带凭证」的分享仍然能被接收方正常连上，只是需要自己补凭据。
    """
    normalized = str(name or "").strip().casefold().replace("_", "-")
    return any(hint in normalized for hint in _CREDENTIAL_HEADER_HINTS)


def without_credential_headers(server: Any) -> str:
    """返回**剔除全部疑似凭据头后重新加密**的 auth_headers。

    分享 MCP 时若用户选择「不携带凭证」，只剥 ``Authorization`` 是不够的：
    很多网关用 ``X-Api-Key`` / ``X-Auth-Token`` 认证，这些头会原样泄露给对方，
    变成「以为没带、其实带了」。固定 Token（``fixed_token_encrypted``）与断言私钥
    由调用方另行置空，本函数只处理 ``auth_headers``。历史脏值解析失败时返回空配置，
    宁可让对方重新填，也不把未知内容原样带出去。
    """
    try:
        headers = _parse_auth_headers(_stored_auth_headers(getattr(server, "auth_headers", None)))
    except (TypeError, ValueError):
        return encrypt_mcp_auth_headers({})
    cleaned = {
        str(key): str(value)
        for key, value in headers.items()
        if not looks_like_credential_header(key)
    }
    return encrypt_mcp_auth_headers(cleaned)


def without_authorization_headers(server: Any) -> str:
    """兼容旧名；行为已升级为剔除所有疑似凭据头。"""
    return without_credential_headers(server)


def mcp_auth_headers_summary(server: Any) -> tuple[bool, dict[str, str]]:
    """返回 Authorization 状态与脱敏后的其他 Header，不返回任何凭证原文。"""
    try:
        headers = resolve_mcp_auth_headers(server)
    except (TypeError, ValueError):
        return False, {}

    authorization_configured = any(
        str(key).strip().casefold() == "authorization" and str(value).strip()
        for key, value in headers.items()
    )
    masked_headers = {
        str(key): "********"
        for key in headers
        if str(key).strip().casefold() != "authorization"
    }
    return authorization_configured, masked_headers


def load_mcp_private_key(server: Any) -> Any:
    encrypted_key = str(
        getattr(server, "user_assertion_private_key_encrypted", "") or ""
    ).strip()
    if not encrypted_key:
        return None
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    raw_key = get_api_key_manager().decrypt_api_key(encrypted_key)
    return load_pem_private_key(raw_key.encode("utf-8"), password=None)


def build_mcp_user_assertion_headers(
    server: Any,
    *,
    user_info: Mapping[str, Any] | None = None,
    agent_info: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    private_key: Any = None,
    issuer: str | None = None,
    assertion_header: str | None = None,
    audience: str | None = None,
) -> dict[str, str]:
    """只构造用户身份断言 Header；未开启开关时返回空字典。

    签名私钥与 key_id 只能来自已保存的 MCP 配置，Header 名、audience、issuer
    允许调用方覆盖，用于编辑态在保存前预览探测结果。
    """
    if not bool(getattr(server, "user_assertion_enabled", False)):
        return {}

    effective_audience = str(
        audience or getattr(server, "user_assertion_audience", "") or ""
    ).strip()
    key_id = str(getattr(server, "user_assertion_key_id", "") or "").strip()
    effective_header = str(
        assertion_header
        or getattr(server, "user_assertion_header", None)
        or DEFAULT_ASSERTION_HEADER
    ).strip()
    effective_issuer = str(
        getattr(server, "user_assertion_issuer", None) or issuer or "nanzi-platform"
    )
    private_key = private_key or load_mcp_private_key(server)
    if not private_key:
        raise ValueError("MCP UserContext private key is required")
    if not effective_audience:
        raise ValueError("MCP UserContext audience is required")
    if not key_id:
        raise ValueError("MCP UserContext key ID is required")
    if not effective_header or effective_header.lower() == "authorization":
        raise ValueError("MCP UserContext header cannot be Authorization")

    effective_request_id = str(request_id or uuid.uuid4())
    assertion = issue_user_assertion(
        user_info=user_info or {},
        agent_info=agent_info or {},
        audience=effective_audience,
        request_id=effective_request_id,
        private_key=private_key,
        key_id=key_id,
        issuer=effective_issuer,
    )
    return {effective_header: assertion, "X-Request-ID": effective_request_id}


def build_mcp_headers(
    server: Any,
    *,
    user_info: Mapping[str, Any] | None = None,
    agent_info: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    private_key: Any = None,
    issuer: str = "nanzi-platform",
) -> dict[str, str]:
    """根据 MCP Server 配置构造一次出站请求 Header。

    User Assertion 只在显式开启时生成，默认行为完全保留旧的静态 auth_headers。
    """
    headers = resolve_mcp_auth_headers(server)
    # UserContext 透传独立于 MCP 自身认证方式。MCP 可以没有
    # Authorization Bearer Token，但只要开启该开关，仍应发送用户断言。
    headers.update(
        build_mcp_user_assertion_headers(
            server,
            user_info=user_info,
            agent_info=agent_info,
            request_id=request_id,
            private_key=private_key,
            issuer=issuer,
        )
    )
    return headers
