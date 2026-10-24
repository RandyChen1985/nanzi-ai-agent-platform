from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]


def test_archived_versions_offer_clone_without_making_archive_editable():
    source = (ROOT / "frontend/src/components/agent/AgentVersionsDrawer.vue").read_text(encoding="utf-8")
    archived = source[source.index("<!-- Archived List -->"):]

    assert '@click="emit(\'edit-version\', v)"' in archived
    assert '@click="emit(\'create-version\', v)"' in archived

    clone_action = archived[archived.index('@click="emit(\'create-version\', v)"') - 300:]
    # 归档版本只提供「克隆」，不提供直接编辑；克隆属于版本内容编辑权
    assert 'v-if="agent?.can_edit_versions !== false"' in clone_action
    assert 'title="基于此版本新建"' in clone_action


def test_archived_clone_reuses_parent_new_draft_flow():
    management = (ROOT / "frontend/src/views/AgentManagement.vue").read_text(encoding="utf-8")

    assert "const handleDrawerCreateVersion = (baseVersion?: AIAgentVersion) =>" in management
    assert "openVersionModal(baseVersion, true);" in management
    assert "id: undefined, // Clear ID to create new" in management
    assert "version_number: undefined, // Let backend assign next" in management
    assert 'status: "DRAFT", // Reset status' in management
