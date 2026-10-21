from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure


COMPONENT = Path("frontend/src/components/system/McpServerRegistry.vue")


def test_global_mcp_registry_has_one_click_echo_entry_and_selects_created_server():
    source = COMPONENT.read_text(encoding="utf-8")

    assert "创建 Echo 测试 MCP" in source
    assert "createEchoTestMcp" in source
    assert "axios.post('/api/portal/mcp/servers/echo-test'" in source
    assert "params: { scope: props.scope }" in source
    assert "所有智能体可挂载" in source
    assert "selectedServer" in source


def test_echo_ui_does_not_render_raw_credentials():
    source = COMPONENT.read_text(encoding="utf-8")

    assert "X-Nanzi-User-Assertion" in source
    assert "原始凭证" not in source
