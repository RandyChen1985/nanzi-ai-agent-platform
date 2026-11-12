"""护栏自检：测试绝不允许清空真实用户的表。

背景（真实事故，两次）：本项目测试连的是真实开发库，`db_session` 就是
`AsyncSessionLocal`，没有库级隔离。历史上两处丢了真实数据：

- `tests/api/portal/test_notifications.py` 里 `delete(UserNotificationConfig)`
  没有 WHERE —— 每跑一次测试就清空**所有用户**配好的钉钉/飞书/企微群；
- `tests/services/test_scheduler_service.py` 的 `cleanup_tasks` 里
  `delete(AgentScheduledTask)` 没有 WHERE —— 清空所有人的定时任务。

两者都立刻 commit，删掉就回不来。修掉这两处只能治当时，`conftest.py` 里的
`_forbid_unscoped_delete` 才是护栏 —— 本文件确保护栏一直有效：

1. 无 WHERE 的 `delete(Model)` 直接抛错；
2. 无 WHERE 的 `DELETE FROM ...` 原始 SQL 直接抛错；
3. `TRUNCATE` 直接抛错；
4. 带 WHERE 的删除与单条 `session.delete(obj)` 必须照常可用（护栏不能把
   正常测试一起拦死，否则下一个人会直接删掉护栏）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete, text

from app.models.user_notification_config import UserNotificationConfig
from app.models.user import User


class _FakeExecuteState:
    def __init__(self, statement):  # noqa: ANN001
        self.statement = statement


@pytest.mark.no_infrastructure
def test_unscoped_orm_delete_is_blocked():
    """护栏本身：无 WHERE 的 delete(Model) 必须在执行前就炸。"""
    from tests.conftest import _forbid_unscoped_delete

    with pytest.raises(RuntimeError) as exc:
        _forbid_unscoped_delete(_FakeExecuteState(delete(UserNotificationConfig)))

    assert "不带 WHERE" in str(exc.value)
    assert "user_notification_configs" in str(exc.value)


@pytest.mark.no_infrastructure
def test_unscoped_raw_delete_is_blocked():
    from tests.conftest import _forbid_unscoped_delete

    with pytest.raises(RuntimeError):
        _forbid_unscoped_delete(_FakeExecuteState(text("DELETE FROM user_notification_configs")))


@pytest.mark.no_infrastructure
def test_truncate_is_blocked():
    from tests.conftest import _forbid_unscoped_delete

    with pytest.raises(RuntimeError):
        _forbid_unscoped_delete(_FakeExecuteState(text("TRUNCATE TABLE user_notification_configs")))


@pytest.mark.no_infrastructure
def test_scoped_delete_is_allowed():
    """带 WHERE 的删除必须放行，否则护栏会被当成障碍物拆掉。"""
    from tests.conftest import _forbid_unscoped_delete

    _forbid_unscoped_delete(
        _FakeExecuteState(
            delete(UserNotificationConfig).where(UserNotificationConfig.user_id == 1)
        )
    )
    _forbid_unscoped_delete(
        _FakeExecuteState(
            text("DELETE FROM user_notification_configs WHERE user_id = :uid")
        )
    )


@pytest.mark.no_infrastructure
def test_select_is_allowed():
    from tests.conftest import _forbid_unscoped_delete

    _forbid_unscoped_delete(_FakeExecuteState(text("SELECT * FROM ai_agent_users")))
    _forbid_unscoped_delete(_FakeExecuteState(delete(User).where(User.id == 1)))


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_guard_is_wired_into_real_session():
    """确认事件真的挂在 Session 上 —— 只测函数本身会漏掉「没接线」。"""
    from app.core.orm import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        with pytest.raises(RuntimeError):
            await session.execute(delete(UserNotificationConfig))
