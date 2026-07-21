from types import SimpleNamespace
import pytest

from app.services.ai.agent_types import (
    AgentType,
    normalize_agent_capabilities,
    normalize_agent_capabilities_for_agent,
    resolve_agent_type,
    resolve_agent_type_for_engine,
)

pytestmark = pytest.mark.no_infrastructure


def test_chatbi_adds_locked_data_query_and_preserves_extensions():
    assert normalize_agent_capabilities(
        AgentType.CHATBI,
        [" reporting ", "data_query"],
    ) == ["data_query", "reporting"]


def test_switching_to_knowledge_removes_other_primary_tags():
    assert normalize_agent_capabilities(
        AgentType.KNOWLEDGE_BASE,
        ["data_query", "general_chat", "qa"],
    ) == ["knowledge_base", "qa"]


def test_general_type_deduplicates_and_sorts_extension_tags():
    assert normalize_agent_capabilities(
        AgentType.GENERAL,
        [" coding ", "coding", "qa", ""],
    ) == ["general_chat", "coding", "qa"]


def test_legacy_knowledge_agent_type_is_inferred_from_name():
    agent = SimpleNamespace(
        agent_type=None,
        name="knowledge-base",
        capabilities=["knowledge_retrieval"],
    )

    assert resolve_agent_type(agent) is AgentType.KNOWLEDGE_BASE


def test_external_engine_forces_general_type_and_general_chat():
    assert resolve_agent_type_for_engine("RAGFLOW", AgentType.CHATBI) is AgentType.GENERAL
    assert normalize_agent_capabilities_for_agent(
        engine_type="OPENCLAW",
        agent_type=AgentType.CHATBI,
        values=["data_query", "contract_review"],
    ) == ["general_chat", "contract_review"]
