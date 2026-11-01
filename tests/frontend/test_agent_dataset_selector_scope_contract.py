"""智能体/系统配置的知识库选择弹窗：要有可见范围标签，且不列个人私有库。

私有库只有创建人能检索，配进智能体或系统默认库对其他使用者无效，
所以这类"给所有人用"的选择器必须把它排除；知识检索测试页则要保留，
否则用户没法测自己的私有库。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
SELECTOR = ROOT / "frontend/src/components/RagFlowResourceSelector.vue"
AGENT_PAGE = ROOT / "frontend/src/views/AgentManagement.vue"
SYSTEM_PAGE = ROOT / "frontend/src/views/SystemConfig.vue"
RETRIEVAL_PAGE = ROOT / "frontend/src/views/KnowledgeRetrievalTest.vue"


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


def test_selector_exposes_visibility_badges():
    page = _read(SELECTOR)
    # 三档徽标与标签文案，和知识库管理页、权限配置页保持一致
    assert "授权可见" in page and "TEAM 级" in page and "个人私有" in page
    assert "scopeBadge(ds)" in page
    assert "bg-blue-50 text-blue-600" in page      # 授权可见
    assert "bg-violet-50 text-violet-600" in page  # TEAM 级
    assert "bg-amber-50 text-amber-600" in page    # 个人私有


def test_selector_hides_private_datasets_behind_a_flag():
    page = _read(SELECTOR)
    assert "excludePrivate" in page
    # 渲染走过滤后的集合，而不是原始列表
    assert 'v-for="ds in filteredDatasets"' in page
    assert "filteredDatasets.length === 0" in page
    # 不能静默隐藏：要说明隐藏了几个，以及仍在配置里的私有库
    assert "hiddenPrivateCount" in page
    assert "selectedPrivateNames" in page


def test_selected_private_dataset_is_not_silently_dropped():
    """已选中的私有库必须保留在配置里并显式提示，不能因为隐藏就悄悄去掉。"""
    page = _read(SELECTOR)
    # 合法性校验用的是完整列表（datasets），不是 visibleDatasets
    assert "const validIds = new Set(datasets.value.map((d) => d.id))" in page
    assert "改选授权可见或 TEAM 级" in page
    # 提示之外还要给出可执行的出口：能让用户当场解绑
    assert 'v-for="ds in pinnedPrivateDatasets"' in page
    assert '@click="toggleSelection(ds.id)"' in page


def test_agent_and_system_config_exclude_private_but_retrieval_test_keeps_them():
    agent = _read(AGENT_PAGE)
    assert ':exclude-private="ragSelectorType === \'dataset\'"' in agent

    system = _read(SYSTEM_PAGE)
    assert "exclude-private" in system

    # 检索测试页要保留私有库：不传这个 prop
    retrieval = _read(RETRIEVAL_PAGE)
    assert "exclude-private" not in retrieval
