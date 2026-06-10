import pytest
import time
from unittest.mock import MagicMock, AsyncMock, patch
from app.services.ai.tools.registry import ToolRegistry
from app.models.tool import SysApiTool
from app.models.mcp import McpToolCache

pytestmark = pytest.mark.no_infrastructure

@pytest.fixture(autouse=True)
def clear_registry_cache():
    """每个测试前清理缓存，确保测试隔离"""
    ToolRegistry._db_tool_cache.clear()
    ToolRegistry._db_tool_source_cache.clear()
    ToolRegistry._db_tool_ids_fetched_at.clear()
    yield

@pytest.mark.asyncio
async def test_get_static_tool():
    """测试获取静态注册的工具"""
    tool = await ToolRegistry.get_tool("get_dataset_schema")
    assert tool is not None
    assert tool.name == "get_dataset_schema"

@pytest.mark.asyncio
async def test_get_db_tool_and_caching():
    """测试从数据库加载工具并验证缓存逻辑"""
    mock_config = SysApiTool(name="db_tool", is_active=True, parameter_schema="{}")
    mock_tool_instance = MagicMock()
    mock_tool_instance.name = "db_tool"
    
    # 模拟异步 Session
    mock_session = AsyncMock()
    
    # 定义副作用函数：第一次查 MCP 返回空，第二次查 Generic 返回 mock_config
    def mock_execute_side_effect(stmt):
        mock_result = MagicMock()
        # 简单根据语句中包含的关键字判断是在查哪个表
        sql_str = str(stmt).lower()
        if "mcp" in sql_str:
            mock_result.scalar_one_or_none.return_value = None
        else:
            mock_result.scalar_one_or_none.return_value = mock_config
        return mock_result

    mock_session.execute.side_effect = mock_execute_side_effect
    
    mock_session_instance = AsyncMock()
    mock_session_instance.__aenter__.return_value = mock_session
    mock_session_instance.__aexit__.return_value = None
    
    with patch("app.services.ai.tools.registry.AsyncSessionLocal", return_value=mock_session_instance), \
         patch("app.services.ai.tools.generic_api.GenericApiToolFactory.create_tool", return_value=mock_tool_instance):
        
        # 1. 第一次调用：查询数据库 (2次查询：MCP + Generic)
        tool = await ToolRegistry.get_tool("db_tool")
        assert tool == mock_tool_instance
        assert mock_session.execute.call_count == 2
        
        # 2. 第二次调用：应当命中缓存，不查数据库
        tool_cached = await ToolRegistry.get_tool("db_tool")
        assert tool_cached == mock_tool_instance
        assert mock_session.execute.call_count == 2

@pytest.mark.asyncio
async def test_get_db_tool_ttl_expiry():
    """测试缓存过期后的重新加载"""
    mock_config = SysApiTool(name="expired_tool", is_active=True, parameter_schema="{}")
    mock_tool_instance = MagicMock()
    
    ToolRegistry._db_tool_cache_ttl = 0.1 # 设置极短的 TTL
    
    mock_session = AsyncMock()
    def mock_execute_side_effect(stmt):
        mock_result = MagicMock()
        if "mcp" in str(stmt).lower():
            mock_result.scalar_one_or_none.return_value = None
        else:
            mock_result.scalar_one_or_none.return_value = mock_config
        return mock_result
    mock_session.execute.side_effect = mock_execute_side_effect

    mock_session_instance = AsyncMock()
    mock_session_instance.__aenter__.return_value = mock_session
    mock_session_instance.__aexit__.return_value = None
    
    with patch("app.services.ai.tools.registry.AsyncSessionLocal", return_value=mock_session_instance), \
         patch("app.services.ai.tools.generic_api.GenericApiToolFactory.create_tool", return_value=mock_tool_instance):
        
        # 第一次获取
        await ToolRegistry.get_tool("expired_tool")
        assert mock_session.execute.call_count == 2
        
        # 等待过期
        time.sleep(0.15)
        
        # 第二次获取：应当再次查询数据库
        await ToolRegistry.get_tool("expired_tool")
        assert mock_session.execute.call_count == 4

@pytest.mark.asyncio
async def test_get_tools_batch():
    """测试批量获取工具"""
    tools = await ToolRegistry.get_tools(["get_dataset_schema", "non_existent", "execute_sql_query"])
    assert len(tools) == 2
    assert tools[0].name == "get_dataset_schema"
    assert tools[1].name == "execute_sql_query"

def test_dynamic_register():
    """测试手动注册新工具"""
    mock_tool = MagicMock()
    ToolRegistry.register("custom_tool", mock_tool)
    assert ToolRegistry._registry["custom_tool"] == mock_tool


def test_system_executive_tool_names_are_current():
    assert "read_file" in ToolRegistry._registry
    assert "write_file" in ToolRegistry._registry
    assert "exec_command" in ToolRegistry._registry
    assert "manage_process" in ToolRegistry._registry
    assert "list_process" in ToolRegistry._registry
    assert "search_text" in ToolRegistry._registry

    assert "read_local_file" not in ToolRegistry._registry
    assert "write_local_file" not in ToolRegistry._registry
    assert "execute_system_command" not in ToolRegistry._registry
    assert "manage_system_process" not in ToolRegistry._registry
