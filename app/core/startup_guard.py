"""启动阶段（lifespan）旁路步骤的超时兜底与耗时观测。

背景：``app/main.py`` 的 lifespan 里有一串「旁路」启动步骤——读平台时区配置、预热
提示词品牌名、初始化 LLM 链路追踪、把对外地址写进 MCP 白名单、后台预热记忆索引。
它们原先只有 ``try/except``。``except`` 只在**抛异常**时兜底；一旦底层卡在 TCP
建连、表元数据锁等待，或服务端连上了却不回包，协程就永远停在 ``await`` 上，表现是
「进程活着、端口没监听、日志停在上一条、没有任何报错」，排查时只能靠猜。

``run_startup_step`` 给每个旁路步骤加 deadline：

- 超时：记 ERROR 后放弃这一步，后续启动流程照常继续（与原先 ``except`` 的降级语义一致）；
- 异常：记 WARNING 后继续；
- 正常：耗时超过阈值时记一条 INFO，用于事后判断「这次启动慢在哪一步」。

两条边界必须记住：

1. ``asyncio.wait_for`` 只能中断**真正的 await 点**。同步阻塞调用（例如 APScheduler
   的 ``SQLAlchemyJobStore`` 走同步 pymysql、模块首次 import）会堵住事件循环本身，
   deadline 计时器根本轮不上——那类调用必须由驱动自带 connect/read 超时，见
   ``app/services/ai/scheduler_service.py`` 的 ``_build_jobstore_url``。
2. 超时会**取消**被包裹的协程，因此只适合可重入、幂等的旁路步骤；「失败就不该把
   服务起起来」的初始化（如 ``init_db``）不要包进来，否则会从「启动失败」变成
   「带着半初始化状态对外服务」。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# 默认 deadline（秒）：启动旁路步骤基本是「读一次配置 / 建一次客户端」，
# 正常在百毫秒内完成；10s 已经足够宽松，超过它基本可以判定为真的卡住了。
DEFAULT_STARTUP_STEP_TIMEOUT: float = 10.0

# 超过这个耗时才记 INFO：避免每次启动都为 0.00s 的步骤刷一屏日志。
DEFAULT_SLOW_STEP_SECONDS: float = 1.0


async def run_startup_step(
    label: str,
    step: Callable[[], Awaitable[Any]],
    *,
    timeout: float = DEFAULT_STARTUP_STEP_TIMEOUT,
    slow_seconds: float = DEFAULT_SLOW_STEP_SECONDS,
) -> Optional[Any]:
    """执行一个启动旁路步骤，超时/失败都只降级、不冒泡。

    Args:
        label: 中文步骤名，会出现在日志里，用于定位「卡在哪一步」。
        step: 零参协程工厂。写成 ``lambda``/局部函数而不是协程对象，是为了让
            协程在 deadline 生效之后才创建，超时未执行到的步骤不会留下
            "coroutine was never awaited" 告警。
        timeout: deadline（秒）。
        slow_seconds: 超过该耗时就记一条 INFO。

    Returns:
        步骤返回值；超时或异常时返回 ``None``（调用方需按「该能力已降级」处理）。

    Note:
        外层取消（应用关停、部署滚动）抛的是 ``asyncio.CancelledError``，它继承
        ``BaseException``，不会被这里的 ``except Exception`` 吞掉，会正常向上传播。
    """
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(step(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.error(
            "⏱️ 启动步骤「%s」超过 %.1fs 未完成，已放弃并继续启动（该能力本次运行降级）。"
            "常见原因：主库/Redis 网络不可达、表被元数据锁挡住（迁移脚本未提交）、"
            "对端连上却不回包。",
            label,
            timeout,
        )
        return None
    except Exception as exc:  # noqa: BLE001 - 旁路步骤失败不能阻断启动
        logger.warning("启动步骤「%s」失败（已降级继续启动）: %s", label, exc)
        return None

    elapsed = time.monotonic() - started
    if elapsed >= slow_seconds:
        logger.info("⏱️ 启动步骤「%s」耗时 %.2fs", label, elapsed)
    return result
