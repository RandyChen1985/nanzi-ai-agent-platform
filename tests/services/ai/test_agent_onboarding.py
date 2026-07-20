from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.models.agent import AIAgent, AIAgentVersion
from app.schemas.agent import AIAgentBase, AIAgentVersionBase
from app.services.ai.agent_manager import AgentManagerService

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_onboarding_creates_agent_and_v1_draft_in_one_commit():
    session = AsyncMock(spec=AsyncSession)
    no_existing = MagicMock()
    no_existing.scalar_one_or_none.return_value = None
    session.execute.return_value = no_existing
    session.flush.side_effect = lambda: None
    data = AIAgentBase(
        name="sales-data-agent",
        display_name="销售数据助手",
        agent_type="CHATBI",
        capabilities=["reporting"],
    )
    template = {
        "model_name": "qwen3.7-plus",
        "temperature": 0.1,
        "system_prompt": "ChatBI template snapshot",
        "tools": ["get_dataset_schema", "execute_sql_query"],
        "skills_custom": False,
        "skills": [],
        "template_fallback": False,
    }

    with patch.object(
        AgentManagerService,
        "_resolve_onboarding_template",
        AsyncMock(return_value=template),
    ):
        result = await AgentManagerService.create_agent_onboarding(
            session,
            data,
            onboarding_key="key-1",
            user={"user_name": "admin", "role": "admin"},
        )

    added = [call.args[0] for call in session.add.call_args_list]
    assert len(added) == 2
    agent = next(item for item in added if isinstance(item, AIAgent))
    version = next(item for item in added if isinstance(item, AIAgentVersion))
    assert agent.onboarding_key == "key-1"
    assert agent.onboarding_step == "VERSION"
    assert agent.capabilities == ["data_query", "reporting"]
    assert version.agent_id == agent.id
    assert version.version_number == 1
    assert version.status == "DRAFT"
    assert version.system_prompt == "ChatBI template snapshot"
    session.commit.assert_awaited_once()
    assert result.agent is agent
    assert result.version is version
    assert result.template_fallback is False


@pytest.mark.asyncio
async def test_onboarding_key_returns_existing_agent_without_duplicate_insert():
    session = AsyncMock(spec=AsyncSession)
    existing_agent = AIAgent(
        id="agent-1",
        name="existing",
        display_name="Existing",
        created_by="admin",
        onboarding_key="same-key",
        onboarding_step="RESOURCE",
    )
    existing_version = AIAgentVersion(
        id="version-1",
        agent_id="agent-1",
        version_number=1,
        status="DRAFT",
        system_prompt="snapshot",
        tools=[],
    )
    existing_result = MagicMock()
    existing_result.scalar_one_or_none.return_value = existing_agent
    version_result = MagicMock()
    version_result.scalar_one_or_none.return_value = existing_version
    session.execute.side_effect = [existing_result, version_result]

    result = await AgentManagerService.create_agent_onboarding(
        session,
        AIAgentBase(name="ignored", display_name="Ignored"),
        onboarding_key="same-key",
        user={"user_name": "admin", "role": "admin"},
    )

    session.add.assert_not_called()
    session.commit.assert_not_awaited()
    assert result.agent is existing_agent
    assert result.version is existing_version


@pytest.mark.asyncio
async def test_concurrent_onboarding_insert_returns_winning_agent():
    session = AsyncMock(spec=AsyncSession)
    no_existing = MagicMock()
    no_existing.scalar_one_or_none.return_value = None
    winner_agent = AIAgent(id="winner", name="winner", display_name="Winner", onboarding_step="VERSION")
    winner_version = AIAgentVersion(id="winner-v1", agent_id="winner", version_number=1, status="DRAFT", system_prompt="winner", tools=[])
    winner_agent_result = MagicMock()
    winner_agent_result.scalar_one_or_none.return_value = winner_agent
    winner_version_result = MagicMock()
    winner_version_result.scalar_one_or_none.return_value = winner_version
    session.execute.side_effect = [no_existing, no_existing, winner_agent_result, winner_version_result]
    session.commit.side_effect = IntegrityError("insert", {}, Exception("duplicate"))

    with patch.object(AgentManagerService, "_resolve_onboarding_template", AsyncMock(return_value={
        "model_name": "model", "temperature": 0, "system_prompt": "prompt", "tools": [],
        "skills_custom": False, "skills": [], "template_fallback": False,
    })):
        result = await AgentManagerService.create_agent_onboarding(
            session,
            AIAgentBase(name="racing", display_name="Racing"),
            onboarding_key="same-race-key",
            user={"user_name": "admin", "role": "admin"},
        )

    session.rollback.assert_awaited_once()
    assert result.agent is winner_agent
    assert result.version is winner_version


@pytest.mark.asyncio
async def test_updating_initial_draft_advances_onboarding_to_resource():
    session = AsyncMock(spec=AsyncSession)
    agent = AIAgent(
        id="agent-1",
        name="draft-agent",
        display_name="Draft Agent",
        created_by="admin",
        onboarding_step="VERSION",
    )
    version = AIAgentVersion(
        id="version-1",
        agent_id="agent-1",
        version_number=1,
        status="DRAFT",
        system_prompt="old",
        tools=[],
    )
    session.get.side_effect = [agent, version]

    updated = await AgentManagerService.update_agent_version(
        session,
        "agent-1",
        "version-1",
        AIAgentVersionBase(system_prompt="new", tools=[]),
        user={"user_name": "admin", "role": "admin"},
    )

    assert updated is version
    assert agent.onboarding_step == "RESOURCE"


@pytest.mark.asyncio
async def test_publishing_marks_onboarding_complete():
    session = AsyncMock(spec=AsyncSession)
    agent = AIAgent(
        id="agent-1",
        name="general-agent",
        display_name="General Agent",
        created_by="admin",
        agent_type="GENERAL",
        capabilities=["general_chat"],
        onboarding_step="RESOURCE",
        engine_config={},
    )
    version = AIAgentVersion(
        id="version-1",
        agent_id="agent-1",
        version_number=1,
        status="DRAFT",
        system_prompt="prompt",
        tools=[],
    )
    session.get.side_effect = [agent, version]

    success = await AgentManagerService.publish_version(
        session,
        "agent-1",
        "version-1",
        user={"user_name": "admin", "role": "admin"},
    )

    assert success is True
    assert agent.onboarding_step == "COMPLETE"
