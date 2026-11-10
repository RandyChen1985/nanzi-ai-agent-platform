"""凭据解析器的集成测试。

直接打真实数据库：这里要验证的正是「SQL 写对了没有」——
绑定可见性、grant 的部门匹配、过期窗口，都是 SQL 条件，mock 掉就什么都没测到。

表结构由迁移脚本（``db-prod/V209-add-credential-center.sql``）负责，测试**不**建表、
**不**删表：测试连的是业务库，DDL 会污染开发库；表不在时应当直接报 1146，提醒你先跑迁移。
"""

from __future__ import annotations

import uuid
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
from app.services.secrets.policy import (
    DENY_BINDING,
    DENY_GRANT,
    DENY_HOST,
    DENY_SCENARIO,
    DENY_SCRIPT_POLICY,
    SCENE_API_TOOL,
    SCENE_MCP,
    SCENE_SCRIPT_ENV,
)
from app.services.secrets.resolver import SecretResolver
from app.utils.encryption import get_api_key_manager

SECRET_TABLES = [
    SecretUsageLog.__table__,
    SecretBinding.__table__,
    SecretGrant.__table__,
    SecretUserPref.__table__,
    UserSecret.__table__,
]

USER_ID = "pytest-7"
OTHER_USER = "pytest-8"
DEPT_CODE = "DEPT-DATA"
PLAINTEXT = "P@ssw0rd!2024"


@pytest.fixture
async def clean_tables():
    """只清理本用例建的凭据，避免用例之间互相看到对方的绑定。

    原来这里是 ``DELETE FROM sys_secrets`` 全表清空——这直接删掉过开发者自己
    建的凭据。测试没有权利假设整张表都是它建的。
    """
    from tests.secret_test_guard import (
        purge_created_after,
        snapshot_secret_ids,
        snapshot_user_prefs,
    )

    async with AsyncSessionLocal() as session:
        keep = await snapshot_secret_ids(session)
        keep_users = await snapshot_user_prefs(session)
    try:
        yield
    finally:
        async with AsyncSessionLocal() as session:
            await purge_created_after(
                session, keep_secret_ids=keep, keep_user_ids=keep_users
            )


@pytest.fixture
def resolver(monkeypatch):
    """平台策略固定为「脚本开、外部放行」，让单个用例只验证自己那一层。"""

    async def _flags():
        # 第三个是单次注入上限；留默认 3，用例只验证各自那一层
        return True, True, 3

    monkeypatch.setattr(SecretResolver, "_platform_flags", staticmethod(_flags))
    return SecretResolver()


def _encrypt(value: str) -> str:
    return get_api_key_manager().encrypt_api_key(value)


async def _create_secret(
    *,
    secret_key: str = "jira.prod.token",
    owner_scope: str = "user",
    owner_id: str = USER_ID,
    secret_type: str = "credential",
    scenarios: list[str] | None = None,
    hosts: list[str] | None = None,
    status: str = "active",
    expires_at: datetime | None = None,
    previous_expires_at: datetime | None = None,
    previous_value: str | None = None,
) -> str:
    secret_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        session.add(
            UserSecret(
                id=secret_id,
                owner_scope=owner_scope,
                owner_id=owner_id,
                secret_key=secret_key,
                name=secret_key,
                secret_type=secret_type,
                value_encrypted=_encrypt(PLAINTEXT),
                value_fingerprint="0123456789abcdef",
                allowed_scenarios=scenarios if scenarios is not None else [SCENE_MCP],
                allowed_hosts=hosts if hosts is not None else [],
                status=status,
                expires_at=expires_at,
                previous_value_encrypted=_encrypt(previous_value) if previous_value else None,
                previous_expires_at=previous_expires_at,
            )
        )
        await session.commit()
    return secret_id


async def _bind(
    secret_id: str,
    *,
    consumer_type: str = "mcp_server",
    consumer_id: str = "mcp-1",
    slot: str = "header:Authorization",
    bound_by: str | None = USER_ID,
) -> None:
    async with AsyncSessionLocal() as session:
        session.add(
            SecretBinding(
                id=str(uuid.uuid4()),
                secret_id=secret_id,
                consumer_type=consumer_type,
                consumer_id=consumer_id,
                slot=slot,
                bound_by_user_id=bound_by,
            )
        )
        await session.commit()


async def _grant(secret_id: str, grantee_type: str, grantee_id: str, expires_at=None) -> None:
    async with AsyncSessionLocal() as session:
        session.add(
            SecretGrant(
                id=str(uuid.uuid4()),
                secret_id=secret_id,
                grantee_type=grantee_type,
                grantee_id=grantee_id,
                expires_at=expires_at,
            )
        )
        await session.commit()


async def _resolve(resolver, **overrides):
    kwargs = dict(
        user_id=USER_ID,
        scene=SCENE_MCP,
        consumer_type="mcp_server",
        consumer_id="mcp-1",
        slot="header:Authorization",
    )
    kwargs.update(overrides)
    return await resolver.resolve(**kwargs)


async def _usage_rows(secret_id: str) -> list[SecretUsageLog]:
    """只取这条凭据的流水。

    开发库是共享的：别处（别的用例、手动验证、真实聊天）写的流水都会留在表里，
    全表断言 ``len(rows) == 1`` 必然随机失败，且失败原因与当前断言的行为无关。
    """
    async with AsyncSessionLocal() as session:
        return list(
            (
                await session.execute(
                    select(SecretUsageLog).where(SecretUsageLog.secret_id == secret_id)
                )
            ).scalars().all()
        )


class TestResolveHappyPath:
    async def test_returns_sensitive_value(self, clean_tables, resolver):
        secret_id = await _create_secret()
        await _bind(secret_id)

        result = await _resolve(resolver)

        assert result.ok is True
        assert result.secret_key == "jira.prod.token"
        assert result.value is not None
        assert result.value.reveal() == PLAINTEXT
        # 值只能通过 reveal 拿到，repr 不能泄露
        assert PLAINTEXT not in repr(result.value)

    async def test_writes_success_usage_log(self, clean_tables, resolver):
        secret_id = await _create_secret()
        await _bind(secret_id)

        await _resolve(resolver)

        rows = await _usage_rows(secret_id)
        assert len(rows) == 1
        assert rows[0].result == "success"
        assert rows[0].secret_key_snapshot == "jira.prod.token"
        assert rows[0].user_id == USER_ID
        assert rows[0].scene == SCENE_MCP


class TestBindingGate:
    async def test_no_binding_is_denied(self, clean_tables, resolver):
        await _create_secret()

        result = await _resolve(resolver)

        assert result.denied is True
        assert result.denial == DENY_BINDING

    async def test_binding_by_other_user_is_denied(self, clean_tables, resolver):
        # 个人绑定只对绑定者生效：A 绑到共享工具上，B 用不了。
        secret_id = await _create_secret()
        await _bind(secret_id, bound_by=OTHER_USER)

        result = await _resolve(resolver)

        assert result.denial == DENY_BINDING

    async def test_global_binding_works_for_everyone(self, clean_tables, resolver):
        secret_id = await _create_secret(owner_scope="system", owner_id="")
        await _bind(secret_id, bound_by=None)
        await _grant(secret_id, "user", USER_ID)

        result = await _resolve(resolver)

        assert result.ok is True

    async def test_personal_binding_overrides_global(self, clean_tables, resolver):
        """同一槽位既有全局绑定又有个人绑定时，以个人为准。"""
        global_secret = await _create_secret(secret_key="global.token", owner_scope="system", owner_id="")
        personal_secret = await _create_secret(secret_key="mine.token")
        await _bind(global_secret, bound_by=None)
        await _grant(global_secret, "user", USER_ID)
        await _bind(personal_secret, bound_by=USER_ID)

        result = await _resolve(resolver)

        assert result.secret_key == "mine.token"


class TestHostGate:
    """域名白名单。MCP 这条链路之前一条测试都没有。

    `apply_mcp_bindings` 用 `server.sse_url` 取目标域名——它是 MCP 服务唯一的
    URL 字段，`nullable=False`，所以正常情况下一定有值。这里锁住两侧：
    白名单命中就放行，不命中就拒绝，且「无目标域名 + 有白名单」必须拒绝
    （否则「先注入、再拼 URL」的绕过路径就成立了）。
    """

    def test_exact_host_match_allowed(self):
        from app.services.secrets.policy import host_allowed

        assert host_allowed("mcp.example.com", ["mcp.example.com"]) is True

    def test_other_host_rejected(self):
        from app.services.secrets.policy import host_allowed

        assert host_allowed("evil.example.com", ["mcp.example.com"]) is False

    def test_wildcard_subdomain_does_not_match_the_apex(self):
        """`*.example.com` 不该匹配 `example.com` 本身。"""
        from app.services.secrets.policy import host_allowed

        assert host_allowed("a.example.com", ["*.example.com"]) is True
        assert host_allowed("example.com", ["*.example.com"]) is False

    def test_empty_list_only_allows_internal_targets(self):
        from app.services.secrets.policy import host_allowed

        assert host_allowed(None, []) is True
        assert host_allowed("127.0.0.1", []) is True
        assert host_allowed("mcp.example.com", []) is False

    def test_missing_target_is_rejected_when_allowlist_declared(self):
        """有白名单却没有目标域名 → 拒绝，别当成「平台内部调用」放行。"""
        from app.services.secrets.policy import host_allowed

        assert host_allowed(None, ["mcp.example.com"]) is False

    async def test_mcp_scene_hits_host_gate(self, clean_tables, resolver):
        """端到端：白名单不匹配时 MCP 场景必须 DENY_HOST 而不是放行。"""
        from app.services.secrets import policy

        secret_id = await _create_secret(
            owner_scope="user",
            owner_id="pytest-alice",
            scenarios=[policy.SCENE_MCP],
            hosts=["mcp.example.com"],
        )
        await _bind(secret_id, bound_by="pytest-alice")

        denied = await _resolve(
            resolver,
            user_id="pytest-alice",
            scene=policy.SCENE_MCP,
            target_host="evil.example.com",
        )
        assert denied.denial == policy.DENY_HOST

        allowed = await _resolve(
            resolver,
            user_id="pytest-alice",
            scene=policy.SCENE_MCP,
            target_host="mcp.example.com",
        )
        assert allowed.ok is True


class TestGrantGate:
    async def test_system_secret_without_grant_is_denied(self, clean_tables, resolver):
        secret_id = await _create_secret(owner_scope="system", owner_id="")
        await _bind(secret_id, bound_by=None)

        result = await _resolve(resolver)

        assert result.denial == DENY_GRANT

    async def test_dept_grant_matches(self, clean_tables, resolver):
        secret_id = await _create_secret(owner_scope="system", owner_id="")
        await _bind(secret_id, bound_by=None)
        await _grant(secret_id, "dept", DEPT_CODE)

        result = await _resolve(resolver, dept_code=DEPT_CODE)

        assert result.ok is True

    async def test_dept_grant_ignored_without_dept_match(self, clean_tables, resolver):
        secret_id = await _create_secret(owner_scope="system", owner_id="")
        await _bind(secret_id, bound_by=None)
        await _grant(secret_id, "dept", DEPT_CODE)

        result = await _resolve(resolver, dept_code="DEPT-OTHER")

        assert result.denial == DENY_GRANT

    async def test_personal_grant_matches(self, clean_tables, resolver):
        """授权给**具体某个人**：只有她能取到，别人不行。

        「看得见」和「取得到」是两条独立路径（`list_visible_secrets` 走 SQL，
        `_has_grant` 走 Python），之前 `all` 那条只在可见性侧漏了，表现就是
        「全员可见但选不到」。反过来漏 `user` 就是「看得见但用不了」。
        """
        secret_id = await _create_secret(owner_scope="system", owner_id="")
        await _bind(secret_id, bound_by=None)
        await _grant(secret_id, "user", "pytest-alice")

        granted = await _resolve(resolver, user_id="pytest-alice")
        assert granted.ok is True

        other = await _resolve(resolver, user_id="pytest-bob")
        assert other.denial == DENY_GRANT

    async def test_personal_grant_is_case_sensitive(self, clean_tables, resolver):
        """授权对象按 id 精确比对，不能因为大小写/空格就放行。"""
        secret_id = await _create_secret(owner_scope="system", owner_id="")
        await _bind(secret_id, bound_by=None)
        await _grant(secret_id, "user", "pytest-alice")

        result = await _resolve(resolver, user_id="pytest-alice ")

        assert result.denial == DENY_GRANT

    async def test_all_grant_covers_everyone(self, clean_tables, resolver):
        """``all`` 不比对 grantee_id：它不指向任何实体，只表示不限范围。"""
        secret_id = await _create_secret(owner_scope="system", owner_id="")
        await _bind(secret_id, bound_by=None)
        await _grant(secret_id, "all", "")

        # 三个互不相干的用户都要能取到，包括没有部门的
        for uid, dept in (("pytest-alice", "DEPT-A"), ("pytest-bob", "DEPT-B"), ("pytest-carol", None)):
            result = await _resolve(resolver, user_id=uid, dept_code=dept)
            assert result.ok is True, uid

    async def test_all_grant_does_not_bypass_other_gates(self, clean_tables, resolver):
        """全体授权只解决「给谁」，不解决「能不能用」——场景/状态照旧拦。"""
        secret_id = await _create_secret(owner_scope="system", owner_id="", scenarios=[SCENE_API_TOOL])
        await _bind(secret_id, bound_by=None)
        await _grant(secret_id, "all", "")

        result = await _resolve(resolver)  # 默认按 mcp 场景取

        assert result.denial == DENY_SCENARIO

    async def test_expired_grant_does_not_count(self, clean_tables, resolver):
        secret_id = await _create_secret(owner_scope="system", owner_id="")
        await _bind(secret_id, bound_by=None)
        await _grant(secret_id, "user", USER_ID, expires_at=datetime.utcnow() - timedelta(days=1))

        result = await _resolve(resolver)

        assert result.denial == DENY_GRANT


class TestPolicyGate:
    async def test_scene_not_enabled(self, clean_tables, resolver):
        secret_id = await _create_secret(scenarios=[SCENE_MCP])
        await _bind(secret_id)

        result = await _resolve(resolver, scene=SCENE_API_TOOL)

        assert result.denial == DENY_SCENARIO

    async def test_script_env_requires_user_preference(self, clean_tables, resolver):
        secret_id = await _create_secret(scenarios=[SCENE_SCRIPT_ENV])
        await _bind(secret_id, consumer_type="script_tool", consumer_id="script-1", slot="env:DB_PASSWORD")

        result = await _resolve(
            resolver,
            scene=SCENE_SCRIPT_ENV,
            consumer_type="script_tool",
            consumer_id="script-1",
            slot="env:DB_PASSWORD",
        )

        assert result.denial == DENY_SCRIPT_POLICY

        async with AsyncSessionLocal() as session:
            session.add(SecretUserPref(user_id=USER_ID, allow_script_env=True))
            await session.commit()

        result = await _resolve(
            resolver,
            scene=SCENE_SCRIPT_ENV,
            consumer_type="script_tool",
            consumer_id="script-1",
            slot="env:DB_PASSWORD",
        )
        assert result.ok is True

    async def test_platform_switch_off_blocks_script_even_if_user_on(
        self, clean_tables, monkeypatch
    ):
        """两层开关取交集：用户开了、平台关了，仍然拒绝。"""
        secret_id = await _create_secret(scenarios=[SCENE_SCRIPT_ENV])
        await _bind(secret_id, consumer_type="script_tool", consumer_id="script-1", slot="env:DB_PASSWORD")
        async with AsyncSessionLocal() as session:
            session.add(SecretUserPref(user_id=USER_ID, allow_script_env=True))
            await session.commit()

        async def _flags():
            return False, True, 3

        monkeypatch.setattr(SecretResolver, "_platform_flags", staticmethod(_flags))
        resolver = SecretResolver()

        result = await _resolve(
            resolver,
            scene=SCENE_SCRIPT_ENV,
            consumer_type="script_tool",
            consumer_id="script-1",
            slot="env:DB_PASSWORD",
        )

        assert result.denial == DENY_SCRIPT_POLICY

    async def test_host_not_whitelisted(self, clean_tables, resolver):
        secret_id = await _create_secret(hosts=["api.github.com"])
        await _bind(secret_id)

        result = await _resolve(resolver, target_host="evil.example.com")

        assert result.denial == DENY_HOST

    async def test_host_whitelisted(self, clean_tables, resolver):
        secret_id = await _create_secret(hosts=["*.atlassian.net"])
        await _bind(secret_id)

        result = await _resolve(resolver, target_host="mcp.atlassian.net")

        assert result.ok is True


class TestRotationWindow:
    async def test_expired_value_falls_back_to_previous(self, clean_tables, resolver):
        """新值过期但旧值还在双写窗口 → 用旧值，且不中断调用。"""
        secret_id = await _create_secret(
            expires_at=datetime.utcnow() - timedelta(hours=1),
            previous_expires_at=datetime.utcnow() + timedelta(days=6),
            previous_value="old-value-123",
        )
        await _bind(secret_id)

        result = await _resolve(resolver)

        assert result.ok is True
        assert result.value.reveal() == "old-value-123"

    async def test_both_expired_is_denied(self, clean_tables, resolver):
        secret_id = await _create_secret(
            expires_at=datetime.utcnow() - timedelta(days=10),
            previous_expires_at=datetime.utcnow() - timedelta(days=3),
            previous_value="old-value-123",
        )
        await _bind(secret_id)

        result = await _resolve(resolver)

        assert result.denied is True


class TestFailureModes:
    async def test_disabled_secret(self, clean_tables, resolver):
        secret_id = await _create_secret(status="disabled")
        await _bind(secret_id)

        result = await _resolve(resolver)

        assert result.denied is True
        assert result.denial == "disabled"

    async def test_missing_identity_is_denied(self, clean_tables, resolver):
        result = await resolver.resolve(
            user_id="",
            scene=SCENE_MCP,
            consumer_type="mcp_server",
            consumer_id="mcp-1",
            slot="header:Authorization",
        )

        assert result.denial == DENY_BINDING

    async def test_broken_ciphertext_does_not_crash(self, clean_tables, resolver):
        """密文坏了按「无凭据」处理，不能让工具调用直接 500。"""
        secret_id = str(uuid.uuid4())
        async with AsyncSessionLocal() as session:
            session.add(
                UserSecret(
                    id=secret_id,
                    owner_scope="user",
                    owner_id=USER_ID,
                    secret_key="broken",
                    name="broken",
                    secret_type="credential",
                    value_encrypted="not-a-fernet-token",
                    allowed_scenarios=[SCENE_MCP],
                    allowed_hosts=[],
                    status="active",
                )
            )
            await session.commit()
        await _bind(secret_id)

        result = await _resolve(resolver)

        assert result.denied is True
