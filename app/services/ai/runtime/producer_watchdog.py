"""producer 硬超时看门狗：整轮对话的总时长兜底。

**维度说明**（也是它最容易被误解的地方）：看门狗管的是「整轮」——从用户发出消息到
收尾，涵盖鉴权与能力准备、每一次工具调用、每一次模型调用。它不针对单个工具或单次
模型调用，所以 `agent_max_toolcall_timeout` 配得比它大并不会生效：工具还没到自己的
超时，整轮就先被取消并释放会话锁。该约束由迁移 SQL 的 description 说明。

**值语义**：`0`（或负值）表示关闭看门狗——整轮不再有总时长兜底，只依赖模型流空闲
守卫与工具各自的超时；其余取值必须在 60-3600 秒之间。上限不让再大，是因为看门狗的
职责是「保证会话最终一定能被释放」，太长就形同虚设。
"""
from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

PRODUCER_HARD_TIMEOUT_KEY = "agent_chat_producer_hard_timeout_seconds"
DEFAULT_PRODUCER_HARD_TIMEOUT_SECONDS = 600.0
MIN_PRODUCER_HARD_TIMEOUT_SECONDS = 60.0
MAX_PRODUCER_HARD_TIMEOUT_SECONDS = 3600.0

# 关闭看门狗的统一表示：传入的 0 / 负值都归一到这里，避免出现「-600 被当成小超时」。
PRODUCER_HARD_TIMEOUT_DISABLED = 0.0


def parse_producer_hard_timeout(
    value: Any,
    *,
    default: float = DEFAULT_PRODUCER_HARD_TIMEOUT_SECONDS,
) -> float:
    """解析看门狗配置值；无法解析时回退 ``default``。

    与 ``validate_producer_hard_timeout`` 的分工（重要）：
    - **校验**约束写入侧（管理端保存）：只接受 0 或 60-3600；
    - **解析**只负责读取侧的健壮性——脏数据不能阻断对话，但**合法数字一律原样使用**，
      不在这里二次判定范围。

    读取侧再判范围会造成两类问题：DB 里手工填的 30 被静默改成 600（「配置写着 30、
    实际跑 600」是最难排查的一类问题），以及等待小超时的集成测试根本无从下手。
    """
    if isinstance(value, bool):
        return default
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    try:
        number = float(text)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    if number <= 0:
        return PRODUCER_HARD_TIMEOUT_DISABLED
    return number


def validate_producer_hard_timeout(value: Any) -> None:
    """校验管理端保存的看门狗取值：0 表示关闭，其余必须在 60-3600 秒之间。"""
    if isinstance(value, bool):
        raise ValueError(_range_message())
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(_range_message()) from exc
    if not math.isfinite(number):
        raise ValueError(_range_message())
    if number == PRODUCER_HARD_TIMEOUT_DISABLED:
        return
    if number < MIN_PRODUCER_HARD_TIMEOUT_SECONDS or number > MAX_PRODUCER_HARD_TIMEOUT_SECONDS:
        raise ValueError(_range_message())


def _range_message() -> str:
    return (
        f"{PRODUCER_HARD_TIMEOUT_KEY} 必须为数字（秒）：0 表示关闭看门狗，"
        f"或 {MIN_PRODUCER_HARD_TIMEOUT_SECONDS:g}-{MAX_PRODUCER_HARD_TIMEOUT_SECONDS:g} 秒之间。"
        f"注意它约束的是**整轮**总时长——工具调用超时"
        f"（agent_max_toolcall_timeout）配得比它大不会生效"
    )


async def load_producer_hard_timeout() -> float:
    """读取当前生效的看门狗超时；读配置失败时回退默认值（不阻断对话）。"""
    from app.services.config_service import ConfigService

    try:
        raw = await ConfigService.get(
            PRODUCER_HARD_TIMEOUT_KEY,
            default=str(int(DEFAULT_PRODUCER_HARD_TIMEOUT_SECONDS)),
        )
    except Exception as exc:  # noqa: BLE001 - 配置读取失败不应阻断对话
        logger.warning(
            "[ProducerWatchdog] Failed to read %s, falling back to %ss: %s",
            PRODUCER_HARD_TIMEOUT_KEY,
            DEFAULT_PRODUCER_HARD_TIMEOUT_SECONDS,
            exc,
        )
        raw = None
    return parse_producer_hard_timeout(raw)
