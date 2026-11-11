"""事件循环延迟（loop lag）的采集与口径。

为什么值得单测：这个指标的**全部价值**在于「它测得出来」。如果采样协程
自己被堵住却记 0，界面就会永远显示「延迟 0 ms」，比不显示更糟——看上去
一切正常，实际进程已经卡住了。

所以这里既测算术（P95、窗口裁剪），也测**真实阻塞**：把事件循环堵 120ms，
采样必须报出 ≥ 100ms。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.core.request_metrics import LoopLagMonitor


pytestmark = pytest.mark.no_infrastructure


def test_empty_window_reads_zero_not_none():
    """没有样本时给 0，页面不需要处理 null。"""
    monitor = LoopLagMonitor()

    assert monitor.snapshot() == {
        "loop_lag_p95_ms": 0.0,
        "loop_lag_max_ms": 0.0,
        "loop_lag_samples": 0,
    }


def test_p95_tracks_the_long_tail_when_it_is_not_rare():
    """卡顿是长尾问题：10% 的样本迟到时，P95 必须报出来。"""
    monitor = LoopLagMonitor()
    for _ in range(90):
        monitor.observe(1.0)
    for _ in range(10):
        monitor.observe(200.0)

    snapshot = monitor.snapshot()
    assert snapshot["loop_lag_max_ms"] == 200.0
    assert snapshot["loop_lag_p95_ms"] == 200.0


def test_a_single_hiccup_is_caught_by_max_not_by_p95():
    """偶发一次卡顿不该把 P95 拉爆，但 max 要抓得住——两个数并存的意义就在这里。"""
    monitor = LoopLagMonitor()
    for _ in range(99):
        monitor.observe(1.0)
    monitor.observe(200.0)

    snapshot = monitor.snapshot()
    assert snapshot["loop_lag_p95_ms"] == 1.0
    assert snapshot["loop_lag_max_ms"] == 200.0


def test_samples_older_than_the_window_are_dropped():
    """窗口是「最近 60 秒」：十分钟前的一次卡顿不该继续影响当前读数。"""
    monitor = LoopLagMonitor()
    monitor.observe(500.0, timestamp=time.time() - 600)
    monitor.observe(2.0)

    snapshot = monitor.snapshot()
    assert snapshot["loop_lag_samples"] == 1
    assert snapshot["loop_lag_p95_ms"] == 2.0


def test_missing_field_does_not_invent_a_value():
    """老节点心跳里没有这个字段时，读回 0 而不是报错（滚动升级期间会混跑）。"""
    from app.services.instance_registry import _to_float

    assert _to_float(None) == 0.0


def test_real_blockage_is_measured():
    """真堵一次：采样协程醒来迟到多少，就该报多少。

    这条是「指标会不会永远显示 0」的体检——把 asyncio.sleep 拿掉、
    或者改成上一拍的时间戳，这里都会红。
    """

    async def scenario() -> dict:
        monitor = LoopLagMonitor(interval_seconds=0.05)
        monitor.start()
        try:
            await asyncio.sleep(0.3)  # 先正常跑几拍
            idle = monitor.snapshot()
            assert idle["loop_lag_samples"] > 0, "采样协程没跑起来"

            # 同步阻塞：期间事件循环完全停摆，定时器必然迟到
            time.sleep(0.12)
            for _ in range(3):
                await asyncio.sleep(0.06)
            return monitor.snapshot()
        finally:
            await monitor.stop()

    snapshot = asyncio.run(scenario())
    assert snapshot["loop_lag_p95_ms"] >= 100.0, (
        f"阻塞 120ms 只报出 {snapshot['loop_lag_p95_ms']}ms——采样没在量真实延迟"
    )


def test_start_is_idempotent_and_stop_is_safe():
    """重复 start 不该起两个协程；没启动过就 stop 也不该炸。"""

    async def scenario() -> None:
        monitor = LoopLagMonitor(interval_seconds=0.05)
        monitor.start()
        first = monitor._task
        monitor.start()
        assert monitor._task is first, "重复 start 起了第二个采样协程"
        await monitor.stop()
        assert monitor._task is None

    asyncio.run(scenario())
    asyncio.run(LoopLagMonitor().stop())  # 从未启动
