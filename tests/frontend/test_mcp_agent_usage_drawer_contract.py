from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

DRAWER = Path(
    "frontend/src/components/system/McpServerAgentsDrawer.vue"
).read_text()
REGISTRY = Path(
    "frontend/src/components/system/McpServerRegistry.vue"
).read_text()


def test_registry_exposes_agent_usage_entry_from_usage_summary():
    """服务头部的统计行必须能点开清单，而不是只给一个数字。"""
    assert "McpServerAgentsDrawer" in REGISTRY
    assert "showAgentUsageDrawer" in REGISTRY
    assert "openAgentUsageDrawer" in REGISTRY
    assert 'aria-label="查看绑定该 MCP 服务的智能体清单"' in REGISTRY


def test_registry_reuses_single_usage_endpoint_for_drawer():
    """抽屉不得自带请求：绑定关系只由后端一处口径计算。"""
    assert "selectedServerUsage" in REGISTRY
    assert ":usage=\"selectedServerUsage\"" in REGISTRY
    assert "refreshSelectedServerUsage" in REGISTRY


def test_registry_closes_drawer_when_server_selection_is_cleared():
    """切服务/返回列表后不能残留上一个服务的绑定名单。"""
    cleared_blocks = REGISTRY.count("showAgentUsageDrawer.value = false")
    assert cleared_blocks >= 2


def test_drawer_renders_agent_status_and_version_count():
    assert "bound_agent_count" in DRAWER
    assert "active_agent_count" in DRAWER
    assert "bound_version_count" in DRAWER
    assert "version_count" in DRAWER
    assert "生效中" in DRAWER
    assert "未生效" in DRAWER
    assert "已停用" in DRAWER


def test_drawer_reuses_parent_usage_without_own_request():
    assert "axios" not in DRAWER
    assert "McpServerAgentUsage | null" in DRAWER


def test_drawer_handles_empty_state_and_search():
    assert "暂无智能体绑定该 MCP" in DRAWER
    assert "暂时无法获取绑定情况" in DRAWER
    assert "搜索智能体名称" in DRAWER
    assert "没有匹配" in DRAWER


def test_drawer_is_accessible_and_dismissible():
    assert 'role="dialog"' in DRAWER
    assert 'aria-modal="true"' in DRAWER
    assert "Escape" in DRAWER
    assert "emit('close')" in DRAWER
