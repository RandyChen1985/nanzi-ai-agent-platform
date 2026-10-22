from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse


BrowserActionClass = Literal["read", "interact", "commit"]


class BrowserUrlBlocked(ValueError):
    """地址未通过服务端出站入口的 SSRF 与地址范围校验。"""


class BrowserScriptBlocked(ValueError):
    """浏览器页面脚本违反资源限制。"""


@dataclass(frozen=True)
class BrowserDecision:
    allowed: bool
    requires_confirmation: bool
    reason: str


_COMMIT_TERMS = (
    "提交",
    "发送",
    "删除",
    "购买",
    "下单",
    "支付",
    "确认",
    "submit",
    "send",
    "delete",
    "buy",
    "purchase",
    "checkout",
    "pay",
    "confirm",
)
_INTERACTIVE_ROLES = {
    "button",
    "checkbox",
    "combobox",
    "link",
    "menuitem",
    "option",
    "radio",
    "searchbox",
    "select",
    "slider",
    "spinbutton",
    "tab",
    "textbox",
}
_PUBLIC_SCHEMES = {"http", "https"}

# 仅服务端出站入口（网页预览实测）使用：该入口由后端进程按用户给的地址主动
# 发起请求，因此仍然拒绝内网、环回与云元数据地址。
_PUBLIC_ONLY_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
    "metadata.google.com",
}

_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_BROWSER_SCRIPT_MAX_LENGTH = 50000


def _is_blocked_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    # 兼容代理/VPN 工具（Clash, Surge, Sing-box）TUN 模式下的 Fake-IP 网段 (198.18.0.0/15)
    if ip.version == 4 and ip in _FAKE_IP_NETWORK:
        return False
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


def _resolved_addresses(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise BrowserUrlBlocked(f"无法解析浏览器导航地址：{hostname}") from exc
    return list({str(info[4][0]) for info in infos if info[4]})


def _check_scheme_and_host(url: str, allowed_schemes: set[str]) -> str:
    """只校验协议、凭据与主机名存在性，不限制目标地址所在网段。"""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in allowed_schemes:
        raise BrowserUrlBlocked("浏览器只允许访问 http、https、ws 或 wss 地址")
    if parsed.username or parsed.password:
        raise BrowserUrlBlocked("浏览器导航地址不允许携带用户名或密码")

    hostname = (parsed.hostname or "").strip()
    if not hostname:
        raise BrowserUrlBlocked("浏览器导航地址缺少主机名")
    return url


def validate_browser_navigation(url: str) -> str:
    """浏览器导航地址不做任何限制：协议、凭据与目标网段全部放行。"""
    return url


def validate_browser_request(url: str) -> str:
    """页面发出的网络请求不做任何限制：协议与目标网段全部放行。"""
    return url


def validate_public_navigation(url: str) -> str:
    """服务端出站入口（网页预览实测）专用：在协议校验之上拒绝内网与元数据地址。

    浏览器工具本身已放开内网访问；这里保留原 SSRF 判定，是因为该入口由后端
    进程按用户提交的地址主动出站，放开等于把内网探测能力暴露给任意登录用户。
    """
    _check_scheme_and_host(url, _PUBLIC_SCHEMES)

    hostname = (urlparse(url).hostname or "").rstrip(".").lower()
    if hostname in _PUBLIC_ONLY_BLOCKED_HOSTNAMES or hostname.endswith((".local", ".internal")):
        raise BrowserUrlBlocked(f"禁止访问平台内部主机：{hostname}")

    addresses = [hostname] if _is_blocked_ip(hostname) else _resolved_addresses(hostname)
    if not addresses:
        raise BrowserUrlBlocked(f"无法解析浏览器导航地址：{hostname}")
    blocked = next((address for address in addresses if _is_blocked_ip(address)), None)
    if blocked:
        raise BrowserUrlBlocked(f"禁止访问内部或元数据地址：{blocked}")
    return url


def classify_browser_action(*, role: str | None, name: str | None) -> BrowserActionClass:
    """根据可访问性语义把浏览器动作分为读取、交互或提交。"""
    role_text = (role or "").strip().casefold()
    name_text = (name or "").strip().casefold()
    if any(term.casefold() in name_text for term in _COMMIT_TERMS):
        return "commit"
    if role_text in _INTERACTIVE_ROLES:
        return "interact"
    return "read"


def decide_browser_action(mode: str, action_class: BrowserActionClass) -> BrowserDecision:
    """仅在 guarded 模式下拦截高风险提交动作，平台级禁止项仍由调用方执行。"""
    normalized_mode = (mode or "autopilot").strip().casefold()
    if action_class == "commit" and normalized_mode != "autopilot":
        return BrowserDecision(
            allowed=False,
            requires_confirmation=True,
            reason="该浏览器动作可能提交、删除或产生外部副作用，需要用户确认",
        )
    return BrowserDecision(
        allowed=True,
        requires_confirmation=False,
        reason="浏览器动作已通过当前会话审批模式",
    )


def redact_browser_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """生成审计用参数副本，避免把敏感输入写入日志或 SSE。"""
    payload = dict(arguments)
    if payload.get("sensitive") is True:
        for key in ("value", "text", "input"):
            if key in payload:
                payload[key] = "<redacted>"
    return payload


def redact_browser_cookies(cookies: Any) -> Any:
    """保留 Cookie 元数据，但永不把 Cookie 值带入审计或工具结果。"""
    if not isinstance(cookies, list):
        return cookies
    redacted: list[Any] = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            redacted.append("<redacted>")
            continue
        payload = dict(cookie)
        if "value" in payload:
            payload["value"] = "<redacted>"
        redacted.append(payload)
    return redacted


def validate_browser_script(script: str) -> str:
    """保留页面脚本的自动化能力，仅限制空脚本和过大的脚本输入。"""
    raw_script = str(script or "").strip()
    if not raw_script:
        raise BrowserScriptBlocked("待执行的 JavaScript 脚本不能为空")
    if len(raw_script) > _BROWSER_SCRIPT_MAX_LENGTH:
        raise BrowserScriptBlocked(
            f"浏览器脚本长度超过限制（最多 {_BROWSER_SCRIPT_MAX_LENGTH} 个字符）"
        )
    return raw_script
