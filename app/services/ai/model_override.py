"""会话级模型覆盖的统一解析口径。

真实下发给模型的模型名、执行链路卡片上的模型名、审计轨迹里的模型名必须是同一个
名字。与 :mod:`app.services.ai.temperature` 同构：那边记的是「输入框调到 0.3，卡片
仍显示温度 0」，这边记的是「输入框选了 gpt-oss-120b，卡片仍显示版本配置的模型」。

真实调用的优先级在 :func:`AgentConfigProvider.get_configured_llm` 里确定：

    工具级运行时覆盖 > 会话级覆盖（execution_options["model"]） > 智能体版本 > 系统全局

卡片与审计轨迹此前只读「智能体版本」，漏掉了会话级覆盖，于是展示口径和真实口径
对不上。这里把这层优先级抽成唯一实现，供展示侧复用。
"""

from typing import Any, Optional

from app.core.context import get_execution_option

SESSION_MODEL_OVERRIDE_KEY = "model"


def normalize_model_override(value: Any) -> Optional[str]:
    """把配置值转成模型名；`None`、空串与纯空白统一表示「未设置」。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def session_model_override() -> Optional[str]:
    """读取本次请求（`execution_options`）里的会话级模型覆盖。"""
    return normalize_model_override(get_execution_option(SESSION_MODEL_OVERRIDE_KEY))


def resolve_effective_model_name(
    *,
    tool_model: Any = None,
    recorded_model: Any = None,
    session_model: Any = None,
    config_model: Any = None,
    global_model: Any = None,
) -> str:
    """按真实调用优先级解析本次生效的模型名。

    ``recorded_model`` 是主模型句柄上真实下发的模型名（含注册表别名解析结果），
    一旦记录就该盖过会话别名与版本配置；``session_model`` 留空时由展示侧自行传入
    :func:`session_model_override` 的结果，便于调用点显式表达来源。
    """
    for candidate in (
        tool_model,
        recorded_model,
        session_model,
        config_model,
        global_model,
    ):
        resolved = normalize_model_override(candidate)
        if resolved is not None:
            return resolved
    return ""
