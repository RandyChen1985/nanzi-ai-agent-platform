from types import SimpleNamespace

import pytest

from app.services.ai.config import (
    _DATASET_MENU_DETAIL_BUDGET,
    AgentConfigProvider,
)

pytestmark = pytest.mark.no_infrastructure


def _dataset(name: str, *, tables=(), metrics=(), desc: str = "用途说明"):
    return SimpleNamespace(
        name=name,
        display_name=f"{name}中文名",
        description=desc,
        tags=["标签"],
        tables=[
            SimpleNamespace(term=term, description=f"{term}描述", status=1) for term in tables
        ],
        metrics=[SimpleNamespace(display_name=metric, name=metric) for metric in metrics],
    )


def test_full_menu_keeps_table_details():
    menu = AgentConfigProvider._format_dataset_menu(
        [_dataset("sales", tables=["订单", "客户"], metrics=["销售额"])]
    )

    assert "【当前用户有权访问的数据集】共 1 个" in menu
    assert "- Dataset: sales [标签]" in menu
    assert "  Display Name: sales中文名" in menu
    assert "  Includes Tables: 订单, 客户" in menu
    assert "  Table Details:" in menu
    assert "    - 订单: 订单描述" in menu
    assert "  Metrics: 销售额" in menu
    # 完整版要告诉模型存在表描述字段
    assert "Table Details=表描述" in menu


def test_compact_menu_drops_table_details_but_keeps_table_terms():
    menu = AgentConfigProvider._format_dataset_menu(
        [_dataset("sales", tables=["订单", "客户"], metrics=["销售额"])],
        include_table_details=False,
    )

    assert "  Includes Tables: 订单, 客户" in menu
    assert "  Table Details:" not in menu
    assert "订单描述" not in menu
    # 表术语还在，指标也在
    assert "  Metrics: 销售额" in menu
    # 字段说明必须与实际正文一致，不能还写着 Table Details
    assert "Table Details=表描述" not in menu
    assert "get_dataset_schema" in menu


def test_empty_menu_marks_no_authorized_datasets():
    menu = AgentConfigProvider._format_dataset_menu([])

    assert "【当前用户有权访问的数据集】" in menu
    assert "(No authorized datasets available)" in menu


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


async def _run_generate(monkeypatch, datasets):
    async def fake_search(*_args, **_kwargs):
        return datasets

    monkeypatch.setattr("app.core.orm.AsyncSessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(
        "app.services.metadata_service.MetadataService.search_datasets",
        fake_search,
    )
    return await AgentConfigProvider._generate_dataset_menu_content(user_id=1, is_admin=False)


@pytest.mark.asyncio
async def test_small_menu_stays_complete(monkeypatch):
    datasets = [_dataset("sales", tables=["订单", "客户"], metrics=["销售额"])]

    menu = await _run_generate(monkeypatch, datasets)

    assert len(menu) <= _DATASET_MENU_DETAIL_BUDGET
    assert "  Table Details:" in menu


@pytest.mark.asyncio
async def test_oversized_menu_falls_back_to_compact(monkeypatch):
    """目录超预算时只丢表描述，表术语与数据集本身必须保留。"""
    datasets = [
        _dataset(
            f"ds{index}",
            tables=[f"表{index}_{n}" for n in range(30)],
            metrics=["指标A"],
            desc="较长的业务描述" * 30,
        )
        for index in range(60)
    ]

    menu = await _run_generate(monkeypatch, datasets)

    assert len(menu) < len(AgentConfigProvider._format_dataset_menu(datasets))
    assert "\n  Table Details:\n" not in menu
    assert "  Includes Tables:" in menu
    assert "- Dataset: ds0 [标签]" in menu


def test_normalize_bound_dataset_ids_matches_runtime_filter():
    """统计口径与 get_dataset_schema 入口共用同一份规则。"""
    from app.services.ai.metadata_dataset_scope import normalize_bound_dataset_ids

    assert normalize_bound_dataset_ids(["11", "22", " 33 "]) == ["11", "22", "33"]
    # 名称项会被运行时丢弃，这里同样丢弃
    assert normalize_bound_dataset_ids(["11", "销售数据集"]) == ["11"]
    assert normalize_bound_dataset_ids(["销售数据集"]) == []
    assert normalize_bound_dataset_ids(None) == []
    assert normalize_bound_dataset_ids(["11", "11"]) == ["11"]
