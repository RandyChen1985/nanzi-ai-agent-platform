import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.schemas.agent import ChatConfig
from app.services.ai.context_manager import AgentContextManager
from app.core.context import get_current_agent_context

# 32-character hex strings matching RAGFlow dataset ID regex
ID_USER_CHECKED_1 = "11111111111111111111111111111111"
ID_USER_CHECKED_2 = "22222222222222222222222222222222"
ID_AGENT_BOUND_1 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
ID_USER_PERM_1 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
ID_USER_PERM_2 = "cccccccccccccccccccccccccccccccc"
ID_DB_ALL_1 = "dddddddddddddddddddddddddddddddd"
ID_DB_ALL_2 = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"

@pytest.mark.asyncio
async def test_setup_context_frontend_specified():
    # 测试前端指定了知识库，只使用前端指定的
    config = ChatConfig(
        agent_id="test-agent",
        agent_name="Test Agent",
        model_name="DeepSeek",
        temperature=0.7,
        system_prompt="prompt",
        tools=[],
        capabilities=[],
        engine_config={"dataset_ids": [ID_AGENT_BOUND_1]}
    )
    
    await AgentContextManager.setup_context(
        config=config,
        knowledge_dataset_ids=[ID_USER_CHECKED_1, ID_USER_CHECKED_2],
        user_info={"user_id": 1, "role": "user"}
    )
    
    ctx = get_current_agent_context()
    assert ctx is not None
    # 仅包含前端指定的，且覆盖了智能体绑定的
    assert set(ctx.dataset_ids) == {ID_USER_CHECKED_1, ID_USER_CHECKED_2}
    assert set(ctx.knowledge_dataset_ids) == {ID_USER_CHECKED_1, ID_USER_CHECKED_2}
    # 检查是否回写回 config.engine_config
    assert set(config.engine_config.get("dataset_ids")) == {ID_USER_CHECKED_1, ID_USER_CHECKED_2}

@pytest.mark.asyncio
async def test_setup_context_fallback_user_permissions():
    # 测试前端未传，普通用户，合并智能体配置与用户权限绑定的知识库
    config = ChatConfig(
        agent_id="test-agent",
        agent_name="Test Agent",
        model_name="DeepSeek",
        temperature=0.7,
        system_prompt="prompt",
        tools=[],
        capabilities=[],
        engine_config={"dataset_ids": [ID_AGENT_BOUND_1]}
    )
    
    # Mock PermissionService.get_knowledge_base_access
    mock_access = {"is_admin": False, "accessible_ids": {ID_USER_PERM_1, ID_USER_PERM_2}}
    
    with patch("app.services.permission_service.PermissionService.get_knowledge_base_access", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_access
        
        # Mock AsyncSessionLocal 的上下文管理器
        mock_session = AsyncMock()
        mock_session_context = MagicMock()
        mock_session_context.__aenter__.return_value = mock_session
        
        with patch("app.services.ai.context_manager.AsyncSessionLocal", return_value=mock_session_context):
            await AgentContextManager.setup_context(
                config=config,
                knowledge_dataset_ids=None,
                user_info={"user_id": 100, "user_name": "test_user", "role": "user"}
            )
            
    ctx = get_current_agent_context()
    assert ctx is not None
    # 应该是智能体配置 + 用户权限的并集
    assert set(ctx.dataset_ids) == {ID_AGENT_BOUND_1, ID_USER_PERM_1, ID_USER_PERM_2}
    assert ctx.knowledge_dataset_ids == []
    assert set(config.engine_config.get("dataset_ids")) == {ID_AGENT_BOUND_1, ID_USER_PERM_1, ID_USER_PERM_2}

@pytest.mark.asyncio
async def test_setup_context_admin_user():
    # 测试前端未传，管理员用户，兜底获取数据库全部未删除知识库
    config = ChatConfig(
        agent_id="test-agent",
        agent_name="Test Agent",
        model_name="DeepSeek",
        temperature=0.7,
        system_prompt="prompt",
        tools=[],
        capabilities=[],
        engine_config={"dataset_ids": [ID_AGENT_BOUND_1]}
    )
    
    mock_access = {"is_admin": True}
    
    # Mock 数据库查询结果
    mock_rows = [ID_DB_ALL_1, ID_DB_ALL_2]
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = mock_rows
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    
    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_result
    
    mock_session_context = MagicMock()
    mock_session_context.__aenter__.return_value = mock_session
    
    with patch("app.services.permission_service.PermissionService.get_knowledge_base_access", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_access
        with patch("app.services.ai.context_manager.AsyncSessionLocal", return_value=mock_session_context):
            await AgentContextManager.setup_context(
                config=config,
                knowledge_dataset_ids=None,
                user_info={"user_id": 1, "user_name": "admin_user", "role": "admin"}
            )
            
    ctx = get_current_agent_context()
    assert ctx is not None
    # 应该是智能体配置 + 数据库所有非deleted的知识库
    assert set(ctx.dataset_ids) == {ID_AGENT_BOUND_1, ID_DB_ALL_1, ID_DB_ALL_2}
    assert ctx.knowledge_dataset_ids == []
    assert set(config.engine_config.get("dataset_ids")) == {ID_AGENT_BOUND_1, ID_DB_ALL_1, ID_DB_ALL_2}
