"""会话历史筛选：前后端契约与两处接线一致性。"""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_shared_composable_is_the_single_source_of_filter_contract():
    shared = _read("frontend/src/composables/chat/useHistoryFilters.ts")
    for contract in (
        "export interface ChatHistoryFilters",
        "export interface AgentOption",
        "export const DEFAULT_HISTORY_FILTERS",
        "export function buildHistoryTimeRange",
        "export function buildHistoryFilterParams",
    ):
        assert contract in shared
    assert '"today"' in shared
    assert '"7d"' in shared
    assert '"30d"' in shared


def test_sidebar_exposes_filter_contract_and_keeps_funnel_outside_input():
    sidebar = _read("frontend/src/components/ChatHistorySidebar.vue")
    for contract in (
        "ChatHistoryFilters",
        "AgentOption",
        "update:filters",
        "reset-filters",
        "showAgentFilter",
        "availableAgents",
        "filterPanelOpen",
        "activeFilterChips",
        "activeFilterCount",
        "filterButtonRef",
        "clearSearch",
    ):
        assert contract in sidebar
    # 类型单一来源：必须从组合式导入，而不是在组件内重复定义
    assert "@/composables/chat/useHistoryFilters" in sidebar
    assert "interface ChatHistoryFilters" not in sidebar
    # 漏斗入口必须在输入框容器之外，避免与内嵌清空按钮挤压。
    # 注意：必须在 template 区间内比较——script 区也声明了这两个标识符
    # （如 `const filterButtonRef = ref(...)`），用全文首次匹配会命中 script 声明。
    template = sidebar[sidebar.index("<template"):]
    assert template.index("filterButtonRef") > template.index("clearSearch")


def test_sidebar_resets_collapsed_groups_on_filter_change():
    sidebar = _read("frontend/src/components/ChatHistorySidebar.vue")
    assert "collapsedGroups.value = { older: false }" in sidebar


def test_sidebar_distinguishes_empty_filter_result():
    sidebar = _read("frontend/src/components/ChatHistorySidebar.vue")
    assert "无匹配筛选结果" in sidebar
    assert "暂无会话历史" in sidebar


def test_embed_chat_maps_filters_into_request_params():
    embed = _read("frontend/src/views/EmbedChat.vue")
    for contract in (
        "useHistoryFilters",
        "historyFilters",
        "buildHistoryFilterParams",
        "resetHistoryFilters",
        "showAgentFilter",
        "@update:filters",
        "@reset-filters",
    ):
        assert contract in embed
    assert "historyPage.value = 1" in embed


def test_agent_debug_is_wired_like_embed_chat():
    debug = _read("frontend/src/views/AgentDebug.vue")
    for contract in (
        "useHistoryFilters",
        "historyFilters",
        "buildHistoryFilterParams",
        "resetHistoryFilters",
        "showAgentFilter",
        "@update:filters",
        "@reset-filters",
    ):
        assert contract in debug
    # 智能体候选复用调试页已有的 agents 数据源，不新增列表请求
    assert "agents.value" in debug
    assert "agentParams.agent_id" in debug


def test_backend_endpoint_uses_shared_query_builder():
    chat = _read("app/api/v1/endpoints/chat.py")
    assert "from app.services.ai.history_query import build_history_query" in chat
    assert "build_history_query(" in chat
    assert "scope: Optional[str] = None" in chat
