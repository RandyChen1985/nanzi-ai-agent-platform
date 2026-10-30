"""工具配置里「绑定了哪些数据集」必须在保存时就校验。

运行时 `get_dataset_schema` 入口只认纯数字 ID，写成数据集名称会被静默丢弃，
等于没绑定；配置页却仍显示「已绑定 N 个」，管理员无从发现。这里锁住保存侧的
校验，把静默失效前移成可见错误。
"""

import pytest
from pydantic import ValidationError

from app.schemas.agent import ToolConfigItem


def test_accepts_numeric_dataset_ids():
    item = ToolConfigItem(name="get_dataset_schema", metadata_dataset_ids=["11", "22"])
    assert item.metadata_dataset_ids == ["11", "22"]


def test_rejects_dataset_names():
    with pytest.raises(ValidationError) as exc:
        ToolConfigItem(name="get_dataset_schema", metadata_dataset_ids=["11", "销售数据集"])

    message = str(exc.value)
    assert "销售数据集" in message
    assert "只接受数据集 ID" in message


def test_other_tools_are_not_validated():
    """只有 get_dataset_schema 用这个字段表达数据集范围，其它工具不要误伤。"""
    item = ToolConfigItem(name="execute_sql_query", metadata_dataset_ids=["销售数据集"])
    assert item.metadata_dataset_ids == ["销售数据集"]


def test_empty_or_missing_ids_pass():
    assert ToolConfigItem(name="get_dataset_schema").metadata_dataset_ids is None
    assert ToolConfigItem(name="get_dataset_schema", metadata_dataset_ids=[]).metadata_dataset_ids == []
