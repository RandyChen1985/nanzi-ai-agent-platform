"""凭据中心写操作的单元测试。

重点不是「能存进去」，而是几条不该被绕过的不变量：
标识合法性、重复检测、轮换不覆盖、不能碰别人的凭据、未授权的系统级不出现。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.orm import AsyncSessionLocal
from app.models.secret import (
    SecretBinding,
    SecretGrant,
    SecretUsageLog,
    SecretUserPref,
    UserSecret,
)
from app.services.secrets import center
from app.services.secrets.policy import (
    SCENE_MCP,
    SCENE_SCRIPT_ENV,
    SCOPE_SYSTEM,
    TYPE_CREDENTIAL,
    TYPE_VARIABLE,
)

SECRET_TABLES = [
    SecretUsageLog.__table__,
    SecretBinding.__table__,
    SecretGrant.__table__,
    SecretUserPref.__table__,
    UserSecret.__table__,
]

USER_ID = "pytest-7"


@pytest.fixture
async def session():
    # 只清理本用例建的凭据，绝不清表：这四个测试连的是开发库，
    # 清表会把开发者自己的凭据一起删掉（已发生过一次）。
    from tests.secret_test_guard import (
        purge_created_after,
        snapshot_secret_ids,
        snapshot_user_prefs,
    )

    async with AsyncSessionLocal() as s:
        keep = await snapshot_secret_ids(s)
        keep_users = await snapshot_user_prefs(s)
        try:
            yield s
        finally:
            await purge_created_after(s, keep_secret_ids=keep, keep_user_ids=keep_users)


async def _make(session, **overrides) -> UserSecret:
    kwargs = dict(
        owner_id=USER_ID,
        secret_key="jira.prod.token",
        name="Jira 生产 Token",
        value="tok-abcdefgh-123456",
        allowed_scenarios=[SCENE_MCP],
    )
    kwargs.update(overrides)
    return await center.create_secret(session, **kwargs)


class TestCreateSecret:
    async def test_stores_ciphertext_and_fingerprint(self, session):
        row = await _make(session)

        assert row.value_encrypted and row.value_encrypted != "tok-abcdefgh-123456"
        assert row.value_fingerprint and len(row.value_fingerprint) == 16
        assert row.status == "active"
        assert row.secret_key == "jira.prod.token"

    async def test_credential_never_keeps_preview(self, session):
        # 预览就是泄露，哪怕只有 8 个字符。
        row = await _make(session, secret_type=TYPE_CREDENTIAL)
        assert row.value_preview is None

    async def test_variable_keeps_short_preview(self, session):
        row = await _make(
            session,
            secret_key="tenant.code",
            secret_type=TYPE_VARIABLE,
            value="tenant-001-abcdefg",
        )
        assert row.value_preview == "tenant-0"

    async def test_key_is_normalized_to_lowercase(self, session):
        row = await _make(session, secret_key="Jira.PROD.Token")
        assert row.secret_key == "jira.prod.token"

    @pytest.mark.parametrize(
        "bad_key",
        ["", "   ", "带中文", "has space", "a:b", "-leading", "x" * 200],
    )
    async def test_rejects_invalid_key(self, session, bad_key):
        with pytest.raises(center.SecretValidationError):
            await _make(session, secret_key=bad_key)

    async def test_rejects_duplicate_key(self, session):
        await _make(session)
        with pytest.raises(center.SecretValidationError, match="已存在"):
            await _make(session, name="重复的")

    async def test_allows_same_key_for_different_owner(self, session):
        await _make(session)
        other = await _make(session, owner_id="pytest-8")
        assert other.owner_id == "pytest-8"

    async def test_rejects_empty_value(self, session):
        with pytest.raises(center.SecretValidationError, match="不能为空"):
            await _make(session, value="")

    async def test_rejects_unknown_scenario(self, session):
        with pytest.raises(center.SecretValidationError, match="未知的注入场景"):
            await _make(session, allowed_scenarios=["mcp", "telepathy"])

    async def test_hosts_are_normalized(self, session):
        row = await _make(
            session,
            allowed_hosts=["HTTPS://MCP.Atlassian.NET/rest", "api.github.com:443"],
        )
        assert row.allowed_hosts == ["mcp.atlassian.net", "api.github.com"]

    async def test_system_secret_uses_empty_owner_id(self, session):
        row = await _make(session, owner_scope="system")
        assert row.owner_id == ""
        # 系统级也必须能重复检测，而不是因为 owner_id 为 NULL 而漏掉
        with pytest.raises(center.SecretValidationError, match="已存在"):
            await _make(session, owner_scope="system")

    async def test_invalidates_registry_snapshot(self, session, monkeypatch):
        """新值必须立刻进入落库扫描快照，否则 60 秒内仍会明文进审计。"""
        called = {"n": 0}

        def _spy():
            called["n"] += 1

        monkeypatch.setattr(center.secret_registry, "invalidate", _spy)
        await _make(session)
        assert called["n"] == 1


class TestRotateSecret:
    async def test_old_value_moves_to_previous(self, session):
        row = await _make(session, value="old-value-123")
        # 同一个 session 里 row 就是 rotated（同一 ORM 对象），必须在轮换前取旧值
        old_cipher = row.value_encrypted
        old_fingerprint = row.value_fingerprint

        rotated = await center.rotate_secret(
            session, secret_id=row.id, new_value="new-value-456", rotated_by=USER_ID
        )

        assert rotated.previous_value_encrypted == old_cipher
        assert rotated.previous_expires_at > datetime.now()
        assert rotated.last_rotated_at is not None
        assert rotated.value_fingerprint != old_fingerprint

    async def test_refuses_to_rotate_someone_elses_secret(self, session):
        """轮换是**写值**：知道 id 就能把别人的凭据换成自己的 token，
        之后对方的工具会带着攻击者的身份去调外部服务。"""
        row = await _make(session)

        with pytest.raises(center.SecretPermissionError):
            await center.rotate_secret(
                session, secret_id=row.id, new_value="attacker", rotated_by="someone-else"
            )

    async def test_refuses_system_secret_unless_admin(self, session):
        row = await _make(session, owner_scope=SCOPE_SYSTEM, owner_id="")

        try:
            with pytest.raises(center.SecretPermissionError):
                await center.rotate_secret(
                    session, secret_id=row.id, new_value="x", rotated_by=USER_ID
                )

            # 管理员可以：系统凭据本来就要有人能换
            rotated = await center.rotate_secret(
                session,
                secret_id=row.id,
                new_value="x",
                rotated_by="admin-1",
                actor_is_admin=True,
            )
            assert rotated.last_rotated_at is not None
        finally:
            # 只删本用例自己建的那一条：这是一条系统级凭据，留着会让后面模块
            # 的可见性/授权用例看到一条「没有 grant 的系统凭据」而误判。
            # 不碰表结构、不清空表。
            from sqlalchemy import delete as sa_delete

            await session.execute(sa_delete(UserSecret).where(UserSecret.id == row.id))
            await session.commit()

    async def test_new_value_is_actually_decryptable(self, session):
        from app.utils.encryption import get_api_key_manager

        row = await _make(session, value="old-value-123")
        rotated = await center.rotate_secret(
            session, secret_id=row.id, new_value="new-value-456", rotated_by=USER_ID
        )

        manager = get_api_key_manager()
        assert manager.decrypt_api_key(rotated.value_encrypted) == "new-value-456"
        assert manager.decrypt_api_key(rotated.previous_value_encrypted) == "old-value-123"

    async def test_overlap_window_is_configurable(self, session):
        row = await _make(session)
        rotated = await center.rotate_secret(
            session,
            secret_id=row.id,
            new_value="new-value-456",
            overlap_days=1,
            rotated_by=USER_ID,
        )
        remaining = rotated.previous_expires_at - datetime.now()
        assert timedelta(hours=23) < remaining < timedelta(days=1, minutes=1)

    async def test_rejects_empty_new_value(self, session):
        row = await _make(session)
        with pytest.raises(center.SecretValidationError):
            await center.rotate_secret(session, secret_id=row.id, new_value="")

    async def test_rejects_unknown_secret(self, session):
        with pytest.raises(center.SecretValidationError, match="不存在"):
            await center.rotate_secret(session, secret_id="nope", new_value="x")


class TestBindSecret:
    async def test_binds_own_secret(self, session):
        row = await _make(session)

        binding = await center.bind_secret(
            session,
            secret_id=row.id,
            consumer_type="mcp_server",
            consumer_id="mcp-1",
            slot="header:Authorization",
            bound_by_user_id=USER_ID,
        )

        assert binding.secret_id == row.id
        assert binding.bound_by_user_id == USER_ID

    async def test_cannot_bind_someone_elses_secret(self, session):
        row = await _make(session, owner_id="pytest-8")

        with pytest.raises(center.SecretValidationError, match="他人"):
            await center.bind_secret(
                session,
                secret_id=row.id,
                consumer_type="mcp_server",
                consumer_id="mcp-1",
                slot="header:Authorization",
                bound_by_user_id=USER_ID,
            )

    async def test_rebinding_same_slot_replaces(self, session):
        first = await _make(session, secret_key="a.token")
        second = await _make(session, secret_key="b.token")

        await center.bind_secret(
            session,
            secret_id=first.id,
            consumer_type="mcp_server",
            consumer_id="mcp-1",
            slot="header:Authorization",
            bound_by_user_id=USER_ID,
        )
        await center.bind_secret(
            session,
            secret_id=second.id,
            consumer_type="mcp_server",
            consumer_id="mcp-1",
            slot="header:Authorization",
            bound_by_user_id=USER_ID,
        )

        bindings = (
            (await session.execute(select(SecretBinding))).scalars().all()
        )
        assert len(bindings) == 1
        assert bindings[0].secret_id == second.id

    @pytest.mark.parametrize("bad_slot", ["", "Authorization", "header:"])
    async def test_rejects_malformed_slot(self, session, bad_slot):
        row = await _make(session)
        with pytest.raises(center.SecretValidationError):
            await center.bind_secret(
                session,
                secret_id=row.id,
                consumer_type="mcp_server",
                consumer_id="mcp-1",
                slot=bad_slot,
                bound_by_user_id=USER_ID,
            )

    async def test_unbind_returns_count(self, session):
        row = await _make(session)
        await center.bind_secret(
            session,
            secret_id=row.id,
            consumer_type="mcp_server",
            consumer_id="mcp-1",
            slot="header:Authorization",
            bound_by_user_id=USER_ID,
        )

        removed = await center.unbind_slot(
            session,
            consumer_type="mcp_server",
            consumer_id="mcp-1",
            slot="header:Authorization",
            bound_by_user_id=USER_ID,
        )
        assert removed == 1

        again = await center.unbind_slot(
            session,
            consumer_type="mcp_server",
            consumer_id="mcp-1",
            slot="header:Authorization",
            bound_by_user_id=USER_ID,
        )
        assert again == 0


class TestGrants:
    async def test_grant_system_secret(self, session):
        row = await _make(session, owner_scope="system")

        grant = await center.grant_secret(
            session, secret_id=row.id, grantee_type="dept", grantee_id="DEPT-DATA"
        )

        assert grant.grantee_id == "DEPT-DATA"

    async def test_cannot_grant_personal_secret(self, session):
        row = await _make(session)
        with pytest.raises(center.SecretValidationError, match="系统级"):
            await center.grant_secret(
                session, secret_id=row.id, grantee_type="user", grantee_id="8"
            )

    async def test_rejects_duplicate_grant(self, session):
        row = await _make(session, owner_scope="system")
        await center.grant_secret(
            session, secret_id=row.id, grantee_type="user", grantee_id="8"
        )
        with pytest.raises(center.SecretValidationError, match="已存在"):
            await center.grant_secret(
                session, secret_id=row.id, grantee_type="user", grantee_id="8"
            )

    async def test_revoke(self, session):
        row = await _make(session, owner_scope="system")
        await center.grant_secret(
            session, secret_id=row.id, grantee_type="user", grantee_id="8"
        )

        assert (
            await center.revoke_grant(
                session, secret_id=row.id, grantee_type="user", grantee_id="8"
            )
            == 1
        )
        assert (
            await center.revoke_grant(
                session, secret_id=row.id, grantee_type="user", grantee_id="8"
            )
            == 0
        )


class TestVisibility:
    async def test_sees_own_secrets_only(self, session):
        await _make(session, secret_key="mine.token")
        await _make(session, secret_key="theirs.token", owner_id="pytest-8")

        rows = await center.list_visible_secrets(session, user_id=USER_ID)

        assert [r.secret_key for r in rows] == ["mine.token"]

    async def test_sees_granted_system_secrets(self, session):
        granted = await _make(session, owner_scope="system", secret_key="corp.wecom.secret")
        await _make(session, owner_scope="system", secret_key="sap.prod.api_key")
        await center.grant_secret(
            session, secret_id=granted.id, grantee_type="user", grantee_id=USER_ID
        )

        rows = await center.list_visible_secrets(session, user_id=USER_ID)

        assert [r.secret_key for r in rows] == ["corp.wecom.secret"]

    async def test_dept_grant_extends_visibility(self, session):
        row = await _make(session, owner_scope="system", secret_key="bi.gateway.url")
        await center.grant_secret(
            session, secret_id=row.id, grantee_type="dept", grantee_id="DEPT-DATA"
        )

        visible = await center.list_visible_secrets(
            session, user_id=USER_ID, dept_code="DEPT-DATA"
        )
        hidden = await center.list_visible_secrets(
            session, user_id=USER_ID, dept_code="DEPT-OTHER"
        )

        assert [r.secret_key for r in visible] == ["bi.gateway.url"]
        assert hidden == []

    async def test_expired_grant_hides_secret(self, session):
        row = await _make(session, owner_scope="system")
        await center.grant_secret(
            session,
            secret_id=row.id,
            grantee_type="user",
            grantee_id=USER_ID,
            expires_at=datetime.now() - timedelta(days=1),
        )

        assert await center.list_visible_secrets(session, user_id=USER_ID) == []


class TestDelete:
    async def test_deletes_own_secret(self, session):
        row = await _make(session)
        await center.delete_secret(session, secret_id=row.id, actor_id=USER_ID)
        assert await session.get(UserSecret, row.id) is None

    async def test_cannot_delete_someone_elses(self, session):
        row = await _make(session, owner_id="pytest-8")
        with pytest.raises(center.SecretValidationError, match="他人"):
            await center.delete_secret(session, secret_id=row.id, actor_id=USER_ID)

    async def test_delete_also_removes_grants_and_bindings(self, session):
        """表间没有外键，级联要手动做。

        漏了这两条 delete 不会报错——解析时是 join，孤儿记录匹配不上，功能
        照常。但「这条凭据授权给了谁」会变成查不清的垃圾，下次建同名凭据时
        也分不清哪些绑定是陈旧的。
        """
        from sqlalchemy import func, select

        from app.models.secret import SecretBinding, SecretGrant

        # 直接落 ORM 对象而不是走 grant_secret：那条路只允许系统级凭据，
        # 这里要验的是 delete 的清理行为，与授权入口的规则无关。
        row = await _make(session)
        session.add(
            SecretGrant(
                id="grant-1",
                secret_id=row.id,
                grantee_type="user",
                grantee_id="9",
                granted_by=USER_ID,
            )
        )
        session.add(
            SecretBinding(
                id="bind-1",
                secret_id=row.id,
                consumer_type="api_tool",
                consumer_id="tool-1",
                slot="header:Authorization",
                bound_by_user_id=USER_ID,
            )
        )
        await session.commit()

        await center.delete_secret(session, secret_id=row.id, actor_id=USER_ID)

        grants = (
            await session.execute(
                select(func.count()).select_from(SecretGrant).where(SecretGrant.secret_id == row.id)
            )
        ).scalar()
        bindings = (
            await session.execute(
                select(func.count()).select_from(SecretBinding).where(SecretBinding.secret_id == row.id)
            )
        ).scalar()
        assert grants == 0
        assert bindings == 0


class TestDetachConsumer:
    """删消费者时要清掉挂在它上面的绑定。

    表间没有外键（要能单独导出再导入），所以没人替我们做级联。留下的孤儿绑定
    不会报错——解析按 consumer_id 查，消费者没了就永远查不到——但「这条凭据被
    绑到哪了」的反查会显示一个已经不存在的目标。
    """

    async def test_removes_only_that_consumer(self, session):
        row = await _make(session)
        session.add_all([
            SecretBinding(
                id="b1", secret_id=row.id, consumer_type="api_tool",
                consumer_id="tool-1", slot="header:Authorization",
            ),
            SecretBinding(
                id="b2", secret_id=row.id, consumer_type="mcp_server",
                consumer_id="mcp-1", slot="header:Authorization",
            ),
        ])
        await session.commit()

        removed = await center.detach_consumer(
            session, consumer_type="api_tool", consumer_id="tool-1"
        )
        assert removed == 1

        left = (await session.execute(select(SecretBinding))).scalars().all()
        assert [b.consumer_id for b in left] == ["mcp-1"]

    async def test_missing_consumer_type_is_noop(self, session):
        assert await center.detach_consumer(session, consumer_type="", consumer_id="x") == 0
