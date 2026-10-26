"""配额必须真的可配，而不是"代码里写着两个数字"。

默认值 50 / 2000 只是兜底。如果配置项不落进 `system_configs`，系统配置页的
「知识库」分组里根本看不到它们 —— 那就等于写死了，管理员无从调整。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/SystemConfig.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_quota_keys_are_ordered_into_knowledge_group(page: str) -> None:
    order_block = page.split("if (category === 'knowledge')", 1)[1].split("])", 1)[0]
    assert "knowledge_quota_max_datasets_per_user" in order_block
    assert "knowledge_quota_max_documents_per_dataset" in order_block


def test_quota_inputs_only_accept_digits(page: str) -> None:
    block = page.split("['knowledge_quota_max_datasets_per_user', 'knowledge_quota_max_documents_per_dataset'].includes(item.key)", 1)[1]
    block = block.split("</div>\n                          <div", 1)[0]
    assert "replace(/\\D/g, '')" in block


def test_quota_inputs_explain_zero_means_unlimited(page: str) -> None:
    assert "填 0 表示不限" in page


def test_quota_presets_offer_an_unlimited_option(page: str) -> None:
    block = page.split("['knowledge_quota_max_datasets_per_user', 'knowledge_quota_max_documents_per_dataset'].includes(item.key)", 1)[1]
    block = block.split("</div>\n                          <div", 1)[0]
    assert "'不限'" in block
