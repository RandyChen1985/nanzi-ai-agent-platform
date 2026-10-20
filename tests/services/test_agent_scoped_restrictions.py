"""智能体级资源限制（用户/角色 × 智能体）的核心行为测试。

口径（与 docs/superpowers/specs/2026-10-13-agent-scoped-resource-restrictions-design.md 一致）：
- 有效禁用 = 全局禁用 ∪ **当前智能体**的智能体级禁用；
- 写入只动该 agent 的行：全局行与**其它智能体**的行必须原样保留；
- 缓存键必须带作用域，否则"A 智能体的限制串到 B 智能体"；
- 未迁移（缺少 agent_id 列）时必须降级为既有行为，而不是让权限接口整体报错。

这些用例跑在真实库上（迁移已执行），因此同时覆盖了 SQL 层面的作用域过滤。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.permission import ResourcePermission
from app.services.permission_service import PermissionService


async def _make_user(db_session, prefix: str):
    from app.models.user import User

    name = f"{prefix}_{uuid.uuid4().hex[:8]}"
    # api_key_hash 在库里是 NOT NULL（历史设计），测试用户必须给一个唯一值
    user = User(
        user_name=name,
        role="user",
        status=1,
        api_key_hash=f"hash_{uuid.uuid4().hex}",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _add_global_forbidden(
    db_session, *, user_id=None, role_id=None, resource_type, resource_id
):
    """全局禁用行：``agent_id`` 为 NULL。ORM 未映射该列，因此显式写 NULL。"""
    from sqlalchemy import text

    await db_session.execute(
        text(
            "INSERT INTO ai_agent_resource_permissions "
            "(user_id, role_id, agent_id, resource_type, resource_id, enabled, created_at, updated_at) "
            "VALUES (:uid, :rid_subject, NULL, :rtype, :rid, 1, NOW(), NOW())"
        ).bindparams(
            uid=int(user_id) if user_id is not None else None,
            rid_subject=int(role_id) if role_id is not None else None,
            rtype=resource_type,
            rid=resource_id,
        )
    )
    await db_session.flush()


async def _agent_scoped_rows(db_session, *, user_id=None, role_id=None):
    """该主体名下 ``agent_id IS NOT NULL`` 的行（ORM 看不到 agent_id，只能走原生 SQL）。"""
    from sqlalchemy import text

    subject = "user_id" if user_id is not None else "role_id"
    value = int(user_id if user_id is not None else role_id)
    rows = (
        await db_session.execute(
            text(
                f"SELECT agent_id, resource_type, resource_id FROM ai_agent_resource_permissions "
                f"WHERE {subject} = :value AND agent_id IS NOT NULL "
                f"ORDER BY agent_id, resource_type, resource_id"
            ).bindparams(value=value)
        )
    ).all()
    return [tuple(row) for row in rows]


async def _global_rows(db_session, *, user_id=None, role_id=None):
    """该主体名下 ``agent_id IS NULL`` 的禁用/白名单行。"""
    from sqlalchemy import text

    subject = "user_id" if user_id is not None else "role_id"
    value = int(user_id if user_id is not None else role_id)
    rows = (
        await db_session.execute(
            text(
                f"SELECT resource_type, resource_id FROM ai_agent_resource_permissions "
                f"WHERE {subject} = :value AND agent_id IS NULL "
                f"ORDER BY resource_type, resource_id"
            ).bindparams(value=value)
        )
    ).all()
    return [tuple(row) for row in rows]


async def _make_role(db_session, prefix: str):
    from app.models.permission import Role

    role = Role(code=f"{prefix}_{uuid.uuid4().hex[:8]}", name=f"范围测试角色 {prefix}")
    db_session.add(role)
    await db_session.flush()
    return role


async def _add_agent_forbidden(
    db_session, *, user_id=None, role_id=None, agent_id, resource_type, resource_id
):
    """智能体级行：必须走原生 SQL —— ORM 刻意不映射 ``agent_id``（见 app/models/permission.py），

    这样"代码先于迁移上线"时才不会让权限接口整体报 Unknown column。
    """
    from sqlalchemy import text

    await db_session.execute(
        text(
            "INSERT INTO ai_agent_resource_permissions "
            "(user_id, role_id, agent_id, resource_type, resource_id, enabled, created_at, updated_at) "
            "VALUES (:uid, :rid_subject, :agent, :rtype, :rid, 1, NOW(), NOW())"
        ).bindparams(
            uid=int(user_id) if user_id is not None else None,
            rid_subject=int(role_id) if role_id is not None else None,
            agent=agent_id,
            rtype=resource_type,
            rid=resource_id,
        )
    )
    await db_session.flush()


@pytest.mark.asyncio
async def test_effective_forbidden_merges_global_and_agent_scope(db_session):
    """在智能体 X 里：全局禁 a + X 级禁 b → 两者都不可用；在智能体 Y 里只有 a 不可用。"""
    user = await _make_user(db_session, "scope_merge")
    service = PermissionService(db_session)
    await _add_global_forbidden(
        db_session, user_id=user.id, resource_type="forbidden_tool", resource_id="global_tool_a"
    )
    await _add_agent_forbidden(
        db_session,
        user_id=user.id,
        agent_id="agent-x",
        resource_type="forbidden_tool",
        resource_id="scoped_tool_b",
    )

    in_x = await service.get_user_permissions(user.id, "agent-x")
    assert set(in_x.permissions.forbidden_tools) == {"global_tool_a", "scoped_tool_b"}

    in_y = await service.get_user_permissions(user.id, "agent-y")
    assert set(in_y.permissions.forbidden_tools) == {"global_tool_a"}

    global_view = await service.get_user_permissions(user.id)
    assert set(global_view.permissions.forbidden_tools) == {"global_tool_a"}


@pytest.mark.asyncio
async def test_global_scope_excludes_agent_rows_everywhere(db_session):
    """全局视角（含"用户直配"回显）绝不能混入智能体级行，否则禁用资源页会误显示。"""
    user = await _make_user(db_session, "scope_direct")
    service = PermissionService(db_session)
    await _add_global_forbidden(
        db_session, user_id=user.id, resource_type="forbidden_tool", resource_id="g_tool"
    )
    await _add_agent_forbidden(
        db_session,
        user_id=user.id,
        agent_id="agent-z",
        resource_type="forbidden_tool",
        resource_id="z_tool",
    )

    direct = await service.get_user_direct_permissions(user.id)
    assert set(direct.forbidden_tools) == {"g_tool"}

    scoped = await service.get_agent_scoped_forbidden_ids(user_id=user.id, agent_id="agent-z")
    assert scoped == {"forbidden_tools": ["z_tool"], "forbidden_skills": []}


@pytest.mark.asyncio
async def test_writing_one_agent_never_touches_global_or_other_agents(db_session):
    """作用域隔离：写入 X 的限制后，全局行与 Y 的行必须原样保留。

    这是最容易写错的地方——"按主体整表重建"会把别的智能体的限制一起清掉。
    """
    user = await _make_user(db_session, "scope_isolate")
    service = PermissionService(db_session)
    await _add_global_forbidden(
        db_session, user_id=user.id, resource_type="forbidden_tool", resource_id="keep_global"
    )
    await _add_agent_forbidden(
        db_session,
        user_id=user.id,
        agent_id="agent-y",
        resource_type="forbidden_tool",
        resource_id="keep_y",
    )
    await db_session.commit()

    await service.update_agent_scoped_forbidden_ids(
        user_id=user.id,
        agent_id="agent-x",
        forbidden_tools=["new_x"],
        forbidden_skills=[],
    )
    await db_session.commit()

    from sqlalchemy import text

    rows = (
        await db_session.execute(
            text(
                "SELECT agent_id, resource_id FROM ai_agent_resource_permissions "
                "WHERE user_id = :uid"
            ).bindparams(uid=int(user.id))
        )
    ).all()
    by_scope: dict = {}
    for agent_id, resource_id in rows:
        by_scope.setdefault(agent_id, set()).add(resource_id)

    assert by_scope.get(None) == {"keep_global"}, by_scope
    assert by_scope.get("agent-y") == {"keep_y"}, by_scope
    assert by_scope.get("agent-x") == {"new_x"}, by_scope


@pytest.mark.asyncio
async def test_update_is_overwrite_and_supports_clearing(db_session, monkeypatch):
    # 写入端会剔除"运行时不读"的技能 id（个人技能/不存在的 id），所以这里固定公共技能清单，
    # 让用例不依赖运行环境里恰好装了哪些技能。
    monkeypatch.setattr(
        "app.services.ai.skill_resolver.list_skill_metas",
        lambda *a, **k: [{"id": "s1"}, {"id": "s2"}],
    )
    user = await _make_user(db_session, "scope_overwrite")
    service = PermissionService(db_session)

    await service.update_agent_scoped_forbidden_ids(
        user_id=user.id, agent_id="agent-o", forbidden_tools=["a", "b", "a"], forbidden_skills=["s1"]
    )
    await db_session.commit()
    data = await service.get_agent_scoped_forbidden_ids(user_id=user.id, agent_id="agent-o")
    assert sorted(data["forbidden_tools"]) == ["a", "b"], "重复项应去重且覆盖式写入"
    assert data["forbidden_skills"] == ["s1"]

    # 传空列表 = 清空该类别；不传（None）= 不修改
    await service.update_agent_scoped_forbidden_ids(
        user_id=user.id, agent_id="agent-o", forbidden_tools=[], forbidden_skills=None
    )
    await db_session.commit()
    data = await service.get_agent_scoped_forbidden_ids(user_id=user.id, agent_id="agent-o")
    assert data["forbidden_tools"] == []
    assert data["forbidden_skills"] == ["s1"], "未提交的类别必须保持原值"


@pytest.mark.asyncio
async def test_write_drops_skill_ids_the_runtime_never_reads(db_session, monkeypatch):
    """禁一个"只属于个人技能/根本不存在"的 id 是无效写入，必须在写入端就丢掉。

    这类行会落库成功、弹框回显"已禁"，但运行时三处技能过滤都不读它（个人技能永不受限），
    管理员以为限住了、实际什么都没发生。真实库里那 28 行 `s1` / `scoped_skill` 就是这么来的。
    """
    monkeypatch.setattr(
        "app.services.ai.skill_resolver.list_skill_metas",
        lambda *a, **k: [{"id": "real-public-skill"}],
    )
    user = await _make_user(db_session, "scope_drop")
    service = PermissionService(db_session)

    data = await service.update_agent_scoped_forbidden_ids(
        user_id=user.id,
        agent_id="agent-drop",
        forbidden_skills=["real-public-skill", "s1", "scoped_skill"],
    )
    await db_session.commit()
    assert data["forbidden_skills"] == ["real-public-skill"], "只有公共技能才写得进去"

    rows = await _agent_scoped_rows(db_session, user_id=user.id)
    assert [r[2] for r in rows if r[1] == "forbidden_skill"] == ["real-public-skill"]


@pytest.mark.asyncio
async def test_write_keeps_ids_when_catalog_is_unreadable(db_session, monkeypatch):
    """目录不可用时**一律不剥离**：宁可存一条无效项，也不能误删管理员设的有效限制。"""
    monkeypatch.setattr(
        "app.services.ai.skill_resolver.list_skill_metas",
        lambda *a, **k: [],
    )
    user = await _make_user(db_session, "scope_keep")
    service = PermissionService(db_session)

    data = await service.update_agent_scoped_forbidden_ids(
        user_id=user.id, agent_id="agent-keep", forbidden_skills=["whatever"]
    )
    await db_session.commit()
    assert data["forbidden_skills"] == ["whatever"]


@pytest.mark.asyncio
async def test_role_scoped_restriction_merges_for_members(db_session):
    """角色级限制对本角色成员生效，且不影响非成员。"""
    from app.models.permission import Role, UserRoleRelation

    member = await _make_user(db_session, "scope_member")
    outsider = await _make_user(db_session, "scope_outsider")
    role = Role(code=f"role_{uuid.uuid4().hex[:8]}", name="范围测试角色")
    db_session.add(role)
    await db_session.flush()
    db_session.add(UserRoleRelation(user_id=member.id, role_id=role.id))
    await db_session.flush()

    service = PermissionService(db_session)
    await service.update_agent_scoped_forbidden_ids(
        role_id=role.id, agent_id="agent-r", forbidden_tools=["role_scoped_tool"], forbidden_skills=[]
    )
    await db_session.commit()

    in_role = await service.get_user_permissions(member.id, "agent-r")
    assert "role_scoped_tool" in set(in_role.permissions.forbidden_tools)

    # 非成员不受影响
    outside = await service.get_user_permissions(outsider.id, "agent-r")
    assert "role_scoped_tool" not in set(outside.permissions.forbidden_tools)

    # 全局视角不包含角色级智能体行
    global_view = await service.get_user_permissions(member.id)
    assert "role_scoped_tool" not in set(global_view.permissions.forbidden_tools)


@pytest.mark.asyncio
async def test_cache_key_carries_agent_scope(db_session):
    """缓存键必须带作用域，否则 A 智能体的限制会串到 B 智能体。"""
    service = PermissionService(db_session)
    global_key = await service._get_cache_key(42)
    scoped_a = await service._get_cache_key(42, "agent-a")
    scoped_b = await service._get_cache_key(42, "agent-b")

    assert global_key != scoped_a != scoped_b
    # 版本段随权限结构升版（v6 加智能体作用域、v7 菜单 id 归位），这里钉住当前版本
    assert "v7" in global_key
    assert scoped_a.endswith("agent-a")
    assert global_key.endswith("agent:-")


@pytest.mark.asyncio
async def test_skill_policy_cache_key_is_scoped():
    from app.services.ai.skills import policy as policy_mod

    assert policy_mod._scoped_cache_key("42", "agent-a") == "42|agent-a"
    assert policy_mod._scoped_cache_key("42") == "42|-"
    assert policy_mod._scoped_cache_key("42", "agent-a") != policy_mod._scoped_cache_key("42", "agent-b")


@pytest.mark.asyncio
async def test_agent_scope_support_probe_reflects_migration(db_session):
    """迁移已执行 → 探测为 True（若未执行则降级为既有行为，由 probe 的 warning 记录）。"""
    service = PermissionService(db_session)
    assert await service._supports_agent_scope() is True


@pytest.mark.asyncio
async def test_agent_scope_filters_degrade_when_column_missing(db_session, monkeypatch):
    """列不存在时必须返回空过滤条件（= 既有行为），而不是抛错让权限接口整体 5xx。"""
    service = PermissionService(db_session)
    monkeypatch.setattr(
        "app.services.permission_service._AGENT_SCOPE_COLUMN_STATE", False, raising=False
    )
    assert await service._agent_scope_filters("agent-x") == ()
    # 探测被缓存为 False 时，依然能正常读全局权限（不引用 agent_id）
    assert await service.get_user_permissions(1) is not None


def test_split_agent_tool_bindings_handles_real_shapes():
    """真实数据里 MCP 的形态是 ``mcp-<server 标识>:<tool_name>``。

    前缀是 **server_name**（且常被去掉开头 ``mcp-``），不是 ``McpServer.id``——
    见 test_mcp_items_resolve_description_and_server_label 里对线上形态的固化。
    """
    from app.services.agent_scoped_restrictions import split_agent_tool_bindings

    plain, mcp = split_agent_tool_bindings(
        [
            "read_file",
            "exec_command",
            "mcp-public-admin-hcp:get-tickets",
            "mcp-public-admin-hcp:get-current-date",
            "mcp-other-server:do_thing",
        ]
    )
    assert plain == ["read_file", "exec_command"]
    assert mcp == [
        ("public-admin-hcp", "get-tickets"),
        ("public-admin-hcp", "get-current-date"),
        ("other-server", "do_thing"),
    ]
    # 历史脏数据不应让整个弹框失败
    assert split_agent_tool_bindings(None) == ([], [])
    assert split_agent_tool_bindings('["a"]') == (["a"], [])
    assert split_agent_tool_bindings("not-json") == ([], [])
    # 前缀像 mcp 但缺冒号：按普通工具处理，不能丢
    assert split_agent_tool_bindings(["mcp-broken"]) == (["mcp-broken"], [])


class TestBatchCountsEndpointContract:
    """批量角标接口的契约：一次查询、口径与单智能体 GET 一致、未迁移时降级。

    读源码做契约测试（与 tests/frontend 的既有风格一致）：这些约束是"以后改坏了会静默
    变慢/变错"的类型，行为测试很难覆盖（例如有人改回逐个查询，功能照常但请求数翻倍）。
    """

    @staticmethod
    def _source() -> str:
        from pathlib import Path

        return Path("app/api/portal/endpoints/management.py").read_text(encoding="utf-8")

    def test_batch_endpoint_uses_a_single_in_query(self):
        import re

        source = self._source()
        start = source.index('@router.get("/agent-restrictions/counts")')
        # 截到下一个路由装饰器为止，避免匹配到别的端点
        end = source.find("@router.", start + 10)
        handler = source[start:] if end == -1 else source[start:end]

        assert "agent_id IN (" in handler, "必须一次 IN 查询，而不是逐个智能体查"
        assert re.search(r"SELECT\s+agent_id,\s*resource_type,\s*resource_id", handler), (
            "必须返回清单而不是 COUNT：角标要剔除「全局已禁用」项，口径必须与弹框一致"
        )
        assert "enabled = TRUE" in handler, (
            "读取必须过滤 enabled（存在即禁用是写入语义，读取仍看开关）；"
            "写 TRUE 而非 1，PG 的 BOOLEAN 列不接受整型字面量"
        )

    def test_batch_endpoint_degrades_before_migration(self):
        source = self._source()
        start = source.index('@router.get("/agent-restrictions/counts")')
        end = source.find("@router.", start + 10)
        handler = source[start:] if end == -1 else source[start:end]

        assert "_supports_agent_scope()" in handler, (
            "未迁移时必须降级返回空清单，而不是 500 让权限页整体打不开"
        )

    def test_batch_endpoint_requires_a_subject(self):
        source = self._source()
        start = source.index('@router.get("/agent-restrictions/counts")')
        end = source.find("@router.", start + 10)
        handler = source[start:] if end == -1 else source[start:end]

        assert "user_id 或 role_id 必须提供一个" in handler, (
            "缺视角参数时必须显式 400：静默返回全 0 会让管理员以为没有限制"
        )

    def test_all_agent_scope_endpoints_require_edit_permission(self):
        import re

        source = self._source()
        endpoints = [
            '@router.get("/agents/{agent_id}/restrictable-resources")',
            '@router.get("/users/{user_id}/agent-restrictions/{agent_id}")',
            '@router.put("/users/{user_id}/agent-restrictions/{agent_id}")',
            '@router.get("/roles/{role_id}/agent-restrictions/{agent_id}")',
            '@router.put("/roles/{role_id}/agent-restrictions/{agent_id}")',
            '@router.get("/agent-restrictions/counts")',
        ]
        for decorator in endpoints:
            start = source.index(decorator)
            end = source.find("@router.", start + 10)
            handler = source[start:] if end == -1 else source[start:end]
            assert re.search(
                r'require_permission\(\s*"element"\s*,\s*"element:user:edit"\s*\)', handler
            ), f"{decorator} 必须要求 element:user:edit（能配禁用才需要看清单）"


# ---------------------------------------------------------------------------
# 回归：全局权限的保存必须**只**动全局行
#
# ORM 刻意不映射 `agent_id`（防"代码先于迁移上线"时接口整体 503），代价是 ORM 生成的
# DELETE 里没有作用域条件。迁移一旦执行，全局保存就会把该主体在所有智能体上的限制
# 一起删掉——管理员改一次菜单或角色，智能体级配置全部蒸发。这是静默的数据丢失。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_saving_global_user_permissions_keeps_agent_scoped_rows(db_session):
    user = await _make_user(db_session, "global_save_keeps_scope")
    service = PermissionService(db_session)
    await _add_agent_forbidden(
        db_session, user_id=user.id, agent_id="agent-x",
        resource_type="forbidden_tool", resource_id="scoped_tool",
    )
    await _add_agent_forbidden(
        db_session, user_id=user.id, agent_id="agent-y",
        resource_type="forbidden_skill", resource_id="scoped_skill",
    )
    await _add_global_forbidden(
        db_session, user_id=user.id, resource_type="forbidden_tool", resource_id="old_global"
    )

    from app.schemas.permission import PermissionUpdate

    await service.update_user_permissions(
        int(user.id), PermissionUpdate(forbidden_tools=["new_global"])
    )

    assert await _agent_scoped_rows(db_session, user_id=user.id) == [
        ("agent-x", "forbidden_tool", "scoped_tool"),
        ("agent-y", "forbidden_skill", "scoped_skill"),
    ], "保存全局禁用不得删除该用户的智能体级行"
    # 全局行本身仍是覆盖式语义：旧值被新值替换
    assert await _global_rows(db_session, user_id=user.id) == [("forbidden_tool", "new_global")]


@pytest.mark.asyncio
async def test_saving_global_role_permissions_keeps_agent_scoped_rows(db_session):
    role = await _make_role(db_session, "global_save_role")
    service = PermissionService(db_session)
    await _add_agent_forbidden(
        db_session, role_id=role.id, agent_id="agent-r",
        resource_type="forbidden_tool", resource_id="role_scoped_tool",
    )
    await _add_global_forbidden(
        db_session, role_id=role.id, resource_type="forbidden_tool", resource_id="old_role_global"
    )

    from app.schemas.permission import PermissionUpdate

    await service.update_role_permissions(
        int(role.id), PermissionUpdate(forbidden_tools=["new_role_global"])
    )

    assert await _agent_scoped_rows(db_session, role_id=role.id) == [
        ("agent-r", "forbidden_tool", "role_scoped_tool"),
    ], "保存角色全局禁用不得删除该角色的智能体级行"


# ---------------------------------------------------------------------------
# 回归：角色的全局权限读取不得把智能体级行算进来
#
# `get_user_permissions` / `get_user_direct_permissions` / `_role_derived_forbidden_ids`
# 都带了作用域过滤，`get_role_permissions` 当时漏了。它被角色权限弹框用来回显，
# 智能体级行一旦混进去，管理员点一次保存就把"只针对某智能体"固化成"角色级全局禁用"。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_permissions_exclude_agent_scoped_rows(db_session):
    role = await _make_role(db_session, "role_scope_read")
    await _add_global_forbidden(
        db_session, role_id=role.id, resource_type="forbidden_tool", resource_id="global_role_tool"
    )
    await _add_global_forbidden(
        db_session, role_id=role.id, resource_type="forbidden_skill", resource_id="global_role_skill"
    )
    await _add_agent_forbidden(
        db_session, role_id=role.id, agent_id="agent-r",
        resource_type="forbidden_tool", resource_id="role_agent_tool",
    )
    await _add_agent_forbidden(
        db_session, role_id=role.id, agent_id="agent-r",
        resource_type="forbidden_skill", resource_id="role_agent_skill",
    )

    response = await PermissionService(db_session).get_role_permissions(int(role.id))

    assert response.permissions.forbidden_tools == ["global_role_tool"], (
        "角色权限只讲全局；智能体级行混进来会被弹框固化提升为角色级全局禁用"
    )
    assert response.permissions.forbidden_skills == ["global_role_skill"]


@pytest.mark.asyncio
async def test_role_scoped_rows_stay_hidden_from_every_global_reader(db_session):
    """同一份数据在**所有**全局视角入口都不该露头，避免只修一处留下旁路。"""
    role = await _make_role(db_session, "role_scope_all_readers")
    await _add_agent_forbidden(
        db_session, role_id=role.id, agent_id="agent-only",
        resource_type="forbidden_tool", resource_id="hidden_tool",
    )

    response = await PermissionService(db_session).get_role_permissions(int(role.id))
    assert response.permissions.forbidden_tools == []


# ---------------------------------------------------------------------------
# 回归：个人技能清单必须按**目标**视角取，而不是当前登录管理员
#
# 端点拿 `admin`（登录者）去扫个人技能目录，于是：
#  - 配用户时看到的是管理员自己的私有技能，目标用户的技能一个都不显示；
#  - 配角色时角色根本没有个人技能，却塞进了管理员的私有技能。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_personal_skill_metas_follow_the_given_identity(db_session, tmp_path, monkeypatch):
    """`list_personal_skill_metas` 扫的是**传入身份**的目录，不是调用者。"""
    from app.services import agent_scoped_restrictions as module

    owner_dir = tmp_path / "owner" / "skills"
    (owner_dir / "owner_skill").mkdir(parents=True)
    (owner_dir / "owner_skill" / "SKILL.md").write_text("# owner", encoding="utf-8")
    other_dir = tmp_path / "other" / "skills"
    (other_dir / "other_skill").mkdir(parents=True)
    (other_dir / "other_skill" / "SKILL.md").write_text("# other", encoding="utf-8")

    from app.services.ai.runtime.agentscope import workspace as workspace_module

    def fake_key(*, user_id, user_name):
        return "owner" if str(user_id) == "1" else "other"

    # `get_user_personal_skills_dir` 在函数体内 `from ... import`，所以打在 workspace 模块上
    # 就能生效，不需要（也不能）替换 skill_resolver 里那个名字本身。
    monkeypatch.setattr(workspace_module, "resolve_workspace_user_key", fake_key)
    monkeypatch.setattr(workspace_module, "default_workspace_root", lambda: str(tmp_path))

    owner = module.list_personal_skill_metas({"id": 1, "user_name": "owner"})
    other = module.list_personal_skill_metas({"id": 2, "user_name": "other"})

    owner_names = {item.get("name") or item.get("skill_name") for item in owner}
    other_names = {item.get("name") or item.get("skill_name") for item in other}
    assert "owner_skill" in owner_names, f"传入身份的技能必须被列出，实际 {owner_names}"
    assert "other_skill" not in owner_names, "不得扫描到别人的个人技能目录"
    assert "other_skill" in other_names
    assert module.list_personal_skill_metas(None) == [], "无身份（角色场景）必须返回空"


def test_restrictable_endpoint_never_lists_the_operator_personal_skills():
    """端点必须传目标用户身份；角色场景传空。

    这条是契约断言：缺陷的根源在**调用点**（把 `admin` 传了进去），
    行为测试要造出 agent 绑定 + 两个用户目录，代价远高于收益，
    而调用点的形状一变契约就失效，正适合用源码断言钉住。
    """
    from pathlib import Path as _Path

    source = (
        _Path(__file__).resolve().parents[2]
        / "app/api/portal/endpoints/management.py"
    ).read_text(encoding="utf-8")

    assert "list_personal_skill_metas(admin)" not in source, (
        "不得用当前登录管理员作为个人技能视角"
    )
    assert "personal_skill_owner" in source, "必须显式构造目标视角的身份"
    assert '{"id": person.id, "user_name": person.user_name}' in source, (
        "用户场景要用目标用户自己的 id / user_name"
    )


async def _make_agent_with_version(
    db_session, *, tools=None, skills=None, skills_custom=False, status="PUBLISHED"
):
    from app.models.agent import AIAgent, AIAgentVersion

    agent_id = str(uuid.uuid4())
    agent = AIAgent(
        id=agent_id,
        name=f"agent_{uuid.uuid4().hex[:8]}",
        display_name="范围测试智能体",
        agent_type="GENERAL",
        is_enabled=True,
    )
    db_session.add(agent)
    db_session.add(
        AIAgentVersion(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            version_number=1,
            system_prompt="test",
            tools=list(tools or []),
            skills=skills,
            skills_custom=skills_custom,
            status=status,
        )
    )
    await db_session.flush()
    return agent


# ---------------------------------------------------------------------------
# SKILLS 候选的范围由 `skills_custom` 决定，不是由 `skills` 是否为空决定
#
# `agent_manager.py` 的注释写得很清楚：skills_custom=False 时运行时**使用全部公共技能**
# （skill_count 也按启用中的公共技能总数算）。所以默认智能体的 SKILLS 页签若是空的
# （截图里的 0/0），管理员就没有任何技能可以限制——可那些技能在实际运行中全都生效。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_agent_lists_all_public_skills(db_session, monkeypatch):
    from app.services import agent_scoped_restrictions as module
    from app.services.ai import skill_resolver

    monkeypatch.setattr(
        skill_resolver,
        "list_global_skill_metas",
        lambda: [{"id": "pdf", "name": "PDF 处理"}, {"id": "report", "name": "报表生成"}],
    )
    agent = await _make_agent_with_version(
        db_session, tools=[], skills=[], skills_custom=False
    )

    result = await module.build_restrictable_resources(db_session, agent_id=agent.id)

    public = [group for group in result["groups"] if group["key"] == "public_skills"]
    assert public, "默认（非自定义白名单）智能体必须列出全部公共技能，否则无从限制"
    assert [item["id"] for item in public[0]["items"]] == ["pdf", "report"]
    assert [item["label"] for item in public[0]["items"]] == ["PDF 处理", "报表生成"]


@pytest.mark.asyncio
async def test_whitelist_agent_lists_only_its_own_skills(db_session, monkeypatch):
    """自定义白名单时只列它自己选中的：多列会让管理员限制到"本来就不加载"的技能。"""
    from app.services import agent_scoped_restrictions as module
    from app.services.ai import skill_resolver

    monkeypatch.setattr(
        skill_resolver,
        "list_global_skill_metas",
        lambda: [{"id": "pdf", "name": "PDF 处理"}, {"id": "report", "name": "报表生成"}],
    )
    agent = await _make_agent_with_version(
        db_session, tools=[], skills=["report"], skills_custom=True
    )

    result = await module.build_restrictable_resources(db_session, agent_id=agent.id)

    public = [group for group in result["groups"] if group["key"] == "public_skills"]
    assert public and [item["id"] for item in public[0]["items"]] == ["report"]


@pytest.mark.asyncio
async def test_draft_agent_without_skills_still_lists_all_public_skills(db_session, monkeypatch):
    """从未发布 / skills 为 NULL 的历史数据同样按"全量公共技能"处理，不能退化成空页签。"""
    from app.services import agent_scoped_restrictions as module
    from app.services.ai import skill_resolver

    monkeypatch.setattr(
        skill_resolver, "list_global_skill_metas", lambda: [{"id": "pdf", "name": "PDF"}]
    )
    agent = await _make_agent_with_version(
        db_session, tools=[], skills=None, skills_custom=False, status="DRAFT"
    )

    result = await module.build_restrictable_resources(db_session, agent_id=agent.id)

    public = [group for group in result["groups"] if group["key"] == "public_skills"]
    assert public and [item["id"] for item in public[0]["items"]] == ["pdf"]


# ---------------------------------------------------------------------------
# 工具候选要带出 API 工具的业务分组
#
# 前端按「业务分组优先、否则按关键字猜能力域」展示，与智能体编辑页同一套规则。
# 不带 group_name 的话弹框只能退回关键字，同一个工具在两个页面会归属不同的组。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_tool_items_carry_their_business_group(db_session, monkeypatch):
    from app.models.tool import SysApiTool
    from app.services import agent_scoped_restrictions as module

    suffix = uuid.uuid4().hex[:8]
    grouped_name = f"crm_sync_{suffix}"
    plain_name = f"plain_api_{suffix}"
    db_session.add(
        SysApiTool(
            id=str(uuid.uuid4()),
            name=grouped_name,
            description="客户同步",
            group_name="客户域",
            method="GET",
            url_template="https://example.invalid/crm",
        )
    )
    db_session.add(
        SysApiTool(
            id=str(uuid.uuid4()),
            name=plain_name,
            description="未分组",
            group_name=None,
            method="GET",
            url_template="https://example.invalid/plain",
        )
    )
    await db_session.flush()

    agent = await _make_agent_with_version(db_session, tools=[grouped_name, plain_name])

    result = await module.build_restrictable_resources(db_session, agent_id=agent.id)

    api_group = next(group for group in result["groups"] if group["key"] == "api_tools")
    by_id = {item["id"]: item for item in api_group["items"]}
    assert by_id[grouped_name]["group_name"] == "客户域"
    assert "group_name" not in by_id[plain_name], "未分组不要塞空字符串，前端按缺失处理"


# ---------------------------------------------------------------------------
# 原生 SQL 的布尔字面量必须可移植
#
# 这个特性刻意绕开 ORM（见 app/models/permission.py 的说明），因此布尔值得手写。写 `1`
# 在 MySQL 上没问题（enabled 是 TINYINT(1)，TRUE 就是 1 的别名），但 PG 里这一列是
# `BOOLEAN NOT NULL DEFAULT TRUE`（db-prod-pg/V0-baseline.sql），而 PG 在 BOOLEAN 与
# INTEGER 之间**没有隐式转换**：`WHERE enabled = 1` 会直接抛
# `operator does not exist: boolean = integer`，`VALUES (..., 1, ...)` 会抛
# `column "enabled" is of type boolean but expression is of type integer`。
#
# 测试跑在 SQLite 上，而 SQLite 的 BOOLEAN 亲和 NUMERIC，`1` 照样能过——也就是说
# **这类方言问题测试永远测不出来**，只能靠契约断言钉住。
# ---------------------------------------------------------------------------

_AGENT_SCOPE_SOURCES = (
    "app/services/permission_service.py",
    "app/api/portal/endpoints/management.py",
)


def test_agent_scoped_native_sql_uses_portable_boolean_literals():
    for relative in _AGENT_SCOPE_SOURCES:
        source = (Path(__file__).resolve().parents[2] / relative).read_text(encoding="utf-8")
        assert "enabled = 1" not in source, (
            f"{relative} 里的 `enabled = 1` 在 PostgreSQL 下会报 boolean = integer"
        )
        assert "enabled = TRUE" in source, f"{relative} 的 enabled 过滤请显式写 TRUE"

    service = (
        Path(__file__).resolve().parents[2] / "app/services/permission_service.py"
    ).read_text(encoding="utf-8")
    assert ":rid, TRUE, NOW(), NOW()" in service, (
        "插入语句的 enabled 也要写 TRUE：PG 不接受整型字面量"
    )
    assert ":rid, 1, NOW(), NOW()" not in service


def test_boolean_literals_survive_both_dialects():
    """`TRUE` 在 MySQL 与 PG 都是合法布尔字面量（MySQL 视其为 1 的别名）。"""
    from sqlalchemy import text
    from sqlalchemy.dialects import mysql, postgresql, sqlite

    statement = text("SELECT 1 FROM t WHERE enabled = TRUE")
    for dialect in (mysql.dialect(), postgresql.dialect(), sqlite.dialect()):
        compiled = str(statement.compile(dialect=dialect))
        assert "TRUE" in compiled.upper() or "1" in compiled


# ---------------------------------------------------------------------------
# 候选条目必须带中文描述
#
# 没有描述，弹框里每个工具就只是一行裸 id（`system_http_request`），管理员看不出这是
# 什么工具；而智能体编辑页一直显示的是描述。两边信息量必须对齐。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_builtin_tool_items_carry_chinese_descriptions(db_session):
    from app.services import agent_scoped_restrictions as module
    from app.services.ai.tools.forbidden_catalog import BUILTIN_FORBIDDEN_TOOL_CANDIDATES

    expected = {
        str(candidate.id): str(candidate.description).strip()
        for candidate in BUILTIN_FORBIDDEN_TOOL_CANDIDATES
        if str(candidate.description or "").strip()
    }
    assert expected, "内置候选本身必须带描述，否则这条断言没有意义"
    sample = "read_file"
    assert sample in expected, "read_file 应有内置中文描述"

    agent = await _make_agent_with_version(db_session, tools=[sample])

    result = await module.build_restrictable_resources(db_session, agent_id=agent.id)

    builtin = next(group for group in result["groups"] if group["key"] == "builtin_tools")
    item = next(entry for entry in builtin["items"] if entry["id"] == sample)
    assert item["description"] == expected[sample], "内置工具的副标题要显示中文描述"
    assert item["label"] == sample, "主标题仍是规范 id，与全局禁用候选保持一致"


@pytest.mark.asyncio
async def test_api_tool_items_carry_registry_descriptions(db_session):
    from app.models.tool import SysApiTool
    from app.services import agent_scoped_restrictions as module

    name = f"crm_sync_{uuid.uuid4().hex[:8]}"
    db_session.add(
        SysApiTool(
            id=str(uuid.uuid4()),
            name=name,
            description="同步 CRM 客户主数据",
            group_name="客户域",
            method="GET",
            url_template="https://example.invalid/crm",
        )
    )
    await db_session.flush()
    agent = await _make_agent_with_version(db_session, tools=[name])

    result = await module.build_restrictable_resources(db_session, agent_id=agent.id)

    api_group = next(group for group in result["groups"] if group["key"] == "api_tools")
    assert api_group["items"][0]["description"] == "同步 CRM 客户主数据"


@pytest.mark.asyncio
async def test_public_skill_items_carry_descriptions(db_session, monkeypatch):
    from app.services import agent_scoped_restrictions as module
    from app.services.ai import skill_resolver

    monkeypatch.setattr(
        skill_resolver,
        "list_global_skill_metas",
        lambda: [{"id": "pdf", "name": "PDF 处理", "description": "拆分与合并 PDF"}],
    )
    agent = await _make_agent_with_version(db_session, tools=[], skills=[], skills_custom=False)

    result = await module.build_restrictable_resources(db_session, agent_id=agent.id)

    skills = next(group for group in result["groups"] if group["key"] == "public_skills")
    assert skills["items"][0]["description"] == "拆分与合并 PDF"


@pytest.mark.asyncio
async def test_catalog_reports_the_source_version(db_session):
    """候选来自哪个版本必须显式返回：编辑页看的是草稿，数量差异需要能解释。"""
    from app.services import agent_scoped_restrictions as module

    agent = await _make_agent_with_version(db_session, tools=["read_file"], status="PUBLISHED")

    result = await module.build_restrictable_resources(db_session, agent_id=agent.id)

    source = result["source_version"]
    assert source["version_number"] == 1
    assert source["status"] == "PUBLISHED"
    assert source["is_published"] is True

    draft = await _make_agent_with_version(db_session, tools=["read_file"], status="DRAFT")
    draft_result = await module.build_restrictable_resources(db_session, agent_id=draft.id)
    assert draft_result["source_version"]["is_published"] is False, (
        "从未发布时退回最新版本，但要标明尚未发布"
    )


# ---------------------------------------------------------------------------
# MCP：描述与分组名
#
# 线上数据里，绑定前缀既不是 McpServer.id，也不是完整的 server_name：
#   McpServer.server_name        = 'mcp-private-admin-55555'
#   AIAgentVersion.tools 里的项   = 'mcp-private-admin-55555:get-current-date'
#   McpToolCache.tool_name       = 'mcp-private-admin-55555:get-current-date'
# 也就是说：绑定前缀是**去掉开头 mcp- 的 server_name**，而缓存表的 tool_name 用的是
# **完整 server_name** 作前缀。按 (server_id, 工具段) 建索引永远匹配不上，
# 于是描述明明存在，弹框里却只显示一行裸 id。
# ---------------------------------------------------------------------------


async def _make_mcp_server_with_tool(db_session, server_name: str, tool: str, description: str):
    from app.models.mcp import McpServer, McpToolCache

    server_id = str(uuid.uuid4())
    db_session.add(
        McpServer(id=server_id, server_name=server_name, sse_url="https://example.invalid/sse")
    )
    db_session.add(
        McpToolCache(
            id=str(uuid.uuid4()),
            server_id=server_id,
            tool_name=f"{server_name}:{tool}",
            tool_description=description,
        )
    )
    await db_session.flush()
    return server_id


@pytest.mark.asyncio
async def test_mcp_items_resolve_description_and_server_label(db_session):
    from app.services import agent_scoped_restrictions as module

    # server_name 上有唯一约束，用真实存在的名字会撞库；后缀保证唯一，形态保持一致
    server_name = f"mcp-private-admin-{uuid.uuid4().hex[:6]}"
    await _make_mcp_server_with_tool(
        db_session, server_name, "get-current-date", "获取当前日期，以上海时区（UTC+8）为准"
    )
    # 线上的绑定原文就是「完整 server_name + : + 工具段」，解析时才去掉 mcp- 前缀
    agent = await _make_agent_with_version(
        db_session, tools=[f"{server_name}:get-current-date"]
    )

    result = await module.build_restrictable_resources(db_session, agent_id=agent.id)

    group = next(g for g in result["groups"] if g["key"].startswith("mcp:"))
    assert group["label"] == server_name, "分组标题要用完整 server_name，而不是被去掉 mcp- 的前缀"
    item = group["items"][0]
    assert item["id"] == f"{server_name}:get-current-date", (
        "落库标识必须是 server_name:工具段 全名：运行时注册名由全名的哈希生成，"
        "只存工具段会导致禁用判定永远匹配不上（勾了禁不掉）"
    )
    assert item["label"] == "get-current-date", "展示名仍用工具段，不必啰嗦地带上 server"
    assert item["description"] == "获取当前日期，以上海时区（UTC+8）为准", (
        "缓存表用「server_name:工具段」作完整名，必须把前缀拼回去才查得到描述"
    )


@pytest.mark.asyncio
async def test_mcp_prefix_may_be_the_server_uuid(db_session):
    """绑定的 server 段写成 McpServer.id 时（早期形态）同样要能解析出名称与描述。"""
    from app.services import agent_scoped_restrictions as module

    server_name = f"mcp-public-admin-{uuid.uuid4().hex[:6]}"
    server_id = await _make_mcp_server_with_tool(db_session, server_name, "get-tickets", "查询余票")
    agent = await _make_agent_with_version(db_session, tools=[f"mcp-{server_id}:get-tickets"])

    result = await module.build_restrictable_resources(db_session, agent_id=agent.id)

    group = next(g for g in result["groups"] if g["key"].startswith("mcp:"))
    assert group["key"] == f"mcp:{server_id}"
    assert group["label"] == server_name, "UUID 前缀也要反查回 server_name"
    assert group["items"][0]["description"] == "查询余票"


@pytest.mark.asyncio
async def test_mcp_tool_without_cache_row_is_still_listed(db_session):
    """缓存未同步（甚至 server 已被删）时仍要展示，只是没有描述——不能因此漏配。"""
    from app.services import agent_scoped_restrictions as module

    agent = await _make_agent_with_version(
        db_session, tools=["mcp-ghost-server:do_thing"]
    )

    result = await module.build_restrictable_resources(db_session, agent_id=agent.id)

    group = next(g for g in result["groups"] if g["key"].startswith("mcp:"))
    assert group["items"][0]["id"] == "ghost-server:do_thing", (
        "server 反查不到也要用绑定里的前缀拼出全名——退化成工具段就永远禁不掉"
    )
    assert "description" not in group["items"][0], "没有缓存描述就不要编一个"
    assert group["label"] == "ghost-server", "server 解析不到就退回前缀本身"


@pytest.mark.asyncio
async def test_agent_scoped_mcp_id_expands_to_the_runtime_registered_name(db_session):
    """智能体级 MCP 项的 id 必须能展开出运行时的模型调用名（「勾了禁不掉」的回归钉）。

    运行时的 MCP 工具注册名是 ``build_model_tool_name("server_name:工具段")``，而禁用判定
    会把名单里的 id 逐个交给 ``to_runtime_tool_names`` 再与调用名比对。id 若只存工具段，
    全名哈希与工具段哈希不同，两条名字对不上——策略静默失效，管理员只会看到"禁了没反应"。
    全局级候选一直用的是全名（``McpToolCache.tool_name``），智能体级当初漏了这一步。
    """
    from app.services import agent_scoped_restrictions as module
    from app.services.ai.tools.model_tool_name import (
        build_model_tool_name,
        to_runtime_tool_names,
    )

    server_name = f"mcp-private-admin-{uuid.uuid4().hex[:6]}"
    await _make_mcp_server_with_tool(db_session, server_name, "get-current-date", "获取当前日期")
    agent = await _make_agent_with_version(db_session, tools=[f"{server_name}:get-current-date"])

    result = await module.build_restrictable_resources(db_session, agent_id=agent.id)
    group = next(g for g in result["groups"] if g["key"].startswith("mcp:"))
    item_id = group["items"][0]["id"]

    assert build_model_tool_name(f"{server_name}:get-current-date") in to_runtime_tool_names(item_id), (
        "智能体级 MCP 候选的 id 必须与运行时注册名对得上，否则拦截永远不命中"
    )


# ---------------------------------------------------------------------------
# 个人技能必须标记为「不可限定」
#
# 权限侧三处过滤（清单 excluded_skill_ids、显式挂载 drop_forbidden_skill_items、
# 上下文清洗 sanitize_forbidden_skill_context）**全部**把个人技能排除在外，理由是个人技能
# 与公共技能可能同名，只按 id 判定会连坐用户自建的那一份。
#
# 所以弹框里允许勾选个人技能是个空转：能保存、库里也真写了，运行时任何一处都不读，
# 而且界面不给任何反馈——管理员只会看到"禁了但没生效"。改为置灰 + 说明原因。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_personal_skills_are_marked_not_restrictable(db_session, monkeypatch):
    """个人技能仍列出（管理员要看得到），但必须带不可限定标记与原因。"""
    from app.services import agent_scoped_restrictions as module
    from app.services.ai import skill_resolver

    monkeypatch.setattr(
        skill_resolver,
        "list_global_skill_metas",
        lambda: [{"id": "pdf", "name": "PDF 处理"}],
    )
    agent = await _make_agent_with_version(
        db_session, tools=[], skills=[], skills_custom=False
    )

    result = await module.build_restrictable_resources(
        db_session,
        agent_id=agent.id,
        personal_skills=[{"id": "my_skill", "name": "我的技能", "description": "私有"}],
    )

    personal = [group for group in result["groups"] if group["key"] == "personal_skills"]
    assert personal, "个人技能仍要列出来，管理员需要知道该用户有哪些个人技能"
    items = personal[0]["items"]
    assert [item["id"] for item in items] == ["my_skill"]
    assert all(item.get("not_restrictable") is True for item in items), (
        "个人技能必须标记为不可限定，否则界面仍可勾选并写库"
    )
    assert "公共技能" in items[0]["not_restrictable_reason"], "原因要说明只对公共技能生效"
    assert items[0]["description"] == "私有", "标记不应影响描述透传"


@pytest.mark.asyncio
async def test_public_skills_are_still_restrictable(db_session, monkeypatch):
    """标记不能误伤公共技能——它是本功能唯一真正生效的目标。"""
    from app.services import agent_scoped_restrictions as module
    from app.services.ai import skill_resolver

    monkeypatch.setattr(
        skill_resolver,
        "list_global_skill_metas",
        lambda: [{"id": "pdf", "name": "PDF 处理"}],
    )
    agent = await _make_agent_with_version(
        db_session, tools=[], skills=[], skills_custom=False
    )

    result = await module.build_restrictable_resources(
        db_session,
        agent_id=agent.id,
        personal_skills=[{"id": "my_skill", "name": "我的技能"}],
    )

    public = [group for group in result["groups"] if group["key"] == "public_skills"]
    assert public, "公共技能必须仍然列出"
    assert [item["id"] for item in public[0]["items"]] == ["pdf"]
    assert public[0]["items"][0].get("not_restrictable") is None, (
        "公共技能不得被标记为不可限定"
    )
