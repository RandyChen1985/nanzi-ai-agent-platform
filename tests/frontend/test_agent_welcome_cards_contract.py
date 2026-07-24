from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_local_agent_version_editor_has_welcome_card_step_and_generation_action():
    management = _source("frontend/src/views/AgentManagement.vue")
    drawer = _source("frontend/src/components/agent/AgentVersionEditorDrawer.vue")
    api = _source("frontend/src/api/agent.ts")

    assert "'welcome'" in management
    assert "欢迎语设置" in management
    assert "欢迎语设置" in drawer
    assert "随机自动推荐" in drawer
    assert "generateWelcomeCards" not in api
    assert "每次打开欢迎页时自动推荐" in drawer
    assert "完整填写 3 张欢迎卡片" in management


def test_welcome_dashboard_uses_version_cards_only_when_enabled():
    dashboard = _source("frontend/src/components/embed/WelcomeDashboard.vue")
    embed = _source("frontend/src/views/EmbedChat.vue")

    assert "welcomeCards" in dashboard
    assert "emit('quick-question', card.prompt)" in dashboard
    assert ':welcome-cards="welcomeCards"' in embed
    assert "/welcome-cards" in embed


def test_welcome_cards_transition_between_fallback_and_refreshed_sets():
    dashboard = _source("frontend/src/components/embed/WelcomeDashboard.vue")

    assert '<Transition name="welcome-card-set"' in dashboard
    assert 'mode="out-in"' in dashboard
    assert "welcomeCardSetKey" in dashboard
    assert "welcome-card-item" in dashboard
    assert "@media (prefers-reduced-motion: reduce)" in dashboard


def test_restored_expert_cards_reload_after_token_becomes_available():
    embed = _source("frontend/src/views/EmbedChat.vue")

    assert "void loadWelcomeCards(effectiveEmbedChatAgentId.value);" in embed


def test_all_editable_local_drafts_offer_save_and_publish_and_clone_creates_version_first():
    drawer = _source("frontend/src/components/agent/AgentVersionEditorDrawer.vue")
    management = _source("frontend/src/views/AgentManagement.vue")

    assert "const canPublishLocalVersion = computed" in drawer
    assert 'v-if="isLastStep() && canPublishLocalVersion"' in drawer
    assert "if (!versionForm.value.id) {" in management
    assert "const created = await agentApi.createVersion(" in management
    assert "const agentId = selectedAgent.value?.id || onboardingAgent.value?.id;" in management
    assert "const versionId = versionForm.value.id || onboardingVersion.value?.id;" in management


def test_published_version_view_explains_why_it_is_read_only():
    drawer = _source("frontend/src/components/agent/AgentVersionEditorDrawer.vue")

    assert "当前为已发布版本，如需修改请克隆新版本" in drawer
    assert "selectedAgent?.is_editable === false" in drawer


def test_welcome_switch_thumb_stays_within_its_track():
    drawer = _source("frontend/src/components/agent/AgentVersionEditorDrawer.vue")
    switch_section = drawer[drawer.index("开启后替换聊天页顶部固定能力卡片"):drawer.index("<template v-if=\"welcomeConfig.enabled\"", drawer.index("开启后替换聊天页顶部固定能力卡片"))]

    assert "flex-shrink-0" in switch_section
    assert "left-0.5" in switch_section
