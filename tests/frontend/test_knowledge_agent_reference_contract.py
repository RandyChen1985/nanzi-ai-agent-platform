"""知识库被智能体引用的影响面提示契约。

绑定关系存在 ai_agents.engine_config.dataset_ids，没有反向索引。删除知识库或
收窄可见范围前必须能回答「会打挂哪些智能体」——否则智能体会静默检索不到内容。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_detail_header_shows_agent_reference_count(page: str) -> None:
    assert "agent_ref_count" in page
    assert "openDatasetReferences" in page
    assert "/agents" in page


def test_references_tab_lists_agents_and_grants_separately(page: str) -> None:
    """给角色分配知识库 ≠ 智能体引用，两者必须在同一个 tab 里并列说清。"""
    assert "引用与授权" in page
    assert "智能体引用" in page
    assert "授权访问" in page
    assert "给角色分配知识库不等于智能体会用它" in page


def test_detail_header_shows_reference_slot_even_when_zero(page: str) -> None:
    """0 引用时也要有「未被智能体引用」，否则没人知道这里有这个维度。"""
    assert "未被智能体引用" in page


def test_delete_confirm_lists_referencing_agents(page: str) -> None:
    assert "loadDeleteAgentRefs" in page
    assert "deleteAgentRefs" in page
    assert "检索不到本库内容" in page


def test_delete_flow_loads_refs_when_opening_confirm(page: str) -> None:
    start = page.index("const triggerDeleteDataset")
    end = page.index("const deleteAgentRefs")
    segment = page[start:end]
    assert "loadDeleteAgentRefs(dataset)" in segment
