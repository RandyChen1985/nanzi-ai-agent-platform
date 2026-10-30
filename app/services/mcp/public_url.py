"""MCP 传输安全白名单与公网地址的运行期刷新。

``TransportSecuritySettings`` 实例由各 MCP 模块自己持有，并在
``streamable_http_app()`` 时传给传输层中间件，之后每次请求都读它的
``allowed_hosts`` / ``allowed_origins``。因此「系统配置里的对外地址」要热生效，
只能原地改这个实例的字段，不能替换实例本身（否则已挂载的子应用继续用旧白名单）。

注意：mcp 1.x 时代该对象挂在 ``FastMCP.settings.transport_security`` 上，2.x 起
``MCPServer.settings`` 不再包含它，所以刷新入口改为各模块的访问器
（``get_platform_transport_security`` / ``get_echo_transport_security``）。

同时维护一份缓存，供平台 MCP 里那些同步函数（``platform_mcp_resource_url`` 等）
在不改 async 签名的前提下读到最新值。
"""

from __future__ import annotations

from mcp.server.transport_security import TransportSecuritySettings

from app.services.mcp.transport_security import build_mcp_transport_security


#: 与 FastMCP 内置的 localhost 默认白名单保持一致
LOCAL_ALLOWED_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")
LOCAL_ALLOWED_ORIGINS = (
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
)

#: 启动时/配置保存后刷新的公网地址缓存（空串表示未配置）
_cached_public_url: str = ""


def get_cached_public_url() -> str:
    """返回最近一次解析出的公网地址，未配置时为空串。"""
    return _cached_public_url


def sync_transport_security(
    target: TransportSecuritySettings | None,
    public_url: str | None,
) -> None:
    """把公网地址写进已有的传输安全设置（原地更新，保持对象身份不变）。"""
    if target is None:
        return

    resolved = build_mcp_transport_security(public_url)
    hosts = list(resolved.allowed_hosts) if resolved is not None else list(LOCAL_ALLOWED_HOSTS)
    origins = (
        list(resolved.allowed_origins)
        if resolved is not None
        else list(LOCAL_ALLOWED_ORIGINS)
    )

    target.enable_dns_rebinding_protection = True
    # 用切片赋值而不是重新绑定属性：中间件持有的是这个 list 的引用
    target.allowed_hosts[:] = hosts
    target.allowed_origins[:] = origins


def mutable_transport_security(public_url: str | None) -> TransportSecuritySettings:
    """构造期占位对象：永远返回可变实例，便于之后原地刷新。"""
    resolved = build_mcp_transport_security(public_url)
    if resolved is not None:
        return resolved
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(LOCAL_ALLOWED_HOSTS),
        allowed_origins=list(LOCAL_ALLOWED_ORIGINS),
    )


def apply_public_url_to_mcp(public_url: str | None) -> str:
    """把公网地址应用到两个 MCP 实例，并更新缓存。返回规整后的地址。"""
    global _cached_public_url

    from app.services.app_public_url_service import normalize_public_url

    resolved = normalize_public_url(public_url)
    _cached_public_url = resolved

    from app.services.mcp.echo_server import get_echo_transport_security
    from app.services.mcp.platform_mcp import get_platform_transport_security

    for target in (get_echo_transport_security(), get_platform_transport_security()):
        sync_transport_security(target, resolved)
    return resolved


async def refresh_mcp_public_url() -> str:
    """从系统配置重新解析并刷新 MCP；供启动流程与配置保存后调用。"""
    from app.services.app_public_url_service import get_app_public_url

    return apply_public_url_to_mcp(await get_app_public_url())
