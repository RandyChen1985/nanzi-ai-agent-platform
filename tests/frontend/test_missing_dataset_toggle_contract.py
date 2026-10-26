"""权限配置页的「失联知识库」开关：可隐藏，但已授权的绝不能因此被抹掉。

失联 = 元数据还在、RAGFlow 侧已查不到。这类库不该再分配新授权，
但**已经分配过的授权必须保留**：用户担心的正是「之前配过的库失联后看不见」。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGES = {
    "users": ROOT / "frontend/src/views/Users.vue",
    "roles": ROOT / "frontend/src/views/Roles.vue",
}


def _read(name: str) -> str:
    path = PAGES[name]
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("page_name", sorted(PAGES))
def test_toggle_exists_to_show_or_hide_missing_datasets(page_name):
    page = _read(page_name)
    # 开关 + 计数 + 过滤集三件套
    assert "showMissingDatasets" in page
    assert "missingDatasetCount" in page
    assert "visibleResources" in page
    # 复选框只在知识库页签、且确实存在失联库时出现
    assert "activeResTab === 'datasets' && missingDatasetCount" in page
    assert "显示失联知识库（{{ missingDatasetCount }}）" in page
    # 列表渲染走过滤集合
    assert "visibleResources.value.map" in page


@pytest.mark.parametrize("page_name", sorted(PAGES))
def test_missing_datasets_stay_hidden_by_default_but_selected_ones_are_kept(page_name):
    page = _read(page_name)
    # 默认关闭 = 默认隐藏失联
    assert "const showMissingDatasets = ref(false)" in page
    # 已授权的失联库不进过滤：否则用户看不到"我之前配过它"
    assert "isSelectedDatasetResource(res)" in page
    assert "!isMissingKnowledgeBase(res) || isSelectedDatasetResource(res)" in page


@pytest.mark.parametrize("page_name", sorted(PAGES))
def test_existing_missing_grants_are_no_longer_stripped(page_name):
    """回显与保存都不能剔除失联库，否则一次保存就把既有授权静默删掉。"""
    page = _read(page_name)
    assert "collectMissingDatasetIds" not in page
    # 回显只剩私有库被剔除
    assert "(id: string) => !privateIds.has(id)" in page
    # 保存侧同样只剔私有库
    assert "missingIds" not in page


@pytest.mark.parametrize("page_name", sorted(PAGES))
def test_already_granted_missing_dataset_can_still_be_revoked(page_name):
    """已授权 + 失联：保持可点（能主动取消），未授权的失联库才禁选。"""
    page = _read(page_name)
    assert "isPrivateKnowledgeBase(res) ||" in page
    assert "(isMissingKnowledgeBase(res) && !isSelectedDatasetResource(res))" in page
    # 提示文案要区分「保留中」与「不可分配」
    assert "此前分配的授权仍保留，库恢复后自动生效" in page
    assert "该知识库已在 RAGFlow 失联，不可分配权限" in page


@pytest.mark.parametrize("page_name", sorted(PAGES))
def test_toggle_is_not_chained_with_the_list_branch(page_name):
    """开关是独立工具条，不能和「空态 / 列表」串成 v-if / v-else-if / v-else。

    曾经写成 `v-else-if`：只要存在失联库（开关显示出来），列表分支就永远
    不渲染 —— 现象是「勾了『显示失联知识库』依然一片空白」，而 tab 计数是 0。
    """
    page = _read(page_name)
    assert 'v-else-if="modelResourceEntries.length === 0"' not in page, (
        f"{page_name}: 空态分支仍是 v-else-if，会被失联开关的 v-if 吃掉"
    )
    assert 'v-if="modelResourceEntries.length === 0"' in page
