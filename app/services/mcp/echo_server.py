"""NanZi 内置 Echo MCP：用于验证 MCP 调用链路和用户身份透传。"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import hmac
import ipaddress
import json
import smtplib
import socket
import ssl
import time
import uuid
from contextlib import asynccontextmanager
from collections.abc import Mapping
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Literal

import jwt
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select

from app.core.config import settings
from app.models.mcp import McpServer
from app.services.mcp.mcp_auth_policy import (
    load_mcp_private_key,
    resolve_mcp_auth_headers,
)
from app.services.mcp.public_url import mutable_transport_security
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
ECHO_MAIL_TOOL_LOCAL_NAME = "send_mail"
ECHO_MAIL_TOOL_NAME = f"{ECHO_SERVER_NAME}:{ECHO_MAIL_TOOL_LOCAL_NAME}"
ECHO_MAIL_TOOL_DESCRIPTION = (
    "测试邮件工具，三档 ``mode``：默认 ``mock`` 只回显参数（不发信）；"
    "``verify`` 用注入的 SMTP 参数真连并登录（不发信，可反复测）；"
    "``send`` 真发一封到 ``to``。参数名与「邮箱 SMTP 发送」凭据模板字段一一对齐"
    "（host / port / security / username / password / from_name），一条结构化凭据就能"
    "把这几个参数一次铺满；``mailkey`` 留给单值凭据验收。标成「来自凭据」后模型看不到"
    "这些值，由平台按 ``to`` 匹配后注入。返回每个参数的到达情况（非敏感给明文、敏感只给"
    "掩码与指纹）与真实连接结果（阶段 / 应答码 / 失败原因）。"
    "**只有用户明确要求「真发 / 真连测试」时才传 verify 或 send**。"
)
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


def personal_echo_slow_tool_name(server_name: str) -> str:
    """个人 Echo 的进度测试工具名，与 ``personal_echo_tool_name`` 同一命名空间。"""
    return f"{server_name}:{ECHO_SLOW_TOOL_NAME}"


def personal_echo_mail_tool_name(server_name: str) -> str:
    """个人 Echo 的发信测试工具名，同上。"""
    return f"{server_name}:{ECHO_MAIL_TOOL_LOCAL_NAME}"


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


#: 传输安全白名单的持有者（mcp 2.x 起 MCPServer.settings 不再保存该对象，
#: 必须由调用方持有并在 streamable_http_app() 时传入；运行期靠原地切片赋值刷新，
#: 见 app/services/mcp/public_url.py）。
_ECHO_TRANSPORT_SECURITY = mutable_transport_security(settings.APP_PUBLIC_URL)


def get_echo_transport_security() -> TransportSecuritySettings:
    """返回 Echo MCP 的传输安全设置，供 public_url 运行期原地刷新白名单。"""
    return _ECHO_TRANSPORT_SECURITY


echo_mcp = MCPServer(
    ECHO_SERVER_NAME,
    instructions="用于验证 NanZi MCP 协议和用户身份透传，不执行任何业务操作。",
)


_ECHO_HTTP_APP: Any = None


def echo_mcp_http_app():  # type: ignore[no-untyped-def]
    """构造（并缓存）Echo MCP 的 Streamable HTTP 应用（端点参数在 mcp 2.x 移到此处）。

    与 ``platform_mcp_http_app`` 同理：``streamable_http_app()`` 每次调用都会
    新建 ``StreamableHTTPSessionManager`` 并覆盖 ``server._session_manager``，
    而只有经 ``echo_mcp_lifespan`` 跑过 ``run()`` 的实例才能处理请求，
    因此这里必须缓存，避免二次调用把宿主挂载的实例顶掉。
    """
    global _ECHO_HTTP_APP
    if _ECHO_HTTP_APP is None:
        _ECHO_HTTP_APP = echo_mcp.streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
            transport_security=_ECHO_TRANSPORT_SECURITY,
            max_request_body_size=32 * 1024 * 1024,
            session_idle_timeout=3600.0,
        )
    return _ECHO_HTTP_APP


@asynccontextmanager
async def echo_mcp_lifespan():
    """把 FastMCP Streamable HTTP 的 Task Group 接入宿主应用生命周期。"""
    async with echo_mcp.session_manager.run():
        yield


@echo_mcp.tool(name="echo", description=ECHO_TOOL_DESCRIPTION)
async def echo(
    ctx: Context,
    api_key: str | None = None,
    tenant_token: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """返回 Echo 请求的安全认证诊断。

    参数专供**凭据注入验收**：在 MCP 管理页把 ``api_key`` / ``tenant_token``
    标成「来自凭据」后，模型侧看不到它们，由平台在 ``tools/call`` 里补上。
    这里回显的是掩码值和长度——能看到「注入了、多长」，但看不到明文。
    """
    request = getattr(getattr(ctx, "request_context", None), "request", None)
    headers = getattr(request, "headers", {}) if request is not None else {}
    server = await _load_echo_server(echo_server_id_from_headers(headers))
    private_key = load_mcp_private_key(server)
    result = build_echo_diagnostics(headers, server, private_key)
    result["received_arguments"] = _describe_arguments(
        api_key=api_key, tenant_token=tenant_token, note=note
    )
    return result


def _describe_arguments(
    *, api_key: str | None, tenant_token: str | None, note: str | None
) -> dict[str, Any]:
    """把收到的入参做成可核对的描述，**不含明文**。

    ``note`` 是普通参数（模型填），``api_key`` / ``tenant_token`` 是凭据参数
    （平台注入）。放在一起就是为了对比：前者随模型措辞变化，后者每次都是
    你在凭据中心绑的那条。
    """

    def describe(value: str | None) -> dict[str, Any]:
        if not value:
            return {"received": False}
        return {
            "received": True,
            "length": len(value),
            "masked": _mask_secret(value),
        }

    return {
        "api_key": describe(api_key),
        "tenant_token": describe(tenant_token),
        "note": note or None,
    }


ECHO_SLOW_TOOL_NAME = "echo_slow"
ECHO_SLOW_TOOL_DESCRIPTION = (
    "逐步上报进度的测试工具：用于验证 MCP 工具进度流（工具卡进度条 / 最近进度文案）。"
    "它会按 steps 逐步调用 report_progress，并返回汇总结果。"
)
#: 每步默认间隔（毫秒）。测试里传 0 可以直接跑完，不真正等待。
ECHO_SLOW_DEFAULT_INTERVAL_MS = 800


REAL_MAIL_MODES = ("verify", "send")
DEFAULT_MAIL_MODE = "mock"
_SMTP_SECURITY_MODES = ("ssl", "starttls", "none")
_SMTP_CONNECT_TIMEOUT = 10
_SMTP_SEND_TIMEOUT = 30
_SMTP_SERVER_TEXT_LIMIT = 300
_MAX_SMTP_HOST_LENGTH = 255
# 与 app/services/mcp/url_policy.py 的 _BLOCKED_IP_NETWORKS 同源，但**策略不同**：
# 那边明确放行私网与 loopback（MCP 服务常部署在内网），SMTP 侧必须更严格。
# 注意 Python 3.11 里 100.100.100.200（阿里云元数据）的 is_private 为 False，
# 所以这些网段必须显式列出，不能只靠 is_private。
_BLOCKED_MAIL_IP_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("0.0.0.0/32"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("100.100.100.200/32"),
    ipaddress.ip_network("168.63.129.16/32"),
    ipaddress.ip_network("fd00:ec2::254/128"),
)


def _normalize_smtp_host(host: Any) -> str:
    """去掉方括号、首尾空白与结尾点号（``[::1]`` / ``smtp.qq.com.`` 都见过）。"""
    text = str(host or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    return text.lower().rstrip(".")


def _host_has_illegal_chars(host: str) -> bool:
    """主机名不可能出现的字符：控制字符、空白、路径分隔符、``@``。"""
    for char in host:
        code = ord(char)
        if code < 0x20 or code == 0x7F:
            return True
        if char in {" ", "\t", "\r", "\n", "\\", "/", "@"}:
            return True
    return False


def _looks_like_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _is_blocked_mail_ip(ip: "ipaddress._BaseAddress") -> bool:
    """回环 / 私网 / 链路本地 / 保留 / 组播 / 未指定，以及云元数据地址。

    ``::ffff:a.b.c.d``（IPv4-mapped IPv6）必须先拆回 IPv4 再判。Python 3.11 里
    这类地址 ``is_private`` / ``is_reserved`` 全是 False，而它的 ``version`` 是 6，
    会跳过下面那张 IPv4 网段表——一条 DNS AAAA 记录就能把元数据地址送进来。
    实测 ``_is_blocked_mail_ip(::ffff:100.100.100.200)`` 曾返回 False，而同一个
    地址写成 IPv4 是 True。`url_policy` 那边一直是对的，这份守卫漏了。
    """
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return _is_blocked_mail_ip(mapped)
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return True
    for network in _BLOCKED_MAIL_IP_NETWORKS:
        # 混版本（IPv4 地址 vs IPv6 网段）的 ``in`` 在 3.11 返回 False，
        # 但显式比版本更清楚，也避免依赖该实现细节。
        if ip.version == network.version and ip in network:
            return True
    return False


def _resolve_host_addresses(host: str, port: int) -> list[str]:
    """同步解析，调用方负责丢线程池。返回去重后的全部 A/AAAA 地址。"""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in addresses:
            addresses.append(address)
    return addresses


def _validate_real_mail_params(
    *,
    to: Any,
    host: Any,
    port: Any,
    security: Any,
    username: Any,
    password: Any,
) -> dict[str, Any]:
    """真实模式的参数校验分级。

    区分 ``missing_params``（值没到 → 大概率绑定没生效）与 ``invalid_params``
    （值到了但用法不对 → 配置写错），两者排查方向完全不同。
    """
    missing: list[str] = []
    for name, value in (("host", host), ("username", username), ("password", password)):
        if not str(value or "").strip():
            missing.append(name)
    if missing:
        return {
            "ok": False,
            "reason": "missing_params",
            "missing": missing,
            "message": (
                f"真实模式缺少参数：{', '.join(missing)}。"
                "请检查该参数是否已标成「来自凭据」并且绑定已生效"
            ),
        }

    normalized_host = _normalize_smtp_host(host)
    if not normalized_host:
        return {"ok": False, "reason": "invalid_params", "message": "SMTP 主机为空"}
    if len(normalized_host) > _MAX_SMTP_HOST_LENGTH or _host_has_illegal_chars(normalized_host):
        return {
            "ok": False,
            "reason": "invalid_params",
            "message": f"SMTP 主机名不合法：{normalized_host!r}",
        }

    recipient = str(to or "").strip()
    if (
        not recipient
        or "," in recipient
        or ";" in recipient
        or "@" not in recipient
        or any(char.isspace() for char in recipient)
    ):
        return {
            "ok": False,
            "reason": "invalid_params",
            "message": f"收件人不合法：{to!r}。真实模式只支持单个邮箱地址",
        }

    from app.services.email_delivery_service import EmailDeliveryService

    raw_port = port
    if raw_port is None or str(raw_port).strip() == "":
        raw_port = 465 if str(security or "").strip().lower() == "ssl" else 587
    # `int(True) == 1` 会顺着下面那条范围校验溜过去，最后表现成「连接端口 1
    # 失败」——把一个类型错误伪装成网络错误。凭据里 JSON 写成 `"port": true`
    # 就是这么来的，必须在这里按类型拒掉。
    if isinstance(raw_port, bool):
        return {
            "ok": False,
            "reason": "invalid_params",
            "message": f"端口不合法：{port!r}，应为 1-65535 的整数",
        }
    try:
        normalized_port = int(raw_port)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "reason": "invalid_params",
            "message": f"端口不合法：{port!r}，应为 1-65535 的整数",
        }
    if not 1 <= normalized_port <= 65535:
        return {
            "ok": False,
            "reason": "invalid_params",
            "message": f"端口不合法：{port!r}，应为 1-65535 的整数",
        }

    raw_security = str(security or "").strip().lower()
    if raw_security and raw_security not in _SMTP_SECURITY_MODES:
        return {
            "ok": False,
            "reason": "invalid_params",
            "message": (
                f"加密方式不合法：{raw_security!r}，"
                f"只支持 {' / '.join(_SMTP_SECURITY_MODES)}"
            ),
        }
    # 缺省时与个人中心「测试发信」同源推断：465 → ssl，其余 → starttls。
    normalized_security = EmailDeliveryService.infer_security(
        {"security": raw_security}, normalized_port
    )

    return {
        "ok": True,
        "reason": "ok",
        "normalized": {
            "host": normalized_host,
            "port": normalized_port,
            "security": normalized_security,
            "username": str(username).strip(),
            "password": str(password),
            "to": recipient,
        },
    }


async def _guard_smtp_host(host: str, port: int) -> dict[str, Any]:
    """出站地址守卫：拦住回环 / 私网 / 保留 / 云元数据地址。

    只看「解析出来的地址」，不看 hostname 语义——SMTP 服务器的域名毫无规律
    （``smtp.内网域名`` 之类），按域名猜必然误伤。DNS 在线程池执行，不阻塞事件循环。
    """
    normalized = _normalize_smtp_host(host)
    if not normalized:
        return {"ok": False, "reason": "invalid_params", "message": "SMTP 主机为空"}
    if len(normalized) > _MAX_SMTP_HOST_LENGTH or _host_has_illegal_chars(normalized):
        return {
            "ok": False,
            "reason": "invalid_params",
            "message": f"SMTP 主机名不合法：{normalized!r}",
        }

    if _looks_like_ip(normalized):
        candidates = [normalized]
        source = "literal"
    else:
        loop = asyncio.get_running_loop()
        try:
            candidates = await loop.run_in_executor(
                None, _resolve_host_addresses, normalized, port
            )
        except (socket.gaierror, OSError) as exc:
            return {
                "ok": False,
                "reason": "dns_failed",
                "message": f"SMTP 主机解析失败：{normalized}（{exc}）",
            }
        source = "dns"
        if not candidates:
            return {
                "ok": False,
                "reason": "dns_failed",
                "message": f"SMTP 主机没有解析出任何地址：{normalized}",
            }

    blocked: list[str] = []
    for candidate in candidates:
        try:
            ip = ipaddress.ip_address(candidate)
        except ValueError:
            return {
                "ok": False,
                "reason": "dns_failed",
                "message": f"SMTP 主机解析出无法识别的地址：{candidate}",
            }
        if _is_blocked_mail_ip(ip):
            blocked.append(candidate)
    if blocked:
        return {
            "ok": False,
            "reason": "blocked_address",
            "blocked": blocked,
            "message": (
                f"SMTP 主机指向内网 / 回环 / 保留地址（{', '.join(blocked)}），"
                "真实模式已拒绝连接；这是安全拦截，不是凭据问题"
            ),
        }

    return {"ok": True, "reason": None, "addresses": candidates, "source": source}


def _build_real_mail_message(
    *, to: str, subject: str, body: str, username: str, from_name: str
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((from_name, username)) if from_name else username
    message["To"] = to
    message["Subject"] = subject or "NanZi Echo 注入验收"
    message.set_content(body)
    return message


def _build_default_mail_body(
    *, mode: str, host: str, port: int, security: str, username: str, password: str
) -> str:
    fingerprint = hashlib.sha256(password.encode("utf-8")).hexdigest()[:12]
    return "\n".join(
        [
            "这是一封由 NanZi 平台 Echo 测试 MCP 发出的真实测试邮件。",
            "",
            f"模式：{mode}",
            f"SMTP：{host}:{port}（{security}）",
            f"登录账号：{username}",
            f"密码指纹：{fingerprint}（明文不会被回显）",
            "",
            "收到这封邮件说明：凭据注入链路与 SMTP 认证都是真实可用的。",
        ]
    )


def _smtp_transaction(
    *,
    host: str,
    port: int,
    security: str,
    username: str,
    password: str,
    to: str,
    subject: str,
    body: str,
    from_name: str,
    mode: str,
) -> dict[str, Any]:
    """阻塞式阶段机，调用方必须丢线程池。

    每个阶段先落 ``stage`` 再执行，异常时按「当前阶段 + 异常类型」映射 reason，
    这样 ``login`` 失败（凭据错）与 ``connect`` 失败（网络错）能被明确区分。
    """
    from app.services.email_delivery_service import EmailDeliveryService

    def _text(value: Any) -> str:
        if isinstance(value, (bytes, bytearray)):
            return value.decode("utf-8", errors="replace")
        return str(value or "")

    def mask(text: Any) -> str:
        return EmailDeliveryService.sanitize_error_text(_text(text), password)

    started = time.monotonic()
    state: dict[str, Any] = {"stage": "connect", "code": None, "server": ""}
    connection: smtplib.SMTP | None = None

    def finish(ok: bool, reason: str, message: str) -> dict[str, Any]:
        return {
            "ok": ok,
            "reason": reason,
            "message": message,
            "stage": state["stage"],
            "code": state["code"],
            "server": str(state["server"])[:_SMTP_SERVER_TEXT_LIMIT],
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    try:
        context = ssl.create_default_context()
        if security == "ssl":
            connection = smtplib.SMTP_SSL(
                host, port, timeout=_SMTP_CONNECT_TIMEOUT, context=context
            )
        else:
            connection = smtplib.SMTP(host, port, timeout=_SMTP_CONNECT_TIMEOUT)

        state["stage"] = "ehlo"
        code, server_text = connection.ehlo()
        state["code"], state["server"] = code, _text(server_text)

        if security == "starttls":
            state["stage"] = "starttls"
            code, server_text = connection.starttls(context=context)
            state["code"], state["server"] = code, _text(server_text)
            code, server_text = connection.ehlo()
            state["code"], state["server"] = code, _text(server_text)

        state["stage"] = "login"
        code, server_text = connection.login(username, password)
        state["code"], state["server"] = code, _text(server_text)

        if mode == "verify":
            state["stage"] = "authenticated"
            # QUIT 失败不该把「已认证成功」翻成失败，忽略即可（finally 会 close）
            try:
                connection.quit()
            except Exception:
                pass
            return finish(
                True,
                "ok",
                "真实校验通过：已用注入参数登录成功（未发送邮件）",
            )

        state["stage"] = "sendmail"
        # smtplib.SMTP 没有 settimeout()（那是 socket 的方法），发信阶段单独放宽超时
        if connection.sock is not None:
            connection.sock.settimeout(_SMTP_SEND_TIMEOUT)
        message = _build_real_mail_message(
            to=to, subject=subject, body=body, username=username, from_name=from_name
        )
        connection.sendmail(username, [to], message.as_string())
        state["stage"] = "sent"
        try:
            connection.quit()
        except Exception:
            pass
        return finish(True, "ok", f"已用注入参数真实发送 1 封邮件到 {to}")

    except smtplib.SMTPAuthenticationError as exc:
        state["code"] = exc.smtp_code
        state["server"] = mask(exc.smtp_error)
        # 535 是「值到了但服务器不认」——这一点必须说死，否则用户会一直怀疑
        # 绑定有没有生效、注入有没有通。能走到 login 说明 host/port/TLS 全对，
        # 账号也送到了服务器，剩下的只有「这个账号 + 这个密码」不被接受。
        #
        # 按概率列出原因，是因为这几种在回执上长得一模一样，用户没法自己区分：
        # 最常见的是邮箱后台根本没开 SMTP 服务（授权码再对也报 535），其次是
        # 把登录密码当授权码填（163/126 只认单独生成的授权码）。
        head = "真实校验失败" if mode == "verify" else "真实发送失败"
        # 认不出来码就用 535：`SMTPAuthenticationError` 也会被 530/534 触发，
        # 写死码会让用户对着一个错的原因排查。
        code = exc.smtp_code or 535
        return finish(
            False,
            "auth_failed",
            f"{head}：SMTP 认证被拒（{code}）。"
            "注入链路是通的——主机、端口、TLS 都通了，账号也已送达服务器，"
            "问题出在「这个账号 + 这个密码」不被接受，重试不会变。按概率排查："
            "① 邮箱后台没开启 POP3/SMTP 服务（163/126/QQ 必须手动开启，"
            "没开时授权码填得再对也是 535）；"
            "② 密码栏填的是登录密码而不是授权码（163/126 需要单独生成授权码，"
            "重新生成后旧的立即失效，只有新的能用）；"
            "③ 授权码首尾带了空格或换行（粘贴时容易带入，看不出但一定失败）；"
            "④ 账号要写完整邮箱地址（只写前缀服务器认不出）；"
            "⑤ 新注册、长期未登录或异地登录被风控。",
        )
    except (
        smtplib.SMTPRecipientsRefused,
        smtplib.SMTPSenderRefused,
        smtplib.SMTPDataError,
    ) as exc:
        # SMTPRecipientsRefused 没有 smtp_code/smtp_error（它只带 recipients 字典），
        # 拒收码要从 {收件人: (code, msg)} 里取，否则结果里 code 永远是 None。
        refused_recipients = getattr(exc, "recipients", None) or {}
        first_refusal = None
        for value in refused_recipients.values():
            first_refusal = value
            break
        refused_code = None
        refused_text = None
        if first_refusal is not None:
            try:
                refused_code = int(first_refusal[0])
            except (TypeError, ValueError, IndexError):
                refused_code = None
            try:
                refused_text = first_refusal[1]
            except (TypeError, IndexError):
                refused_text = None
        state["code"] = getattr(exc, "smtp_code", None) or refused_code
        state["server"] = mask(
            getattr(exc, "smtp_error", None) or refused_text or exc
        )
        return finish(
            False,
            "send_rejected",
            f"邮件被服务器拒绝（{state['code'] or '无应答码'}）：{state['server']}",
        )
    except ssl.SSLCertVerificationError as exc:
        return finish(False, "cert_error", f"TLS 证书校验失败：{mask(exc)}")
    except smtplib.SMTPServerDisconnected as exc:
        return finish(False, "connect_failed", f"SMTP 服务器提前断开连接：{mask(exc)}")
    except smtplib.SMTPResponseException as exc:
        state["code"] = getattr(exc, "smtp_code", None)
        state["server"] = mask(getattr(exc, "smtp_error", exc))
        reason = "auth_failed" if state["stage"] == "login" else "send_rejected"
        return finish(False, reason, f"SMTP 拒绝（{state['code']}）：{state['server']}")
    except (socket.timeout, TimeoutError) as exc:
        return finish(False, "timeout", f"SMTP 在 {state['stage']} 阶段超时：{mask(exc)}")
    except socket.gaierror as exc:
        return finish(False, "dns_failed", f"SMTP 主机解析失败：{mask(exc)}")
    except OSError as exc:
        return finish(False, "connect_failed", f"无法连接 SMTP 服务器：{mask(exc)}")
    except Exception as exc:  # 兜底：绝不把异常抛给模型侧，否则拿不到回执
        if state["stage"] == "login":
            fallback_reason = "auth_failed"
        elif state["stage"] in {"sendmail", "sent"}:
            fallback_reason = "send_rejected"
        else:
            fallback_reason = "connect_failed"
        return finish(False, fallback_reason, f"SMTP 调用异常：{mask(exc)}")
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def _receipt(name: str, value: Any, *, sensitive: bool) -> dict[str, Any]:
    """一个注入参数的「回执」：到了没有、是什么。

    非敏感参数（host / port / username …）直接回显明文——验收时要能一眼看出
    「host 映射到了 host」，只给指纹反而要拿凭据原文去对。
    敏感参数（password / mailkey）只给掩码与指纹：明文回显等于把凭据
    泄露进对话与 trace，而指纹足够回答「注入的是 A 那条还是 B 那条」。
    """
    text = "" if value is None else str(value)
    receipt: dict[str, Any] = {"present": bool(text)}
    if not text:
        return receipt
    if sensitive:
        receipt["masked"] = _mask_secret(text)
        receipt["fingerprint"] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        receipt["length"] = len(text)
    else:
        receipt["value"] = value
    return receipt


@echo_mcp.tool(name=ECHO_MAIL_TOOL_LOCAL_NAME, description=ECHO_MAIL_TOOL_DESCRIPTION)
async def send_mail(
    ctx: Context,
    to: str,
    subject: str = "",
    host: str | None = None,
    port: int | None = None,
    security: str | None = None,
    username: str | None = None,
    password: str | None = None,
    from_name: str | None = None,
    mailkey: str | None = None,
    mode: Literal["mock", "verify", "send"] = DEFAULT_MAIL_MODE,
    body: str | None = None,
) -> dict[str, Any]:
    """模拟 / 真实校验 / 真实发送邮件，并回显每个 SMTP 参数的到达情况。

    参数名**刻意与内置「邮箱 SMTP 发送」模板的字段一一对齐**（``host`` / ``port`` /
    ``security`` / ``username`` / ``password`` / ``from_name``）：凭据多字段映射面板
    有个「按名字自动匹配」，同名的话一条 SMTP 凭据能一键铺满这些参数，不用逐个选。

    Args:
        to: 收件人。凭据规则通常用它当键（``arg:to``）。
        subject: 邮件主题，普通参数，用来确认「非凭据参数照常由模型填」。
        host: SMTP 服务器。凭据参数。
        port: 端口。**声明成 int 而不是 str**：凭据里的 ``port: 465`` 必须原样
            以数字注入，写成 ``"465"`` 会被真实服务判类型不符。这里能顺带验收
            类型保留。
        security: 加密方式（ssl / starttls / none）。凭据参数。
        username: SMTP 登录账号。注意与 ``to``（收件人）不是一回事。
        password: 密码 / 授权码。敏感，只回显掩码与指纹。
        from_name: 发件人显示名。凭据参数。
        mailkey: 单值凭据的注入验收位。模型侧看不到，由平台注入。
        mode: ``mock``（默认，只回显不发信）/ ``verify``（真连 + 真登录，不发信）/
            ``send``（真发一封到 ``to``）。真实档位用**注入进来的**参数连接，
            因此能证明「值不仅到了，而且能用」。
        body: 仅 ``send`` 使用；留空用内置测试正文。

    真实模式的失败一律以 ``ok: False`` + ``real.reason`` 返回，不抛异常——否则调用方
    拿不到参数回执，分不清「注入没到位」还是「凭据无效」。
    """
    base: dict[str, Any] = {
        "to": to,
        "subject": subject,
        "smtp": {
            "host": _receipt("host", host, sensitive=False),
            "port": _receipt("port", port, sensitive=False),
            "security": _receipt("security", security, sensitive=False),
            "username": _receipt("username", username, sensitive=False),
            "password": _receipt("password", password, sensitive=True),
            "from_name": _receipt("from_name", from_name, sensitive=False),
        },
        # 单值凭据走的也是这条：整段就是一个值，没有字段可拆
        "mailkey": _receipt("mailkey", mailkey, sensitive=True),
    }

    normalized_mode = str(mode or DEFAULT_MAIL_MODE).strip().lower()
    if normalized_mode not in (DEFAULT_MAIL_MODE, *REAL_MAIL_MODES):
        return {
            "ok": False,
            "mode": normalized_mode,
            **base,
            "real": {
                "attempted": False,
                "host": host,
                "port": port,
                "security": security,
                "reason": "invalid_params",
            },
            "message": (
                f"mode 不合法：{normalized_mode!r}，"
                f"只支持 {DEFAULT_MAIL_MODE} / {' / '.join(REAL_MAIL_MODES)}"
            ),
        }

    if normalized_mode == DEFAULT_MAIL_MODE:
        # 键集必须与改造前完全一致：既有凭据注入验收测试依赖它。
        return {"ok": True, **base, "message": f"已模拟给 {to} 发送邮件"}

    validation = _validate_real_mail_params(
        to=to,
        host=host,
        port=port,
        security=security,
        username=username,
        password=password,
    )
    real: dict[str, Any] = {
        "attempted": False,
        "host": host,
        "port": port,
        "security": security,
    }
    if not validation["ok"]:
        real["reason"] = validation["reason"]
        return {
            "ok": False,
            "mode": normalized_mode,
            **base,
            "real": real,
            "message": validation["message"],
        }

    normalized = validation["normalized"]
    guard = await _guard_smtp_host(normalized["host"], normalized["port"])
    if not guard["ok"]:
        real.update(
            {
                "host": normalized["host"],
                "port": normalized["port"],
                "security": normalized["security"],
                "reason": guard["reason"],
                "blocked": guard.get("blocked", []),
            }
        )
        return {
            "ok": False,
            "mode": normalized_mode,
            **base,
            "real": real,
            "message": guard["message"],
        }

    real.update(
        {
            "attempted": True,
            "host": normalized["host"],
            "port": normalized["port"],
            "security": normalized["security"],
        }
    )
    mail_body = body or _build_default_mail_body(
        mode=normalized_mode,
        host=normalized["host"],
        port=normalized["port"],
        security=normalized["security"],
        username=normalized["username"],
        password=normalized["password"],
    )
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        functools.partial(
            _smtp_transaction,
            host=normalized["host"],
            port=normalized["port"],
            security=normalized["security"],
            username=normalized["username"],
            password=normalized["password"],
            to=normalized["to"],
            subject=subject,
            body=mail_body,
            from_name=str(from_name or "").strip(),
            mode=normalized_mode,
        ),
    )
    real.update(
        {
            "stage": result["stage"],
            "code": result["code"],
            "server": result["server"],
            "duration_ms": result["duration_ms"],
            "reason": result["reason"],
        }
    )
    return {
        "ok": result["ok"],
        "mode": normalized_mode,
        **base,
        "real": real,
        "message": result["message"],
    }


def send_mail_tool_schema() -> str:
    """返回 ``send_mail`` 的稳定 JSON Schema，供 MCP 管理页缓存。

    注意：模型实际看到的 schema 由 FastMCP 按 ``send_mail`` 的签名自动生成，
    本函数只是「服务连不上时管理页展示参数」的副本，签名一改必须同步，
    否则两份漂移（``mode`` / ``body`` 就是本次同步的对象）。
    """
    return json.dumps(
        {
            "type": "object",
            "properties": {
                # 名字与「邮箱 SMTP 发送」模板字段对齐：映射面板的「按名字自动匹配」
                # 靠的就是同名，改名前先想清楚会不会让一键铺满失效。
                "to": {"type": "string", "description": "收件人。凭据规则通常用它当键。"},
                "subject": {"type": "string", "description": "邮件主题"},
                "host": {"type": "string", "description": "SMTP 服务器。凭据参数。"},
                "port": {"type": "integer", "description": "端口。凭据参数，按数字注入。"},
                "security": {
                    "type": "string",
                    "description": "加密方式：ssl / starttls / none。凭据参数。",
                },
                "username": {"type": "string", "description": "SMTP 登录账号。凭据参数。"},
                "password": {
                    "type": "string",
                    "description": "密码 / 授权码。凭据参数，敏感，只回显指纹。",
                },
                "from_name": {"type": "string", "description": "发件人显示名。凭据参数。"},
                "mailkey": {
                    "type": "string",
                    "description": "单值凭据的注入验收位：标成「来自凭据」后由平台注入。",
                },
                "mode": {
                    "type": "string",
                    "enum": ["mock", "verify", "send"],
                    "default": "mock",
                    "description": (
                        "mock 只回显参数不发信；verify 真连并登录（不发信）；"
                        "send 真发一封到 to"
                    ),
                },
                "body": {
                    "type": "string",
                    "description": "仅 send 使用：邮件正文，留空则用内置测试正文",
                },
            },
            "required": ["to"],
        },
        ensure_ascii=False,
    )


@echo_mcp.tool(name=ECHO_SLOW_TOOL_NAME, description=ECHO_SLOW_TOOL_DESCRIPTION)
async def echo_slow(
    ctx: Context,
    steps: int = 5,
    total: int | None = None,
    interval_ms: int = ECHO_SLOW_DEFAULT_INTERVAL_MS,
) -> dict[str, Any]:
    """按 ``steps`` 逐步上报进度。

    参数含义（进度流验收用）：

    * ``total=None``：正常进度，前端显示 ``3/5 第 3/5 步``；
    * ``total=0``：显式不提供总数，前端显示不确定态的呼吸条 + 文案；
    * ``interval_ms``：每步间隔，测试时传 0 立即跑完。
    """
    step_count = max(1, min(int(steps or 1), 50))
    # 三态：未传 total → 用 steps 当总数；total>0 → 按它算；total==0 → 显式不带总数，
    # 前端应渲染不确定态的呼吸条。
    if total is None:
        reported_total: int | None = step_count
    elif int(total) > 0:
        reported_total = int(total)
    else:
        reported_total = None
    interval_seconds = max(0, int(interval_ms or 0)) / 1000.0

    for index in range(1, step_count + 1):
        await ctx.report_progress(
            index,
            reported_total,
            f"第 {index}/{step_count} 步",
        )
        if interval_seconds and index < step_count:
            await asyncio.sleep(interval_seconds)

    return {
        "ok": True,
        "steps": step_count,
        "total_declared": reported_total,
        "message": f"已完成 {step_count} 步",
    }


def echo_slow_tool_schema() -> str:
    """返回 ``echo_slow`` 的稳定 JSON Schema，供 MCP 管理页缓存。"""
    return json.dumps(
        {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "integer",
                    "description": "要上报的步数（1-50，默认 5）",
                    "default": 5,
                },
                "total": {
                    "type": "integer",
                    "description": "声明的总数；传 0 表示不提供总数（前端显示不确定态进度）",
                },
                "interval_ms": {
                    "type": "integer",
                    "description": "每步间隔毫秒数（默认 800，测试可传 0）",
                    "default": ECHO_SLOW_DEFAULT_INTERVAL_MS,
                },
            },
            "additionalProperties": False,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def echo_tool_schema() -> str:
    """返回内置 echo 工具的稳定 JSON Schema，供 MCP 管理页缓存。

    ``api_key`` / ``tenant_token`` 是给凭据注入验收用的：在管理页把它们标成
    「来自凭据」后，模型侧就看不到这两个参数了。``note`` 是普通参数，留作对比。
    """
    return json.dumps(
        {
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "description": "测试用 API Key。标成「来自凭据」后由平台注入。",
                },
                "tenant_token": {
                    "type": "string",
                    "description": "测试用租户令牌。标成「来自凭据」后由平台注入。",
                },
                "note": {
                    "type": "string",
                    "description": "普通参数，模型自己填；用来对比凭据参数与模型参数的区别。",
                },
            },
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
