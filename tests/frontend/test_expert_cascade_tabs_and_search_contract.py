"""Contract: EmbedChat ExpertCascadeMenu supports dual tabs (system/custom) and search filtering."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPERT_MENU = ROOT / "frontend/src/components/embed/ExpertCascadeMenu.vue"
TASK_CENTER = ROOT / "frontend/src/views/TaskCenter.vue"


def test_expert_cascade_menu_dual_tabs_and_search_contract():
    content = EXPERT_MENU.read_text(encoding="utf-8")

    # Tab state & search state
    assert "expertTab = ref<'system' | 'custom'>('system')" in content
    assert "expertSearchQuery = ref('')" in content

    # Computed filtered lists
    assert "filteredSystemAgents" in content
    assert "filteredCustomAgents" in content
    assert "shouldShowAutoCard" in content

    # Conditional search bar (> 5 items or has query)
    assert "currentTabTotalCount" in content
    assert "showSearchInput" in content
    assert "currentTabTotalCount.value > 5 || !!expertSearchQuery.value.trim()" in content
    assert 'v-if="showSearchInput"' in content

    # Dual tabs markup & switch tab handler
    assert '@click.stop="switchTab(\'system\')"' in content
    assert '@click.stop="switchTab(\'custom\')"' in content
    assert "系统专家" in content
    assert "自定义专家" in content

    # Search bar markup
    assert 'placeholder="搜索专家名称、标识或说明..."' in content
    assert 'v-model="expertSearchQuery"' in content
    assert "expertSearchQuery = ''" in content

    # Empty match state
    assert "匹配的系统专家" in content
    assert "匹配的自定义专家" in content
    assert "清空搜索条件" in content


def test_task_center_agent_dropdown_search_contract():
    content = TASK_CENTER.read_text(encoding="utf-8")

    assert "currentAgentTabTotalCount" in content
    assert "showAgentSearchInput" in content
    assert "currentAgentTabTotalCount.value > 5 || !!agentSearchQuery.value.trim()" in content
    assert 'v-if="showAgentSearchInput"' in content
    assert '@click.stop="switchAgentTab(\'system\')"' in content
    assert '@click.stop="switchAgentTab(\'custom\')"' in content
