"""数据集三层授权解析（数据集 / 表 / 行）的单元测试。

这里只测纯逻辑：授权行的合并语义、表范围的解析、过期判断。
需要数据库的部分（``resolve_user_dataset_scopes`` 的 SQL、表级门禁的报错文案）
留给集成测试；本文件保证「合并规则」不会随重构漂移。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from app.services.dataset_scope_service import (
    MODE_ALL,
    MODE_LIST,
    ROW_POLICY_CUSTOM,
    ROW_POLICY_INHERIT,
    ROW_POLICY_NONE,
    SUBJECT_ALL,
    SUBJECT_ROLE,
    SUBJECT_USER,
    _is_expired,
    _merge_scope,
    _parse_row_policy,
    _parse_scope,
    _subject_of,
    build_dataset_scope,
)


def _item(
    subject: str,
    *,
    mode: str = MODE_ALL,
    tables: tuple[int, ...] = (),
    row_policy: str = ROW_POLICY_INHERIT,
    condition: str | None = None,
) -> dict:
    return {
        "subject": subject,
        "mode": mode,
        "table_ids": frozenset(tables),
        "row_policy": row_policy,
        "row_condition": condition,
    }


class TestParseScope:
    def test_missing_scope_is_all_for_legacy_rows(self):
        # 历史授权行没有 scope 列（或值为 NULL）：语义必须是「数据集内全部表」。
        assert _parse_scope(None) == (MODE_ALL, frozenset())

    def test_json_string_from_mysql(self):
        assert _parse_scope('{"mode":"list","tables":[3,5]}') == (MODE_LIST, frozenset({3, 5}))

    def test_dict_from_postgres(self):
        assert _parse_scope({"mode": "list", "tables": [7]}) == (MODE_LIST, frozenset({7}))

    def test_string_table_ids_are_cast_to_int(self):
        assert _parse_scope({"mode": "list", "tables": ["9", "11", "oops"]}) == (
            MODE_LIST,
            frozenset({9, 11}),
        )

    def test_empty_list_is_fail_closed(self):
        # 空白名单不能退化成 all，否则「一张表都不给」会变成「全部给」。
        assert _parse_scope({"mode": "list", "tables": []}) == (MODE_LIST, frozenset())

    def test_unknown_mode_falls_back_to_all(self):
        assert _parse_scope({"mode": "whatever", "tables": [1]}) == (MODE_ALL, frozenset())

    def test_malformed_json_falls_back_to_all(self):
        assert _parse_scope("{not json") == (MODE_ALL, frozenset())

    def test_bytes_payload(self):
        payload = json.dumps({"mode": "list", "tables": [2]}).encode()
        assert _parse_scope(payload) == (MODE_LIST, frozenset({2}))


class TestParseRowPolicy:
    def test_known_values(self):
        assert _parse_row_policy("none") == ROW_POLICY_NONE
        assert _parse_row_policy("CUSTOM") == ROW_POLICY_CUSTOM
        assert _parse_row_policy("inherit") == ROW_POLICY_INHERIT

    def test_unknown_and_empty_fall_back_to_inherit(self):
        assert _parse_row_policy(None) == ROW_POLICY_INHERIT
        assert _parse_row_policy("") == ROW_POLICY_INHERIT
        assert _parse_row_policy("bogus") == ROW_POLICY_INHERIT


class TestSubjectInference:
    def test_declared_subject_wins(self):
        assert _subject_of(1, None, "all") == SUBJECT_ALL

    def test_infers_from_user_id(self):
        assert _subject_of(5, None, None) == SUBJECT_USER

    def test_infers_from_role_id(self):
        assert _subject_of(None, 2, None) == SUBJECT_ROLE

    def test_both_null_requires_explicit_all_marker(self):
        # 「全员」必须由 subject_type='all' 显式声明：同表里 menu/element 权限行
        # 也是 user_id = role_id = NULL，不能默认按全员处理（否则等于提权）。
        assert _subject_of(None, None, "all") == SUBJECT_ALL
        assert _subject_of(None, None, None) is None


class TestExpiry:
    def test_none_never_expires(self):
        assert _is_expired(None) is False

    def test_past_is_expired(self):
        assert _is_expired(datetime.now() - timedelta(days=1)) is True

    def test_future_is_active(self):
        assert _is_expired(datetime.now() + timedelta(days=1)) is False

    def test_iso_string_is_parsed(self):
        past = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        assert _is_expired(past) is True

    def test_timezone_aware_value_does_not_crash(self):
        aware = datetime.now().astimezone() - timedelta(minutes=5)
        assert _is_expired(aware) is True


class TestMergeScope:
    def test_all_wins_over_list(self):
        assert _merge_scope(
            [_item(SUBJECT_ROLE, mode=MODE_LIST, tables=(1,)), _item(SUBJECT_ALL)]
        ) == (MODE_ALL, frozenset())

    def test_lists_are_unioned(self):
        assert _merge_scope(
            [
                _item(SUBJECT_ROLE, mode=MODE_LIST, tables=(1, 2)),
                _item(SUBJECT_ROLE, mode=MODE_LIST, tables=(2, 3)),
            ]
        ) == (MODE_LIST, frozenset({1, 2, 3}))


class TestBuildDatasetScope:
    def test_user_entry_overrides_role_entry(self):
        # 用户级 5 张表 + 角色级全部表 ⇒ 以用户级为准（按人收窄）。
        scope = build_dataset_scope(
            1,
            [
                _item(SUBJECT_USER, mode=MODE_LIST, tables=(1, 2)),
                _item(SUBJECT_ROLE),
            ],
        )
        assert scope.mode == MODE_LIST
        assert scope.table_ids == frozenset({1, 2})
        assert scope.sources == (SUBJECT_USER,)

    def test_multiple_roles_are_unioned(self):
        scope = build_dataset_scope(
            1,
            [
                _item(SUBJECT_ROLE, mode=MODE_LIST, tables=(1,)),
                _item(SUBJECT_ROLE, mode=MODE_LIST, tables=(2,)),
            ],
        )
        assert scope.mode == MODE_LIST
        assert scope.table_ids == frozenset({1, 2})
        assert scope.sources == (SUBJECT_ROLE,)

    def test_all_members_baseline_cannot_be_narrowed(self):
        # 全员基线：公共维表（3、4）；个人只被授予 1、2 ⇒ 最终 1、2、3、4。
        scope = build_dataset_scope(
            1,
            [
                _item(SUBJECT_USER, mode=MODE_LIST, tables=(1, 2)),
                _item(SUBJECT_ALL, mode=MODE_LIST, tables=(3, 4)),
            ],
        )
        assert scope.mode == MODE_LIST
        assert scope.table_ids == frozenset({1, 2, 3, 4})
        assert scope.sources == (SUBJECT_USER, SUBJECT_ALL)

    def test_all_members_all_scope_opens_everything(self):
        scope = build_dataset_scope(
            1,
            [
                _item(SUBJECT_USER, mode=MODE_LIST, tables=(1,)),
                _item(SUBJECT_ALL, mode=MODE_ALL),
            ],
        )
        assert scope.mode == MODE_ALL
        assert scope.allows_all_tables is True

    def test_row_policy_comes_from_base_not_public(self):
        scope = build_dataset_scope(
            1,
            [
                _item(SUBJECT_USER, mode=MODE_LIST, tables=(1,), row_policy=ROW_POLICY_CUSTOM,
                      condition="fact_a.dept = 'FIN'"),
                _item(SUBJECT_ALL, row_policy=ROW_POLICY_NONE),
            ],
        )
        assert scope.row_policy == ROW_POLICY_CUSTOM
        assert scope.row_condition == "fact_a.dept = 'FIN'"

    def test_public_only_keeps_inherit_row_policy(self):
        scope = build_dataset_scope(1, [_item(SUBJECT_ALL, mode=MODE_LIST, tables=(8,))])
        assert scope.row_policy == ROW_POLICY_INHERIT
        assert scope.row_condition is None

    def test_allows_table(self):
        scope = build_dataset_scope(1, [_item(SUBJECT_USER, mode=MODE_LIST, tables=(2, 3))])
        assert scope.allows_table(2) is True
        assert scope.allows_table(4) is False
        assert scope.allows_all_tables is False


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeSession:
    """只回答「表归属」查询；授权解析由用例本身 monkeypatch 掉。"""

    def __init__(self, rows):
        self._rows = rows
        self.queries = 0

    async def execute(self, *_args, **_kwargs):
        self.queries += 1
        return _FakeResult(self._rows)


class TestEnforceTableScopeForRefs:
    @staticmethod
    def _patch_scopes(monkeypatch, scopes):
        from app.services import dataset_scope_service as dss

        async def fake_resolve(_session, _user_id, *, dataset_ids=None):
            if dataset_ids is None:
                return dict(scopes)
            return {int(did): scopes[int(did)] for did in dataset_ids if int(did) in scopes}

        monkeypatch.setattr(dss, "resolve_user_dataset_scopes", fake_resolve)

    @pytest.mark.asyncio
    async def test_narrowed_table_is_denied_with_dataset_name(self, monkeypatch):
        from app.services.dataset_scope_service import DatasetScope, enforce_table_scope_for_refs

        self._patch_scopes(monkeypatch, {1: DatasetScope(1, MODE_LIST, frozenset({11}))})
        session = _FakeSession(
            [(11, "fact_a", 1, "sales"), (12, "fact_b", 1, "sales")]
        )

        err = await enforce_table_scope_for_refs(
            session,
            refs={"fact_b": "fact_b"},
            user_id=4,
            user_identity_label="张三(4)",
        )

        assert err is not None
        assert err.startswith("[Permission Denied]")
        assert "fact_b" in err
        assert "sales" in err
        assert "张三(4)" in err

    @pytest.mark.asyncio
    async def test_allowed_table_passes(self, monkeypatch):
        from app.services.dataset_scope_service import DatasetScope, enforce_table_scope_for_refs

        self._patch_scopes(monkeypatch, {1: DatasetScope(1, MODE_LIST, frozenset({11}))})
        session = _FakeSession([(11, "fact_a", 1, "sales")])

        err = await enforce_table_scope_for_refs(
            session, refs={"fact_a": "fact_a"}, user_id=4
        )
        assert err is None

    @pytest.mark.asyncio
    async def test_all_scope_never_queries_grants(self, monkeypatch):
        from app.services.dataset_scope_service import DatasetScope, enforce_table_scope_for_refs

        self._patch_scopes(monkeypatch, {1: DatasetScope(1, MODE_ALL, frozenset())})
        session = _FakeSession([(11, "fact_a", 1, "sales")])

        err = await enforce_table_scope_for_refs(
            session, refs={"fact_a": "fact_a"}, user_id=4
        )
        assert err is None
        # 表级范围是 all 时仍要查归属（为了知道它属于哪个数据集），但不影响结果。
        assert session.queries == 1

    @pytest.mark.asyncio
    async def test_unregistered_table_is_left_to_physical_table_check(self, monkeypatch):
        from app.services.dataset_scope_service import DatasetScope, enforce_table_scope_for_refs

        self._patch_scopes(monkeypatch, {1: DatasetScope(1, MODE_LIST, frozenset({11}))})
        session = _FakeSession([])  # 没有任何元数据表匹配

        err = await enforce_table_scope_for_refs(
            session, refs={"ghost_table": "ghost_table"}, user_id=4
        )
        assert err is None

    @pytest.mark.asyncio
    async def test_admin_and_missing_user_are_skipped(self, monkeypatch):
        from app.services.dataset_scope_service import enforce_table_scope_for_refs

        session = _FakeSession([(12, "fact_b", 1, "sales")])
        assert await enforce_table_scope_for_refs(
            session, refs={"fact_b": "fact_b"}, user_id=4, is_admin=True
        ) is None
        assert await enforce_table_scope_for_refs(
            session, refs={"fact_b": "fact_b"}, user_id=None
        ) is None
        assert session.queries == 0
