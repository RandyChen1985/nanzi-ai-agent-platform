"""站内信保留期清理：每日审计维护任务按写死的 30 天清理 portal_notifications。"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import portal_notification_service as inbox_module
from app.services.ai import scheduler_service
from app.services.portal_notification_service import PortalNotificationService

pytestmark = pytest.mark.no_infrastructure


def _result(rowcount: int) -> SimpleNamespace:
    return SimpleNamespace(rowcount=rowcount)


@pytest.mark.asyncio
async def test_purge_older_than_deletes_in_batches_until_drained(monkeypatch):
    """一批删满 batch_size 说明可能还有残留，必须继续循环；不足一批即收尾。"""
    monkeypatch.setattr(
        inbox_module, "settings", SimpleNamespace(normalized_database_type="mysql")
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_result(5000), _result(12)])

    deleted = await PortalNotificationService.purge_older_than(db, 30)

    assert deleted == 5012
    assert db.execute.await_count == 2
    assert db.commit.await_count == 2
    statement = str(db.execute.await_args_list[0].args[0])
    assert "portal_notifications" in statement
    assert "created_at < :threshold" in statement
    assert "LIMIT :batch_size" in statement


@pytest.mark.asyncio
async def test_purge_uses_threshold_in_local_time_matching_created_at(monkeypatch):
    """created_at 由 datetime.now() 写入，阈值必须同源，不能用 UTC，否则会多删 8 小时。"""
    monkeypatch.setattr(
        inbox_module, "settings", SimpleNamespace(normalized_database_type="mysql")
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_result(0))
    before = datetime.now()

    await PortalNotificationService.purge_older_than(db, 30)

    params = db.execute.await_args.args[1]
    expected = before - timedelta(days=30)
    assert abs((params["threshold"] - expected).total_seconds()) < 5
    assert params["threshold"].tzinfo is None
    assert params["batch_size"] == 5000


@pytest.mark.asyncio
async def test_purge_supports_postgresql_without_delete_limit(monkeypatch):
    monkeypatch.setattr(
        inbox_module, "settings", SimpleNamespace(normalized_database_type="postgresql")
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_result(0))

    await PortalNotificationService.purge_older_than(db, 30)

    statement = str(db.execute.await_args.args[0])
    assert 'FROM "portal_notifications"' in statement
    assert "ctid IN" in statement


@pytest.mark.asyncio
async def test_purge_skips_when_retention_disabled():
    db = AsyncMock()
    assert await PortalNotificationService.purge_older_than(db, 0) == 0
    db.execute.assert_not_awaited()


def test_daily_audit_maintenance_job_reuses_30_day_constant():
    """契约：站内信清理挂在 system_audit_log_maintenance 这个 job 上，保留天数写死 30 天。"""
    assert scheduler_service.PORTAL_NOTIFICATION_RETENTION_DAYS == 30

    source = inspect.getsource(scheduler_service._system_audit_log_maintenance_job)
    assert "PortalNotificationService.purge_older_than(" in source
    assert "PORTAL_NOTIFICATION_RETENTION_DAYS" in source
    assert "portal_notifications" in source
    # 保留天数必须来自常量，不能再读审计日志的 retention 配置
    assert "audit_log_retention_days" in source  # 审计日志自身仍用配置
    purge_block = source.split("PortalNotificationService.purge_older_than(")[1].split(")")[0]
    assert "retention_days" not in purge_block


def test_job_id_and_schedule_unchanged():
    """站内信清理不新增 job，挂在每日 02:00 的既有任务里，避免多把锁与多轮调度。"""
    source = inspect.getsource(scheduler_service.TaskSchedulerService.start)
    assert 'id="system_audit_log_maintenance"' in source
    assert "CronTrigger(hour=2, minute=0, timezone=tz)" in source
