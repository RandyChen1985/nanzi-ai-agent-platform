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
    toggle_index = source.index("仅运行中")
    assert search_index < toggle_index, "搜索框应排在「仅运行中」左边"
    # 工具栏固定两行：第一行搜索 + 新建，第二行筛选 + 排序 + 计数
    assert "flex flex-col gap-2 border-b" in source[:search_index]


def test_only_running_toggle_filters_disabled_servers():
    source = _source()
    assert "const onlyRunning = ref(false)" in source
    # 复选框改成了 chip 按钮：语义仍是「切换只看运行中」，用 aria-pressed 表达状态
    assert '@click="onlyRunning = !onlyRunning"' in source
    assert ':aria-pressed="onlyRunning"' in source
    filter_body = source.split("const filteredServers = computed(", 1)[1].split("})", 1)[0]
    assert "enabled_status === 1" in filter_body
    # 关键词过滤要建立在启用过滤之后，否则勾选会被关键词分支绕过
    assert filter_body.index("enabled_status === 1") < filter_body.index("includes(query)")


def test_empty_state_clears_both_filters():
    source = _source()
    assert 'searchQuery ? \'未找到匹配的 MCP 服务\' : \'没有运行中的 MCP 服务\'' in source.replace('"', "'")
    assert "searchQuery = ''; onlyRunning = false" in source
    assert "清空搜索与筛选" in source
