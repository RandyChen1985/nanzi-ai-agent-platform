"""MCP 出站认证策略：兼容静态 Header，并按配置附加用户断言。"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Mapping

from app.utils.encryption import get_api_key_manager
from app.services.mcp.user_context_assertion import issue_user_assertion

logger = logging.getLogger(__name__)

#: 已经被判定为非法并丢弃过的头名。只用来给日志去重：列表页每刷一次都会解析一遍
#: 全部服务的头，不去重会把同一条配置问题刷成几百行。
_DROPPED_HEADER_WARNED: set[str] = set()


def _warn_dropped_header(name: str, reason: Exception) -> None:
    """宽松读路径丢弃非法头时留一条线索（只记头名，不记值）。"""
    if name in _DROPPED_HEADER_WARNED:
        return
    _DROPPED_HEADER_WARNED.add(name)
    logger.warning(
        "[MCPAuth] 已丢弃非法请求头 %r（原因：%s）。出站请求会少带这个头，"
        "请在服务配置里改正或删除它。",
        name,
        reason,
    )


DEFAULT_ASSERTION_HEADER = "X-Nanzi-User-Assertion"
MCP_AUTH_HEADERS_PREFIX = "enc:v1:"
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
# 禁止通过自定义认证头覆盖传输帧控制头，避免请求走私（长度/分块语义被改写）。
#
# 刻意**不**禁 ``Host``：内网 MCP 网关按 vhost 路由时，用 IP 访问 + 自定义 Host 是
# 合法配置；而且这条拦截是本轮新增的，一旦生效，存量里已经配了 Host 的服务会在读取
# 路径被静默丢弃、认证直接失效。禁止它换来的安全收益接近零（目标地址本来就是用户
# 自己填的，改 Host 不扩大可达面），代价却是打断在用配置。
_FORBIDDEN_AUTH_HEADERS = frozenset({"content-length", "transfer-encoding"})


def generate_mcp_private_key_pem() -> str:
    """为一个 MCP 实例生成独立的 Ed25519 PKCS8 PEM 私钥。"""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    return Ed25519PrivateKey.generate().private_bytes(
        Encoding.PEM,
        PrivateFormat.PKCS8,
        NoEncryption(),
    ).decode("utf-8")


def validate_mcp_header_pair(key: str, value: str) -> tuple[str, str]:
    normalized_key = str(key).strip()
    if not normalized_key:
        return "", ""
    if not _HEADER_NAME_RE.match(normalized_key):
        raise ValueError(f"MCP Header 名称包含非法字符: {normalized_key}")
    if normalized_key.casefold() in _FORBIDDEN_AUTH_HEADERS:
        raise ValueError(f"MCP Header 不允许覆盖保留协议头: {normalized_key}")
    text_value = str(value)
    if any(ch in text_value for ch in ("\r", "\n", "\x00")):
        raise ValueError(f"MCP Header {normalized_key} 的值包含非法控制字符")
    return normalized_key, text_value


_validate_header_pair = validate_mcp_header_pair


def _set_header_case_insensitive(headers: dict[str, str], key: str, value: str) -> None:
    target = key.casefold()
    for existing in list(headers):
        if str(existing).strip().casefold() == target:
            headers.pop(existing, None)
    headers[key] = value


def _register_credential_for_redaction(value: str) -> None:
    """将解密后的静态 MCP 凭据登记进当前上下文脱敏池，防止远端回显泄露进日志/模型。"""
    text = str(value or "").strip()
    if not text:
        return
    try:
        from app.services.secrets.redact import register_injected
    except Exception:  # noqa: BLE001
        return
    register_injected(text)
    # 兼容 "Bearer <token>" / "Basic <cred>" / "Token <token>"：远端可能只回显裸令牌主体
    parts = text.split(None, 1)
    if len(parts) == 2 and parts[1].strip():
        register_injected(parts[1].strip())


def _parse_auth_headers(
    raw: Any, *, strict: bool = True, dropped: list[str] | None = None
) -> dict[str, str]:
    """解析认证头。``dropped`` 非空时收集被宽松路径丢弃的头名，供调用方判断状态。"""
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
    parsed: dict[str, str] = {}
    for key, item in value.items():
        if strict:
            clean_key, clean_val = validate_mcp_header_pair(str(key), str(item))
        else:
            try:
                clean_key, clean_val = validate_mcp_header_pair(str(key), str(item))
            except ValueError as exc:
                # 历史存量脏数据读取路径跳过非法头（不外发、也不让列表/探测整页 500）。
                # 但静默跳过会让「出站少一个认证头 → 远端 401」无从排查，
                # 所以至少要留下一条能定位到具体头名的告警。
                _warn_dropped_header(str(key), exc)
                if dropped is not None:
                    dropped.append(str(key))
                continue
        if not clean_key:
            continue
        _set_header_case_insensitive(parsed, clean_key, clean_val)
    return parsed


def encrypt_mcp_auth_headers(raw: Any) -> str:
    """加密保存 MCP 静态认证头，兼容接口传入的 JSON 字符串或映射。"""
    headers = _parse_auth_headers(raw, strict=True)
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
        dropped: list[str] = []
        parsed = _parse_auth_headers(
            _stored_auth_headers(getattr(server, "auth_headers", None)),
            strict=False,
            dropped=dropped,
        )
        # 全部被当成非法头丢弃时也算「已配置」：库里确实有东西，只是这条脏数据
        # 需要人工修。返回 False 会让界面显示「没有配置其他 Header」，
        # 用户照着界面重新填一遍就把原来的配置覆盖掉了。
        return bool(parsed) or bool(dropped)
    except (TypeError, ValueError):
        # 历史脏值仍视为已配置，避免 UI 误导用户覆盖未知凭据。
        raw = getattr(server, "auth_headers", None)
        return bool(str(raw or "").strip() not in {"", "{}", "null"})


def resolve_mcp_auth_headers(server: Any) -> dict[str, str]:
    """解析固定 MCP 凭证，优先使用新加密字段并兼容旧 auth_headers。"""
    headers = _parse_auth_headers(
        _stored_auth_headers(getattr(server, "auth_headers", None)),
        strict=False,
    )
    encrypted_token = getattr(server, "fixed_token_encrypted", None)
    if encrypted_token:
        token = get_api_key_manager().decrypt_api_key(encrypted_token)
        _set_header_case_insensitive(headers, "Authorization", f"Bearer {token}")
        _register_credential_for_redaction(token)
    for key, val in headers.items():
        if looks_like_credential_header(key):
            _register_credential_for_redaction(val)
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
        headers = _parse_auth_headers(
            _stored_auth_headers(getattr(server, "auth_headers", None)),
            strict=False,
        )
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


def strip_credential_headers_from_encrypted(raw: Any) -> str:
    """对「已加密的 auth_headers 字符串」再剔除凭据头并重新加密。

    分享接受时若接收方改了服务地址，快照里的凭据不能跟过去；这里复用与
    ``without_credential_headers`` 相同的判定，但入参是 config 里的密文字符串。
    """
    try:
        headers = _parse_auth_headers(_stored_auth_headers(raw), strict=False)
    except Exception:  # noqa: BLE001 - 损坏/旧格式凭据按“无凭据”处理，不能阻断接受分享
        headers = {}
    cleaned = {
        str(key): str(value)
        for key, value in headers.items()
        if not looks_like_credential_header(key)
    }
    return encrypt_mcp_auth_headers(cleaned)


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
        issuer or getattr(server, "user_assertion_issuer", None) or "nanzi-platform"
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
    issuer: str | None = None,
) -> dict[str, str]:
    """根据 MCP Server 配置构造一次出站请求 Header。

    User Assertion 只在显式开启时生成，默认行为完全保留旧的静态 auth_headers。
    """
    headers = resolve_mcp_auth_headers(server)
    # UserContext 透传独立于 MCP 自身认证方式。MCP 可以没有
    # Authorization Bearer Token，但只要开启该开关，仍应发送用户断言。
    assertion_headers = build_mcp_user_assertion_headers(
        server,
        user_info=user_info,
        agent_info=agent_info,
        request_id=request_id,
        private_key=private_key,
        issuer=issuer,
    )
    for key, val in assertion_headers.items():
        _set_header_case_insensitive(headers, key, val)
    return headers
