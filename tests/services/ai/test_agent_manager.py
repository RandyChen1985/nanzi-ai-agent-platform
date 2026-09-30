import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.ai.agent_manager import AgentManagerService
from app.models.agent import AIAgent, AIAgentVersion
from app.schemas.agent import AIAgentBase, AIAgentVersionBase

pytestmark = pytest.mark.no_infrastructure

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
    caps = AgentManagerService.summarize_version_capabilities(
        version, enabled_global_skill_count=12
    )
    assert caps["tool_count"] == 1
    assert caps["mcp_count"] == 1
    assert caps["skill_count"] == 12
    assert caps["skills_custom"] is False
    assert caps["metadata_dataset_count"] is None
    assert caps["knowledge_base_count"] is None

    caps_default = AgentManagerService.summarize_version_capabilities(version)
    assert caps_default["skill_count"] == 0


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
    
    # Check existing name -> None。
    # 注意 find_agent_name_conflict 走的是 result.scalars().first()，
    # 若只 mock scalar_one_or_none，scalars() 会返回 MagicMock、first() 为真值，
    # 直接被判成「标识符已被占用」。
    mock_empty_result = MagicMock()
    mock_empty_result.scalar_one_or_none.return_value = None
    mock_empty_result.scalars.return_value.first.return_value = None
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
@pytest.mark.parametrize(
    ("engine_type", "engine_config", "message"),
    [
        ("RAGFLOW", {}, "RAGFlow 模式必须填写 App ID"),
        ("OPENCLAW", {"base_url": "", "model": ""}, "OpenClaw 模式必须填写地址和机器人 ID"),
    ],
)
async def test_create_external_agent_rejects_missing_engine_parameters(
    mock_session,
    mock_user_admin,
    engine_type,
    engine_config,
    message,
):
    with pytest.raises(ValueError, match=message):
        await AgentManagerService.create_agent(
            mock_session,
            AIAgentBase(
                name="external-agent",
                display_name="External Agent",
                engine_type=engine_type,
                engine_config=engine_config,
            ),
            mock_user_admin,
        )

    mock_session.add.assert_not_called()

@pytest.mark.asyncio
async def test_update_agent_permission(mock_session, mock_user_normal):
    """测试更新 Agent 权限校验 (非 Admin 不能改 System 或他人 Agent)"""
    # Case 1: System Agent
    sys_agent = AIAgent(id="s1", name="sys", is_system=True, created_by="admin")
    mock_session.get.return_value = sys_agent
    
    res = await AgentManagerService.update_agent(mock_session, "s1", AIAgentBase(name="x", display_name="X"), mock_user_normal)
    assert res is None # Forbidden


@pytest.mark.asyncio
async def test_update_main_rejects_disabling_even_for_admin(mock_session, mock_user_admin):
    main_agent = AIAgent(
        id="sys-agent-chat",
        name="main",
        display_name="主助手(Main)",
        is_system=False,
        is_enabled=True,
        engine_type="LOCAL",
    )
    mock_session.get.return_value = main_agent

    with pytest.raises(ValueError, match="主助手不可禁用"):
        await AgentManagerService.update_agent(
            mock_session,
            "sys-agent-chat",
            AIAgentBase(
                name="main",
                display_name="主助手(Main)",
                is_enabled=False,
            ),
            mock_user_admin,
        )

    assert main_agent.is_enabled is True
    mock_session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_delete_main_is_rejected_without_deleting(mock_session, mock_user_admin):
    main_agent = AIAgent(
        id="sys-agent-chat",
        name="main",
        display_name="主助手(Main)",
        is_system=False,
        is_enabled=True,
    )
    mock_session.get.return_value = main_agent

    assert await AgentManagerService.delete_agent(
        mock_session, "sys-agent-chat", user=mock_user_admin
    ) is False
    mock_session.delete.assert_not_called()
    mock_session.commit.assert_not_called()

    # Case 2: Other's Agent
    other_agent = AIAgent(id="o1", name="other", is_system=False, created_by="other_user")
    mock_session.get.return_value = other_agent
    
    res = await AgentManagerService.update_agent(mock_session, "o1", AIAgentBase(name="x", display_name="X"), mock_user_normal)
    assert res is None # Forbidden


@pytest.mark.asyncio
async def test_update_agent_keeps_original_primary_type(mock_session, mock_user_admin):
    agent = AIAgent(
        id="a1",
        name="fixed-type",
        display_name="Fixed Type",
        created_by="admin",
        agent_type="CHATBI",
        capabilities=["data_query", "reporting"],
        engine_type="LOCAL",
    )
    mock_session.get.return_value = agent

    await AgentManagerService.update_agent(
        mock_session,
        "a1",
        AIAgentBase(
            name="fixed-type",
            display_name="Fixed Type Updated",
            agent_type="GENERAL",
            capabilities=["general_chat", "writing"],
        ),
        mock_user_admin,
    )

    assert agent.agent_type == "CHATBI"
    assert agent.capabilities == ["data_query", "writing"]


@pytest.mark.asyncio
async def test_update_agent_rejects_engine_type_change_without_touching_engine_config(mock_session, mock_user_admin):
    agent = AIAgent(
        id="a1",
        name="external-agent",
        display_name="External Agent",
        created_by="admin",
        agent_type="GENERAL",
        capabilities=["general_chat"],
        engine_type="OPENCLAW",
        engine_config={"base_url": "https://old.example.com", "model": "bot-old"},
    )
    mock_session.get.return_value = agent

    with pytest.raises(ValueError, match="执行引擎创建后不可修改"):
        await AgentManagerService.update_agent(
            mock_session,
            "a1",
            AIAgentBase(
                name="external-agent",
                display_name="External Agent",
                engine_type="RAGFLOW",
                engine_config={"app_id": "ragflow-app"},
            ),
            mock_user_admin,
        )

    assert agent.engine_type == "OPENCLAW"
    assert agent.engine_config == {"base_url": "https://old.example.com", "model": "bot-old"}


@pytest.mark.asyncio
async def test_update_agent_allows_parameters_for_existing_engine(mock_session, mock_user_admin):
    agent = AIAgent(
        id="a1",
        name="external-agent",
        display_name="External Agent",
        created_by="admin",
        agent_type="GENERAL",
        capabilities=["general_chat"],
        engine_type="OPENCLAW",
        engine_config={"base_url": "https://old.example.com", "model": "bot-old"},
    )
    mock_session.get.return_value = agent

    await AgentManagerService.update_agent(
        mock_session,
        "a1",
        AIAgentBase(
            name="external-agent",
            display_name="External Agent",
            engine_type="OPENCLAW",
            engine_config={"base_url": "https://new.example.com", "model": "bot-new"},
        ),
        mock_user_admin,
    )

    assert agent.engine_type == "OPENCLAW"
    assert agent.engine_config == {"base_url": "https://new.example.com", "model": "bot-new"}


@pytest.mark.asyncio
async def test_update_agent_allows_engine_config_when_engine_type_is_omitted(mock_session, mock_user_admin):
    agent = AIAgent(
        id="a1",
        name="external-agent",
        display_name="External Agent",
        created_by="admin",
        agent_type="GENERAL",
        capabilities=["general_chat"],
        engine_type="OPENCLAW",
        engine_config={"base_url": "https://old.example.com", "model": "bot-old"},
    )
    mock_session.get.return_value = agent

    await AgentManagerService.update_agent(
        mock_session,
        "a1",
        AIAgentBase.model_validate({
            "name": "external-agent",
            "display_name": "External Agent",
            "engine_config": {"base_url": "https://new.example.com", "model": "bot-new"},
        }),
        mock_user_admin,
    )

    assert agent.engine_type == "OPENCLAW"
    assert agent.engine_config == {"base_url": "https://new.example.com", "model": "bot-new"}

@pytest.mark.asyncio
async def test_publish_version_logic(mock_session, mock_user_admin):
    """测试版本发布逻辑"""
    agent = AIAgent(
        id="a1",
        created_by="admin",
        agent_type="GENERAL",
        capabilities=["general_chat"],
        engine_config={},
    )
    version = AIAgentVersion(id="v_new", agent_id="a1", tools=[])
    mock_session.get.side_effect = [agent, version]
    
    with patch(
        "app.services.ai.tools.registry.ToolRegistry.clear_db_tool_cache"
    ) as mock_clear_tool_cache:
        await AgentManagerService.publish_version(mock_session, "a1", "v_new", mock_user_admin)
    
    # Check Updates
    # 1. Archive old
    # 2. Publish new
    assert mock_session.execute.call_count == 2
    mock_session.commit.assert_called_once()
    mock_clear_tool_cache.assert_called_once()


@pytest.mark.asyncio
async def test_publish_version_accepts_legacy_general_agent_without_primary_capability(
    mock_session, mock_user_admin
):
    agent = AIAgent(
        id="legacy-general-1",
        created_by="admin",
        agent_type="GENERAL",
        capabilities=["metadata_parsing", "ddl_analysis", "schema_governance"],
        engine_config={},
    )
    version = AIAgentVersion(id="legacy-draft-1", agent_id="legacy-general-1", tools=[])
    mock_session.get.side_effect = [agent, version]

    with patch(
        "app.services.ai.tools.registry.ToolRegistry.clear_db_tool_cache"
    ) as mock_clear_tool_cache:
        success = await AgentManagerService.publish_version(
            mock_session,
            "legacy-general-1",
            "legacy-draft-1",
            mock_user_admin,
        )

    assert success is True
    assert mock_session.execute.call_count == 2
    mock_session.commit.assert_called_once()
    mock_clear_tool_cache.assert_called_once()


@pytest.mark.asyncio
async def test_publish_version_rejects_chatbi_without_query_tool_before_archiving(mock_session, mock_user_admin):
    from app.services.ai.agent_manager import AgentNotReadyError

    agent = AIAgent(
        id="chatbi-1",
        created_by="admin",
        agent_type="CHATBI",
        capabilities=["data_query"],
        engine_config={"dataset_ids": []},
    )
    version = AIAgentVersion(
        id="draft-1",
        agent_id="chatbi-1",
        tools=[],
    )
    mock_session.get.side_effect = [agent, version]

    with pytest.raises(AgentNotReadyError) as exc_info:
        await AgentManagerService.publish_version(
            mock_session,
            "chatbi-1",
            "draft-1",
            mock_user_admin,
        )

    assert exc_info.value.missing == ("data_query_tool",)
    mock_session.execute.assert_not_called()
    mock_session.commit.assert_not_called()
