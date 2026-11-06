"""数据集权限总览（全局只读聚合）的测试。

两半：

- 纯逻辑：到期天数、风险排序、筛选口径——这些是「总览说没人能访问、实际却进得去」
  这类矛盾的来源，必须单独锁住；
- 真库：一条授权的过期 / 收窄 / 行级能否被正确归类到对应数据集。
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from app.models.metadata import MetaDataset
from app.models.permission import ResourcePermission
from app.services import dataset_permission_overview_service as overview
from app.services import dataset_scope_service as dss

TEST_USER_ID = 987_654_322


def _register_dataset(session, dataset_id: int) -> None:
    """总览以 meta_datasets 为准，授权行挂在不存在的数据集上不会出现。"""
    session.add(
        MetaDataset(
            id=dataset_id,
            name=f"perm_overview_test_{dataset_id}",
            display_name="权限总览测试集",
            data_source="mysql",
            status=1,
        )
    )


class TestDaysLeft:
    def test_none_expiry_means_no_deadline(self):
        assert overview._days_left(None, datetime.now().date()) is None

    def test_future_and_past(self):
        today = datetime(2026, 11, 5).date()
        assert overview._days_left(datetime(2026, 11, 12), today) == 7
        assert overview._days_left(datetime(2026, 10, 30), today) == -6

    def test_accepts_string_and_date(self):
        today = datetime(2026, 11, 5).date()
        assert overview._days_left("2026-11-06", today) == 1
        assert overview._days_left(today, today) == 0

    def test_unparsable_value_is_treated_as_no_deadline(self):
        assert overview._days_left("not-a-date", datetime.now().date()) is None


class TestRiskRank:
    def _bucket(self, **kwargs):
        base = overview._empty_bucket()
        base.update(kwargs)
        return base

    def test_nobody_can_access_ranks_first(self):
        assert overview._risk_rank(self._bucket(alive=0)) == 0

    def test_expiring_ranks_before_plain(self):
        assert overview._risk_rank(self._bucket(alive=1, soon=1)) == 1
        assert overview._risk_rank(self._bucket(alive=1, dead=1)) == 1

    def test_narrowed_ranks_after_expiring(self):
        assert overview._risk_rank(self._bucket(alive=1, narrowed=1)) == 2

    def test_plain_config_last(self):
        assert overview._risk_rank(self._bucket(alive=1)) == 3


class TestFilters:
    def _bucket(self, **kwargs):
        base = overview._empty_bucket()
        base.update(kwargs)
        return base

    def test_unauthorized_means_nobody_alive(self):
        # 全部过期也算「无人可访问」，这正是要区分「没配过」和「配过但过期」的地方
        assert overview._match_filter(self._bucket(alive=0, total=3, dead=3), "unauthorized")

    def test_soon_includes_expired(self):
        assert overview._match_filter(self._bucket(alive=1, soon=2), "soon")
        assert overview._match_filter(self._bucket(alive=1, dead=1), "soon")
        assert not overview._match_filter(self._bucket(alive=1), "soon")

    def test_rowlevel_covers_custom_and_no_filter(self):
        assert overview._match_filter(self._bucket(alive=1, custom_row=1), "rowlevel")
        assert overview._match_filter(self._bucket(alive=1, no_row=1), "rowlevel")
        assert not overview._match_filter(self._bucket(alive=1), "rowlevel")

    def test_all_baseline(self):
        assert overview._match_filter(self._bucket(alive=1, has_all=True), "all_baseline")
        assert not overview._match_filter(self._bucket(alive=1), "all_baseline")

    def test_unknown_filter_matches_everything(self):
        assert overview._match_filter(self._bucket(alive=0), "whatever")


class TestTickBucket:
    def test_expired_rows_do_not_count_as_subjects(self):
        bucket = overview._empty_bucket()
        overview._tick_bucket(bucket, dss.SUBJECT_USER, dss.MODE_ALL, dss.ROW_POLICY_INHERIT, -1)
        assert bucket["total"] == 1
        assert bucket["dead"] == 1
        assert bucket["alive"] == 0
        assert bucket["users"] == 0

    def test_alive_row_counts_every_dimension(self):
        bucket = overview._empty_bucket()
        overview._tick_bucket(bucket, dss.SUBJECT_ROLE, dss.MODE_LIST, dss.ROW_POLICY_CUSTOM, 3)
        assert bucket == {
            "total": 1,
            "alive": 1,
            "dead": 0,
            "has_all": False,
            "roles": 1,
            "users": 0,
            "narrowed": 1,
            "column_narrowed": 0,
            "custom_row": 1,
            "no_row": 0,
            "soon": 1,
            "soon_min_days": 3,
        }

    def test_column_rules_mark_row_as_column_narrowed(self):
        """字段级是第四层：这条授权限制了字段，就要在「有字段」里计一次。"""
        bucket = overview._empty_bucket()
        overview._tick_bucket(
            bucket, dss.SUBJECT_USER, dss.MODE_LIST, dss.ROW_POLICY_INHERIT, None, column_tables=2
        )
        assert bucket["column_narrowed"] == 1
        assert bucket["narrowed"] == 1

    def test_expired_row_does_not_count_column_narrowing(self):
        """过期授权不参与判定，也不该让总览的「有字段」计数虚高。"""
        bucket = overview._empty_bucket()
        overview._tick_bucket(
            bucket, dss.SUBJECT_USER, dss.MODE_ALL, dss.ROW_POLICY_INHERIT, -1, column_tables=3
        )
        assert bucket["column_narrowed"] == 0

    def test_soon_keeps_the_nearest_deadline(self):
        bucket = overview._empty_bucket()
        overview._tick_bucket(bucket, dss.SUBJECT_USER, dss.MODE_ALL, dss.ROW_POLICY_INHERIT, 6)
        overview._tick_bucket(bucket, dss.SUBJECT_USER, dss.MODE_ALL, dss.ROW_POLICY_INHERIT, 2)
        assert bucket["soon"] == 2
        assert bucket["soon_min_days"] == 2

    def test_long_lived_row_is_not_soon(self):
        bucket = overview._empty_bucket()
        overview._tick_bucket(bucket, dss.SUBJECT_ALL, dss.MODE_ALL, dss.ROW_POLICY_INHERIT, 30)
        assert bucket["soon"] == 0
        assert bucket["soon_min_days"] is None
        assert bucket["has_all"] is True


@pytest.mark.asyncio
async def test_overview_groups_grants_by_dataset(db_session):
    """一条过期 + 一条收窄的授权，要分别落到 dead / narrowed，而不是混在一起。"""
    dataset_id = 900_000_000 + random.randint(1, 999_999)
    suffix = str(dataset_id)
    _register_dataset(db_session, dataset_id)
    rows = [
        ResourcePermission(
            resource_type="metadata",
            resource_id=suffix,
            user_id=TEST_USER_ID,
            enabled=True,
        ),
        ResourcePermission(
            resource_type="metadata",
            resource_id=suffix,
            user_id=TEST_USER_ID + 1,
            enabled=True,
        ),
    ]
    db_session.add_all(rows)
    await db_session.flush()

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    await dss.update_grant_scope(
        db_session,
        rows[0].id,
        scope={"mode": dss.MODE_LIST, "tables": [1, 2]},
        row_policy=dss.ROW_POLICY_CUSTOM,
        row_condition="t.region = 'CN'",
        expire_at=None,
        subject_type=dss.SUBJECT_USER,
    )
    await dss.update_grant_scope(
        db_session,
        rows[1].id,
        scope={"mode": dss.MODE_ALL},
        row_policy=dss.ROW_POLICY_INHERIT,
        row_condition=None,
        expire_at=yesterday,
        subject_type=dss.SUBJECT_USER,
    )
    await db_session.flush()

    try:
        # 共享库里有上千个测试数据集，未授权的排在最前面，这里要按名称精确定位
        result = await overview.build_dataset_permission_overview(
            db_session, keyword=f"perm_overview_test_{dataset_id}", page_size=200
        )
        target = next((d for d in result["datasets"] if d["id"] == dataset_id), None)
        assert target is not None, "总览里应该带上这个数据集"
        assert target["total"] == 2
        assert target["alive"] == 1
        assert target["dead"] == 1
        assert target["users"] == 1
        assert target["narrowed"] == 1
        assert target["custom_row"] == 1
        assert len(target["grants"]) == 2
        # 生效中的排在前面，过期的置后置灰
        assert target["grants"][0]["expired"] is False
        assert target["grants"][0]["scope"] == dss.MODE_LIST
        assert target["grants"][0]["table_count"] == 2
        assert target["grants"][-1]["expired"] is True
    finally:
        await db_session.rollback()


@pytest.mark.asyncio
async def test_overview_summary_is_global_even_when_filtered(db_session):
    """筛选只看局部，summary 必须还按全量算，否则数字会随着筛选跳来跳去。"""
    result_all = await overview.build_dataset_permission_overview(db_session, page_size=1)
    result_filtered = await overview.build_dataset_permission_overview(
        db_session, filter_key="unauthorized", page_size=1
    )
    assert result_all["summary"] == result_filtered["summary"]
    assert result_filtered["total"] <= result_all["total"]


@pytest.mark.asyncio
async def test_overview_keyword_searches_grant_subject(db_session):
    """搜索要能按授权对象命中——排查「谁能进这个数据集」时最直接的入口。"""
    dataset_id = 900_000_000 + random.randint(1, 999_999)
    _register_dataset(db_session, dataset_id)
    row = ResourcePermission(
        resource_type="metadata",
        resource_id=str(dataset_id),
        user_id=TEST_USER_ID,
        enabled=True,
    )
    db_session.add(row)
    await db_session.flush()
    await dss.update_grant_scope(
        db_session,
        row.id,
        scope={"mode": dss.MODE_ALL},
        row_policy=dss.ROW_POLICY_INHERIT,
        row_condition=None,
        expire_at=None,
        subject_type=dss.SUBJECT_USER,
    )
    await db_session.flush()

    try:
        result = await overview.build_dataset_permission_overview(
            db_session, keyword=str(TEST_USER_ID), page_size=50
        )
        assert any(d["id"] == dataset_id for d in result["datasets"])
    finally:
        await db_session.rollback()


@pytest.mark.asyncio
async def test_overview_shows_column_scope_across_grants(db_session):
    """字段级是第四层，总览必须能看到「谁被裁了列」。

    两条授权各自限制的表会重叠：数据集层要取**并集**（1、2、3 三张表），
    而不是把每条授权的表数相加（2 + 2 = 4）。
    """
    dataset_id = 900_000_000 + random.randint(1, 999_999)
    suffix = str(dataset_id)
    _register_dataset(db_session, dataset_id)
    rows = [
        ResourcePermission(
            resource_type="metadata",
            resource_id=suffix,
            user_id=TEST_USER_ID,
            enabled=True,
        ),
        ResourcePermission(
            resource_type="metadata",
            resource_id=suffix,
            user_id=TEST_USER_ID + 1,
            enabled=True,
        ),
    ]
    db_session.add_all(rows)
    await db_session.flush()

    # 用户 A：1、2 两张表，分别排除 2 列 / 1 列
    await dss.update_grant_scope(
        db_session,
        rows[0].id,
        scope={
            "mode": dss.MODE_ALL,
            "columns": {
                "1": {"mode": "exclude", "names": ["phone", "id_card"]},
                "2": {"mode": "exclude", "names": ["remark"]},
            },
        },
        row_policy=dss.ROW_POLICY_INHERIT,
        row_condition=None,
        expire_at=None,
        subject_type=dss.SUBJECT_USER,
    )
    # 用户 B：2 重复、3 是白名单三列
    await dss.update_grant_scope(
        db_session,
        rows[1].id,
        scope={
            "mode": dss.MODE_ALL,
            "columns": {
                "2": {"mode": "exclude", "names": ["remark"]},
                "3": {"mode": "include", "names": ["id", "name", "amount"]},
            },
        },
        row_policy=dss.ROW_POLICY_INHERIT,
        row_condition=None,
        expire_at=None,
        subject_type=dss.SUBJECT_USER,
    )
    await db_session.flush()

    try:
        result = await overview.build_dataset_permission_overview(
            db_session, keyword=f"perm_overview_test_{dataset_id}", page_size=200
        )
        target = next((d for d in result["datasets"] if d["id"] == dataset_id), None)
        assert target is not None
        assert target["column_narrowed"] == 2, "两条授权都限了字段"
        assert target["column_tables"] == 3, "数据集层按并集算：表 1、2、3"
        assert target["column_hidden"] == 2, "黑名单取各表最大值（排除 2 列）"
        assert target["column_limited"] == 3, "白名单取各表最大值（只留 3 列）"
        assert {g["column_tables"] for g in target["grants"]} == {2}

        # 列表筛选也要支持「有字段」
        filtered = await overview.build_dataset_permission_overview(
            db_session, keyword=f"perm_overview_test_{dataset_id}", filter_key="columns", page_size=200
        )
        assert any(d["id"] == dataset_id for d in filtered["datasets"])
        assert filtered["summary"]["columns"] >= 1
    finally:
        await db_session.rollback()
