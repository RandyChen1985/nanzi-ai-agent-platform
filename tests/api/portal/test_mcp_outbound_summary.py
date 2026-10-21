"""普通用户的「我的 MCP 调用概览」契约。

全平台审计只给管理员，但普通用户应当能看到自己服务的调用情况。
这个接口的安全边界只有一条：**只按 user_id 聚合本人名下的个人 MCP**，
不返回任何他人调用的明细行；一旦这里放松成「全平台」就变成越权。
"""
from pathlib import Path

import pytest

from app.api.portal.endpoints import mcp as mcp_endpoints

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
MCP_ENDPOINT = ROOT / "app/api/portal/endpoints/mcp.py"


def _summary_body() -> str:
    source = MCP_ENDPOINT.read_text(encoding="utf-8")
    body = source.split("async def get_my_mcp_outbound_summary(", 1)[1]
    return body.split("\n@router.", 1)[0]


def test_summary_route_is_registered():
    routes = {
        (method.upper(), route.path)
        for route in mcp_endpoints.router.routes
        for method in getattr(route, "methods", set()) or set()
    }
    assert ("GET", "/outbound-summary/mine") in routes


def test_summary_is_scoped_to_own_personal_servers():
    body = _summary_body()
    assert 'McpServer.scope == "personal"' in body
    assert "McpServer.user_id == user_id" in body
    # 不能顺手放开成「所有服务」或给管理员豁免——这里本就该所有人只看自己
    assert 'McpServer.scope == "global"' not in body
    assert 'user.get("role") != "admin"' not in body


def test_summary_does_not_leak_other_users_logs():
    """日志表没有归属列，必须靠 server_id 白名单限定范围。"""
    body = _summary_body()
    assert "McpOutboundAuditLog.server_id.in_(server_ids)" in body
    assert "if not server_ids" in body


def test_summary_returns_expected_shape():
    body = _summary_body()
    for key in ('"summary"', '"servers"', '"tools"', '"daily"'):
        assert key in body
    for key in ('"total_calls"', '"success_calls"', '"failed_calls"', '"success_rate"', '"avg_latency_ms"'):
        assert key in body, f"概览缺少 {key}"


def test_summary_range_falls_back_to_7d():
    body = _summary_body()
    assert 'range if range in {"24h", "7d", "30d"} else "7d"' in body
