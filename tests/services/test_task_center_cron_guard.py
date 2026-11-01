"""任务中心 cron 防线：非法表达式必须在落库前被拒。

背景：`TaskCenterService.create_task` 是 Web API 与 AI 工具共用的入口，此前不做任何
cron 校验。AI 生成的 6 段（带秒）、`@daily`、或「日 + 周同时限定」会先成功入库
（status=1），随后 `_add_job_to_memory` 只打日志跳过 —— 用户与模型都收到「创建成功」，
任务却永不执行。本文件锁住「服务层先校验、再落库」这一顺序。

另：`TaskSchedulerService` 是单例且 `_scheduler` 是类属性，所有替换一律走
`monkeypatch`，否则会污染后续用例（曾把全局调度器直接替换成 MagicMock）。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import task_center_service as tcs
from app.services.ai import scheduler_service as sched_mod
from app.services.ai.tools import task_manager_tools as tools
from app.services.task_center_service import TaskCenterService


pytestmark = pytest.mark.no_infrastructure


async def _create(db, cron_expr: str):
    return await TaskCenterService.create_task(
        db=db,
        user_id=7,
        name="巡检",
        agent_id="main",
        cron_expr=cron_expr,
        prompt="检查负载",
    )


@pytest.mark.parametrize(
    "cron_expr",
    [
        "0 0 8 * * *",   # 6 段（带秒）：调度器无法解析
        "@daily",        # 宏：调度器无法解析
        "0 8 * * 8",     # 星期越界
        "0 8 1 * 1",     # 日 + 周同时限定，会落成 AND 语义几乎不执行
        "",              # 空表达式
    ],
)
@pytest.mark.asyncio
async def test_create_task_rejects_invalid_cron_before_persisting(cron_expr):
    db = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    with patch.object(tcs, "scheduler_service") as sched:
        sched.upsert_task = AsyncMock()
        with pytest.raises(ValueError):
            await _create(db, cron_expr)

    # 关键：校验发生在 add/commit 之前，库里不会留下「运行中但永不执行」的脏任务
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    sched.upsert_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_task_persists_and_returns_next_run_for_valid_cron():
    db = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    with patch.object(tcs, "scheduler_service") as sched:
        sched.upsert_task = AsyncMock()
        sched.get_next_run_time = MagicMock(return_value="2026-11-02 08:00:00")
        task = await _create(db, "0 8 * * 1-5")

    db.add.assert_called_once()
    db.commit.assert_awaited_once()
    sched.upsert_task.assert_awaited_once()
    assert task.cron_expr == "0 8 * * 1-5"
    assert task.next_run_at == "2026-11-02 08:00:00"


@pytest.mark.asyncio
async def test_update_task_rejects_invalid_cron_without_writing():
    db = MagicMock()
    db.execute = AsyncMock()

    with pytest.raises(ValueError):
        await TaskCenterService.update_task(db, 3, {"cron_expr": "0 0 8 * * *"})

    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_task_refuses_to_start_task_with_invalid_stored_cron():
    """历史脏任务直接启用时，调度器只会跳过；启动动作必须失败并说明原因。"""
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=lambda: "0 8 1 * 1")
    )

    with pytest.raises(ValueError, match="同时限定"):
        await TaskCenterService.update_task(db, 3, {"status": 1})


@pytest.mark.asyncio
async def test_update_task_allows_starting_task_with_valid_stored_cron():
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=lambda: "0 8 * * 1-5")
    )
    db.commit = AsyncMock()

    with patch.object(tcs, "scheduler_service") as sched:
        sched.upsert_task = AsyncMock()
        sched.get_next_run_time = MagicMock(return_value=None)
        with patch.object(TaskCenterService, "get_task", AsyncMock(return_value=None)):
            await TaskCenterService.update_task(db, 3, {"status": 1})

    db.commit.assert_awaited_once()


def test_ai_tool_documents_five_field_crontab_rules():
    """工具描述是模型唯一的格式说明，必须写明段数、星期基准与禁止项。"""
    doc = tools.create_recurring_task.func.__doc__ or ""
    assert "exactly 5 fields" in doc
    assert "0 or 7 = Sunday" in doc
    assert "Do NOT use 6 fields" in doc
    assert "Do NOT restrict both day-of-month and day-of-week" in doc


# ---------------------------------------------------------------------------
# 报表订阅装载：单条脏表达式不得中断其余订阅，也不得让 start() 冒泡失败
# ---------------------------------------------------------------------------


def _sub(sub_id: int, cron: str):
    return SimpleNamespace(id=sub_id, cron_expr=cron, status="active", timezone="Asia/Shanghai")


def _fake_scheduler(monkeypatch, svc, *, existing_job=None, jobs=None):
    scheduler = MagicMock()
    scheduler.get_job.return_value = existing_job
    scheduler.get_jobs.return_value = jobs or []
    monkeypatch.setattr(svc, "_scheduler", scheduler, raising=False)
    return scheduler


def _fake_session(subscriptions):
    session = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: subscriptions))
    )
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


@pytest.mark.asyncio
async def test_upsert_subscription_skips_invalid_cron_without_raising(monkeypatch):
    svc = sched_mod.TaskSchedulerService()
    scheduler = _fake_scheduler(monkeypatch, svc, existing_job=MagicMock())

    await svc.upsert_saved_report_subscription(_sub(1, "0 0 2 * * *"))

    scheduler.add_job.assert_not_called()
    # 残留旧 job 必须被摘掉，避免继续按错误排期执行
    scheduler.remove_job.assert_called_once()


@pytest.mark.asyncio
async def test_reload_subscriptions_continues_when_one_upsert_raises(monkeypatch):
    """reload 层的兜底：即使单条 upsert 抛出意外异常，其余订阅仍要被装载。"""
    svc = sched_mod.TaskSchedulerService()
    _fake_scheduler(monkeypatch, svc)

    ctx = _fake_session([_sub(1, "0 9 * * *"), _sub(2, "0 9 * * 1-5")])
    visited = []

    async def flaky_upsert(subscription):
        visited.append(subscription.id)
        if subscription.id == 1:
            raise RuntimeError("scheduler exploded")

    with patch.object(sched_mod, "AsyncSessionLocal", MagicMock(return_value=ctx)), patch.object(
        svc, "upsert_saved_report_subscription", side_effect=flaky_upsert
    ):
        await svc.reload_saved_report_subscriptions()

    assert visited == [1, 2]


@pytest.mark.asyncio
async def test_reload_subscriptions_loads_valid_and_skips_invalid_cron(monkeypatch):
    svc = sched_mod.TaskSchedulerService()
    scheduler = _fake_scheduler(monkeypatch, svc)

    ctx = _fake_session(
        [_sub(1, "0 9 * * *"), _sub(3, "0 0 2 * * *"), _sub(2, "0 9 * * 1-5")]
    )

    with patch.object(sched_mod, "AsyncSessionLocal", MagicMock(return_value=ctx)):
        await svc.reload_saved_report_subscriptions()

    scheduled = [call.kwargs.get("id") for call in scheduler.add_job.call_args_list]
    assert scheduled == ["saved_report_subscription_1", "saved_report_subscription_2"]
