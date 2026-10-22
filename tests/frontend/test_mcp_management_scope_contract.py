"""平台 MCP 已收紧为 admin；非管理员不应看到会 403 的平台页签/请求。"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

SOURCE = Path("frontend/src/views/McpManagement.vue").read_text(encoding="utf-8")


def test_non_admin_defaults_to_personal_scope():
    assert "props.personalOnly || !isAdmin.value ? 'personal' : 'global'" in SOURCE


def test_global_tab_and_registry_are_admin_only():
    assert 'v-if="isAdmin"\n        id="tab-global-mcp"' in SOURCE
    assert 'v-else-if="isAdmin"\n        ref="registryRef"' in SOURCE


def test_global_server_count_is_not_requested_for_non_admin():
    assert "isAdmin.value\n      ? axios.get('/api/portal/mcp/servers', { params: { scope: 'global' } })" in SOURCE
