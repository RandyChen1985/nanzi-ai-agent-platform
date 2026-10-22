"""MetadataGeneratorService.describe_ai_analysis_config 的三种分支测试。

提示条必须如实反映真实执行口径：
- 智能体禁用/不存在 → 不查版本配置，提示词来源为 builtin；
- 智能体启用且有提示词 → prompt_source=agent，模型来源为 agent_config；
- 智能体启用但拿不到配置 → 仍启用，但提示词与模型回退到内置/系统默认。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.no_infrastructure, pytest.mark.asyncio]


def _make_session_factory(agent):
    """构造 AsyncSessionLocal() 的替身：execute().scalar_one_or_none() 返回给定智能体。"""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=agent)
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


async def test_disabled_agent_skips_version_lookup_and_reports_builtin_prompt():
    from app.services.ai.agent_manager import AgentManagerService
    from app.services.metadata_generator import MetadataGeneratorService

    agent = SimpleNamespace(display_name="元数据专家", is_enabled=False)
    factory = _make_session_factory(agent)
    model_info = SimpleNamespace(
        effective_model_id="deepseek-chat", source="system_default"
    )

    with patch("app.services.metadata_generator.AsyncSessionLocal", factory), patch(
        "app.services.metadata_generator.resolve_runtime_model_info",
        AsyncMock(return_value=model_info),
    ), patch.object(
        AgentManagerService,
        "get_active_agent_config",
        AsyncMock(),
    ) as get_config:
        result = await MetadataGeneratorService.describe_ai_analysis_config()

    get_config.assert_not_awaited()
    assert result == {
        "agent_name": "metadata-specialist",
        "display_name": "元数据专家",
        "enabled": False,
        "model_name": "deepseek-chat",
        "model_source": "system_default",
        "prompt_source": "builtin",
    }


async def test_enabled_agent_with_prompt_reports_agent_sources():
    from app.services.ai.agent_manager import AgentManagerService
    from app.services.metadata_generator import MetadataGeneratorService

    agent = SimpleNamespace(display_name="元数据专家", is_enabled=True)
    factory = _make_session_factory(agent)
    agent_config = SimpleNamespace(
        system_prompt="你是元数据专家", model_name="meta-model"
    )
    model_info = SimpleNamespace(effective_model_id="meta-model", source="agent_config")

    with patch("app.services.metadata_generator.AsyncSessionLocal", factory), patch(
        "app.services.metadata_generator.resolve_runtime_model_info",
        AsyncMock(return_value=model_info),
    ), patch.object(
        AgentManagerService,
        "get_active_agent_config",
        AsyncMock(return_value=agent_config),
    ) as get_config:
        result = await MetadataGeneratorService.describe_ai_analysis_config()

    get_config.assert_awaited_once()
    assert result["enabled"] is True
    assert result["model_name"] == "meta-model"
    assert result["model_source"] == "agent_config"
    assert result["prompt_source"] == "agent"


async def test_enabled_agent_without_config_falls_back_to_builtin_prompt():
    from app.services.ai.agent_manager import AgentManagerService
    from app.services.metadata_generator import MetadataGeneratorService

    agent = SimpleNamespace(display_name="元数据专家", is_enabled=True)
    factory = _make_session_factory(agent)
    model_info = SimpleNamespace(
        effective_model_id="deepseek-chat", source="system_default"
    )

    with patch("app.services.metadata_generator.AsyncSessionLocal", factory), patch(
        "app.services.metadata_generator.resolve_runtime_model_info",
        AsyncMock(return_value=model_info),
    ), patch.object(
        AgentManagerService,
        "get_active_agent_config",
        AsyncMock(return_value=None),
    ):
        result = await MetadataGeneratorService.describe_ai_analysis_config()

    assert result["enabled"] is True
    assert result["model_source"] == "system_default"
    assert result["prompt_source"] == "builtin"


async def test_missing_agent_uses_display_name_fallback():
    from app.services.metadata_generator import MetadataGeneratorService

    factory = _make_session_factory(None)
    model_info = SimpleNamespace(
        effective_model_id="deepseek-chat", source="system_default"
    )

    with patch("app.services.metadata_generator.AsyncSessionLocal", factory), patch(
        "app.services.metadata_generator.resolve_runtime_model_info",
        AsyncMock(return_value=model_info),
    ):
        result = await MetadataGeneratorService.describe_ai_analysis_config()

    assert result["enabled"] is False
    assert result["display_name"] == "元数据专家"
    assert result["prompt_source"] == "builtin"
