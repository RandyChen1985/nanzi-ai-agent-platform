"""MCP 服务列表的密度契约：市场条降级、卡片减负、操作收纳、排序与时间显示。"""

from pathlib import Path

import pytest


REGISTRY = Path("frontend/src/components/system/McpServerRegistry.vue")

pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return REGISTRY.read_text(encoding="utf-8")


def test_market_entry_collapses_to_one_line_once_servers_exist():
    """外链市场不该常驻首屏：有服务时收成一行，空列表才展开引导卡。"""
    source = _source()
    assert 'v-if="!loading && servers.length > 0"' in source
    assert "去魔搭发现更多 MCP 服务" in source
    assert 'v-else-if="!loading"' in source
    assert "立即前往市场" in source
    # 外链一律带 noopener
    assert 'rel="noopener noreferrer"' in source


def test_server_card_actions_collapse_into_overflow_menu():
    """四个图标会挤占 360px 左栏，收进「⋯」；但键盘用户必须能看见。"""
    source = _source()
    assert 'aria-label="`${server.server_name} 的更多操作`"' in source
    assert "openActionMenu($event, server)" in source
    for action in ("share", "logs", "edit", "sync", "delete"):
        assert f"runServerAction('{action}')" in source
    assert "lg:opacity-0 lg:group-hover:opacity-100" in source
    assert "lg:focus-visible:opacity-100" in source


def test_server_card_drops_url_and_duplicate_status_text():
    """URL 只在选中后的详情里出现；「运行中」文字与开关重复表达，已收敛为状态点。"""
    source = _source()
    template = source[source.index("<template>"):]
    assert "server.sse_url" not in template
    assert "selectedServer.sse_url" in template
    assert "{{ server.enabled_status === 1 ? '运行中' : '已禁用' }}" not in source
    assert "formatRelativeTime(server.last_sync_at)" in source
    # 备注允许两行，长文本用 title 看全（移动端无法 hover 但桌面可读）
    assert 'class="mt-1 line-clamp-2 text-[11px] leading-snug text-gray-500"' in template
    assert ':title="server.remark"' in template


def test_left_column_uses_fixed_widths_instead_of_one_third():
    """w-1/3 在 1024 只剩 330px、在 1920 又变成 600px，两头都不合适。"""
    source = _source()
    assert "lg:w-[360px]" in source
    assert "xl:w-[400px]" in source
    assert "lg:w-1/3" not in source


def test_sort_defaults_to_running_first_without_mutating_source_list():
    source = _source()
    assert "const sortBy = ref<ServerSortKey>('default')" in source
    assert "const next = [...list]" in source
    assert "Number(b.enabled_status === 1) - Number(a.enabled_status === 1)" in source
    for option in ("recent", "name", "tools"):
        assert f'<option value="{option}">' in source


def test_tool_param_schema_parse_is_guarded():
    """坏 schema 不能在渲染阶段抛错，否则整页工具列表都挂掉。"""
    source = _source()
    assert "function toolParamNames" in source
    assert "JSON.parse(tool.parameter_schema).properties" not in source
    assert "toolParamNames(tool).length" in source


def test_toolbar_is_fixed_two_rows():
    """窄左栏里「新建」会被挤到第二行右侧单独占位，固定两行更稳。"""
    source = _source()
    start = source.index("flex flex-col gap-2 border-b border-gray-100 bg-gray-50/80")
    end = source.index('class="custom-scrollbar', start)
    toolbar = source[start:end]
    assert 'placeholder="搜索服务或工具..."' in toolbar
    assert "仅运行中" in toolbar
    assert "{{ filteredServers.length }} / {{ servers.length }} 个服务" in toolbar
    # 搜索属于第一行，筛选与排序属于第二行
    assert toolbar.index('placeholder="搜索服务或工具..."') < toolbar.index("仅运行中")

def test_selected_server_is_visually_unmistakable():
    """选中态要同时有左条、底色、内描边和主色名称；hover 用中性色避免混淆。"""
    source = _source()
    template = source[source.index("<template>"):]
    assert "border-primary bg-primary/10 ring-1 ring-inset ring-primary/25" in template
    assert ":aria-current=\"selectedServer?.id === server.id ? 'true' : undefined\"" in template
    assert "selectedServer?.id === server.id ? 'text-primary'" in template
    # 选中项 hover 时保持主色底，不能被灰色 hover 盖掉
    assert "hover:bg-primary/15" in template
    assert "hover:bg-gray-50 dark:hover:bg-gray-800/50" in template


def test_list_divider_does_not_override_selection_border():
    """divide-* 会设置四边 border-color，特异性高于 .border-primary；
    第二项起左边条会被染成分隔色，看上去像「只有第一个选中」。
    分隔线必须只作用于 border-top。"""
    source = _source()
    assert "divide-y divide-gray-50" not in source
    assert "[&>*+*]:border-t" in source
    assert "[&>*+*]:border-t-gray-50" in source
