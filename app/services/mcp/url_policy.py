"""MCP 出站地址策略。

产品口径：**允许连接内网 / 本机 MCP**（很多部署把 MCP 放在同一内网），因此这里
不拦截 RFC1918、loopback 等私网地址；但仍然拒绝那些几乎只可能被用来做 SSRF 的
目标与写法：

- 非 http/https scheme（file/gopher/dict 等）；
- URL 里内嵌账号密码（会进日志、也可能被转发给第三方）；
- 云元数据 / link-local / 组播 / 未指定地址（``169.254.0.0/16``、``fe80::/10``、
  ``metadata.google.internal`` 等）——这类地址不是业务 MCP，拿到就足以窃取实例凭据；
- 原始控制字符、空白、反斜杠（解析歧义，可能被不同 HTTP 栈解释成不同主机）。

只按「字面 host」判断，不做 DNS 解析：私网既然放行，DNS 解析结果本身不能作为
安全边界；真正的边界是网络出口策略 / 目标服务鉴权。这里的目标是把明显的误用与
最危险的元数据地址挡在录入阶段。
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

MAX_MCP_URL_LENGTH = 2048

_ALLOWED_SCHEMES = {"http", "https"}
_BLOCKED_HOSTNAMES = {
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
    "metadata.azure.com",
    "instance-data",
    "instance-data.ec2.internal",
}
# 只拦「元数据 / link-local / 组播 / 未指定」；私网与 loopback 明确放行。
_BLOCKED_IP_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),  # IPv4 link-local（含云元数据）
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
    ipaddress.ip_network("ff00::/8"),  # IPv6 multicast
    ipaddress.ip_network("224.0.0.0/4"),  # IPv4 multicast
    ipaddress.ip_network("0.0.0.0/32"),
    ipaddress.ip_network("::/128"),
    # 常见云厂商元数据地址（不是 RFC1918 私网，但属于 SSRF 的高危目标）
    ipaddress.ip_network("100.100.100.200/32"),  # 阿里云
    ipaddress.ip_network("168.63.129.16/32"),  # Azure wire server
    ipaddress.ip_network("fd00:ec2::254/128"),  # AWS IMDS IPv6
)
# NAT64 / IPv4-translated IPv6 前缀：低 32 位内嵌 IPv4，需要剥出后按 IPv4 策略复检，
# 防止 `64:ff9b::169.254.169.254` 或 `::ffff:0:169.254.169.254` 绕过元数据拦截。
#
# 注意这里是 **IPv4-translated**（RFC 2765，`::ffff:0:0:0/96`，第 5 组为 ffff），
# 与 **IPv4-mapped**（`::ffff:a.b.c.d`，第 6 组为 ffff）是两个不同前缀：
# 后者由上面的 ``ipv4_mapped`` 分支处理，重复列在这儿只会让读者以为覆盖了两遍。
# RFC 8215 的 local-use 前缀（`64:ff9b:1::/48`）不在此列：/48 的内嵌位按 RFC 6052
# 分布在 48-63 / 72-95，不在末 32 位，用低 32 位提取判断是错误的，宁可留空也不要
# 给出「已覆盖」的错觉。
_EMBEDDED_IPV4_NETWORKS = (
    ipaddress.ip_network("64:ff9b::/96"),  # RFC 6052 Well-Known NAT64
    ipaddress.ip_network("::ffff:0:0:0/96"),  # RFC 2765 IPv4-translated
)


class McpUrlPolicyError(ValueError):
    """地址不符合 MCP 出站策略。"""


def _has_control_or_ambiguous_chars(value: str) -> bool:
    for char in value:
        code = ord(char)
        if code < 0x20 or code == 0x7F:
            return True
        if char in {" ", "\t", "\r", "\n", "\\"}:
            return True
    return False


def _parse_literal_ip(hostname: str) -> "ipaddress._BaseAddress | None":
    """解析字面 IP，兼顾标准写法、IPv6 zone id 与非标准 IPv4 编码。

    Python ``ipaddress.ip_address()`` 只认 4 段点分十进制 IPv4；而底层
    ``getaddrinfo`` / ``inet_aton`` 会把十进制整数（``2852039166``）、十六进制
    （``0xa9fea9fe``）、八进制（``0251.0376.0251.0376``）、省略段
    （``169.254.43518``）以及 ``0`` 解析为真实 IPv4。这里统一归一，防止绕过拦截。
    """
    candidate = hostname.split("%", 1)[0]
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        pass
    try:
        packed = socket.inet_aton(candidate)
    except OSError:
        return None
    return ipaddress.IPv4Address(packed)


def _ip_is_blocked(ip: "ipaddress._BaseAddress") -> bool:
    """判断地址是否命中「元数据 / link-local / 组播 / 未指定」。

    必须同时检查 IPv4-mapped / IPv4-translated / NAT64 IPv6：``ipaddress`` 对
    ``::ffff:169.254.169.254`` 的 ``is_link_local`` 会跟着映射地址走，但 ``in
    IPv4Network(...)`` 因为版本不同恒为 False，且 NAT64 / ``::ffff:0:0/96`` 不会
    触发 ``ipv4_mapped``——因此提取内嵌 IPv4 递归复检。
    """
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return True
    if any(ip in network for network in _BLOCKED_IP_NETWORKS):
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None and mapped != ip:
        return _ip_is_blocked(mapped)
    if isinstance(ip, ipaddress.IPv6Address) and any(
        ip in network for network in _EMBEDDED_IPV4_NETWORKS
    ):
        embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        return _ip_is_blocked(embedded)
    return False


def validate_mcp_outbound_url(value: str) -> str:
    """校验并返回规范化后的 MCP 地址；不合法时抛 ``McpUrlPolicyError``。"""
    raw = str(value or "").strip()
    if not raw:
        raise McpUrlPolicyError("MCP 服务地址不能为空")
    if len(raw) > MAX_MCP_URL_LENGTH:
        raise McpUrlPolicyError(f"MCP 服务地址不能超过 {MAX_MCP_URL_LENGTH} 个字符")
    if _has_control_or_ambiguous_chars(raw):
        raise McpUrlPolicyError("MCP 服务地址包含非法字符（空白、控制字符或反斜杠）")

    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise McpUrlPolicyError("MCP 服务地址格式不正确") from exc

    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise McpUrlPolicyError("MCP 服务地址只支持 http:// 或 https://")
    if not parsed.netloc or not parsed.hostname:
        raise McpUrlPolicyError("MCP 服务地址缺少主机名")
    if parsed.username or parsed.password:
        raise McpUrlPolicyError("MCP 服务地址不能内嵌账号密码，请改用认证 Header")
    if parsed.fragment:
        raise McpUrlPolicyError("MCP 服务地址不能包含 #fragment")
    try:
        # 触发端口解析，非法端口（如 :99999）会抛 ValueError。
        parsed.port
    except ValueError as exc:
        raise McpUrlPolicyError("MCP 服务地址的端口不合法") from exc

    hostname = str(parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        raise McpUrlPolicyError("MCP 服务地址缺少主机名")
    if hostname in _BLOCKED_HOSTNAMES:
        raise McpUrlPolicyError("MCP 服务地址不能指向云元数据服务")

    ip = _parse_literal_ip(hostname)
    if ip is not None and _ip_is_blocked(ip):
        raise McpUrlPolicyError("MCP 服务地址不能指向云元数据或不可用地址")

    return raw
