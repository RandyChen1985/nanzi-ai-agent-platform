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


def test_system_secret_status_guard_and_admin_actor_fixes():
    """service 层禁非管理员改系统凭据状态；admin 创建/授权用 _actor_id。"""
    from app.services.secrets import center

    sig = inspect.signature(center.set_secret_status)
    assert "actor_is_admin" in sig.parameters
    assert sig.parameters["actor_is_admin"].default is False

    center_src = inspect.getsource(center.set_secret_status)
    assert "SCOPE_SYSTEM" in center_src
    assert "actor_is_admin" in center_src
    assert "SecretPermissionError" in center_src

    for admin_fn in (secrets_endpoint.admin_disable, secrets_endpoint.admin_set_status):
        src = inspect.getsource(admin_fn)
        assert "actor_is_admin=True" in src, admin_fn.__name__

    for admin_fn in (secrets_endpoint.admin_create, secrets_endpoint.admin_grant):
        src = inspect.getsource(admin_fn)
        assert "_actor_id(admin)" in src, admin_fn.__name__


def test_check_binding_passes_user_attrs_and_identity_attrs_skips_bool():
    """/check-binding 必须向解析器传递 user_attrs，且 _identity_attrs 支持整型并跳过 bool。"""
    src = inspect.getsource(secrets_endpoint.check_binding)
    assert "user_attrs=_identity_attrs(user_info)" in src

    attrs = secrets_endpoint._identity_attrs(
        {"user_id": 42, "email": "u@corp.cn", "dept_code": True}
    )
    assert attrs == {"user_id": "42", "email": "u@corp.cn"}


# --------------------------------------------------------------------------- #
# 授权范围的显示名
#
# 卡片里原来渲染成「用户 · 62」——管理员拿这个数字判断不了是谁，得先去用户表
# 反查，而这一栏问的恰恰是「这条凭据给了谁」。后端负责解析名字，前端负责排版，
# 所以这里锁的是「后端给的是名字、拿不到才留空」。
# --------------------------------------------------------------------------- #


class _Grant:
    """只带 `_grantee_name` 需要的字段，避免为一条纯函数测试起来整张表。"""

    def __init__(self, grantee_type: str, grantee_id: str):
        self.grantee_type = grantee_type
        self.grantee_id = grantee_id


def test_grantee_name_prefers_real_name():
    assert (
        secrets_endpoint._grantee_name(_Grant("user", "62"), {"62": "张三"}, {}) == "张三"
    )


def test_grantee_name_for_department():
    assert (
        secrets_endpoint._grantee_name(_Grant("dept", "DEPT-DATA"), {}, {"DEPT-DATA": "数据部"})
        == "数据部"
    )


def test_grantee_name_for_all_users():
    """全体授权的 grantee_id 是空串，照样要有人话名字。"""
    assert secrets_endpoint._grantee_name(_Grant("all", ""), {}, {}) == "全体用户"


def test_grantee_name_returns_none_instead_of_inventing():
    """解析不到就留 None：授权对象被删后 id 是唯一还能定位的线索，不能编名字。"""
    assert secrets_endpoint._grantee_name(_Grant("user", "62"), {}, {}) is None
    assert secrets_endpoint._grantee_name(_Grant("dept", "DEPT-X"), {}, {}) is None
    # 空 ref 不能去查表（不小心会命中空字符串 key）
    assert secrets_endpoint._grantee_name(_Grant("user", ""), {"": "空"}, {}) is None


def test_grantee_name_does_not_leak_between_types():
    """同一串 id 在两张表里都有时不能串：用户 id 与部门 code 各自查各自的表。"""
    grant = _Grant("user", "7")
    assert secrets_endpoint._grantee_name(grant, {"7": "李四"}, {"7": "数据部"}) == "李四"
    assert secrets_endpoint._grantee_name(_Grant("dept", "7"), {"7": "李四"}, {"7": "数据部"}) == "数据部"


def test_admin_list_emits_grantee_name():
    """序列化里必须带上这个字段，前端才有东西可显示。"""
    src = inspect.getsource(secrets_endpoint.admin_list)
    assert '"grantee_name": _grantee_name(g, user_names, dept_names)' in src


def test_admin_list_resolves_grant_users_from_the_same_query():
    """绑定者与被授权用户合并成一次查询：glue 成两次是同一张表的重复往返。"""
    src = inspect.getsource(secrets_endpoint.admin_list)
    assert "grant.grantee_type == GRANTEE_USER" in src
    # 用户名字查询不能再被 `if bindings:` 包着——没有绑定的凭据照样要显示授权对象
    body = src[src.index("owners = "):]
    assert "for b in bindings if b.bound_by_user_id" in body
