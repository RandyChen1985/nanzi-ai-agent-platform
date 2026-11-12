"""MCP 服务列表头部：搜索前置 + 「仅运行中」筛选的契约。"""

from pathlib import Path

import pytest


REGISTRY = Path("frontend/src/components/system/McpServerRegistry.vue")

pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return REGISTRY.read_text(encoding="utf-8")


def test_list_header_drops_useless_title():
    """「已连接服务」这行标题占位但没有信息量（列表就在正下方），已删除。"""
    assert "已连接服务" not in _source()


def test_search_box_comes_before_other_controls():
    source = _source()
    search_index = source.index('placeholder="搜索服务或工具..."')
    # 用 title 定位，而不是「仅运行中」四个字：那个词在筛选链的注释里也会出现
    toggle_index = source.index('title="只显示已启用的 MCP 服务"')
    assert search_index < toggle_index, "搜索框应排在「仅运行中」左边"
    # 工具栏固定两行：第一行搜索 + 新建，第二行筛选 + 排序 + 计数
    assert "flex flex-col gap-2 border-b" in source[:search_index]


def test_only_running_toggle_filters_disabled_servers():
    source = _source()
    assert "const onlyRunning = ref(false)" in source
    # 复选框改成了 chip 按钮：语义仍是「切换只看运行中」，用 aria-pressed 表达状态
    assert '@click="onlyRunning = !onlyRunning"' in source
    assert ':aria-pressed="onlyRunning"' in source
    # 过滤拆成两段：先在 serversMatchingFilters 里筛（启用 → 待办 → 关键词），
    # 再由 filteredServers 套业务分组。启用过滤仍在关键词之前。
    filter_body = source.split("const serversMatchingFilters = computed(", 1)[1].split("/* ---------------- 业务分组", 1)[0]
    assert "enabled_status === 1" in filter_body
    assert filter_body.index("enabled_status === 1") < filter_body.index("includes(query)")
    # 分组条件必须在筛完之后才应用，否则页签数字会偏离「点进去能看到的条数」
    group_body = source.split("const filteredServers = computed(", 1)[1]
    assert "mcpGroupFilter.value === ALL_GROUP_KEY" in group_body


def test_empty_state_clears_both_filters():
    source = _source()
    assert 'searchQuery ? \'未找到匹配的 MCP 服务\' : \'没有运行中的 MCP 服务\'' in source.replace('"', "'")
    assert "searchQuery = ''; onlyRunning = false" in source
    assert "清空搜索与筛选" in source
