"""启动旁路步骤超时兜底（startup_guard）的回归测试。

背景：``app/main.py`` lifespan 里的旁路步骤原先只有 ``try/except``——兜得住抛出的
异常，兜不住「卡住」（库不可达、表被元数据锁挡住时协程永远停在 await，日志停在上一条、
端口不监听、没有任何报错）。这里锁定 ``run_startup_step`` 的三条契约：

1. 步骤正常时原样返回结果；
2. 超时 / 异常都降级为 ``None``，**不向调用方抛异常**（启动流程继续）；
3. 外层取消（应用关停）不能被当成「步骤失败」吞掉。
"""

import asyncio
import logging

import pytest

from app.core.startup_guard import run_startup_step

pytestmark = pytest.mark.no_infrastructure

_LOGGER_NAME = "app.core.startup_guard"


async def test_returns_step_value():
    async def step() -> str:
        return "nanzi"

    assert await run_startup_step("正常步骤", step, timeout=1) == "nanzi"


async def test_timeout_returns_none_and_cancels_step(caplog):
    cancelled = asyncio.Event()

    async def step() -> str:
        try:
            await asyncio.sleep(30)
            return "never-reached"
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with caplog.at_level(logging.ERROR, logger=_LOGGER_NAME):
        result = await run_startup_step("卡住的步骤", step, timeout=0.05)

    assert result is None
    # 超时必须真的把被挂住的协程取消掉，否则它会一直占着一个 pending 任务
    assert cancelled.is_set()
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("卡住的步骤" in r.getMessage() for r in errors)


async def test_exception_degrades_to_none(caplog):
    async def step() -> None:
        raise RuntimeError("主库连接被拒绝")

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = await run_startup_step("失败步骤", step, timeout=1)

    assert result is None
    assert any("主库连接被拒绝" in r.getMessage() for r in caplog.records)


async def test_slow_step_is_reported_once(caplog):
    async def step() -> str:
        await asyncio.sleep(0.05)
        return "ok"

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        assert await run_startup_step(
            "慢步骤", step, timeout=1, slow_seconds=0.001
        ) == "ok"

    assert any("耗时" in r.getMessage() and r.levelno == logging.INFO for r in caplog.records)


async def test_fast_step_stays_quiet(caplog):
    async def step() -> str:
        return "ok"

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        assert await run_startup_step("快步骤", step, timeout=1) == "ok"

    assert [r for r in caplog.records if r.name == _LOGGER_NAME] == []


async def test_outer_cancellation_propagates():
    """应用关停 / 部署滚动触发的取消必须冒泡，不能被降级逻辑吞成 None。"""

    async def step() -> None:
        await asyncio.sleep(30)

    task = asyncio.create_task(run_startup_step("被取消的步骤", step, timeout=30))
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
