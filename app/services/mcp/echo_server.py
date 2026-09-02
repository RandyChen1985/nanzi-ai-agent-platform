"""NanZi 内置 Echo MCP：用于验证 MCP 调用链路和用户身份透传。"""

from __future__ import annotations

import hmac
import json
import uuid
from contextlib import asynccontextmanager
from collections.abc import Mapping
from typing import Any

import jwt
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select

from app.core.config import settings
from app.models.mcp import McpServer
from app.services.mcp.mcp_auth_policy import (
    load_mcp_private_key,
    resolve_mcp_auth_headers,
)
from app.services.mcp.transport_security import (
    _parse_public_url,
    build_mcp_transport_security,
)
from app.services.mcp.user_context_assertion import verify_user_assertion


ECHO_SERVER_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "nanzi:mcp:echo"))
ECHO_SERVER_NAME = "NanZi Echo 测试 MCP"
ECHO_TOOL_NAME = f"{ECHO_SERVER_NAME}:echo"
ECHO_TOOL_DESCRIPTION = "验证 MCP 请求是否收到，以及 NanZi 用户身份断言是否验签成功。"
_USER_CONTEXT_FIELDS = ("user_id", "user_name", "real_name", "dept_code", "org_path")
_AGENT_CONTEXT_FIELDS = ("agent_id", "agent_version_id", "agent_name")


def build_echo_transport_security(
    public_url: str | None,
) -> TransportSecuritySettings | None:
    """根据公网地址构造 Echo MCP 的 DNS rebinding 防护配置。"""
    return build_mcp_transport_security(public_url)


def resolve_echo_base_url(request_base_url: str, public_url: str | None) -> str:
    """优先使用有效 APP_PUBLIC_URL，否则回退到当前请求地址。"""
    parsed = _parse_public_url(public_url)
    if parsed is not None:
        return parsed[0]
    return str(request_base_url).rstrip("/")


def _mask_secret(value: str | None, *, prefix_length: int = 6, suffix_length: int = 6) -> str | None:
    """仅用于诊断展示，确保短凭证也不会原样返回。"""
    if not value:
        return None
    if len(value) <= prefix_length + suffix_length:
        return "***"
    return f"{value[:prefix_length]}***{value[-suffix_length:]}"


def _mask_authorization(value: str | None) -> str | None:
    """保留认证方案，脱敏 Bearer 凭证内容。"""
    if not value:
        return None
    scheme, separator, credentials = value.partition(" ")
    if separator and scheme.casefold() == "bearer":
        return f"{scheme} {_mask_secret(credentials, prefix_length=4, suffix_length=4)}"
    return _mask_secret(value, prefix_length=4, suffix_length=4)


def _get_header(headers: Mapping[str, Any], name: str) -> str | None:
    """兼容 Starlette Headers 和普通字典的大小写不敏感读取。"""
    direct = headers.get(name)
    if direct is not None:
        return str(direct)
    normalized = name.casefold()
    for key, value in headers.items():
        if str(key).casefold() == normalized:
            return str(value)
    return None


def _authorization_is_valid(headers: Mapping[str, Any], server: Any) -> bool:
    expected = resolve_mcp_auth_headers(server).get("Authorization")
    actual = _get_header(headers, "Authorization")
    if not expected or not actual:
        raise ValueError("Echo MCP 未配置 Authorization Bearer Token")
    return hmac.compare_digest(actual, expected)


def _verified_identity_diagnostics(
    assertion: str,
    *,
    private_key: Any,
    server: Any,
    request_id_received: bool,
) -> dict[str, Any]:
    if private_key is None:
        raise ValueError("Echo MCP 未配置用户身份签名私钥")

    try:
        token_header = jwt.get_unverified_header(assertion)
        expected_key_id = str(getattr(server, "user_assertion_key_id", "") or "").strip()
        if token_header.get("alg") != "EdDSA":
            raise jwt.InvalidTokenError("unsupported assertion algorithm")
        if expected_key_id and token_header.get("kid") != expected_key_id:
            raise jwt.InvalidTokenError("assertion key id does not match MCP configuration")

        claims = verify_user_assertion(
            assertion,
            public_key=private_key.public_key(),
            issuer=str(getattr(server, "user_assertion_issuer", None) or "nanzi-platform"),
            audience=str(getattr(server, "user_assertion_audience", None) or ""),
        )
    except (jwt.InvalidTokenError, TypeError, ValueError) as exc:
        raise PermissionError("NanZi 用户身份断言验签失败") from exc

    user_context = claims.get("user_context")
    if not isinstance(user_context, Mapping):
        raise PermissionError("NanZi 用户身份断言缺少用户上下文")
    agent_context = {
        key: claims[key]
        for key in _AGENT_CONTEXT_FIELDS
        if claims.get(key) is not None
    }
    custom_attributes = claims.get("custom_attributes")
    if not isinstance(custom_attributes, Mapping):
        custom_attributes = {}

    return {
        "user_assertion_valid": True,
        "request_id_received": request_id_received,
        "verified_user_id": user_context["user_id"],
        "verified_user_context": {
            key: user_context[key]
            for key in _USER_CONTEXT_FIELDS
            if user_context.get(key) is not None
        },
        "custom_attributes": dict(custom_attributes),
        "verified_agent_context": agent_context,
        "verified_claims": {
            "issuer": claims.get("iss"),
            "audience": claims.get("aud"),
            "subject": claims.get("sub"),
            "key_id": token_header.get("kid"),
        },
        "request_context": {
            "request_id": claims.get("request_id"),
            "request_id_header_received": request_id_received,
        },
    }


def build_echo_diagnostics(
    headers: Mapping[str, Any],
    server: Any,
    private_key: Any,
) -> dict[str, Any]:
    """校验一次 Echo 请求并返回不含原始凭证的安全诊断结果。"""
    processing_log: list[str] = []
    authorization = _get_header(headers, "Authorization")
    if authorization:
        processing_log.append("已收到 Authorization 请求头")
    else:
        processing_log.append("未收到 Authorization 请求头")

    authorization_valid = _authorization_is_valid(headers, server)
    if not authorization_valid:
        processing_log.append("Authorization Bearer Token 校验失败")
        raise PermissionError("Echo MCP Authorization Bearer Token 无效")
    processing_log.append("Authorization Bearer Token 校验通过")

    assertion_header = str(
        getattr(server, "user_assertion_header", None) or "X-Nanzi-User-Assertion"
    )
    assertion = _get_header(headers, assertion_header)
    request_id_received = bool(_get_header(headers, "X-Request-ID"))
    authorization_masked = _mask_authorization(authorization)
    diagnostics: dict[str, Any] = {
        "authorization_valid": True,
        "authorization_masked": authorization_masked,
        "user_assertion_received": bool(assertion),
        "user_assertion_valid": False,
        "user_assertion_masked": _mask_secret(assertion),
        "request_id_received": request_id_received,
        "processing_log": processing_log,
    }
    if assertion:
        processing_log.append(f"已收到 {assertion_header} 请求头")
        diagnostics.update(
            _verified_identity_diagnostics(
                assertion,
                private_key=private_key,
                server=server,
                request_id_received=request_id_received,
            )
        )
        processing_log.append("UserContext 签名校验通过")
        processing_log.append("已解析用户、扩展字段、智能体和请求信息")
    else:
        processing_log.append(f"未收到 {assertion_header} 请求头")
    return {"message": "已收到", "diagnostics": diagnostics}


async def _load_echo_server() -> McpServer:
    from app.core.orm import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        server = (
            await db.execute(
                select(McpServer).where(McpServer.id == ECHO_SERVER_ID)
            )
        ).scalar_one_or_none()
    if server is None or not server.enabled_status:
        raise ValueError("Echo MCP 尚未创建或已被禁用")
    return server


echo_mcp = FastMCP(
    ECHO_SERVER_NAME,
    instructions="用于验证 NanZi MCP 协议和用户身份透传，不执行任何业务操作。",
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    transport_security=build_echo_transport_security(settings.APP_PUBLIC_URL),
)


@asynccontextmanager
async def echo_mcp_lifespan():
    """把 FastMCP Streamable HTTP 的 Task Group 接入宿主应用生命周期。"""
    async with echo_mcp.session_manager.run():
        yield


@echo_mcp.tool(name="echo", description=ECHO_TOOL_DESCRIPTION)
async def echo(ctx: Context) -> dict[str, Any]:
    """返回 Echo 请求的安全认证诊断。"""
    server = await _load_echo_server()
    private_key = load_mcp_private_key(server)
    request = getattr(getattr(ctx, "request_context", None), "request", None)
    headers = getattr(request, "headers", {}) if request is not None else {}
    return build_echo_diagnostics(headers, server, private_key)


def echo_tool_schema() -> str:
    """返回内置 echo 工具的稳定 JSON Schema，供 MCP 管理页缓存。"""
    return json.dumps(
        {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
