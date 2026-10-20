"""数据源 / MCP 页面的按钮级权限隐藏契约。

后端已经按码校验，但如果界面不隐藏按钮，没权限的人点下去只会收到 403，
「能做什么」在界面上看不出来。这里锁住：两个页面的写操作按钮都要按对应码隐藏。

只读入口（页面本身、刷新列表、查看画像）不在这里约束——它们由菜单权限控制。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DATA_SOURCES = ROOT / "frontend/src/views/DataSourceManagement.vue"
MCP_REGISTRY = ROOT / "frontend/src/components/system/McpServerRegistry.vue"
MCP_TOOL_TESTER = ROOT / "frontend/src/components/system/McpToolTester.vue"

pytestmark = pytest.mark.no_infrastructure


def _read(path: Path) -> str:
    assert path.exists(), f"缺少 {path}"
    return path.read_text(encoding="utf-8")


def test_data_source_page_hides_write_actions_without_permission():
    source = _read(DATA_SOURCES)

    assert 'import { useUser }' in source
    assert "hasPermission('element:data_sources:edit')" in source
    assert "hasPermission('element:data_sources:delete')" in source

    # 按钮本身必须真的绑定，光算出来不用等于没做
    assert 'v-if="canEditDataSource"' in source, "添加数据源 / 编辑 / 测试 / SQL 调试要按 edit 隐藏"
    assert 'v-if="canDeleteDataSource"' in source, "删除要按 delete 隐藏"


def test_mcp_registry_hides_actions_per_code():
    source = _read(MCP_REGISTRY)

    for code in [
        "element:mcp_management:create",
        "element:mcp_management:edit",
        "element:mcp_management:delete",
        "element:mcp_management:sync",
        "element:mcp_management:publish",
    ]:
        assert code in source, f"缺少 {code}"

    for flag in [
        "canCreateMcp",
        "canEditMcp",
        "canDeleteMcp",
        "canSyncMcp",
        "canPublishMcp",
    ]:
        assert f'v-if="{flag}"' in source, f"{flag} 没有绑定到按钮上"


def test_mcp_tool_tester_hides_execute_without_permission():
    source = _read(MCP_TOOL_TESTER)

    assert "element:mcp_management:execute" in source
    assert "canExecuteMcp" in source


def test_mcp_enable_switch_is_read_only_without_edit_permission():
    """「我的 MCP」列表里的启用开关：没权限时置灰只读，而不是点了才报 403。"""
    source = _read(MCP_REGISTRY)

    assert '!canEditMcp' in source, "启用开关没有按 edit 权限置灰"
    assert ':disabled="!canSave || !canEditMcp || statusLoading[server.id]"' in source


def test_mcp_publish_button_requires_publish_permission():
    source = _read(MCP_REGISTRY)

    assert '!canPublishMcp' in source


def test_data_source_profiling_dropdown_hidden_without_edit():
    """「摸排」是写操作（触发画像任务），没 edit 权限时整个下拉都不该出现。"""
    source = _read(DATA_SOURCES)

    assert '<div v-if="canEditDataSource" class="relative">' in source
    assert "toggleActionMenu(item.id, 'profile')" in source


def test_profile_ignore_switch_is_read_only_without_edit():
    """画像弹窗里的「忽略表」开关是写操作，没权限时置灰而非消失（状态要可见）。"""
    modal = _read(ROOT / "frontend/src/components/metadata/DbTableProfileExplorerModal.vue")

    assert "canEditProfiles" in modal
    assert "!canEditProfiles" in modal
