import pytest
import asyncio
from typing import List
from app.services.ai.tools.registry import ToolRegistry
from app.schemas.agent import ToolConfigItem
from app.services.ai.runtime.agentscope.chat import legacy_tools_to_openai_schemas

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_dingtalk_tool_description_preservation():
    """
    Test that the description of send_dingtalk_message is preserved 
    after runtime configuration injection.
    """
    # 1. Define a tool config item (simulating UI selection)
    config = ToolConfigItem(
        name="send_dingtalk_message",
        engine_config_override={"webhook_url": "https://test.com"}
    )
    
    # 2. Get the configured tool from registry
    tools = await ToolRegistry.get_tools([config])
    assert len(tools) == 1
    
    target_tool = tools[0]
    
    # 3. Check basic attributes
    print(f"DEBUG: Tool Name = {target_tool.name}")
    print(f"DEBUG: Tool Description = {target_tool.description}")
    
    assert target_tool.name == "send_dingtalk_message"
    assert target_tool.description is not None
    assert isinstance(target_tool.description, str)
    assert len(target_tool.description) > 0

    # 4. Simulate the conversion to OpenAI function (which LLMs use)
    # This is where the 400 error usually happens during validation
    try:
        openai_fn = legacy_tools_to_openai_schemas([target_tool])[0]["function"]
        print(f"DEBUG: OpenAI Function Schema = {openai_fn}")
        assert openai_fn["description"] is not None
        assert openai_fn["description"] == target_tool.description
    except Exception as e:
        pytest.fail(f"Conversion to OpenAI function failed: {e}")

@pytest.mark.asyncio
async def test_all_registry_tools_descriptions():
    """Verify all tools in the registry have valid descriptions."""
    # Test a mix of string names and config objects
    test_configs = [
        "get_dataset_schema",
        ToolConfigItem(name="execute_sql_query"),
        "send_dingtalk_message",
        ToolConfigItem(name="search_knowledge_base")
    ]
    
    tools = await ToolRegistry.get_tools(test_configs)
    
    for t in tools:
        print(f"Checking tool: {t.name}")
        assert t.description is not None, f"Tool {t.name} has None description"
        assert isinstance(t.description, str), f"Tool {t.name} description is not string"
        
        # Ensure it passes the OpenAI schema validation
        fn = legacy_tools_to_openai_schemas([t])[0]["function"]
        assert fn["description"], f"OpenAI conversion for {t.name} resulted in empty description"
