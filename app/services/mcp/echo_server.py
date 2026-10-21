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
ECHO_TOOL_LOCAL_NAME = "echo"
ECHO_TOOL_NAME = f"{ECHO_SERVER_NAME}:{ECHO_TOOL_LOCAL_NAME}"
ECHO_TOOL_DESCRIPTION = "验证 MCP 请求是否收到，以及 NanZi 用户身份断言是否验签成功。"
# 个人 Echo 与平台 Echo 共用同一个 FastMCP 端点，靠路径里的 server_id 区分实例。
ECHO_SERVER_ID_HEADER = "X-Nanzi-Echo-Server"
ECHO_PATH_PREFIX = "/mcp/echo/"
PERSONAL_ECHO_NAMESPACE = "nanzi:mcp:echo:personal"
_USER_CONTEXT_FIELDS = ("user_id", "user_name", "real_name", "dept_code", "org_path")
_AGENT_CONTEXT_FIELDS = ("agent_id", "agent_version_id", "agent_name")


def personal_echo_server_id(user_id: int | str) -> str:
    """每个用户一个个人 Echo 实例：user_id 派生固定 ID，重复创建保持幂等。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{PERSONAL_ECHO_NAMESPACE}:{user_id}"))


def personal_echo_tool_name(server_name: str) -> str:
    """个人 Echo 的工具标识必须独立于平台 Echo。

    运行时按 ``tool_name`` 反查工具缓存（``scalar_one_or_none``）。若个人 Echo 继续
    复用平台 Echo 的 ``NanZi Echo 测试 MCP:echo``，两边同时存在就会查成多行、整条工具
    注册失败，模型只能看到清单却调不到工具。
    """
    return f"{server_name}:{ECHO_TOOL_LOCAL_NAME}"


def build_echo_server_url(base_url: str, server_id: str) -> str:
    """平台 Echo 保持历史路径；个人 Echo 把 server_id 编进路径供服务端选择配置。"""
    base = str(base_url or "").rstrip("/")
    if server_id == ECHO_SERVER_ID:
        return f"{base}{ECHO_PATH_PREFIX}mcp"
    return f"{base}{ECHO_PATH_PREFIX}{server_id}/mcp"


def is_echo_server_sse_url(value: str | None) -> bool:
    return ECHO_PATH_PREFIX in str(value or "")


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


def echo_server_id_from_headers(headers: Mapping[str, Any]) -> str | None:
    """个人 Echo 由路由包装器注入实例 ID；平台 Echo 没有该 Header。"""
    value = _get_header(headers, ECHO_SERVER_ID_HEADER)
    text = str(value or "").strip()
    return text or None


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


async def _load_echo_server(server_id: str | None = None) -> McpServer:
    from app.core.orm import AsyncSessionLocal

    target_id = str(server_id or "").strip() or ECHO_SERVER_ID
    async with AsyncSessionLocal() as db:
        server = (
            await db.execute(
                select(McpServer).where(McpServer.id == target_id)
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
    request = getattr(getattr(ctx, "request_context", None), "request", None)
    headers = getattr(request, "headers", {}) if request is not None else {}
    server = await _load_echo_server(echo_server_id_from_headers(headers))
    private_key = load_mcp_private_key(server)
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


class EchoServerIdMiddleware:
    """按路径选择 Echo 实例，供平台 Echo 与个人 Echo 共用一个端点。

    挂载点 ``/mcp/echo`` 已经剥掉前缀：平台 Echo 收到 ``/mcp``，个人 Echo 收到
    ``/{server_id}/mcp``。这里统一改写成 ``/mcp``，并把实例 ID 放进内部 Header；
    FastMCP 仍然只暴露一个端点，由 ``_load_echo_server`` 选择对应配置并完成验签。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        from starlette.routing import get_route_path

        header_name = ECHO_SERVER_ID_HEADER.lower().encode("utf-8")
        headers = [
            (key, value)
            for key, value in (scope.get("headers") or [])
            if key.lower() != header_name
        ]
        new_scope = {**scope, "headers": headers}

        parts = [part for part in get_route_path(scope).split("/") if part]
        if len(parts) == 2 and parts[1] == "mcp":
            root_path = str(scope.get("root_path") or "").rstrip("/")
            new_scope["path"] = f"{root_path}/mcp"
            new_scope["raw_path"] = new_scope["path"].encode("utf-8")
            new_scope["headers"] = headers + [(header_name, parts[0].encode("utf-8"))]

        await self.app(new_scope, receive, send)
