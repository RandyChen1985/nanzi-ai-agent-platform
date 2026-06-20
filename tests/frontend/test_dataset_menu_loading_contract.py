from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _assert_embed_portal_contract(source: str) -> None:
    assert "useDatasetPortal" in source
    assert "DatasetPortalDrawer" in source
    assert "const datasetMenuLoading = ref(false);" in source
    assert "DATASET_PORTAL_SLASH_COMMAND" in source
    assert "isDatasetPortalSlashCommand" in source
    assert "await openPortalDrawer();" in source
    assert "DatasetCapabilityMenu" in source
    assert "datasetNavigation?: DatasetNavigationPayload;" in source
    assert "lockToDataQueryAgentForDatasetMenu" in source
    assert "capabilities.includes(\"data_query\")" in source
    assert "refreshDatasetMenuNavigation" in source
    assert "recordDatasetMenuQuestionClick" in source
    assert "dataset_menu_hash" in source
    assert "📚 数据门户" in source
    assert "embed_portal_keep_open" in source
    assert "onPortalLoadingChange" in source
    assert "applyPortalViewportLayout" in _source("frontend/src/composables/useDatasetPortal.ts")


def _assert_agent_debug_portal_contract(source: str) -> None:
    assert "useDatasetPortal" in source
    assert "DatasetPortalDrawer" in source
    assert "DATASET_PORTAL_SLASH_COMMAND" in source
    assert "isDatasetPortalSlashCommand" in source
    assert "await openPortalDrawer();" in source
    assert "DatasetCapabilityMenu" in source
    assert "datasetNavigation?: DatasetNavigationPayload;" in source
    assert "lockToDataQueryAgentForDatasetMenu" in source
    assert "refreshDatasetMenuNavigation" in source
    assert "recordPortalQuestionClick" in source


def test_embed_chat_locks_input_while_dataset_menu_loads():
    _assert_embed_portal_contract(_source("frontend/src/views/EmbedChat.vue"))


def test_agent_debug_locks_input_while_dataset_menu_loads():
    _assert_agent_debug_portal_contract(_source("frontend/src/views/AgentDebug.vue"))


def test_dataset_capability_menu_component_contract():
    source = _source("frontend/src/components/chatbi/DatasetCapabilityMenu.vue")
    assert "defineEmits" in source
    assert "quick-question" in source
    assert "record-question-click" in source
    assert "refresh" in source
    assert "payload.generated_at" in source
    assert "payload.dataset_menu_hash" in source
    assert "payload.from_cache" in source
    assert "payload.has_datasets" in source
    assert "isNoPermissionEmpty" in source
    assert "showStatusBanner" in source
    assert "has_datasets === false" in source
    assert "cacheAgeLabel" in source
    assert "我的数据门户" in source
    assert "click_count" in source
    assert "handleQuestionClick" in source
    assert "props.payload.groups" in source
    assert "group.questions" in source
    assert "group.followups" in source
    assert "related_data" in source
    assert "GROUP_REFRESH_COOLDOWN_MS" in source
    assert "startGroupRefreshCooldown" in source
    assert "换一批太频繁，请稍后再试" in source
    assert "暂无更多不同问题，稍后再试" in source
    assert "QUESTIONS_SECTION_TIP" in source
    assert "FOLLOWUPS_SECTION_TIP" in source
    assert "该场景的入门示例问题" in source
    assert "延伸探索型追问" in source


def test_dataset_portal_composable_contract():
    source = _source("frontend/src/composables/useDatasetPortal.ts")
    assert "PORTAL_OPEN_HOTKEY" in source
    assert "shiftKey && key === \"d\"" in source
    assert "readStoredBoolean" in source
    assert "!isMobileViewport()" in source
    assert "from_cache" in source
    assert "has_datasets !== false" in source
    assert "/api/v1/chat/dataset-menu/click" in source


def test_thought_step_dimming_contract():
    source = _source("frontend/src/utils/turnLogDisplay.ts")
    assert "isActiveThoughtStep" in source
    assert "isDimmedThoughtStep" in source
    embed = _source("frontend/src/views/EmbedChat.vue")
    assert "isDimmedThoughtStep(log, msg.isThinking)" in embed
    assert "进行中" in embed


def test_thought_step_timer_contract():
    handlers = _source("frontend/src/utils/agentscopeSseHandlers.ts")
    assert "finalizePendingStreamLogs" in handlers
    assert "isLiveThoughtStepTimer" in handlers
    assert "findPendingAgentReplyLog" in handlers
    embed = _source("frontend/src/views/EmbedChat.vue")
    assert "isLiveThoughtStepTimer(log, allLogs || [])" in embed
    assert "finalizeAllPendingStreamLogs(agentMsg.value)" in embed


def test_dataset_portal_drawer_pin_contract():
    source = _source("frontend/src/components/chatbi/DatasetPortalDrawer.vue")
    assert 'defineModel<boolean>("pinned"' in source
    assert "v-if=\"!pinned\"" in source
    assert "pointer-events-none" in source
    assert "钉住" in source
    assert "已钉住" in source
    assert "translate-y-full" in source
    assert "isMobile" in source
    assert 'teleport to="body"' in source
    assert "max-h-[92%]" in source
    assert "portal-drawer-scroll" in source


def test_portal_prompt_time_anchor_contract():
    source = _source("app/services/ai/executors/prompts.py")
    assert "build_data_query_time_anchor_block" in source
    assert "_portal_time_recommendation_rules" in source
    assert "dataset_navigation_generation_prompt" in source
    assert "build_group_questions_refresh_prompt" in source
    assert "build_group_followups_refresh_prompt" in source
