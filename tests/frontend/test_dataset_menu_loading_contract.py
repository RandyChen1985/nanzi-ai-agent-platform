from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _assert_dataset_menu_loading_contract(source: str) -> None:
    assert "const datasetMenuLoading = ref(false);" in source
    assert "if (datasetMenuLoading.value || isProcessing.value) {" in source
    assert "datasetMenuLoading.value = true;" in source
    assert "isProcessing.value = true;" in source
    assert "datasetMenuLoading.value = false;" in source
    assert "isProcessing.value = false;" in source
    assert "case \"/dataset_menu\":" in source
    assert "let datasetMenuThoughtTimer: ReturnType<typeof setInterval> | null = null;" in source
    assert "datasetMenuThoughtTimer = setInterval(() => {" in source
    assert "navMsg.value.thoughtDuration = (" in source
    assert "clearInterval(datasetMenuThoughtTimer);" in source
    assert "datasetMenuThoughtTimer = null;" in source
    assert "DatasetCapabilityMenu" in source
    assert "datasetNavigation?: DatasetNavigationPayload;" in source
    assert "navMsg.value.datasetNavigation = payload;" in source
    assert "lockToDataQueryAgentForDatasetMenu" in source
    assert "capabilities.includes(\"data_query\")" in source
    assert "refreshDatasetMenuNavigation" in source
    assert "refresh: true" in source
    assert "recordDatasetMenuQuestionClick" in source
    assert "/api/v1/chat/dataset-menu/click" in source
    assert "dataset_menu_hash" in source
    assert "📚 数据门户" in source
    assert "系统 · 数据门户" in source
    assert "正在生成我的数据门户，请稍后" in source


def test_embed_chat_locks_input_while_dataset_menu_loads():
    _assert_dataset_menu_loading_contract(_source("frontend/src/views/EmbedChat.vue"))


def test_agent_debug_locks_input_while_dataset_menu_loads():
    _assert_dataset_menu_loading_contract(_source("frontend/src/views/AgentDebug.vue"))


def test_dataset_capability_menu_component_contract():
    source = _source("frontend/src/components/chatbi/DatasetCapabilityMenu.vue")
    assert "defineEmits" in source
    assert "quick-question" in source
    assert "record-question-click" in source
    assert "refresh" in source
    assert "payload.generated_at" in source
    assert "payload.dataset_menu_hash" in source
    assert "我的数据门户" in source
    assert "click_count" in source
    assert "handleQuestionClick" in source
    assert "props.payload.groups" in source
    assert "group.questions" in source
    assert "group.followups" in source
    assert "related_data" in source
