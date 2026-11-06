"""角色级「界面模式黑名单」策略。

这里只钉纯逻辑：解析/序列化、并集、双禁降级、管理员豁免、夹逼、写入校验。
DB 查询部分用注入的假 session 验证「查了谁、拿到什么」，不依赖真实数据库。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_infrastructure

from app.services.ui_mode_policy import (  # noqa: E402
    UiModePolicy,
    clamp_mode,
    effective_policy,
    parse_blocked,
    resolve_policy,
    serialize_blocked,
    validate_blocked,
)


class _FakeResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = list(rows or [])
        self._scalar = scalar

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar


class _FakeSession:
    """按调用顺序返回预设结果，并记录执行次数。"""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    async def execute(self, _stmt, *_args, **_kwargs):
        self.calls += 1
        if not self._results:
            raise AssertionError("execute 调用次数超出预期")
        return self._results.pop(0)


# ---------------- 解析 / 序列化 ----------------


def test_parse_blocked_keeps_known_modes_and_normalizes_case():
    assert parse_blocked("") == set()
    assert parse_blocked(None) == set()
    assert parse_blocked("pro") == {"pro"}
    assert parse_blocked("PRO") == {"pro"}
    assert parse_blocked(" pro , user ") == {"pro", "user"}


def test_parse_blocked_drops_unknown_values_instead_of_failing():
    # 脏数据不能让整个账号被误限：只保留能认出来的
    assert parse_blocked("foo,pro") == {"pro"}
    assert parse_blocked("foo") == set()


def test_serialize_blocked_is_stable_and_ordered():
    assert serialize_blocked([]) == ""
    assert serialize_blocked({"pro"}) == "pro"
    assert serialize_blocked({"pro", "user"}) == "user,pro"
    assert serialize_blocked({"user"}) == "user"


# ---------------- 并集 / 降级 / 管理员豁免 ----------------


def test_resolve_policy_unions_roles_and_keeps_source_names():
    policy = resolve_policy([("cs_agent", "一线客服", "pro")], user_role="user")
    assert policy.blocked == {"pro"}
    assert policy.blocked_by == ["一线客服"]
    assert policy.downgraded is False


def test_resolve_policy_ignores_roles_without_blocked_values():
    policy = resolve_policy(
        [("cs_agent", "一线客服", ""), ("data_analyst", "数据分析师", None)],
        user_role="user",
    )
    assert policy.blocked == set()
    assert policy.blocked_by == []


def test_resolve_policy_downgrades_when_union_blocks_both_modes():
    # 两个角色分别禁不同模式：并集双禁。必须降级放行，不能把账号锁死。
    policy = resolve_policy(
        [("a", "角色A", "pro"), ("b", "角色B", "user")],
        user_role="user",
    )
    assert policy.blocked == set()
    assert policy.downgraded is True


def test_resolve_policy_exempts_system_admin_identity():
    policy = resolve_policy([("cs_agent", "一线客服", "pro")], user_role="admin")
    assert policy.blocked == set()
    assert policy.downgraded is False


def test_resolve_policy_exempts_holder_of_admin_business_role():
    # 系统身份是 user，但持有 code=admin 的业务角色——按现有口径等同于管理员
    policy = resolve_policy(
        [("admin", "平台管理员", ""), ("cs_agent", "一线客服", "pro")],
        user_role="user",
    )
    assert policy.blocked == set()


def test_resolve_policy_default_is_both_modes_available():
    assert resolve_policy([], user_role="user") == UiModePolicy()


# ---------------- 夹逼 ----------------


def test_clamp_mode_keeps_preferred_when_allowed():
    assert clamp_mode("pro", {"user"}, "user") == "pro"


def test_clamp_mode_falls_back_to_remaining_mode():
    assert clamp_mode("pro", {"pro"}, "user") == "user"
    assert clamp_mode("user", {"user"}, "admin") == "pro"


def test_clamp_mode_uses_role_default_when_preference_missing_or_dirty():
    assert clamp_mode("", set(), "user") == "user"
    assert clamp_mode("", set(), "admin") == "pro"
    assert clamp_mode("nonsense", set(), "user") == "user"


def test_clamp_mode_never_returns_blocked_mode_even_when_both_blocked():
    # 理论上到不了（并集会降级），但这里是最后一道防线
    assert clamp_mode("user", {"user", "pro"}, "user") == "user"
    assert clamp_mode("user", {"user", "pro"}, "admin") == "pro"


# ---------------- 写入校验 ----------------


def test_validate_blocked_accepts_single_mode():
    validate_blocked({"pro"}, "cs_agent")
    validate_blocked({"user"}, "cs_agent")
    validate_blocked(set(), "cs_agent")


def test_validate_blocked_rejects_blocking_both_modes():
    with pytest.raises(ValueError, match="至少保留一种"):
        validate_blocked({"user", "pro"}, "cs_agent")


def test_validate_blocked_rejects_unknown_mode():
    with pytest.raises(ValueError, match="未知的界面模式"):
        validate_blocked({"pro", "mini"}, "cs_agent")


def test_validate_blocked_rejects_admin_role():
    with pytest.raises(ValueError, match="管理员角色"):
        validate_blocked({"pro"}, "admin")
    with pytest.raises(ValueError, match="管理员角色"):
        validate_blocked({"pro"}, " Admin ")


# ---------------- DB 读取（假 session） ----------------


async def test_effective_policy_reads_user_role_then_roles(monkeypatch):
    from app.services import ui_mode_policy as module

    async def _supports(_db):
        return True

    monkeypatch.setattr(module, "supports_blocked_column", _supports)

    session = _FakeSession(
        [
            _FakeResult(scalar="user"),
            _FakeResult(rows=[("cs_agent", "一线客服", "pro")]),
        ]
    )

    policy = await effective_policy(session, 7)

    assert session.calls == 2
    assert policy.blocked == {"pro"}
    assert policy.blocked_by == ["一线客服"]


async def test_effective_policy_without_migration_treats_as_unrestricted(monkeypatch):
    """代码先于迁移上线：列不存在时按「不禁用」处理，不能把所有人锁成单模式。"""
    from app.services import ui_mode_policy as module

    async def _supports(_db):
        return False

    monkeypatch.setattr(module, "supports_blocked_column", _supports)

    session = _FakeSession(
        [
            _FakeResult(scalar="user"),
            _FakeResult(rows=[("cs_agent", "一线客服")]),
        ]
    )

    policy = await effective_policy(session, 7)

    assert policy.blocked == set()
    assert session.calls == 2


async def test_effective_policy_short_circuits_for_system_admin(monkeypatch):
    """管理员不查角色黑名单：少一次查询，也不会被任何角色限制。"""
    from app.services import ui_mode_policy as module

    async def _supports(_db):
        return True

    monkeypatch.setattr(module, "supports_blocked_column", _supports)

    session = _FakeSession([_FakeResult(scalar="admin")])

    policy = await effective_policy(session, 1)

    assert policy.blocked == set()
    assert session.calls == 1


# ---------------- 角色维度的读写（假 session） ----------------


async def test_blocked_map_returns_empty_without_migration(monkeypatch):
    """列不存在时不查库、直接返回空：列表接口不能因此多一次必然失败的查询。"""
    from app.services import ui_mode_policy as module

    async def _supports(_db):
        return False

    monkeypatch.setattr(module, "supports_blocked_column", _supports)

    session = _FakeSession([])
    mapping = await module.blocked_map_for_roles(session, [1, 2])

    assert mapping == {}
    assert session.calls == 0


async def test_blocked_map_reads_in_one_query(monkeypatch):
    from app.services import ui_mode_policy as module

    async def _supports(_db):
        return True

    monkeypatch.setattr(module, "supports_blocked_column", _supports)

    session = _FakeSession([_FakeResult(rows=[(1, "pro"), (2, "")])])
    mapping = await module.blocked_map_for_roles(session, [1, 2])

    assert mapping == {1: "pro", 2: ""}
    assert session.calls == 1


async def test_set_role_blocked_requires_migration(monkeypatch):
    """迁移没跑时必须明确报错，而不是静默丢弃管理员的配置。"""
    from app.services import ui_mode_policy as module

    async def _supports(_db):
        return False

    monkeypatch.setattr(module, "supports_blocked_column", _supports)

    with pytest.raises(ValueError, match="迁移"):
        await module.set_role_blocked(_FakeSession([]), 3, "pro")


async def test_set_role_blocked_writes_serialized_value(monkeypatch):
    from app.services import ui_mode_policy as module

    async def _supports(_db):
        return True

    monkeypatch.setattr(module, "supports_blocked_column", _supports)

    session = _FakeSession([_FakeResult()])
    await module.set_role_blocked(session, 3, {"pro", "pro"})

    assert session.calls == 1
