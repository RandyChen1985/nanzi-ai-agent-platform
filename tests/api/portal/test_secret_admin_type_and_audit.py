"""系统级凭据的管理员操作：类型收敛与审计操作人。

两个实锤问题：

1. ``admin_list`` 用 ``{str(b.bound_by_user_id)}`` 收集所有者，再喂给
   ``User.id.in_(...)``。``User.id`` 是整型主键 —— MySQL 靠隐式转换能过，
   PostgreSQL（asyncpg）直接报 ``operator does not exist: integer = character
   varying``，接口 500。同仓库 ``center.resolve_usage_users`` 是正确写法。

2. 管理员强制停用 / 状态开关 / 撤销授权，审计里 ``actor_id`` 全是 ``None``。
   凭据被误停用时查不出是谁动的，而函数签名里 ``admin`` 明明就在手上。
"""
from __future__ import annotations

import inspect

import pytest

from app.api.portal.endpoints import secrets as secrets_endpoint


# --------------------------------------------------------------------------- #
# 类型收敛
# --------------------------------------------------------------------------- #


def test_owner_ids_are_integers():
    """字符串 id 必须收敛成 int，否则 PG 下 User.id.in_() 直接报错。"""
    assert secrets_endpoint._owner_ids({"1", "7", "42"}) == [1, 7, 42]


def test_owner_ids_drops_non_numeric():
    """脏值直接丢，不猜、不抛异常拖垮整个列表接口。"""
    assert secrets_endpoint._owner_ids({"", "x", "  ", None, "3"}) == [3]


def test_owner_ids_empty():
    assert secrets_endpoint._owner_ids([]) == []
    assert secrets_endpoint._owner_ids(set()) == []


def test_admin_list_uses_owner_ids_helper():
    """结构性契约：admin_list 里不能再出现裸的 str 集合直接进 in_()。"""
    src = inspect.getsource(secrets_endpoint.admin_list)
    assert "_owner_ids(owners)" in src
    assert "User.id.in_(owners)" not in src


# --------------------------------------------------------------------------- #
# 审计操作人
# --------------------------------------------------------------------------- #


def test_actor_id_reads_admin_primary_key():
    """键名是 ``user_id``：``require_api_key`` 返回的身份字典里就叫这个。

    读 ``id`` 会永远拿到 None —— 修复看起来完成了，审计照样是空的。
    """
    assert secrets_endpoint._actor_id({"user_id": "7"}) == "7"
    assert secrets_endpoint._actor_id({"user_id": 7}) == "7"
    # 兼容直接给 id 的调用方
    assert secrets_endpoint._actor_id({"id": 7}) == "7"


def test_actor_id_returns_string_for_string_column():
    """``SecretAuditLog.actor_id`` 是 String(64)：PG 下传 int 会直接报错。"""
    value = secrets_endpoint._actor_id({"user_id": 7})
    assert isinstance(value, str), "必须转成字符串"


def test_actor_id_never_invents_a_value():
    """取不到就记 None —— 编一个 id 出来比留空更糟。"""
    assert secrets_endpoint._actor_id(None) is None
    assert secrets_endpoint._actor_id({}) is None
    assert secrets_endpoint._actor_id({"id": None}) is None
    # 非数字值不猜：审计里宁可空着，也不能塞个假操作人
    assert secrets_endpoint._actor_id({"user_id": ""}) is None
    assert secrets_endpoint._actor_id({"user_id": None}) is None


def test_admin_endpoints_pass_real_actor():
    """管理员撤销/停用/状态接口必须把当前管理员传下去，不能写死 None。"""
    for func in (
        secrets_endpoint.admin_revoke,
        secrets_endpoint.admin_disable,
        secrets_endpoint.admin_set_status,
    ):
        src = inspect.getsource(func)
        assert "actor_id=_actor_id(admin)" in src, func.__name__
        assert "actor_id=None" not in src, func.__name__


def test_revoke_grant_accepts_actor_id():
    """撤权过去签名里根本没有 actor_id，审计只能写 None。"""
    from app.services.secrets import center

    params = inspect.signature(center.revoke_grant).parameters
    assert "actor_id" in params
    assert params["actor_id"].default is None, "默认 None 以兼容既有调用方"


def test_global_binding_audit_preserves_actor_id():
    """管理员设/改/解全局绑定（bound_by_user_id=None）也必须记录真实操作人。"""
    from app.services.secrets import center

    for fn in (center.bind_secret, center.bind_rule, center.unbind_slot):
        params = inspect.signature(fn).parameters
        assert "actor_id" in params, fn.__name__
        assert params["actor_id"].default is None, fn.__name__

    for endpoint in (secrets_endpoint.create_binding, secrets_endpoint.delete_binding):
        src = inspect.getsource(endpoint)
        assert "actor_id=_actor_id(user_info)" in src, endpoint.__name__
