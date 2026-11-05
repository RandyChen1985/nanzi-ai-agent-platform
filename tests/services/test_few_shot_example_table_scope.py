"""经验库（few-shot 示例）的表级权限过滤。

背景：ChatBI 每次查询都会从**全平台共享**的经验库里检索历史优质 SQL 注入 prompt。
表级授权上线后，A 用户可能命中 B 的历史 SQL，而那段 SQL 里的表恰好被 A 的授权收窄掉了
——表名/字段名会顺着 few-shot 泄露，模型还会照着生成一条注定被 ``[Permission Denied]``
拦下的 SQL。这里锁住「注入前先按用户可见表过滤」这条链路。
"""
from __future__ import annotations

import pytest

from app.services.dataset_scope_service import filter_examples_by_table_scope


class _FakeSessionCtx:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc_info):
        return False


def _patch_scope(monkeypatch, allowed: set[str]) -> None:
    import app.core.orm as orm_mod
    import app.services.dataset_scope_service as scope_mod

    monkeypatch.setattr(orm_mod, "AsyncSessionLocal", lambda: _FakeSessionCtx())

    async def _fake_allowed(session, user_id):
        return set(allowed)

    monkeypatch.setattr(scope_mod, "allowed_physical_names_for_user", _fake_allowed)


BASE = [
    {"id": 1, "question": "华东销售额", "sql": "SELECT * FROM fact_order", "similarity": 0.9},
    {"id": 2, "question": "华南毛利", "sql": "SELECT * FROM fact_secret_margin", "similarity": 0.8},
]


@pytest.mark.asyncio
async def test_drops_examples_touching_invisible_tables(monkeypatch):
    _patch_scope(monkeypatch, {"fact_order"})

    kept = await filter_examples_by_table_scope(BASE, user_id=7)

    assert [ex["id"] for ex in kept] == [1]


@pytest.mark.asyncio
async def test_keeps_example_when_all_tables_visible(monkeypatch):
    _patch_scope(monkeypatch, {"fact_order", "fact_secret_margin"})

    kept = await filter_examples_by_table_scope(BASE, user_id=7)

    assert [ex["id"] for ex in kept] == [1, 2]


@pytest.mark.asyncio
async def test_uses_sql_metadata_tables_when_present(monkeypatch):
    """有 sql_metadata.tables 时优先用它，不必再解析 SQL。"""
    _patch_scope(monkeypatch, {"fact_order"})
    examples = [
        {
            "id": 3,
            "sql": "SELECT * FROM whatever",
            "sql_metadata": {"tables": ["fact_secret_margin"]},
        }
    ]

    kept = await filter_examples_by_table_scope(examples, user_id=7)

    assert kept == []


@pytest.mark.asyncio
async def test_keeps_example_when_sql_cannot_be_parsed(monkeypatch):
    """解析不了就保留：宁可让执行层拦，也不要把经验库整体打空。"""
    _patch_scope(monkeypatch, {"fact_order"})
    examples = [{"id": 4, "sql": "这不是 SQL"}]

    kept = await filter_examples_by_table_scope(examples, user_id=7)

    assert [ex["id"] for ex in kept] == [4]


@pytest.mark.asyncio
async def test_keeps_everything_when_user_has_no_scope_records(monkeypatch):
    """没有任何数据集授权记录时不误杀（执行层仍会拒绝）。"""
    _patch_scope(monkeypatch, set())

    kept = await filter_examples_by_table_scope(BASE, user_id=7)

    assert [ex["id"] for ex in kept] == [1, 2]


@pytest.mark.asyncio
async def test_admin_and_anonymous_skip_filtering(monkeypatch):
    called = {"n": 0}

    async def _fake_allowed(session, user_id):
        called["n"] += 1
        return set()

    import app.services.dataset_scope_service as scope_mod

    monkeypatch.setattr(scope_mod, "allowed_physical_names_for_user", _fake_allowed)

    assert [ex["id"] for ex in await filter_examples_by_table_scope(BASE, user_id=7, is_admin=True)] == [1, 2]
    assert [ex["id"] for ex in await filter_examples_by_table_scope(BASE, user_id=None)] == [1, 2]
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_schema_qualified_table_names_are_normalized(monkeypatch):
    """示例里写成 db.table 时也能对上白名单（白名单存的是裸物理名）。"""
    _patch_scope(monkeypatch, {"fact_order"})
    examples = [{"id": 5, "sql": "SELECT * FROM dw.fact_order"}]

    kept = await filter_examples_by_table_scope(examples, user_id=7)

    assert [ex["id"] for ex in kept] == [5]
