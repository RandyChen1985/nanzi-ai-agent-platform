from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]
VIEW = ROOT / "frontend/src/views/AgentManagement.vue"
SERVICE = ROOT / "app/services/ai/agent_manager.py"
ENDPOINT = ROOT / "app/api/portal/endpoints/agents.py"


def test_main_agent_is_fixed_first_and_not_draggable():
    view = VIEW.read_text(encoding="utf-8")
    service = SERVICE.read_text(encoding="utf-8")

    assert view.count("const isMainAgent") == 1
    assert "isMainAgent" in view
    assert "isMainAgent(a)" in view
    assert "isMainAgent(agent)" in view
    assert "isMainAgent(sourceId)" in view
    assert "MAIN_GENERAL_AGENT_ID" in service
    assert "_is_main_general_agent_record(agent)" in service


def test_agent_management_header_keeps_title_single_line_and_search_takes_full_row():
    """搜索框从「固定 208px」改成「独占一栏」。

    原先和状态/类型筛选挤在一行，长名称搜到一半就看不见了；现在搜索吃掉整行剩余宽度，
    筛选条件挪进「筛选」面板。
    """
    view = VIEW.read_text(encoding="utf-8")

    assert 'class="shrink-0 whitespace-nowrap text-xl font-bold text-gray-900 sm:text-2xl"' in view
    assert 'class="relative min-w-[220px] flex-1"' in view
    assert "sm:w-52 lg:w-56" not in view


def test_agent_management_hides_dragging_hint_when_main_is_fixed():
    view = VIEW.read_text(encoding="utf-8")

    assert "拖动卡片或列表行可调整排序" not in view


def test_drag_sort_payload_excludes_fixed_main_agent():
    view = VIEW.read_text(encoding="utf-8")

    assert "filteredAgents.value" in view
    assert ".filter((agent) => !isMainAgent(agent))" in view
    assert "(a.is_enabled ?? true) !== (b.is_enabled ?? true)" in view


def test_agent_listing_prioritizes_enabled_agents_before_disabled_agents():
    service = SERVICE.read_text(encoding="utf-8")

    assert "enabled_first" in service
    assert "AIAgent.is_enabled == True" in service


def test_reorder_failure_message_explains_fixed_main_agent():
    endpoint = ENDPOINT.read_text(encoding="utf-8")

    assert "排序失败：主智能体不可参与排序" in endpoint
