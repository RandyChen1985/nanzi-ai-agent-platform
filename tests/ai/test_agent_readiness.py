from app.services.ai.agent_readiness import evaluate_agent_readiness
import pytest

pytestmark = pytest.mark.no_infrastructure


def test_chatbi_requires_query_tool_but_inherits_user_datasets_when_unbound():
    result = evaluate_agent_readiness(
        agent_type="CHATBI",
        capabilities=["data_query"],
        engine_config={"dataset_ids": []},
        tools=[],
        has_published_version=True,
    )

    assert result.ready is False
    assert result.missing == ("data_query_tool",)


def test_chatbi_is_ready_without_explicit_dataset_binding_when_query_tool_exists():
    result = evaluate_agent_readiness(
        agent_type="CHATBI",
        capabilities=["data_query"],
        engine_config={"dataset_ids": []},
        tools=["get_dataset_schema", "execute_sql_query"],
        has_published_version=True,
    )

    assert result.ready is True
    assert result.missing == ()


def test_chatbi_accepts_object_tool_entries():
    result = evaluate_agent_readiness(
        agent_type="CHATBI",
        capabilities=["data_query"],
        engine_config={"dataset_ids": ["dataset-1"]},
        tools=[{"name": "execute_sql_query", "enabled": True}],
        has_published_version=True,
    )

    assert result.ready is True
    assert result.missing == ()


def test_knowledge_base_ready_with_binding_and_search_tool():
    result = evaluate_agent_readiness(
        agent_type="KNOWLEDGE_BASE",
        capabilities=["knowledge_base"],
        engine_config={"dataset_ids": ["kb-1"]},
        tools=["search_knowledge_base"],
        has_published_version=True,
    )

    assert result.ready is True
    assert result.missing == ()


def test_general_requires_a_published_version():
    result = evaluate_agent_readiness(
        agent_type="GENERAL",
        capabilities=["general_chat"],
        engine_config={},
        tools=[],
        has_published_version=False,
    )

    assert result.ready is False
    assert result.missing == ("published_version",)
