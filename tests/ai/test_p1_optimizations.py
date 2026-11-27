import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.ai.turn_decision import (
    should_inject_ltm,
    should_inject_memory_recall_hint,
    should_inject_user_context,
    should_run_active_memory_preload,
)
from app.schemas.agent import ChatConfig


@pytest.fixture(scope="function", autouse=True)
async def init_infrastructure():
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis.close_redis", new_callable=AsyncMock), \
         patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock):
        yield


@pytest.fixture
def chat_config():
    return ChatConfig(
        agent_id="test-agent-id",
        agent_name="TestAgent",
        agent_version=None,
        model_name="gpt-4o",
        temperature=0.0,
        system_prompt="You are a test agent.",
        tools=["search_knowledge_base"],
    )


def test_injection_profile_for_data_turns():
    assert should_inject_ltm("data_query") is True
    assert should_inject_ltm("context_action") is True
    assert should_inject_memory_recall_hint("data_query") is False
    assert should_run_active_memory_preload("data_query") is False
    assert should_inject_memory_recall_hint("knowledge") is False
    assert should_inject_user_context("data_query") is True
    assert should_inject_user_context("general") is True


def test_prepend_platform_global_system_prompt_with_tool_config_items():
    from app.services.ai.agent_prompts import AgentServicePrompts
    from app.schemas.agent import ToolConfigItem
    from unittest.mock import MagicMock

    # 模拟一个同时包含 str、ToolConfigItem 实例和 dict 的复杂工具配置
    mock_config = MagicMock()
    mock_config.tools = [
        "get_dataset_schema",
        ToolConfigItem(name="execute_sql_query", model_name="gpt-4o", temperature=0.1),
        {"name": "read_file"}
    ]
    mock_config.capabilities = ["knowledge_base"]

    # 执行全局提示词的 prepend 拼接动作，应成功运行且不抛出 unhashable type 异常
    result = AgentServicePrompts.prepend_platform_global_system_prompt(
        system_prompt="My custom prompt",
        agent_config=mock_config
    )

    # 验证全局守则和业务提示词均被正确合并拼装
    # 品牌名可由品牌个性化配置覆盖，按实际生效值断言，避免依赖 DB 里的品牌设置
    from app.services.branding_settings_service import BrandingSettingsService

    brand = BrandingSettingsService.get_prompt_brand_name()
    assert f"[{brand} · 全局守则]" in result
    assert "My custom prompt" in result
    assert "## 执行倾向" in result
    assert "## 工具调用风格" in result
    assert "## 本轮可用工具" in result
    assert "get_dataset_schema" in result
    assert "execute_sql_query" in result
    assert "## 技能使用" in result
    assert "## 工具确认" in result
    assert "目标边界" in result


def test_platform_global_prompt_skills_section_without_skill_tools(monkeypatch):
    from app.services.ai.agent_prompts import AgentServicePrompts
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        "app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools",
        lambda: [],
    )

    mock_config = MagicMock()
    mock_config.tools = ["memory_search"]
    mock_config.capabilities = []

    result = AgentServicePrompts.prepend_platform_global_system_prompt(
        system_prompt="Base",
        agent_config=mock_config,
    )
    assert "## 技能使用" not in result
    assert "## 工具确认" not in result
