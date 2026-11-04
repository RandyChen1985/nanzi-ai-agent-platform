"""专家中心弹窗的「专家团」tab。

弹窗原来只有「系统专家 / 自定义专家」两栏：团主专家混在自定义里，只能靠一枚
👥 TEAM 徽标区分。团一多就挑不出来，而且选中之后的用法跟普通专家完全不同
（背后是一整团在调度）。现在单开一栏。

分类是**互斥**的：团主一律归到「专家团」，不再出现在系统 / 自定义里——
否则同一张卡在两个 tab 各算一次，三个 tab 的计数之和会对不上「共 N 个专家」。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

MENU = Path("frontend/src/components/embed/ExpertCascadeMenu.vue").read_text()


def _block(start: str, end: str) -> str:
    return MENU.split(start, 1)[1].split(end, 1)[0]


def test_tab_state_has_three_values():
    assert "const expertTab = ref<'system' | 'custom' | 'team'>('system')" in MENU
    assert "const switchTab = (tab: 'system' | 'custom' | 'team') => {" in MENU


def test_team_leads_are_classified_into_the_team_tab_only():
    team = _block("const expertTeams = computed", "const filterList")
    assert "agent.is_expert_team_lead === true" in team

    system = _block("const systemAgents = computed", "const customAgents = computed")
    assert "!agent.is_expert_team_lead" in system

    custom = _block("const customAgents = computed", "const expertTeams = computed")
    assert "!agent.is_expert_team_lead" in custom


def test_team_tab_counts_its_own_list():
    counter = _block("const currentTabTotalCount = computed", "const showSearchInput")
    assert "expertTeams.value.length" in counter
    # 搜索时 tab 上显示「命中/总数」，也要用专家团自己的基数
    assert "${filteredExpertTeams.length}/${expertTeams.length}" in MENU


def test_custom_and_team_tabs_share_one_row_renderer():
    """两栏行结构一致，靠数据源切换，避免复制一整段模板后各改各的。"""
    assert 'v-else-if="expertTab === \'custom\' || expertTab === \'team\'"' in MENU
    assert 'v-for="agent in currentTabAgents"' in MENU
    assert 'v-for="agent in filteredCustomAgents"' not in MENU


def test_team_tab_hides_the_now_redundant_team_badge():
    assert 'v-if="agent.is_expert_team_lead && !isTeamTab"' in MENU


def test_selecting_a_team_lead_opens_the_team_tab():
    watcher = _block("watch(\n  () => [props.expertAgentId, props.routingMode]", "const currentTabTotalCount")
    assert "if (match.is_expert_team_lead) expertTab.value = 'team'" in watcher


def test_footer_count_includes_teams_when_searching():
    footer = _block("<!-- 底部统计 -->", "</template>")
    assert "filteredExpertTeams.length" in footer
