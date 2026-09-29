"""采样温度的统一解析口径。

真实下发给模型的温度、会话时间线卡片上的温度、审计轨迹里的温度必须是同一个
数字。此前工具卡片读的是智能体版本温度，真实下发用的却是会话覆盖温度，
于是出现「输入框调到 0.3，卡片仍显示温度 0」这种对不上账的排查陷阱。

优先级与真实调用保持一致：

    工具级运行时覆盖 > 会话级覆盖 > 智能体版本 > 系统全局 > 默认值

注意 0.0 是合法温度，不能用真假值判断「是否设置过」。
"""

from typing import Any, Optional

from app.core.context import get_debug_option

SESSION_TEMPERATURE_DEBUG_KEY = "temperature"


def coerce_temperature(value: Any) -> Optional[float]:
    """把配置值转成 float；`None`、空串与非法值统一表示「未设置」。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def session_temperature_override() -> Optional[float]:
    """读取本次请求（`debug_options`）里的会话级温度覆盖，0.0 也算有效覆盖。"""
    return coerce_temperature(
        get_debug_option(SESSION_TEMPERATURE_DEBUG_KEY)
    )


def resolve_effective_temperature(
    *,
    tool_temperature: Any = None,
    session_temperature: Any = None,
    config_temperature: Any = None,
    global_temperature: Any = None,
    default: float = 0.0,
) -> float:
    """按真实调用优先级解析本次生效温度。"""
    for candidate in (
        tool_temperature,
        session_temperature,
        config_temperature,
        global_temperature,
    ):
        resolved = coerce_temperature(candidate)
        if resolved is not None:
            return resolved
    return float(default)


def resolve_config_temperature(config: Any, *, tool_temperature: Any = None) -> float:
    """便捷入口：解析某份 ChatConfig 在当前会话下的生效温度。

    供审计轨迹复用；系统全局兜底需要 IO，不在这里处理。
    """
    return resolve_effective_temperature(
        tool_temperature=tool_temperature,
        session_temperature=session_temperature_override(),
        config_temperature=getattr(config, "temperature", None),
    )
