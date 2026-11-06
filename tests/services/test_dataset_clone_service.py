"""数据集「复制新建」服务层测试。

覆盖两类内容：

1. 唯一性预检（向导第 1 步的实时查重）：空值 / 格式非法 / 已占用 / 可用；
2. 复制入口的早退校验（源不存在、ID 非法、重名、空表、数据源非法），
   以及成功路径的摘要与初始化日志条数。

用 MagicMock 拼一个够用的 ``AsyncSession``：这里验证的是「判断逻辑与落库顺序」，
真实 SQL 由集成环境覆盖（本机没有 MySQL 时那部分跑不起来）。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.metadata import MetaDataset
from app.schemas.metadata import (
    CloneDatasetRequest,
    CloneRelationshipPayload,
    ColumnSchema,
    MetricSchema,
    TableCreate,
)
from app.services.dataset_clone_service import CloneValidationError, DatasetCloneService

pytestmark = pytest.mark.no_infrastructure


def _result(value):
    """模拟 ``Result``：同时支持 ``scalar_one_or_none()`` 与 ``first()``。"""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    result.first = MagicMock(return_value=None if value is None else (value,))
    return result


def _session(*results, data_source_exists=True):
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=list(results))
    conn.scalar = AsyncMock(return_value=1 if data_source_exists else None)
    conn.flush = AsyncMock()
    conn.commit = AsyncMock()
    conn.refresh = AsyncMock()

    counter = {"next": 100}

    def _add(obj):
        if getattr(obj, "id", None) is None:
            counter["next"] += 1
            obj.id = counter["next"]

    conn.add = MagicMock(side_effect=_add)
    return conn


def _source(**overrides):
    source = MetaDataset(
        id=12,
        name="nanzi_resources",
        display_name="资源数据集",
        description="源描述",
        tags=["资源中心"],
        data_source="nanzi_clickhouse",
        status=1,
    )
    for key, value in overrides.items():
        setattr(source, key, value)
    return source


def _payload(**overrides):
    data = dict(
        name="nanzi_resources_copy",
        display_name="资源数据集（副本）",
        description="副本描述",
        tags=["副本"],
        data_source="nanzi_clickhouse",
        tables=[
            TableCreate(
                physical_name="dim_resource",
                term="资源维表",
                columns=[
                    ColumnSchema(physical_name="resource_id", term="资源ID", type="String", is_primary=True),
                    ColumnSchema(physical_name="resource_name", term="资源名称", type="String"),
                ],
            )
        ],
        relationships=[],
        metrics=[],
        copy_permissions=False,
        log_mode="detail",
    )
    data.update(overrides)
    return CloneDatasetRequest(**data)


# ---------------------------------------------------------------------------
# 1. 唯一性预检
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_unique_reports_empty_name_and_display_name():
    conn = _session()
    data = await DatasetCloneService.check_unique(conn, name="", display_name="")

    assert data["name"]["available"] is False
    assert data["name"]["reason"] == "empty"
    assert data["display_name"]["available"] is False
    assert data["display_name"]["reason"] == "empty"
    # 空值不该打数据库
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_check_unique_rejects_bad_format_before_hitting_database():
    conn = _session(_result(None))
    data = await DatasetCloneService.check_unique(conn, name="Bad ID!", display_name="副本")

    assert data["name"]["available"] is False
    assert data["name"]["reason"] == "invalid_format"
    # 只有 display_name 需要查库
    assert conn.execute.await_count == 1


@pytest.mark.asyncio
async def test_check_unique_marks_taken_names():
    conn = _session(_result(99), _result(88))
    data = await DatasetCloneService.check_unique(
        conn, name="nanzi_resources", display_name="资源数据集"
    )

    assert data["name"] == {
        "available": False,
        "reason": "taken",
        "message": "该 ID 已被其他数据集占用",
    }
    assert data["display_name"]["reason"] == "taken"


@pytest.mark.asyncio
async def test_check_unique_accepts_free_names():
    conn = _session(_result(None), _result(None))
    data = await DatasetCloneService.check_unique(
        conn, name="nanzi_resources_copy", display_name="资源数据集（副本）"
    )

    assert data["name"]["available"] is True
    assert data["display_name"]["available"] is True


@pytest.mark.asyncio
async def test_check_unique_excludes_self_when_editing():
    conn = _session(_result(None), _result(None))
    await DatasetCloneService.check_unique(
        conn, name="nanzi_resources", display_name="资源数据集", exclude_id=12
    )
    # 只验证两个查询都带上了 exclude 条件（不解析 SQL 细节，够用）
    assert conn.execute.await_count == 2


# ---------------------------------------------------------------------------
# 2. 复制入口校验
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clone_raises_lookup_error_when_source_missing():
    conn = _session(_result(None))
    with pytest.raises(LookupError):
        await DatasetCloneService.clone_dataset(conn, 404, _payload())


@pytest.mark.asyncio
async def test_clone_rejects_invalid_dataset_id():
    conn = _session(_result(_source()))
    with pytest.raises(CloneValidationError, match="元数据 ID"):
        await DatasetCloneService.clone_dataset(conn, 12, _payload(name="Bad ID"))


@pytest.mark.asyncio
async def test_clone_rejects_duplicate_name():
    conn = _session(_result(_source()), _result(999))
    with pytest.raises(CloneValidationError, match="已被占用"):
        await DatasetCloneService.clone_dataset(conn, 12, _payload())


@pytest.mark.asyncio
async def test_clone_rejects_duplicate_display_name():
    conn = _session(_result(_source()), _result(None), _result(999))
    with pytest.raises(CloneValidationError, match="已被占用"):
        await DatasetCloneService.clone_dataset(conn, 12, _payload())


@pytest.mark.asyncio
async def test_clone_requires_at_least_one_table():
    conn = _session(_result(_source()), _result(None), _result(None))
    with pytest.raises(CloneValidationError, match="至少要保留一张表"):
        await DatasetCloneService.clone_dataset(conn, 12, _payload(tables=[]))


@pytest.mark.asyncio
async def test_clone_rejects_unknown_data_source():
    conn = _session(_result(_source()), _result(None), _result(None), data_source_exists=False)
    with pytest.raises(CloneValidationError, match="数据源"):
        await DatasetCloneService.clone_dataset(
            conn, 12, _payload(data_source="not_exists_db")
        )


@pytest.mark.asyncio
async def test_clone_allows_keeping_source_data_source_without_registry_lookup():
    """沿用源数据集的数据源时不查连接表：历史值可能不在连接表里。"""
    conn = _session(_result(_source()), _result(None), _result(None))
    payload = _payload(tables=[])
    with pytest.raises(CloneValidationError, match="至少要保留一张表"):
        await DatasetCloneService.clone_dataset(conn, 12, payload)
    conn.scalar.assert_not_called()


# ---------------------------------------------------------------------------
# 3. 成功路径
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clone_creates_children_and_writes_detail_changelogs():
    conn = _session(_result(_source()), _result(None), _result(None))
    payload = _payload(
        relationships=[
            CloneRelationshipPayload(
                source_table="dim_resource",
                target_table="dim_resource",
                join_condition="dim_resource.resource_id = dim_resource.resource_id",
                join_type="left",
            )
        ],
        metrics=[
            MetricSchema(name="resource_pv", display_name="资源访问量", calculation_logic="SUM(x)")
        ],
    )

    with patch(
        "app.services.dataset_clone_service.ChangelogService.log_change",
        new_callable=AsyncMock,
    ) as log_change, patch.object(
        DatasetCloneService, "_after_create", new_callable=AsyncMock
    ):
        result = await DatasetCloneService.clone_dataset(conn, 12, payload, user_id=7, user_name="张三")

    summary = result["summary"]
    assert summary == {
        "tables": 1,
        "columns": 2,
        "relationships": 1,
        "metrics": 1,
        "skipped_relationships": 0,
    }
    # 数据集 1 条 + 表 1 条 + 关系 1 条 + 指标 1 条
    assert log_change.await_count == 4
    first_call = log_change.await_args_list[0].kwargs
    assert first_call["resource_type"] == "dataset"
    assert first_call["operation"] == "create"
    assert first_call["new_data"]["clone"] is True
    assert first_call["new_data"]["source_dataset_id"] == 12
    assert "复制新建" in first_call["reason"]


@pytest.mark.asyncio
async def test_clone_summary_mode_writes_only_one_changelog():
    conn = _session(_result(_source()), _result(None), _result(None))
    payload = _payload(log_mode="summary")

    with patch(
        "app.services.dataset_clone_service.ChangelogService.log_change",
        new_callable=AsyncMock,
    ) as log_change, patch.object(
        DatasetCloneService, "_after_create", new_callable=AsyncMock
    ):
        await DatasetCloneService.clone_dataset(conn, 12, payload)

    assert log_change.await_count == 1


@pytest.mark.asyncio
async def test_clone_skips_relationships_pointing_to_removed_tables():
    conn = _session(_result(_source()), _result(None), _result(None))
    payload = _payload(
        relationships=[
            CloneRelationshipPayload(
                source_table="dim_resource",
                target_table="dim_removed",
                join_condition="a = b",
            )
        ]
    )

    with patch(
        "app.services.dataset_clone_service.ChangelogService.log_change",
        new_callable=AsyncMock,
    ), patch.object(DatasetCloneService, "_after_create", new_callable=AsyncMock):
        result = await DatasetCloneService.clone_dataset(conn, 12, payload)

    assert result["summary"]["relationships"] == 0
    assert result["summary"]["skipped_relationships"] == 1


@pytest.mark.asyncio
async def test_clone_does_not_inherit_permissions_by_default():
    conn = _session(_result(_source(enable_data_perm=True, row_filter_config={"x": 1})), _result(None), _result(None))

    with patch(
        "app.services.dataset_clone_service.ChangelogService.log_change",
        new_callable=AsyncMock,
    ), patch.object(DatasetCloneService, "_after_create", new_callable=AsyncMock):
        result = await DatasetCloneService.clone_dataset(conn, 12, _payload())

    dataset = result["dataset"]
    assert dataset.enable_data_perm is False
    assert dataset.row_filter_config is None


@pytest.mark.asyncio
async def test_clone_can_inherit_permissions_when_requested():
    conn = _session(_result(_source(enable_data_perm=True, row_filter_config={"x": 1})), _result(None), _result(None))

    with patch(
        "app.services.dataset_clone_service.ChangelogService.log_change",
        new_callable=AsyncMock,
    ), patch.object(DatasetCloneService, "_after_create", new_callable=AsyncMock):
        result = await DatasetCloneService.clone_dataset(
            conn, 12, _payload(copy_permissions=True)
        )

    dataset = result["dataset"]
    assert dataset.enable_data_perm is True
    assert dataset.row_filter_config == {"x": 1}
