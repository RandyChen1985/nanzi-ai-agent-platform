"""前端契约：MCP 服务显示名称固定前缀 + 用户后缀。"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "frontend/src/components/system/McpServerRegistry.vue"
NAME_HELPER = ROOT / "frontend/src/utils/mcpServerName.ts"


def test_mcp_name_helper_builds_visible_user_prefix():
    helper_source = NAME_HELPER.read_text(encoding="utf-8")
    assert "buildMcpServerNamePrefix" in helper_source
    assert "composeMcpServerName" in helper_source
    assert "stripMcpServerNamePrefix" in helper_source
    assert "mcp-private-${userPart}-" in helper_source
    assert "mcp-public-${userPart}-" in helper_source


def test_mcp_registry_shows_fixed_prefix_and_suffix_input():
    registry_source = REGISTRY.read_text(encoding="utf-8")
    assert "namePrefix" in registry_source
    assert "serverNameSuffix" in registry_source
    # 编辑态走「保留原名」的解析函数，不再无条件重拼前缀（会静默改名平台 Echo）。
    assert "resolveEditedServerName" in registry_source
    assert "{{ namePrefix }}" in registry_source
    assert "请填写服务名称后缀" in registry_source
