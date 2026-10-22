"""MCP 出站地址策略：私网放行；非法 scheme / 元数据地址 / 歧义写法必须拒绝。"""
from __future__ import annotations

import pytest

from app.api.portal.endpoints.mcp import McpServerWrite
from app.services.mcp.url_policy import McpUrlPolicyError, validate_mcp_outbound_url

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.parametrize(
    "url",
    [
        "https://crm.example.com/mcp",
        "http://10.20.30.40:8080/mcp",
        "http://172.16.0.8/mcp",
        "http://192.168.1.10:9000/mcp",
        "http://127.0.0.1:8001/mcp/echo/abc",
        "http://localhost:3000/mcp",
        "http://[::1]:3000/mcp",
        "http://mcp-internal:8000/mcp",
    ],
)
def test_private_and_loopback_addresses_are_allowed(url):
    """产品口径：内网/本机 MCP 必须可用。"""
    assert validate_mcp_outbound_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/mcp",
        "gopher://example.com/",
        "http://user:password@example.com/mcp",
        "http://169.254.169.254/latest/meta-data",
        "http://[fe80::1]/mcp",
        "http://metadata.google.internal/computeMetadata/v1",
        "http://100.100.100.200/latest/meta-data",
        "http://168.63.129.16/machine?comp=acmspackage",
        "http://[fd00:ec2::254]/latest/meta-data",
        "http://metadata.azure.com/metadata/instance",
        "http://0.0.0.0:8000/mcp",
        "http://224.0.0.1/mcp",
        "http://example.com/mcp#fragment",
        "http://example.com/a b",
        "http://example.com:99999/mcp",
        "https://",
        "",
    ],
)
def test_dangerous_or_ambiguous_urls_are_rejected(url):
    with pytest.raises(McpUrlPolicyError):
        validate_mcp_outbound_url(url)


def test_write_model_rejects_colon_in_server_name():
    """工具标识用 server:tool；服务名带冒号会取错远端工具名。"""
    with pytest.raises(ValueError, match="冒号"):
        McpServerWrite(
            server_name="bad:name",
            sse_url="https://example.com/mcp",
        )


def test_write_model_rejects_metadata_url_but_accepts_private_one():
    with pytest.raises(ValueError, match="元数据|link-local"):
        McpServerWrite(
            server_name="metadata",
            sse_url="http://169.254.169.254/latest/meta-data",
        )

    request = McpServerWrite(
        server_name="intranet",
        sse_url="http://10.0.0.5:8080/mcp",
    )
    assert request.sse_url == "http://10.0.0.5:8080/mcp"
