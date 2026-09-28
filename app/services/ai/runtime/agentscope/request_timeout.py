"""LLM HTTP 请求的超时策略。

背景：OpenAI SDK 的默认超时是 ``Timeout(timeout=600, connect=5.0)``，即
connect 5s，但 read / write / pool 都是 600s。原先平台只设置了
``client_kwargs={"max_retries": 0}``，没有覆盖 timeout，于是完全依赖这个
默认值。

问题在于 read timeout 的语义是「相邻两次数据到达的最大间隔」（httpx 在每次
网络读取上单独包一层超时），因此一个建立连接后静默的服务端会让平台干等
600s —— 恰好等于 producer 看门狗上限，导致业务层重试
（``MODEL_STREAM_MAX_RETRIES``）永远来不及执行，看门狗成为唯一兜底。

这里把 read timeout 降为可配置的合理值，保证
``重试次数 × read_timeout`` 能落在看门狗预算内，让已有重试真正生效。
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

LLM_REQUEST_READ_TIMEOUT_KEY = "llm_request_read_timeout"

#: read timeout 是「chunk 间隔」而非请求总时长，因此它必须同时覆盖：
#: 首 token 等待（含 thinking 阶段）与生成过程中的偶发卡顿。
#: 默认 180s：两次尝试合计 360s，仍为默认 600s 的 producer 看门狗留出约 240s 余量。
DEFAULT_LLM_REQUEST_READ_TIMEOUT = 180.0
MIN_LLM_REQUEST_READ_TIMEOUT = 30.0
#: 上限 300s：再大则「2 × read_timeout」会撞上 600s 看门狗，重试等于无效。
MAX_LLM_REQUEST_READ_TIMEOUT = 300.0

#: 连接超时沿用 SDK 默认语义；建连慢通常意味着网络/DNS 故障，越早失败越好。
DEFAULT_LLM_REQUEST_CONNECT_TIMEOUT = 5.0
#: 请求体可能很大（长上下文），需要容忍上传，但不能无限等待。
DEFAULT_LLM_REQUEST_WRITE_TIMEOUT = 60.0
#: 连接池排队上限；默认池容量下几乎不会触发，仅作为兜底。
DEFAULT_LLM_REQUEST_POOL_TIMEOUT = 30.0


def parse_llm_request_read_timeout(
    value: Any,
    *,
    default: float = DEFAULT_LLM_REQUEST_READ_TIMEOUT,
) -> float:
    """把配置值解析为安全的 read timeout；非法值回退默认值。

    与 ``tool_timeout.parse_agent_max_toolcall_timeout`` 保持同一套语义：
    只接受有限正数，越界或无法解析一律回退 ``default``，不抛异常。
    """
    if isinstance(value, bool):
        return default
    if value is None or str(value).strip() == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if (
        not math.isfinite(number)
        or number < MIN_LLM_REQUEST_READ_TIMEOUT
        or number > MAX_LLM_REQUEST_READ_TIMEOUT
    ):
        return default
    return number


def validate_llm_request_read_timeout(value: Any) -> None:
    """校验管理端保存的 read timeout 配置。"""
    if isinstance(value, bool):
        raise ValueError(
            f"{LLM_REQUEST_READ_TIMEOUT_KEY} must be a number of seconds "
            f"between {MIN_LLM_REQUEST_READ_TIMEOUT:g} and "
            f"{MAX_LLM_REQUEST_READ_TIMEOUT:g}"
        )
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{LLM_REQUEST_READ_TIMEOUT_KEY} 必须为数字（秒），范围 "
            f"{MIN_LLM_REQUEST_READ_TIMEOUT:g}-{MAX_LLM_REQUEST_READ_TIMEOUT:g}"
        ) from exc
    if (
        not math.isfinite(number)
        or number < MIN_LLM_REQUEST_READ_TIMEOUT
        or number > MAX_LLM_REQUEST_READ_TIMEOUT
    ):
        raise ValueError(
            f"{LLM_REQUEST_READ_TIMEOUT_KEY} 必须在 "
            f"{MIN_LLM_REQUEST_READ_TIMEOUT:g}-{MAX_LLM_REQUEST_READ_TIMEOUT:g} 秒之间"
            f"（超过 {MAX_LLM_REQUEST_READ_TIMEOUT:g} 会导致模型重试来不及执行）"
        )


async def load_llm_request_read_timeout() -> float:
    """读取当前生效的 read timeout；读配置失败时回退默认值。

    注意：两个真实调用方各自有更合适的读取方式（``get_llm_async`` 走单键
    ``ConfigServiceProxy``，``get_configured_llm`` 复用已批量加载的
    ``get_all_from_db`` 结果），故当前没有生产调用者。保留它是为了给将来
    新增的 LLM 入口（如 embedding）提供一个「读配置失败也不阻断调用」的入口。
    """
    from app.services.config_service import ConfigService

    try:
        raw = await ConfigService.get(
            LLM_REQUEST_READ_TIMEOUT_KEY,
            default=str(int(DEFAULT_LLM_REQUEST_READ_TIMEOUT)),
        )
    except Exception as exc:  # 配置读取失败不应阻断模型调用
        logger.warning(
            "[LLMTimeout] Failed to read %s, falling back to %ss: %s",
            LLM_REQUEST_READ_TIMEOUT_KEY,
            DEFAULT_LLM_REQUEST_READ_TIMEOUT,
            exc,
        )
        raw = None
    return parse_llm_request_read_timeout(raw)


def build_llm_request_timeout(read_timeout: Any = None) -> Any:
    """构造 OpenAI/httpx 的 ``Timeout`` 对象（四项显式给全）。

    httpx 的 ``Timeout`` 要求「要么给一个默认值，要么显式给出全部四项」，
    否则会抛 ``ValueError``；这里四项都给全，避免依赖默认值语义。

    返回 ``None`` 表示无法构造（依赖缺失或版本不兼容），调用方应跳过设置，
    保持 SDK 原有行为而不是让模型调用失败。
    """
    resolved = parse_llm_request_read_timeout(read_timeout)
    timeout_cls = None
    try:
        from openai import Timeout as timeout_cls  # type: ignore[assignment]
    except ImportError:  # pragma: no cover - 老版本 openai 的兼容路径
        try:
            from httpx import Timeout as timeout_cls  # type: ignore[assignment]
        except ImportError:
            timeout_cls = None
    if timeout_cls is None:  # pragma: no cover - 依赖缺失时安全降级
        logger.warning(
            "[LLMTimeout] No Timeout implementation available; "
            "keeping SDK default request timeout"
        )
        return None
    try:
        return timeout_cls(
            connect=DEFAULT_LLM_REQUEST_CONNECT_TIMEOUT,
            read=resolved,
            write=DEFAULT_LLM_REQUEST_WRITE_TIMEOUT,
            pool=DEFAULT_LLM_REQUEST_POOL_TIMEOUT,
        )
    except Exception as exc:  # pragma: no cover - 版本不兼容时安全降级
        logger.warning(
            "[LLMTimeout] Failed to build request timeout (read=%ss): %s",
            resolved,
            exc,
        )
        return None


__all__ = [
    "DEFAULT_LLM_REQUEST_CONNECT_TIMEOUT",
    "DEFAULT_LLM_REQUEST_POOL_TIMEOUT",
    "DEFAULT_LLM_REQUEST_READ_TIMEOUT",
    "DEFAULT_LLM_REQUEST_WRITE_TIMEOUT",
    "LLM_REQUEST_READ_TIMEOUT_KEY",
    "MAX_LLM_REQUEST_READ_TIMEOUT",
    "MIN_LLM_REQUEST_READ_TIMEOUT",
    "build_llm_request_timeout",
    "load_llm_request_read_timeout",
    "parse_llm_request_read_timeout",
    "validate_llm_request_read_timeout",
]
