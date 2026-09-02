"""MCP Streamable HTTP 的公网 Host/Origin 白名单构造。"""

from __future__ import annotations

from urllib.parse import urlsplit

from mcp.server.transport_security import TransportSecuritySettings


def _parse_public_url(public_url: str | None) -> tuple[str, str] | None:
    """解析平台公网 Origin，返回 (origin, host[:port])。"""
    value = str(public_url or "").strip()
    if not value:
        return None

    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        return None

    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not hostname:
        return None

    hostname = hostname.lower()
    host = f"[{hostname}]" if ":" in hostname else hostname
    host_with_port = f"{host}:{port}" if port is not None else host
    origin = f"{parsed.scheme.lower()}://{host_with_port}"
    return origin, host_with_port


def build_mcp_transport_security(
    public_url: str | None,
) -> TransportSecuritySettings | None:
    """根据公网地址构造 MCP 的 DNS rebinding 防护配置。"""
    parsed = _parse_public_url(public_url)
    if parsed is None:
        return None

    origin, host = parsed
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[host],
        allowed_origins=[origin],
    )
