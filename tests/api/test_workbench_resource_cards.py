"""工作台个人资源卡：新增「我的知识库 / 我的数据集」，且顺序在最前。

顺序有产品含义：这两项是资源入口，用户打开工作台最想先看到「我有什么能用」，
所以排在其他个人数据之前；同步时前端常量也要保持同样的顺序。
"""

import pytest

from app.services.workbench_home_service import PERSONAL_RESOURCE_DEFS

pytestmark = pytest.mark.no_infrastructure


def test_first_two_cards_are_knowledge_and_datasets():
    keys = [spec["key"] for spec in PERSONAL_RESOURCE_DEFS]
    assert keys[:2] == ["knowledge", "datasets"]


def test_card_defs_keep_existing_seven():
    keys = {spec["key"] for spec in PERSONAL_RESOURCE_DEFS}
    for key in ("memory", "tokens", "data", "skills", "mcp", "tasks", "inbox"):
        assert key in keys


def test_new_cards_jump_to_square_tab():
    by_key = {spec["key"]: spec for spec in PERSONAL_RESOURCE_DEFS}
    assert by_key["knowledge"]["tab"] == "square"
    assert by_key["datasets"]["tab"] == "square"
    assert by_key["knowledge"]["unit"] == "个"
    assert by_key["datasets"]["unit"] == "个"
