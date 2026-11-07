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


@pytest.mark.asyncio
async def test_medium_oversized_menu_uses_progressive_truncation(monkeypatch):
    """略超预算时优先走第一级渐进截断（保留 Table Details 结构但裁剪长表描述与指标数）。"""
    datasets = [
        SimpleNamespace(
            name=f"ds{index}",
            display_name=f"ds{index}中文",
            description="用途说明",
            tags=["核心"],
            tables=[
                SimpleNamespace(
                    term=f"表{index}_{n}",
                    description="单表超长描述内容" * 40,
                    status=1,
                )
                for n in range(5)
            ],
            metrics=[
                SimpleNamespace(display_name=f"指标{m}", name=f"m{m}")
                for m in range(25)
            ],
        )
        for index in range(8)
    ]

    full_menu = AgentConfigProvider._format_dataset_menu(datasets)
    assert len(full_menu) > _DATASET_MENU_DETAIL_BUDGET

    menu = await _run_generate(monkeypatch, datasets)

    assert len(menu) <= _DATASET_MENU_DETAIL_BUDGET
    assert "  Table Details:" in menu
    assert "…" in menu
    assert "(共25项)" in menu


def test_dataset_menu_header_states_tables_are_not_all_queryable():
    """目录列出的是数据集下全部表，必须明说「不代表每一张都能查」并给出查权限的工具。

    `search_datasets` 只按数据集级授权过滤，目录会把表全平铺出来（实测某用户 38 张表里
    只有 1 张有授权）。header 原先写「只能使用这里列出的数据集与表」，等于反过来
    告诉模型这 38 张都能用，模型据此刻写 SQL、执行时才被表级门禁拦下。
    """
    header = AgentConfigProvider._dataset_menu_header(2)

    assert "不代表每一张都对当前账号开放" in header
    assert "list_accessible_datasets" in header
    # 降级模式（没有 Table Details）同样可能误导：Includes Tables 仍会平铺表名
    assert "list_accessible_datasets" in AgentConfigProvider._dataset_menu_header(
        2, include_table_details=False
    )
    # 数据集级约束不能丢
    assert "未列出的数据集一律视为无权访问" in header


def test_dataset_menu_cache_key_carries_prompt_version(monkeypatch):
    """目录文案改动要靠版本号让 90 天缓存失效，否则重启后看到的还是旧目录。"""
    import app.services.ai.config as config_module

    calls: list[str] = []

    class _FakeRedis:
        async def get(self, key):
            calls.append(key)
            return None

        async def set(self, key, value, ex=None):
            calls.append(key)

    async def _fake_get_redis():
        return _FakeRedis()

    async def _fake_generate(_user_id, _is_admin):
        return "【当前用户有权访问的数据集】"

    monkeypatch.setattr("app.core.redis.get_redis", _fake_get_redis)
    monkeypatch.setattr(AgentConfigProvider, "_generate_dataset_menu_content", staticmethod(_fake_generate))

    import asyncio

    asyncio.run(AgentConfigProvider.get_dataset_menu(user_id=178, is_admin=False))

    assert calls, "未访问缓存"
    assert all(config_module._DATASET_MENU_CACHE_VERSION in key for key in calls)
    # 权限变更走 invalidate_dataset_menu_cache（内部就是 dataset_menu_cache_key），
    # 它必须和 get_dataset_menu 用的是同一个键，否则删的是历史键、新键活满 90 天
    assert AgentConfigProvider.dataset_menu_cache_key(user_id=178) in calls
