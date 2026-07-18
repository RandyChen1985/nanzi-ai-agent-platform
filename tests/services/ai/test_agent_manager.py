import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.ai.agent_manager import AgentManagerService
from app.models.agent import AIAgent, AIAgentVersion
from app.schemas.agent import AIAgentBase, AIAgentVersionBase

# --- Mocks ---

@pytest.fixture
def mock_session():
    return AsyncMock(spec=AsyncSession)

@pytest.fixture
def mock_user_admin():
    return {"user_name": "admin", "role": "admin", "id": "u1"}

@pytest.fixture
def mock_user_normal():
    return {"user_name": "user", "role": "user", "id": "u2"}

# --- Tests ---

@pytest.mark.asyncio
async def test_get_active_agent_config_local(mock_session):
    """测试获取本地 Agent 的活跃配置"""
    # Mock Agent
    agent = AIAgent(id="a1", name="test-agent", engine_type="LOCAL", is_enabled=True)
    
    # Mock Version
    version = AIAgentVersion(
        id="v1", agent_id="a1", version_number=1, 
        status="PUBLISHED", model_name="gpt-4", 
        temperature=0.7, system_prompt="Sys Prompt", 
        tools='["tool1"]'
    )
    
    # Mock DB executes
    # First call: Select Agent
    mock_session.execute.side_effect = [
        MagicMock(scalar_one_or_none=lambda: agent),
        MagicMock(scalar_one_or_none=lambda: version)
    ]
    
    config = await AgentManagerService.get_active_agent_config(mock_session, agent_name="test-agent")
    
    assert config is not None
    assert config.agent_name == "test-agent"
    assert config.model_name == "gpt-4"
    assert config.tools == ["tool1"]
    assert config.agent_version == "v1"

@pytest.mark.asyncio
async def test_get_active_agent_config_ragflow(mock_session):
    """测试获取 RAGFlow Agent 的配置 (绕过版本检查)"""
    agent = AIAgent(
        id="r1", name="rag-agent", engine_type="RAGFLOW", 
        engine_config={"app_id": "app_123"}, is_enabled=True
    )
    
    # Correctly mock the async execution result
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = agent
    mock_session.execute.return_value = mock_result
    
    config = await AgentManagerService.get_active_agent_config(mock_session, agent_name="rag-agent")
    
    assert config is not None
    assert config.engine_type == "RAGFLOW"
    assert config.model_name == "RAGFlow-Remote"
    assert config.engine_config["app_id"] == "app_123"

@pytest.mark.asyncio
async def test_list_agents_admin(mock_session, mock_user_admin):
    """测试管理员查看 Agent 列表"""
    agents = [
        AIAgent(id="a1", name="sys", is_system=True, engine_type="LOCAL"),
        AIAgent(id="a2", name="usr", is_system=False, created_by="other", engine_type="LOCAL")
    ]
    
    # Mock Agents result
    mock_list_result = MagicMock()
    mock_list_result.scalars.return_value.all.return_value = agents
    
    mock_count_result = MagicMock()
    mock_count_result.fetchall.return_value = [("a1", 10)]

    published = AIAgentVersion(
        id="v1",
        agent_id="a1",
        version_number=1,
        status="PUBLISHED",
        system_prompt="p",
        tools=[
            {"name": "get_dataset_schema", "metadata_dataset_ids": ["101", "102"]},
            "ops:alert_query",
        ],
        skills_custom=True,
        skills=["skill-a", "skill-b"],
    )
    agents[0].engine_config = {
        "dataset_ids": ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]
    }
    mock_versions_result = MagicMock()
    mock_versions_result.scalars.return_value.all.return_value = [published]
    
    mock_session.execute.side_effect = [
        mock_list_result, # list agents
        mock_count_result, # counts
        mock_versions_result, # published versions
    ]
    
    res = await AgentManagerService.list_agents(mock_session, mock_user_admin)
    
    assert len(res) == 2
    assert res[0].is_editable is True # Admin edits system
    assert res[1].is_editable is True # Admin edits user
    assert res[0].execution_count == 10
    assert res[0].tool_count == 1
    assert res[0].mcp_count == 1
    assert res[0].skill_count == 2
    assert res[0].skills_custom is True
    assert res[0].metadata_dataset_count == 2
    assert res[0].knowledge_base_count == 2
    assert res[1].tool_count is None  # 无发布版
    assert res[1].metadata_dataset_count is None
    assert res[1].knowledge_base_count is None


def test_summarize_version_capabilities_mcp_and_skills_all():
    version = AIAgentVersion(
        id="v1",
        agent_id="a1",
        version_number=1,
        status="PUBLISHED",
        system_prompt="p",
        tools=["search_knowledge_base", {"name": "mcp-server:foo"}, ""],
        skills_custom=False,
        skills=["ignored-when-not-custom"],
    )
    caps = AgentManagerService.summarize_version_capabilities(version)
    assert caps["tool_count"] == 1
    assert caps["mcp_count"] == 1
    assert caps["skill_count"] is None
    assert caps["skills_custom"] is False
    assert caps["metadata_dataset_count"] is None
    assert caps["knowledge_base_count"] is None


def test_summarize_bound_datasets_and_knowledge_bases():
    version = AIAgentVersion(
        id="v1",
        agent_id="a1",
        version_number=1,
        status="PUBLISHED",
        system_prompt="p",
        tools=[{"name": "get_dataset_schema", "metadata_dataset_ids": ["1", "2", ""]}],
        skills_custom=False,
        skills=[],
    )
    caps = AgentManagerService.summarize_version_capabilities(
        version,
        engine_config={"dataset_ids": "cccccccccccccccccccccccccccccccc,dddddddddddddddddddddddddddddddd"},
    )
    assert caps["metadata_dataset_count"] == 2
    assert caps["knowledge_base_count"] == 2

    unbound = AgentManagerService.summarize_version_capabilities(
        AIAgentVersion(
            id="v2",
            agent_id="a1",
            version_number=1,
            status="PUBLISHED",
            system_prompt="p",
            tools=["get_dataset_schema"],
            skills_custom=False,
            skills=[],
        ),
        engine_config={"dataset_ids": []},
    )
    assert unbound["metadata_dataset_count"] is None
    assert unbound["knowledge_base_count"] is None

@pytest.mark.asyncio
async def test_create_agent_success(mock_session, mock_user_normal):
    """测试创建 Agent"""
    data = AIAgentBase(name="new-agent", display_name="New Agent")
    
    # Check existing name -> None (Mock empty result)
    mock_empty_result = MagicMock()
    mock_empty_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_empty_result
    
    # Mock router service invalidate
    with patch("app.services.ai.router_service.router_service.invalidate_cache") as mock_invalidate:
        agent = await AgentManagerService.create_agent(mock_session, data, mock_user_normal)
        
        assert agent.name == "new-agent"
        assert agent.created_by == "user"
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_invalidate.assert_called_once()

@pytest.mark.asyncio
async def test_update_agent_permission(mock_session, mock_user_normal):
    """测试更新 Agent 权限校验 (非 Admin 不能改 System 或他人 Agent)"""
    # Case 1: System Agent
    sys_agent = AIAgent(id="s1", name="sys", is_system=True, created_by="admin")
    mock_session.get.return_value = sys_agent
    
    res = await AgentManagerService.update_agent(mock_session, "s1", AIAgentBase(name="x", display_name="X"), mock_user_normal)
    assert res is None # Forbidden

    # Case 2: Other's Agent
    other_agent = AIAgent(id="o1", name="other", is_system=False, created_by="other_user")
    mock_session.get.return_value = other_agent
    
    res = await AgentManagerService.update_agent(mock_session, "o1", AIAgentBase(name="x", display_name="X"), mock_user_normal)
    assert res is None # Forbidden

@pytest.mark.asyncio
async def test_publish_version_logic(mock_session, mock_user_admin):
    """测试版本发布逻辑"""
    agent = AIAgent(id="a1", created_by="admin")
    mock_session.get.return_value = agent
    
    await AgentManagerService.publish_version(mock_session, "a1", "v_new", mock_user_admin)
    
    # Check Updates
    # 1. Archive old
    # 2. Publish new
    assert mock_session.execute.call_count == 2
    mock_session.commit.assert_called_once()
