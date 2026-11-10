"""凭据使用记录：用户展示信息与按用户检索。

流水表只存 ``user_id``（字符串），界面上直接显示「用户 7」——排查「某用户说用不了」
时，运维还得先去用户表反查这个人是谁。这里补上用户名 / 姓名 / 邮箱，并支持按用户
检索（ID、用户名、姓名、邮箱都能搜）。

两条约束是有意为之：

* 按用户检索只能**缩小**范围，不能越过调用者自己的可见范围（普通用户仍然只看自己）；
* 搜不到人就是空结果，不能退化成「不过滤」——那会把别人的流水漏给查询者。
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import delete

from app.core.orm import AsyncSessionLocal
from app.models.secret import SecretUsageLog
from app.models.user import User
from app.services.secrets import center


@pytest.fixture
async def db():
    """连开发库，只清理本用例建的行（用户表与流水表都不能清表）。"""
    async with AsyncSessionLocal() as session:
        users: list[int] = []
        logs: list[str] = []
        try:
            yield session, users, logs
        finally:
            if logs:
                await session.execute(
                    delete(SecretUsageLog).where(SecretUsageLog.id.in_(logs))
                )
            if users:
                await session.execute(delete(User).where(User.id.in_(users)))
            await session.commit()


async def _make_user(session, users, *, tag: str | None = None) -> User:
    tag = tag or uuid.uuid4().hex[:8]
    user = User(
        user_name=f"pytest_usage_{tag}",
        real_name=f"测试用户{tag}",
        email=f"pytest_usage_{tag}@example.com",
        role="user",
        status=1,
        api_key_hash=f"hash_{tag}",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    users.append(user.id)
    return user


async def _make_log(session, logs, *, user_id: str, secret_key="jira.prod.token") -> SecretUsageLog:
    row = SecretUsageLog(
        id=str(uuid.uuid4()),
        secret_key_snapshot=secret_key,
        user_id=user_id,
        scene="mcp",
        result="success",
        created_at=datetime.utcnow(),
    )
    session.add(row)
    await session.commit()
    logs.append(row.id)
    return row


class TestSearchByUser:
    async def test_search_narrows_to_that_user_by_username(self, db):
        session, users, logs = db
        alice = await _make_user(session, users)
        bob = await _make_user(session, users)
        await _make_log(session, logs, user_id=str(alice.id))
        await _make_log(session, logs, user_id=str(bob.id))

        rows = await center.list_usage(
            session, user_id=None, user_keyword=alice.user_name
        )

        assert [r.user_id for r in rows] == [str(alice.id)]

    async def test_search_matches_email(self, db):
        session, users, logs = db
        alice = await _make_user(session, users)
        await _make_log(session, logs, user_id=str(alice.id))

        rows = await center.list_usage(session, user_id=None, user_keyword=alice.email)

        assert [r.user_id for r in rows] == [str(alice.id)]

    async def test_search_matches_real_name(self, db):
        session, users, logs = db
        alice = await _make_user(session, users)
        await _make_log(session, logs, user_id=str(alice.id))

        rows = await center.list_usage(
            session, user_id=None, user_keyword=alice.real_name
        )

        assert [r.user_id for r in rows] == [str(alice.id)]

    async def test_search_matches_numeric_id(self, db):
        session, users, logs = db
        alice = await _make_user(session, users)
        await _make_log(session, logs, user_id=str(alice.id))

        rows = await center.list_usage(session, user_id=None, user_keyword=str(alice.id))

        assert [r.user_id for r in rows] == [str(alice.id)]

    async def test_unknown_keyword_returns_nothing_not_everything(self, db):
        session, users, logs = db
        alice = await _make_user(session, users)
        await _make_log(session, logs, user_id=str(alice.id))

        rows = await center.list_usage(
            session, user_id=None, user_keyword="不存在的用户_zzz"
        )

        assert rows == [], "搜不到人必须是空结果，不能退化成不过滤"

    async def test_search_still_respects_caller_scope(self, db):
        """普通用户带关键词也只能看到自己的流水。"""
        session, users, logs = db
        alice = await _make_user(session, users)
        bob = await _make_user(session, users)
        await _make_log(session, logs, user_id=str(alice.id))
        await _make_log(session, logs, user_id=str(bob.id))

        rows = await center.list_usage(
            session, user_id=str(alice.id), user_keyword=bob.user_name
        )

        assert rows == []


class TestResolveUsageUsers:
    async def test_fills_name_and_email(self, db):
        session, users, logs = db
        alice = await _make_user(session, users)
        row = await _make_log(session, logs, user_id=str(alice.id))

        resolved = await center.resolve_usage_users(session, [row])

        info = resolved[str(alice.id)]
        assert info["user_name"] == alice.user_name
        assert info["real_name"] == alice.real_name
        assert info["email"] == alice.email

    async def test_ignores_unknown_and_non_numeric_ids(self, db):
        """用户已被删除、或历史里存了非数字标识时，不能因为补信息而报错。"""
        session, users, logs = db
        row = await _make_log(session, logs, user_id="999999999")
        ghost = await _make_log(session, logs, user_id="system-internal")

        resolved = await center.resolve_usage_users(session, [row, ghost])

        assert resolved == {}

    async def test_empty_rows_needs_no_query(self, db):
        session, _users, _logs = db

        assert await center.resolve_usage_users(session, []) == {}
