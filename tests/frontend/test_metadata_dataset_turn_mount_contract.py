"""Contract: 元数据集本轮挂载在聊天与调试入口保持一致。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_dataset_mount_composable_exposes_turn_selection_controls():
    source = _source("frontend/src/composables/useDatasetMount.ts")
    assert "activeMetadataDatasetIds" in source
    assert "toggleMetadataDatasetActive" in source
    assert "clearActiveMetadataDatasets" in source


def test_dataset_portal_drawer_exposes_turn_mount_and_session_pin_controls():
    source = _source("frontend/src/components/chatbi/DatasetPortalDrawer.vue")
    assert "本轮限定数据集" in source
    assert "本轮勾选优先于会话挂载" in source
    assert "将勾选固定到会话" in source
    assert "sessionMountedDatasetIds" in source


def test_embed_chat_sends_turn_metadata_datasets_and_persists_pins():
    source = _source("frontend/src/views/EmbedChat.vue")
    assert "metadata_dataset_ids" in source
    assert "useDatasetMount" in source
    assert "pinMetadataDatasetsToSession" in source
    assert "persistResourceScope" in source


def test_agent_debug_sends_turn_metadata_datasets_and_persists_pins():
    source = _source("frontend/src/views/AgentDebug.vue")
    assert "metadata_dataset_ids" in source
    assert "useDatasetMount" in source
    assert "pinMetadataDatasetsToSession" in source
