"""知识库配额的两个契约：全局固定、口径唯一。

用户在列表页看到 `11`、点进某个库变成 `5 / 50` —— 同一位置两个数，根因是
「我的库配额」被塞进了按库查询的临时状态：

- `datasetQuota` 是详情页的按库用量，`loadDatasetQuota` 每次进库先置 null；
- 左栏又拿它兜 `?? datasets.length`，而 datasets 是「我可见的库」，
  含别人授权给我的，口径与「我拥有的库」完全不同。

正确做法：配额是全局值，只由 /datasets/quota/me 写，整个页面生命周期只增不减。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_owned_quota_has_its_own_state(page: str) -> None:
    """配额不能寄生在 datasetQuota 上，否则切库就被清空。"""
    assert "const ownedQuota = ref<{ used: number; limit: number } | null>(null)" in page
    assert "ownedQuota.value = { used: owned.used, limit:" in page


def test_owned_quota_is_not_derived_from_visible_datasets(page: str) -> None:
    """绝对不允许拿可见库数冒充配额：可见 = 自己的 + 别人共享的。"""
    assert "?? datasets.value.length" not in page
    assert "const ownedDatasetUsage = computed<number | null>(() => ownedQuota.value?.used ?? null)" in page
    assert "const ownedDatasetLimit = computed<number | null>(() => ownedQuota.value?.limit || null)" in page


def test_dataset_quota_no_longer_writes_owned_fields(page: str) -> None:
    """按库接口返回的 owned_datasets 不再回写 datasetQuota，避免两个来源打架。"""
    start = page.index("const loadDatasetQuota = async () => {")
    block = page[start: start + 600]
    assert "owned_datasets" not in block


def test_detail_page_and_sidebar_read_the_same_state(page: str) -> None:
    """详情页「我的知识库」与左栏配额条必须是同一份数据。"""
    assert page.count("ownedDatasetUsage") >= 3
    assert "datasetQuota.owned_datasets" not in page


def test_quota_falls_back_to_dash_not_zero(page: str) -> None:
    """接口失败时显示「—」：编一个 0 会让人以为库被删了。"""
    assert 'title="配额接口暂时不可用">—</span>' in page
    assert "v-if=\"ownedDatasetUsage !== null\"" in page


def test_document_quota_is_visible_in_library_header(page: str) -> None:
    """库头的文档配额要能一眼看到，不必点开「库信息」抽屉。"""
    start = page.index("<!-- 文档配额：以前只藏在「库信息」抽屉里")
    block = page[start: start + 1200]
    assert "文档配额" in block
    assert "datasetQuota?.documents?.used" in block
    assert "datasetQuota?.documents?.limit" in block
    # 条与抽屉同一套配色：接近上限时变黄/变红
    assert "docQuotaTone.split(' ')[0]" in block


def test_document_quota_reads_are_optional_chained(page: str) -> None:
    """详情页可能还没拿到配额，模板不能硬取 documents.limit 而报错。"""
    start = page.index("<!-- 文档配额：以前只藏在「库信息」抽屉里")
    block = page[start: start + 1200]
    assert ":title=\"datasetQuota?.documents?.limit" in block
    assert "{{ datasetQuota?.documents?.limit ?? '不限' }}" in block
