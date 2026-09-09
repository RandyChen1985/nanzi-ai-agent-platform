"""AgentScope 工具调用的统一超时策略。"""

from __future__ import annotations

import math
import re
from dataclasses import replace
from typing import Any, Iterable, Sequence


AGENT_MAX_TOOLCALL_TIMEOUT_KEY = "agent_max_toolcall_timeout"
DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT = 180.0
MIN_AGENT_MAX_TOOLCALL_TIMEOUT = 1.0
MAX_AGENT_MAX_TOOLCALL_TIMEOUT = 3600.0
MAX_AGENT_VERSION_TOOLCALL_TIMEOUT = 86400.0
_INTEGER_PATTERN = re.compile(r"^[0-9]+$")


def _coerce_positive_timeout(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def parse_agent_max_toolcall_timeout(
    value: Any,
    *,
    default: float = DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT,
) -> float:
    """将系统配置解析为安全的整数秒；历史非法值回退默认值。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        candidate = float(value)
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        candidate = value
    elif isinstance(value, str) and _INTEGER_PATTERN.fullmatch(value):
        candidate = float(value)
    else:
        return default
    if not MIN_AGENT_MAX_TOOLCALL_TIMEOUT <= candidate <= MAX_AGENT_MAX_TOOLCALL_TIMEOUT:
        return default
    return candidate


def validate_agent_max_toolcall_timeout(value: Any) -> None:
    """校验管理端保存的工具调用超时配置。"""
    if isinstance(value, bool):
        raise ValueError(
            f"{AGENT_MAX_TOOLCALL_TIMEOUT_KEY} must be an integer between 1 and 3600 seconds"
        )
    if isinstance(value, int):
        candidate = value
    elif isinstance(value, str) and _INTEGER_PATTERN.fullmatch(value):
        candidate = int(value)
    else:
        raise ValueError(
            f"{AGENT_MAX_TOOLCALL_TIMEOUT_KEY} must be an integer between 1 and 3600 seconds"
        )
    if not 1 <= candidate <= 3600:
        raise ValueError(
            f"{AGENT_MAX_TOOLCALL_TIMEOUT_KEY} must be an integer between 1 and 3600 seconds"
        )


def parse_agent_version_toolcall_timeout(value: Any) -> float | None:
    """解析版本级工具调用超时；空值或非法值表示继承全局配置。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        candidate = float(value)
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        candidate = value
    elif isinstance(value, str) and _INTEGER_PATTERN.fullmatch(value):
        candidate = float(value)
    else:
        return None
    if not MIN_AGENT_MAX_TOOLCALL_TIMEOUT <= candidate <= MAX_AGENT_VERSION_TOOLCALL_TIMEOUT:
        return None
    return candidate


def effective_tool_timeout(
    global_timeout: Any,
    tool_timeout: Any = None,
    explicit_timeout: Any = None,
) -> float:
    """按传入配置的优先顺序选择超时秒数，不做最大值合并。"""
    for value in (global_timeout, tool_timeout, explicit_timeout):
        normalized = _coerce_positive_timeout(value)
        if normalized is not None:
            return normalized
    return DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT


def resolve_agent_toolcall_timeout(global_timeout: Any, agent_timeout: Any = None) -> float:
    """按版本级优先、全局次之选择当前智能体工具调用超时。"""
    normalized_agent = parse_agent_version_toolcall_timeout(agent_timeout)
    if normalized_agent is not None:
        return normalized_agent
    return parse_agent_max_toolcall_timeout(global_timeout)


async def load_agent_max_toolcall_timeout() -> float:
    """读取当前请求构建工具时使用的全局工具调用超时。"""
    from app.services.config_service import ConfigService

    try:
        raw = await ConfigService.get(
            AGENT_MAX_TOOLCALL_TIMEOUT_KEY,
            default=str(int(DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT)),
        )
    except Exception:
        raw = None
    return parse_agent_max_toolcall_timeout(raw)


def apply_agent_tool_timeout(
    specs: Iterable[Any],
    global_timeout: Any,
    *,
    agent_timeout: Any = None,
) -> list[Any]:
    """给一批运行时工具应用当前版本选定的配置超时快照。"""
    configured_timeout = resolve_agent_toolcall_timeout(global_timeout, agent_timeout)
    from dataclasses import is_dataclass

    result = []
    for spec in specs:
        if is_dataclass(spec) and not isinstance(spec, type):
            result.append(
                replace(
                    spec,
                    timeout_seconds=effective_tool_timeout(configured_timeout),
                )
            )
        else:
            try:
                setattr(spec, "timeout_seconds", effective_tool_timeout(configured_timeout))
            except Exception:
                pass
            result.append(spec)
    return result


async def apply_configured_agent_tool_timeout(
    specs: Sequence[Any] | Iterable[Any],
    *,
    agent_timeout: Any = None,
) -> list[Any]:
    """读取一次系统配置并返回当前版本的工具超时快照。"""
    return apply_agent_tool_timeout(
        specs,
        await load_agent_max_toolcall_timeout(),
        agent_timeout=agent_timeout,
    )
