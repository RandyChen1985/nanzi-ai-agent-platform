"""系统默认 LLM 模型的统一解析。

历史上三处 UI 兜底各自写死 ``or "DeepSeek-V3.2"``。一旦 ``llm_model_name``
未配置，这些路径就会伪造出一个目标环境并不存在的模型名，实际调用时报
``LLM API Key is missing for model 'DeepSeek-V3.2'``，把「没配默认模型」
误诊成「密钥问题」，还会把这个假模型名写进新建智能体的版本配置里。

这里把解析收敛到一处：配置为空一律返回 None，由调用方决定提示方式，
绝不编造模型名。
"""

from __future__ import annotations

from typing import Optional

DEFAULT_MODEL_CONFIG_KEY = "llm_model_name"

# 提示语要能被运维直接照做，因此指明配置项与配置入口。
CONFIG_HINT = (
    f"系统未配置默认 LLM 模型（{DEFAULT_MODEL_CONFIG_KEY}），"
    "请先在「系统设置 - 模型配置」中指定默认模型后再试"
)


class DefaultModelNotConfigured(RuntimeError):
    """系统未配置默认 LLM 模型。"""

    def __init__(self, message: str = CONFIG_HINT):
        super().__init__(message)


async def get_default_llm_model() -> Optional[str]:
    """读取系统默认 LLM 模型；未配置或为空白时返回 None。

    优先级与 ``get_llm_async`` 保持一致：系统配置 ``llm_model_name`` 优先，
    其次才是环境变量 ``LLM_MODEL_NAME``。这样只配环境变量的部署不会被误判
    成「未配置默认模型」。
    """
    from app.core.config import settings
    from app.services.config_service import ConfigService

    raw = await ConfigService.get(DEFAULT_MODEL_CONFIG_KEY)
    model = str(raw or "").strip()
    if model:
        return model

    env_model = str(getattr(settings, "LLM_MODEL_NAME", "") or "").strip()
    return env_model or None


async def require_default_llm_model() -> str:
    """读取系统默认 LLM 模型；未配置时抛 :class:`DefaultModelNotConfigured`。"""
    model = await get_default_llm_model()
    if not model:
        raise DefaultModelNotConfigured()
    return model
