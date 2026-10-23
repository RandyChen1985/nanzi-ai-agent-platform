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
