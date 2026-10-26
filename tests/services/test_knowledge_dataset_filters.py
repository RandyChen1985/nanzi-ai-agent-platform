"""平台内置知识库的识别规则。

`chatbi-example-meta` 这类库是平台自己建和维护的，不该出现在「发现」或
知识库管理列表里——用户申请了也用不上，看到还会以为是自己该管的库。
"""

import pytest

from app.services.knowledge_dataset_filters import is_internal_dataset_name

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.parametrize(
    "name",
    ["chatbi-example-meta", "chatbi-example", "ChatBI-Example-Meta", "meta-orders", "META-x"],
)
def test_internal_datasets_are_filtered(name):
    assert is_internal_dataset_name(name) is True


@pytest.mark.parametrize("name", ["制度库", "chatbi", "meta", "meta_orders", "制度-meta"])
def test_regular_datasets_are_kept(name):
    assert is_internal_dataset_name(name) is False


@pytest.mark.parametrize("name", [None, "", "   "])
def test_blank_names_count_as_internal(name):
    """没名字的库既没法展示也没法申请，按内置处理，别让它占列表位。"""
    assert is_internal_dataset_name(name) is True
