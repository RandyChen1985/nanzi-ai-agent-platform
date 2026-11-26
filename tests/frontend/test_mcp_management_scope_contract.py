"""平台 MCP 页签对所有登录用户可见（只读），写能力仍按权限码与管理员收敛。

平台页签以前整行对非管理员隐藏，因为列表接口会对非 admin 返回 403。现在平台服务
清单与工具清单对登录用户只读开放（响应已脱敏），因此契约变为：

- 默认落点仍是「我的 MCP」（普通用户日常用的是自己的服务）；
- Tab 行只按 `personalOnly` 收起，平台页签按钮本身不再带 `isAdmin`；
- 平台 registry 走 `v-else` 兜底分支；
- 「只读」提示条只出现在「非 admin + 平台页签」；
- 审计日志 / 定义变更两个页签仍只给管理员。
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

SOURCE = Path("frontend/src/views/McpManagement.vue").read_text(encoding="utf-8")


def test_non_admin_defaults_to_personal_scope():
    assert "props.personalOnly || !isAdmin.value ? 'personal' : 'global'" in SOURCE


def test_global_tab_is_visible_to_every_logged_in_user():
    # 一级 Tab 行只按 personalOnly 收起
    assert 'v-if="!personalOnly"\n      class="flex flex-shrink-0 items-center overflow-x-auto' in SOURCE
    # 平台页签按钮本身不再有 isAdmin 条件
    assert 'v-if="isAdmin"\n        id="tab-global-mcp"' not in SOURCE
    assert 'id="tab-global-mcp"' in SOURCE


def test_global_registry_uses_fallback_branch():
    assert 'v-else-if="isAdmin"\n        ref="registryRef"' not in SOURCE
    assert (
        'v-else\n        ref="registryRef"\n        :key="activeScope"\n        scope="global"'
        in SOURCE
    )


def test_global_server_count_is_requested_for_everyone():
    assert "isAdmin.value\n      ? axios.get('/api/portal/mcp/servers'" not in SOURCE
    assert "axios.get('/api/portal/mcp/servers', { params: { scope: 'global' } })" in SOURCE


def test_readonly_hint_only_for_non_admin_on_global_tab():
    assert "v-if=\"!personalOnly && !isAdmin && activeScope === 'global'\"" in SOURCE
    assert "平台 MCP 为只读" in SOURCE


def test_audit_and_changes_tabs_stay_admin_only():
    assert 'id="tab-audit-logs"' in SOURCE
    assert "activeScope === 'audit' && isAdmin" in SOURCE
    assert "activeScope === 'changes' && isAdmin" in SOURCE
