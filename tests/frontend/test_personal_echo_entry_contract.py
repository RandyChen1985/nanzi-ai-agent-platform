from pathlib import Path


COMPONENT = Path("frontend/src/components/system/McpServerRegistry.vue")


def test_personal_scope_exposes_echo_entry_with_scope_param():
    """个人 MCP 区也要有 Echo 入口，且仍按 create 码收敛。"""
    source = COMPONENT.read_text(encoding="utf-8")

    assert "props.scope === 'personal' && canCreateMcp" in source
    assert "个人联调诊断服务" in source
    assert "创建 Echo 测试 MCP" in source
    assert "params: { scope: props.scope }" in source
    assert "没有创建个人 MCP 服务的权限" in source
    # 旧实现把个人区直接挡在函数入口
    assert "props.scope !== 'global' || !canSave.value" not in source


def test_echo_servers_cannot_be_shared_from_registry():
    source = COMPONENT.read_text(encoding="utf-8")

    assert "const isEchoTestServer" in source
    assert "/mcp/echo/" in source
    assert "props.scope === 'personal' && !isEchoTestServer(server)" in source
