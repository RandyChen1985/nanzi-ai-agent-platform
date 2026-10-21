from pathlib import Path


COMPONENT = Path("frontend/src/components/system/McpServerRegistry.vue")


def test_unsaved_user_assertion_can_be_saved_before_verify():
    """开关未落库时私钥还不存在，必须先提供保存入口，否则第一步探测永远过不去。"""
    source = COMPONENT.read_text(encoding="utf-8")

    assert "savedUserAssertionEnabled" in source
    assert "userAssertionPendingSave" in source
    assert "保存并启用透传" in source
    assert "仅测试连接（不含透传）" in source
    assert "saveAndEnableUserAssertion" in source

    verify_block = source[source.index("const handleVerify"):source.index("const addServer")]
    assert "skipUserAssertion" in verify_block
    assert "payload.user_assertion_enabled = false" in verify_block

    edit_block = source[source.index("const openEditModal"):source.index("const addServer")]
    assert "savedUserAssertionEnabled.value = Boolean(server.user_assertion_enabled)" in edit_block


def test_pending_save_keeps_original_write_endpoints_and_explains_block():
    source = COMPONENT.read_text(encoding="utf-8")

    assert "当前开关尚未保存" in source
    assert "签名私钥在保存时才生成" in source
    assert "await axios.put(`/api/portal/mcp/servers/${editingId.value}`, payload)" in source
    assert "await axios.post('/api/portal/mcp/servers', payload)" in source
    assert "showToast(getApiErrorMessage(e, '操作失败'), 'error')" in source


def test_verify_button_does_not_leak_click_event_into_options():
    source = COMPONENT.read_text(encoding="utf-8")

    assert '@click="handleVerify()"' in source
    assert '@click="handleVerify"' not in source
