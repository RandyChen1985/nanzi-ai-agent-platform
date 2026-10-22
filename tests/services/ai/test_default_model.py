"""系统默认 LLM 模型解析的回归测试。

历史背景：三处 UI 兜底各自写死 ``or "DeepSeek-V3.2"``，一旦 ``llm_model_name``
未配置，就会伪造出一个目标环境并不存在的模型名，最终报错变成
``LLM API Key is missing for model 'DeepSeek-V3.2'``，把「没配默认模型」
误诊成「密钥问题」。这里锁定「配置为空就返回 None / 显式报错」的契约。
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services.ai.default_model import (
    DEFAULT_MODEL_CONFIG_KEY,
    DefaultModelNotConfigured,
    get_default_llm_model,
    require_default_llm_model,
)


def _patch_config(value):
    return patch(
        "app.services.config_service.ConfigService.get",
        new_callable=AsyncMock,
        return_value=value,
    )


@pytest.mark.asyncio
async def test_get_default_llm_model_returns_stripped_value():
    with _patch_config("  qwen3.6-35b  "):
        assert await get_default_llm_model() == "qwen3.6-35b"


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, "", "   ", "\n\t"])
async def test_get_default_llm_model_returns_none_when_unset(monkeypatch, raw):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_MODEL_NAME", None, raising=False)
    with _patch_config(raw):
        assert await get_default_llm_model() is None


@pytest.mark.asyncio
async def test_get_default_llm_model_prefers_system_config(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_MODEL_NAME", "env-model", raising=False)
    with _patch_config("db-model"):
        assert await get_default_llm_model() == "db-model"


@pytest.mark.asyncio
async def test_get_default_llm_model_falls_back_to_env(monkeypatch):
    """只配环境变量 LLM_MODEL_NAME 的部署不应被判成「未配置」。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_MODEL_NAME", "env-model", raising=False)
    with _patch_config(None):
        assert await get_default_llm_model() == "env-model"


@pytest.mark.asyncio
async def test_require_default_llm_model_returns_value():
    with _patch_config("qwen3.6-35b"):
        assert await require_default_llm_model() == "qwen3.6-35b"


@pytest.mark.asyncio
async def test_require_default_llm_model_raises_with_actionable_hint(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_MODEL_NAME", None, raising=False)
    with _patch_config(None):
        with pytest.raises(DefaultModelNotConfigured) as excinfo:
            await require_default_llm_model()

    message = str(excinfo.value)
    assert DEFAULT_MODEL_CONFIG_KEY in message
    assert "系统设置" in message


@pytest.mark.asyncio
async def test_onboarding_template_fails_fast_without_default_model(monkeypatch):
    """新建智能体时若系统未配置默认模型，应报 400 并指向配置项，而不是落库假模型名。"""
    from app.core.config import settings
    from app.services.ai.agent_manager import AgentManagerService

    monkeypatch.setattr(settings, "LLM_MODEL_NAME", None, raising=False)

    with patch.object(
        AgentManagerService, "get_active_agent_config", new=AsyncMock(return_value=None)
    ), _patch_config(None):
        with pytest.raises(HTTPException) as excinfo:
            await AgentManagerService._resolve_onboarding_template(None, "GENERAL")

    assert excinfo.value.status_code == 400
    assert DEFAULT_MODEL_CONFIG_KEY in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_onboarding_template_uses_configured_default_model():
    from app.services.ai.agent_manager import AgentManagerService

    with patch.object(
        AgentManagerService, "get_active_agent_config", new=AsyncMock(return_value=None)
    ), _patch_config("qwen3.6-35b"):
        template = await AgentManagerService._resolve_onboarding_template(None, "GENERAL")

    assert template["model_name"] == "qwen3.6-35b"
    assert template["template_fallback"] is True
