from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.models.metadata import MetaColumn, MetaDataset, MetaTable
from app.services.metadata_service import MetadataService


class _FakeLLMResponse:
    def __init__(self, content: str):
        self.content = content


@pytest.mark.asyncio
@patch(
    "app.services.ai.config.AgentConfigProvider.get_configured_llm",
    new_callable=AsyncMock,
)
async def test_recommend_column_semantic_success_with_samples(mock_get_llm):
    """测试当源表中存在物理字段时，成功采样并调用 LLM 推断业务语义。"""
    mock_db = AsyncMock()

    ds = MetaDataset(id=1, name="员工域", data_source="mysql_hr")
    mock_ds_res = MagicMock()
    mock_ds_res.scalars.return_value.first.return_value = ds

    col_id = MetaColumn(physical_name="id", term="主键编号")
    table = MetaTable(id=10, dataset_id=1, physical_name="employee", columns=[col_id])
    mock_table_res = MagicMock()
    mock_table_res.scalars.return_value.first.return_value = table

    mock_db.execute.side_effect = [mock_ds_res, mock_table_res]

    # mock adapter
    mock_adapter = AsyncMock()
    mock_adapter.get_columns.return_value = [
        {"name": "id", "type": "int", "comment": "主键编号"},
        {"name": "age", "type": "int", "comment": "周岁年龄"},
    ]
    mock_adapter.execute_sql.return_value = {
        "items": [[25], [32], [18]]
    }

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = _FakeLLMResponse(
        '{"term": "员工年龄", "description": "员工实际周岁年龄，用于统计与薪资计算", "synonyms": ["age", "周岁"]}'
    )
    mock_get_llm.return_value = mock_llm

    with patch("app.services.data_adapter.factory.get_adapter", new_callable=AsyncMock) as mock_get_adapter:
        mock_get_adapter.return_value = mock_adapter

        res = await MetadataService.recommend_column_semantic(
            mock_db,
            dataset_id=1,
            table_name="employee",
            column_name="age",
            with_samples=True,
        )

    assert res["physical_exists"] is True
    assert res["physical_type"] == "Int64"
    assert res["comment"] == "周岁年龄"
    assert res["sample_values"] == [25, 32, 18]
    assert res["term"] == "员工年龄"
    assert res["description"] == "员工实际周岁年龄，用于统计与薪资计算"
    assert res["synonyms"] == ["age", "周岁"]
    assert res["llm_succeeded"] is True


@pytest.mark.asyncio
async def test_recommend_column_semantic_column_not_in_physical_table():
    """测试物理字段在源表中不存在时，精准识别 physical_exists=False，不产生虚假推荐。"""
    mock_db = AsyncMock()

    ds = MetaDataset(id=1, name="员工域", data_source="mysql_hr")
    mock_ds_res = MagicMock()
    mock_ds_res.scalars.return_value.first.return_value = ds
    mock_db.execute.return_value = mock_ds_res

    mock_adapter = AsyncMock()
    # 物理表中只有 id 和 name，没有 non_existing_field
    mock_adapter.get_columns.return_value = [
        {"name": "id", "type": "int", "comment": "主键编号"},
        {"name": "name", "type": "varchar", "comment": "姓名"},
    ]

    with patch("app.services.data_adapter.factory.get_adapter", new_callable=AsyncMock) as mock_get_adapter:
        mock_get_adapter.return_value = mock_adapter

        res = await MetadataService.recommend_column_semantic(
            mock_db,
            dataset_id=1,
            table_name="employee",
            column_name="non_existing_field",
        )

    assert res["physical_exists"] is False
    assert "不存在字段 'non_existing_field'" in res["error_message"]
    assert res["term"] is None
    assert res["description"] is None


@pytest.mark.asyncio
async def test_recommend_column_semantic_no_data_source():
    """测试数据集未绑定物理数据源时，提示无法推荐。"""
    mock_db = AsyncMock()

    ds = MetaDataset(id=2, name="空数据源域", data_source="")
    mock_ds_res = MagicMock()
    mock_ds_res.scalars.return_value.first.return_value = ds
    mock_db.execute.return_value = mock_ds_res

    res = await MetadataService.recommend_column_semantic(
        mock_db,
        dataset_id=2,
        table_name="test_tbl",
        column_name="test_col",
    )

    assert res["physical_exists"] is False
    assert "未绑定物理数据源" in res["error_message"]


@pytest.mark.asyncio
async def test_recommend_column_semantic_dataset_not_found():
    """测试数据集不存在时抛出 ValueError。"""
    mock_db = AsyncMock()
    mock_ds_res = MagicMock()
    mock_ds_res.scalars.return_value.first.return_value = None
    mock_db.execute.return_value = mock_ds_res

    with pytest.raises(ValueError):
        await MetadataService.recommend_column_semantic(
            mock_db,
            dataset_id=999,
            table_name="test_tbl",
            column_name="test_col",
        )
