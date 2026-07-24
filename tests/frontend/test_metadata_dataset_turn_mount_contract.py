"""Contract: 元数据集本轮挂载在聊天与调试入口保持一致。"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure


ROOT = Path(__file__).resolve().parents[2]


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_dataset_mount_composable_exposes_turn_selection_controls():
    source = _source("frontend/src/composables/useDatasetMount.ts")
    assert "activeMetadataDatasetIds" in source
    assert "syncActiveMetadataDatasetsFromInput" in source
    assert "toggleMetadataDatasetActive" in source
    assert "clearActiveMetadataDatasets" in source


def test_chat_input_renders_metadata_dataset_chip():
    source = _source("frontend/src/components/embed/ChatInput.vue")
    assert "metadata_dataset" in source
    assert "📊" in source
    assert "数据集" in source


def test_dataset_capability_menu_exposes_card_mount_dropdown():
    source = _source("frontend/src/components/chatbi/DatasetCapabilityMenu.vue")
    assert "仅本次" in source
    assert "本会话默认" in source
    assert "resolveDatasetIdForGroup" in source
    assert "toggle-metadata-dataset" in source
    assert "pin-metadata-dataset" in source


def test_dataset_portal_drawer_forwards_mount_events_without_top_block():
    source = _source("frontend/src/components/chatbi/DatasetPortalDrawer.vue")
    assert "本轮限定数据集" not in source
    assert "将勾选固定到会话" not in source
    assert "pin-metadata-dataset" in source
    assert "unpin-metadata-dataset" in source


def test_embed_chat_sends_turn_metadata_datasets_and_persists_pins():
    source = _source("frontend/src/views/EmbedChat.vue")
    assert "metadata_dataset_ids" in source
    assert "useDatasetMount" in source
    assert "syncActiveMetadataDatasetsFromInput" in source
    assert "pinMetadataDatasetToSession" in source
    assert "unpinMetadataDatasetFromSession" in source
    assert "persistResourceScope" in source
    assert "const turnMetadataDatasetIds = [...activeMetadataDatasetIds.value]" in source
    assert source.index("const turnMetadataDatasetIds = [...activeMetadataDatasetIds.value]") < source.index("chatInputRef.value.uploadedFiles = []")
    assert "body.metadata_dataset_ids = turnMetadataDatasetIds" in source


def test_agent_debug_sends_turn_metadata_datasets_and_persists_pins():
    source = _source("frontend/src/views/AgentDebug.vue")
    assert "metadata_dataset_ids" in source
    assert "useDatasetMount" in source
    assert "syncActiveMetadataDatasetsFromInput" in source
    assert "pinMetadataDatasetToSession" in source
    assert "unpinMetadataDatasetFromSession" in source
    assert "const turnMetadataDatasetIds = [...activeMetadataDatasetIds.value]" in source
    assert source.index("const turnMetadataDatasetIds = [...activeMetadataDatasetIds.value]") < source.index("chatInputRef.value.uploadedFiles = []")
    assert "requestBody.metadata_dataset_ids = turnMetadataDatasetIds" in source
