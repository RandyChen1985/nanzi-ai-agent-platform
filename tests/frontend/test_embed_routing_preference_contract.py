"""Embed routing preference and integration lock source contracts."""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
EMBED = ROOT / "frontend/src/views/EmbedChat.vue"
SETTINGS = ROOT / "frontend/src/components/embed/ChatSettings.vue"


def test_embed_loads_and_saves_user_routing_preference_through_redis_api():
    source = EMBED.read_text(encoding="utf-8")

    assert "fetchUserPortalPreferences" in source
    assert "/api/portal/portal-prefs" in source
    assert "/api/portal/portal-prefs/routing" in source
    assert "routing_mode" in source
    assert "expert_agent_id" in source
    assert "routing_configured" in source
    assert 'localStorage.getItem("yovole_routing_mode")' not in source
    assert 'localStorage.getItem("yovole_expert_agent_id")' not in source


def test_settings_has_auto_and_default_agent_tabs():
    source = SETTINGS.read_text(encoding="utf-8")

    assert "主专家自动委派" in source
    assert "默认智能体" in source
    assert "routingLocked" in source
    assert "allowedAgents" in source
    assert "switch-to-auto" in source
    assert "switch-to-expert" in source


def test_clicking_default_agent_tab_waits_for_explicit_agent_selection():
    source = SETTINGS.read_text(encoding="utf-8")
    routing_handler = source.split("const handleSetRoutingMode", 1)[1].split(
        "const handleSetExpertAgent", 1
    )[0]

    assert "const routingMode = ref" in source
    assert "routingMode.value = 'expert'" in routing_handler
    assert "allowedAgents?.[0]?.id" not in routing_handler
    expert_branch = routing_handler.split("if (mode === 'auto')", 1)[1].split(
        "return;", 1
    )[1]
    assert "saveAndClose();" not in expert_branch
    assert "v-if=\"routingMode === 'expert'\"" in source


def test_clicking_auto_routing_keeps_settings_open_for_further_choice():
    source = SETTINGS.read_text(encoding="utf-8")
    routing_handler = source.split("const handleSetRoutingMode", 1)[1].split(
        "const handleSetExpertAgent", 1
    )[0]
    auto_branch = routing_handler.split("if (mode === 'auto')", 1)[1].split(
        "return;", 1
    )[0]

    assert "routingMode.value = 'auto'" in auto_branch
    assert "emit('switch-to-auto')" in auto_branch
    assert "saveAndClose();" not in auto_branch


def test_setting_changes_save_without_closing_the_settings_modal():
    source = SETTINGS.read_text(encoding="utf-8")
    script = source.split("</script>", 1)[0]

    assert "const saveSettings = () =>" in script
    assert "const saveAndClose =" not in script
    assert "const close = () => emit('update:visible', false);" in script

    setting_handlers = script.split("const handleSetTheme", 1)[1].split(
        "const showConfirmModal", 1
    )[0]
    assert "saveAndClose" not in setting_handlers
    assert setting_handlers.count("saveSettings();") >= 10


def test_routing_mode_help_text_explains_latency_and_delegation():
    source = SETTINGS.read_text(encoding="utf-8")

    assert "主专家自动委派" in source
    assert "未指定专家时，默认由主专家直接回答，或按任务需要自动委派其他智能体" in source
    assert "先识别问题意图，再选择合适的主智能体" not in source
    assert "可能增加一次路由判断耗时" not in source
    assert "主专家仍可按任务需要调用其他智能体" in source


def test_unconfigured_routing_defaults_to_auto_without_selecting_main():
    source = EMBED.read_text(encoding="utf-8")

    preference_segment = source.split("const saved = savedRoutingPreference.value", 1)[1].split(
        "// 自动应用当前激活智能体推荐的排版风格", 1
    )[0]
    assert "saved.routing_configured" in preference_segment
    assert "const mainAgent = res.data.find" not in preference_segment
    assert 'agentId === "main" || agentName === "main"' not in preference_segment
    assert "!saved.routing_configured && mainAgent" not in preference_segment
    assert 'config.routingMode = "auto"' in preference_segment
    assert 'config.expertAgentId = ""' in preference_segment


def test_integration_agent_lock_covers_all_host_entry_points():
    source = EMBED.read_text(encoding="utf-8")

    assert "integrationAgentLockId" in source
    assert "applyIntegrationAgentLock" in source
    assert "data.agent_id" in source
    assert "sessionData.agent_id" in source
    assert "isRoutingSettingsLocked" in source
    assert ':routing-locked="isRoutingSettingsLocked"' in source

    init_segment = source.split("const applyInitConfigPayload", 1)[1].split("if (data.conversation_id)", 1)[0]
    assert "applyIntegrationAgentLock(agentId)" in init_segment
    assert "switchToExpert(agentId)" not in init_segment

    ticket_segment = source.split("const exchangeTicketAndApply", 1)[1].split("const postInitSuccess", 1)[0]
    assert "applyIntegrationAgentLock(sessionData.agent_id)" in ticket_segment


def test_locked_agent_does_not_persist_as_user_default():
    source = EMBED.read_text(encoding="utf-8")

    assert "if (isRoutingSettingsLocked.value) return" in source
    assert 'axios.put("/api/portal/portal-prefs/routing"' in source
    assert "config.expertAgentId = normalizedAgentId" in source
    lock_function = source.split("const applyIntegrationAgentLock", 1)[1].split("const pinnedAgentLabel", 1)[0]
    assert "saveRoutingSettings();" not in lock_function
