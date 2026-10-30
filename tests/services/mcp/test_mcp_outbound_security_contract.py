"""MCP 出站安全接线：URL 策略、禁重定向、错误脱敏、管理操作限流。"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
CLIENT = (ROOT / "app/services/ai/tools/mcp_client.py").read_text(encoding="utf-8")
ENDPOINT = (ROOT / "app/api/portal/endpoints/mcp.py").read_text(encoding="utf-8")


def test_sse_and_probe_do_not_follow_redirects():
    assert "follow_redirects=False" in CLIENT
    assert "_create_no_redirect_mcp_http_client" in CLIENT
    assert "httpx_client_factory=_create_no_redirect_mcp_http_client" in CLIENT
    # mcp 2.x 的 sse_client 只接受 httpx2 客户端；返回 httpx.AsyncClient 会 TypeError
    # 并被静默降级成 Direct HTTP。行为级回归见 test_mcp_client_sse_transport.py。
    assert "httpx2.AsyncClient" in CLIENT


def test_verify_error_does_not_echo_raw_exception():
    assert 'detail=f"连接失败: {str(e)}"' not in ENDPOINT
    assert "连接失败：请检查服务地址、网络连通性与认证配置" in ENDPOINT


def test_management_outbound_actions_are_rate_limited():
    assert ENDPOINT.count("enforce_mcp_management_rate_limit(") == 3
    for action in ('"verify"', '"sync"', '"execute"'):
        assert action in ENDPOINT


def test_sync_uses_a_server_row_lock_and_force_guard():
    assert "with_for_update()" in CLIENT
    assert "McpSyncGuardError" in CLIENT
    assert "force: bool = Query(False" in ENDPOINT
