"""主助手（main）不能被设成专家团主专家。

用户场景：编辑 main 时「专家团」页应当整个消失，避免把系统兜底对话入口配成
一个可被分派的「团」；历史数据里若已被置过 true，也不能再出现在专家团视图里。
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

MANAGEMENT = Path("frontend/src/views/AgentManagement.vue").read_text()
BACKEND = Path("app/services/ai/agent_manager.py").read_text()


def test_team_tab_is_not_in_the_initial_tab_list():
    """team 页必须在条件分支里 push，不能挂在初始数组上再隐藏。"""
    tabs = MANAGEMENT.split("const agentFormTabs", 1)[1].split("// 改类型/引擎", 1)[0]
    initial = tabs.split('tabs.push({ key: "team"', 1)[0]
    assert '{ key: "team", label: "专家团" }' not in initial
    assert "if (!isMainAgent(selectedAgent.value ?? agentForm.value))" in tabs
    assert 'tabs.push({ key: "team", label: "专家团" });' in tabs


def test_opening_main_editor_forces_team_flag_off():
    """历史 main 若被置过 true，打开即按关闭处理，保存时顺手清掉。"""
    assert "is_expert_team_lead: agent.is_expert_team_lead === true && !isMainAgent(agent)" in MANAGEMENT


def test_team_views_and_badges_exclude_main():
    assert "agents.value.filter((agent) => agent.is_expert_team_lead && !isMainAgent(agent))" in MANAGEMENT
    assert "(Boolean(a.is_expert_team_lead) && !isMainAgent(a)) === wantLead" in MANAGEMENT
    assert 'v-if="agent.is_expert_team_lead && !isMainAgent(agent)"' in MANAGEMENT


def test_is_main_agent_declared_before_tab_computed():
    """tab 的 watch 建立时会立即求值，isMainAgent 晚于 agentFormTabs 会 TDZ 白屏。"""
    assert MANAGEMENT.index("const isMainAgent = (") < MANAGEMENT.index("const agentFormTabs")


def test_backend_refuses_to_make_main_a_team_lead():
    assert "bool(data.is_expert_team_lead) and not _is_main_general_agent_record(agent)" in BACKEND
    assert "str(data.name or \"\").strip().lower() not in MAIN_GENERAL_AGENT_NAMES" in BACKEND
