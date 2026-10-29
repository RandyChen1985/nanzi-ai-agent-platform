"""MCP 传输安全白名单必须能在运行期原地更新。

背景：``FastMCP`` 在构造时把 ``TransportSecuritySettings`` 实例交给中间件，中间件
之后每次请求都读 ``self.settings.allowed_hosts``。因此「系统配置里的对外地址」要
热生效，只能原地改这个实例的字段，不能替换实例本身。
"""

import pytest

from mcp.server.transport_security import TransportSecuritySettings


pytestmark = pytest.mark.no_infrastructure


def _placeholder() -> TransportSecuritySettings:
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
    )


def test_updates_allowed_hosts_and_origins_in_place():
    from app.services.mcp.public_url import sync_transport_security

    target = _placeholder()
    before = id(target)
    # 模拟中间件持有的引用
    consumer = target

    sync_transport_security(target, "https://nanzi.example.com:8443")

    assert id(target) == before, "必须原地更新，替换实例会让已挂载的中间件继续用旧白名单"
    assert consumer.allowed_hosts == ["nanzi.example.com:8443"]
    assert consumer.allowed_origins == ["https://nanzi.example.com:8443"]
    assert consumer.enable_dns_rebinding_protection is True


def test_falls_back_to_localhost_whitelist_when_public_url_is_empty():
    from app.services.mcp.public_url import sync_transport_security

    target = _placeholder()
    sync_transport_security(target, "https://nanzi.example.com")
    assert target.allowed_hosts == ["nanzi.example.com"]

    sync_transport_security(target, "")

    assert "localhost:*" in target.allowed_hosts
    assert "https://nanzi.example.com" not in target.allowed_origins
    # 未配置公网地址时仍保留 DNS rebinding 防护
    assert target.enable_dns_rebinding_protection is True


def test_invalid_public_url_falls_back_instead_of_crashing():
    from app.services.mcp.public_url import sync_transport_security

    target = _placeholder()

    sync_transport_security(target, "not-a-url")

    assert "localhost:*" in target.allowed_hosts


def test_apply_public_url_updates_both_mcp_instances():
    """mcp 2.x 起 MCPServer.settings 不再持有该对象，刷新必须落到模块级实例上。

    回归点：``apply_public_url_to_mcp`` 过去通过 ``instance.settings.transport_security``
    取对象，2.x 下该属性不存在，会静默变成空操作（公网地址永远不生效）。
    """
    from app.services.mcp.echo_server import get_echo_transport_security
    from app.services.mcp.platform_mcp import get_platform_transport_security
    from app.services.mcp.public_url import apply_public_url_to_mcp

    echo_target = get_echo_transport_security()
    platform_target = get_platform_transport_security()
    echo_before = id(echo_target)
    platform_before = id(platform_target)

    apply_public_url_to_mcp("https://nanzi.example.com:8443")

    for target in (echo_target, platform_target):
        assert id(target) in (echo_before, platform_before), "实例身份必须保持不变"
        assert any("nanzi.example.com" in host for host in target.allowed_hosts)
        assert any("nanzi.example.com" in origin for origin in target.allowed_origins)

    # 复原，避免影响同进程内其它用例（模块级对象是共享的）
    apply_public_url_to_mcp("")
