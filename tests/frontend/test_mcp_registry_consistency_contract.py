"""MCP 工具集管理页：选中态、请求竞态、批量操作与同步保护的接线契约。"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

SOURCE = Path("frontend/src/components/system/McpServerRegistry.vue").read_text(
    encoding="utf-8"
)


def test_selected_server_is_rebound_after_list_refresh():
    """fetchServers 整体替换数组后必须按 id 重绑 selectedServer。

    否则改名/改地址/同步后的 stale 计数都读旧对象，右侧标题与“清理已失效”
    按钮会一直停在旧值。
    """
    assert "const reconcileSelectedServer = () => {" in SOURCE
    fetch_block = SOURCE.split("const fetchServers = async", 1)[1].split(
        "const isEchoTestServer", 1
    )[0]
    assert "reconcileSelectedServer()" in fetch_block
    assert "selectedServer.value = fresh" in SOURCE


def test_tool_list_responses_cannot_overwrite_a_newer_selection():
    assert "let toolsRequestSeq = 0" in SOURCE
    fetch_block = SOURCE.split("const fetchTools = async", 1)[1].split(
        "const selectServer", 1
    )[0]
    assert "seq !== toolsRequestSeq" in fetch_block
    assert "selectedServer.value?.id !== serverId" in fetch_block
    select_block = SOURCE.split("const selectServer = (server: any) => {", 1)[1].split(
        "const clearSelectedServer", 1
    )[0]
    assert "tools.value = []" in select_block
    assert "toolsRequestSeq += 1" in select_block


def test_batch_publish_uses_allsettled_and_refreshes_even_on_partial_failure():
    assert SOURCE.count("Promise.allSettled") >= 3
    batch_block = SOURCE.split("const batchUpdateStatus = async", 1)[1].split(
        "const publishedToolsCount", 1
    )[0]
    assert "failed" in batch_block
    assert "fetchServers()" in batch_block


def test_empty_remote_tool_list_requires_explicit_force_confirmation():
    assert "const syncGuardServerId = ref<string | null>(null)" in SOURCE
    assert "showSyncGuardConfirm" in SOURCE
    assert "params: { force: true }" in SOURCE
    assert 'title="远端返回 0 个工具"' in SOURCE
    assert "仍要同步" in SOURCE


def test_call_log_entry_requires_server_control_access():
    """公共服务调用日志只给管理员；非管理员不应看到入口（后端也已 403）。"""
    marker = """v-tooltip="'查看该服务的出站调用日志'"""
    index = SOURCE.index(marker)
    header_block = SOURCE[max(0, index - 600) : index]
    assert 'v-if="canSave"' in header_block
    action_block = SOURCE[max(0, SOURCE.index("runServerAction('logs')") - 600) : SOURCE.index("runServerAction('logs')")]
    assert 'v-if="canSave"' in action_block


def test_url_change_keeps_saved_token_without_blocking_verify_or_save():
    """改地址不应阻断测试/保存，也不应要求重新输入 Token。"""
    assert 'const verifyAddressChanged = computed' in SOURCE
    add_block = SOURCE.split("const addServer = async", 1)[1].split(
        "const saveAndEnableUserAssertion", 1
    )[0]
    assert "重新输入 Token 后再保存" not in add_block
    assert "已保存的认证 Header 不会自动沿用" not in add_block
    verify_block = SOURCE.split("const handleVerify = async", 1)[1].split(
        "const addServer = async", 1
    )[0]
    assert "重新输入 Token 后再测试" not in verify_block
    assert "地址已变更，测试与保存会继续沿用已保存的 Token" in SOURCE
