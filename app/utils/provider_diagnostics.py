"""面向安装方的供应商连接诊断文案。

模型配置页的失败提示要能直接回答两个问题：**是哪个地址的哪一类错误**，
以及**下一步去查什么**。这里集中生成这段文本，保证「加载模型列表」和
「测试连接」两个入口措辞一致，并且不会把 API Key 带到前端或日志里。
"""

from __future__ import annotations

import json
import re

import httpx

_BODY_LIMIT = 280
_MESSAGE_LIMIT = 400

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{4,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_\-\.]{8,}"),
)

# 该链路使用 trust_env=False 的 httpx 客户端，所以代理环境变量不生效，
# 排查建议必须把这个前提讲清楚，否则安装方会误以为配了代理却没生效。
def _direct_egress_hint(request_url: str) -> str:
    return (
        f"请在部署该平台的服务器（或容器内）执行 `curl -I {request_url}` 验证能否直连；"
        "注意此请求不读取 HTTP_PROXY/HTTPS_PROXY，若出口必须经过代理，"
        "需要为这个域名放通直连出口或调整容器网络策略。"
    )


_SDK_EGRESS_HINT = (
    "请在部署该平台的服务器上确认能直连供应商域名（可用 curl 验证）；"
    "注意此请求不读取 HTTP_PROXY/HTTPS_PROXY。"
)


def redact_secrets(text: str) -> str:
    """Mask anything that looks like an API key before the text leaves the server."""

    masked = _SECRET_PATTERNS[0].sub("sk-***", str(text or ""))
    return _SECRET_PATTERNS[1].sub(r"\1***", masked)


def _clip(text: str, limit: int = _BODY_LIMIT) -> str:
    collapsed = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[:limit]}…"


def _lines(*parts: str) -> str:
    return "\n".join(part for part in parts if part)


def _provider_message(body: str | None) -> str:
    """Extract the human-meaningful part of a provider error payload."""

    raw = _clip(body or "")
    if not raw:
        return ""
    try:
        payload = json.loads(body or "")
    except (TypeError, ValueError):
        return raw
    if not isinstance(payload, dict):
        return raw

    error = payload.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or error.get("type") or "").strip()
        message = str(error.get("message") or "").strip()
        combined = ": ".join(part for part in (code, message) if part)
        return _clip(combined) or raw

    code = str(payload.get("code") or "").strip()
    message = str(payload.get("message") or payload.get("msg") or "").strip()
    combined = ": ".join(part for part in (code, message) if part)
    return _clip(combined) or raw


def describe_http_failure(
    *,
    method: str,
    request_url: str,
    status_code: int,
    body: str | None = None,
) -> str:
    """Describe a non-2xx provider response, including its advice for the operator."""

    detail = _provider_message(body)

    if status_code in {401, 403}:
        headline = f"供应商鉴权失败（HTTP {status_code}）"
        advice = (
            "请确认 API Key 与 Base URL 属于同一区域（北京 dashscope.aliyuncs.com / "
            "新加坡 dashscope-intl.aliyuncs.com），并确认该 Key 已开通对应模型；"
            "子业务空间的 Key 可能缺少模型权限。"
        )
    elif status_code == 404:
        headline = "接口地址不存在（HTTP 404）"
        advice = (
            "请确认 Base URL 填到服务根路径（通常以 /v1 结尾），没有多写或少写路径段；"
            "部分供应商不提供模型列表接口，可跳过此步骤手工填写模型 ID。"
        )
    elif status_code == 429:
        headline = "触发供应商限流（HTTP 429）"
        advice = "请稍后重试，或在供应商控制台检查该 Key 的并发与配额。"
    elif status_code >= 500:
        headline = f"供应商服务异常（HTTP {status_code}）"
        advice = "这通常是供应商侧的临时故障，请稍后重试并关注其服务状态公告。"
    else:
        headline = f"供应商拒绝了请求（HTTP {status_code}）"
        advice = "请核对 Base URL、模型 ID 与账号权限，下面附带了供应商返回的原始信息。"

    return _lines(
        f"{headline}：{detail}" if detail else headline,
        f"请求：{method} {request_url}",
        f"建议：{advice}",
    )


def _transport_headline(exc: BaseException, request_url: str) -> tuple[str, str]:
    """Map a transport-layer exception to a short headline plus operator advice."""

    name = type(exc).__name__
    raw = str(exc).strip()
    lowered = raw.lower()

    if isinstance(exc, httpx.TimeoutException) or "timeout" in name.lower():
        return (
            "连接供应商超时（未在限定时间内响应）",
            f"网络可达但迟迟没有响应，可能是出口被限速或供应商侧拥塞。{_direct_egress_hint(request_url)}",
        )

    if isinstance(exc, httpx.ProxyError) or "proxy" in lowered:
        return (
            "代理连接失败",
            "该请求不会读取系统代理设置。如果这台机器只能经代理出网，"
            "需要为供应商域名配置直连出口，或让服务进程走允许直连的网络。",
        )

    looks_like_tls = any(
        hint in lowered for hint in ("certificate", "ssl", "tls", "cert verify")
    )
    if looks_like_tls:
        return (
            "TLS 握手失败（证书校验未通过）",
            "常见于企业出口代理做了 HTTPS 解密、或系统根证书缺失。"
            "请为服务进程安装对应根证书，或改用可直连的网络。",
        )

    if isinstance(exc, httpx.ConnectError) or "connect" in name.lower():
        return (
            "无法连接供应商（域名解析或网络不可达）",
            f"通常是容器 DNS 解析失败、防火墙/安全组拦截或出网被限制。{_direct_egress_hint(request_url)}",
        )

    return (
        "与供应商的连接被中断",
        f"对端或中间设备提前断开了连接，常见于网关超时、TLS 拦截。{_direct_egress_hint(request_url)}",
    )


def describe_transport_failure(
    *,
    method: str,
    request_url: str,
    exc: BaseException,
) -> str:
    """Describe a failed request that never produced a usable HTTP response."""

    headline, advice = _transport_headline(exc, request_url)
    raw = _clip(str(exc), _MESSAGE_LIMIT)
    detail = f"{type(exc).__name__}: {raw}" if raw else type(exc).__name__

    return _lines(
        headline,
        f"请求：{method} {request_url}",
        f"错误：{detail}",
        f"建议：{advice}",
    )


def describe_invalid_response(
    *,
    method: str,
    request_url: str,
    body: str | None = None,
    content_type: str | None = None,
) -> str:
    """Describe a 2xx response whose body cannot be parsed as JSON."""

    snippet = _clip(body or "")
    return _lines(
        "供应商返回的内容不是合法 JSON",
        f"请求：{method} {request_url}",
        f"Content-Type：{_clip(content_type or '未提供', 80)}",
        f"响应片段：{snippet}" if snippet else "响应片段：（空）",
        "建议：通常是出口被网关/登录页拦截，或 Base URL 指向了控制台页面而不是 API 地址。"
        "请在服务器上直接 curl 该地址确认返回内容。",
    )


_SDK_CLASS_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("authentication", "permissiondenied", "unauthorized"),
        "供应商鉴权失败",
        "请确认 API Key 与 Base URL 区域一致，且该 Key 已开通此模型。",
    ),
    (
        ("notfound",),
        "模型或接口不存在",
        "请确认模型 ID 是否已在供应商侧开通，以及 Base URL 的路径是否完整。",
    ),
    (
        ("ratelimit",),
        "触发供应商限流",
        "请稍后重试，或检查该 Key 的并发与配额。",
    ),
    (
        ("badrequest", "unprocessable", "invalidrequest"),
        "请求被供应商拒绝（参数不合法）",
        "常见原因：模型 ID 拼写错误、模型不支持某些参数（例如思考开关 enable_thinking）、"
        "或该模型需要多模态输入。请对照供应商文档核对模型 ID 与参数。",
    ),
    (
        ("apitimeout", "connect", "network", "timeout"),
        "连接供应商失败",
        f"请先确认部署机器能直连该供应商域名。{_SDK_EGRESS_HINT}",
    ),
    (
        ("internalserver",),
        "供应商服务异常",
        "这通常是供应商侧故障，请稍后重试。",
    ),
)


def describe_sdk_failure(exc: BaseException) -> str:
    """Describe an SDK-level failure (chat/embedding ping) without leaking secrets."""

    name = type(exc).__name__
    lowered_name = name.lower()
    raw = _clip(str(exc), _MESSAGE_LIMIT)

    headline = "调用供应商接口失败"
    advice = "请结合下方的原始错误与供应商文档核对 Base URL、模型 ID 和账号权限。"
    for tokens, candidate_headline, candidate_advice in _SDK_CLASS_RULES:
        if any(token in lowered_name for token in tokens):
            headline, advice = candidate_headline, candidate_advice
            break

    detail = f"{name}: {raw}" if raw else name
    return _lines(headline, f"错误：{redact_secrets(detail)}", f"建议：{advice}")
