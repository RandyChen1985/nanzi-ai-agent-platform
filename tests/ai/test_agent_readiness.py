from unittest.mock import patch

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


def test_knowledge_base_binding_rejects_disabled_search_tool():
    from app.services.ai.agent_readiness import has_knowledge_binding

    assert has_knowledge_binding(
        capabilities=["knowledge_base"],
        engine_config={"dataset_ids": ["kb-1"]},
        tools=[{"name": "search_knowledge_base", "enabled": False}],
    ) is False


def test_knowledge_base_binding_accepts_tool_config_item():
    from app.schemas.agent import ToolConfigItem
    from app.services.ai.agent_readiness import has_knowledge_binding

    assert has_knowledge_binding(
        capabilities=["knowledge_base"],
        engine_config={"dataset_ids": ["kb-1"]},
        tools=[ToolConfigItem(name="search_knowledge_base")],
    ) is True


def test_legacy_knowledge_base_binding_infers_missing_primary_capability():
    from app.services.ai.agent_readiness import has_knowledge_binding

    assert has_knowledge_binding(
        capabilities=["legacy_retrieval"],
        engine_config={"dataset_ids": ["kb-1"]},
        tools=["search_knowledge_base"],
        agent_type="KNOWLEDGE_BASE",
    ) is True


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


def test_legacy_general_agent_without_primary_capability_is_ready():
    result = evaluate_agent_readiness(
        agent_type="GENERAL",
        capabilities=["metadata_parsing", "ddl_analysis", "schema_governance"],
        engine_config={},
        tools=[],
        has_published_version=True,
    )

    assert result.ready is True
    assert result.missing == ()


# ── 系统级默认数据集与 KNOWLEDGE_BASE 就绪判定 ──────────────────────────────
# 背景：内置「知识库助手」(sys-agent-kb) 按设计不绑定 per-agent 数据集，运行时由
# resolve_knowledge_dataset_ids() 回退系统级 knowledge_ragflow_dataset_ids，因此能正常
# 检索；但旧就绪判定只认 per-agent 绑定，导致它"能用却被判定未就绪、无法被委派"。

SYSTEM_DEFAULT_DATASET = "4525d66cec7111f0a3d00242ac120006"


def test_knowledge_base_ready_with_system_default_dataset_only():
    result = evaluate_agent_readiness(
        agent_type="KNOWLEDGE_BASE",
        capabilities=["knowledge_base"],
        engine_config={"dataset_ids": []},
        tools=["search_knowledge_base"],
        has_published_version=True,
        default_dataset_ids=[SYSTEM_DEFAULT_DATASET],
    )

    assert result.ready is True
    assert result.missing == ()


def test_knowledge_base_still_requires_some_dataset_source():
    """系统默认数据集也没有时必须保持未就绪（不能无条件放行）。"""
    for default_ids in (None, [], [""]):
        result = evaluate_agent_readiness(
            agent_type="KNOWLEDGE_BASE",
            capabilities=["knowledge_base"],
            engine_config={"dataset_ids": []},
            tools=["search_knowledge_base"],
            has_published_version=True,
            default_dataset_ids=default_ids,
        )
        assert result.ready is False
        assert result.missing == ("knowledge_base_binding",)


def test_knowledge_base_own_binding_does_not_depend_on_system_default():
    result = evaluate_agent_readiness(
        agent_type="KNOWLEDGE_BASE",
        capabilities=["knowledge_base"],
        engine_config={"dataset_ids": ["kb-1"]},
        tools=["search_knowledge_base"],
        has_published_version=True,
        default_dataset_ids=[],
    )

    assert result.ready is True
    assert result.missing == ()


def test_knowledge_base_missing_search_tool_is_still_reported():
    result = evaluate_agent_readiness(
        agent_type="KNOWLEDGE_BASE",
        capabilities=["knowledge_base"],
        engine_config={"dataset_ids": []},
        tools=[],
        has_published_version=True,
        default_dataset_ids=[SYSTEM_DEFAULT_DATASET],
    )

    assert result.ready is False
    assert result.missing == ("knowledge_base_tool",)


def test_system_default_dataset_does_not_relax_chatbi():
    """系统默认知识库数据集只放宽 KNOWLEDGE_BASE，不应影响 ChatBI 的工具门槛。"""
    result = evaluate_agent_readiness(
        agent_type="CHATBI",
        capabilities=["data_query"],
        engine_config={"dataset_ids": []},
        tools=[],
        has_published_version=True,
        default_dataset_ids=[SYSTEM_DEFAULT_DATASET],
    )

    assert result.ready is False
    assert result.missing == ("data_query_tool",)


async def test_load_system_default_dataset_ids_normalizes_config_value():
    from unittest.mock import AsyncMock, patch

    from app.services.ai.knowledge_utils import load_system_default_dataset_ids

    with patch(
        "app.services.config_service.ConfigService.get",
        AsyncMock(return_value=f"{SYSTEM_DEFAULT_DATASET}, {'a' * 32}"),
    ):
        ids = await load_system_default_dataset_ids()

    assert ids == [SYSTEM_DEFAULT_DATASET, "a" * 32]


async def test_load_system_default_dataset_ids_is_strict_on_failure():
    """读取失败按「未配置」处理：宁可保持未就绪，也不要错误放行。"""
    from unittest.mock import AsyncMock, patch

    from app.services.ai.knowledge_utils import load_system_default_dataset_ids

    with patch(
        "app.services.config_service.ConfigService.get",
        AsyncMock(side_effect=RuntimeError("config store down")),
    ):
        assert await load_system_default_dataset_ids() == []

    with patch(
        "app.services.config_service.ConfigService.get", AsyncMock(return_value=None)
    ):
        assert await load_system_default_dataset_ids() == []


# ── 委派过滤：必须读 agent 行配置，而不是发布版本对象 ────────────────────────


def _delegable_agent(**overrides):
    from types import SimpleNamespace

    values = {
        "id": "sys-agent-kb",
        "name": "knowledge-base",
        "display_name": "知识库助手",
        "is_enabled": True,
        "is_system": True,
        "engine_type": "LOCAL",
        "agent_type": "KNOWLEDGE_BASE",
        "capabilities": ["knowledge_base"],
        "engine_config": {"dataset_ids": []},
        "sort_order": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _patch_delegation_runtime(agent, version, default_ids):
    from unittest.mock import AsyncMock, patch

    return (
        patch(
            "app.services.ai.agent_manager.AgentManagerService._latest_published_versions_by_agent",
            AsyncMock(return_value={agent.id: version}),
        ),
        patch(
            "app.services.ai.knowledge_utils.load_system_default_dataset_ids",
            AsyncMock(return_value=default_ids),
        ),
    )


async def test_delegation_reads_agent_row_config_not_version():
    """回归锁：ai_agent_versions 没有 capabilities/engine_config 两列。

    旧实现从发布版本对象上 ``getattr(version, "engine_config", None)`` 取值，恒为
    ``{}``，于是任何 LOCAL + KNOWLEDGE_BASE 智能体都永久缺 knowledge_base_binding，
    无论怎么配置都无法被委派（线上表现为「知识库助手尚未就绪」，但直接选用可用）。
    """
    from unittest.mock import AsyncMock

    from app.services.ai.tools.agent_delegate_tool import (
        resolve_runnable_delegable_system_agents,
    )

    agent = _delegable_agent()
    # 版本对象刻意不带 capabilities / engine_config（与真实表结构一致）
    version = type("V", (), {"tools": ["search_knowledge_base"]})()

    versions_patch, default_patch = _patch_delegation_runtime(agent, version, [])
    with versions_patch, default_patch:
        blocked = await resolve_runnable_delegable_system_agents(
            AsyncMock(), [agent], user_id=1, is_admin=True, current_agent_id=None
        )
    assert blocked == []

    versions_patch, default_patch = _patch_delegation_runtime(
        agent, version, [SYSTEM_DEFAULT_DATASET]
    )
    with versions_patch, default_patch:
        ready = await resolve_runnable_delegable_system_agents(
            AsyncMock(), [agent], user_id=1, is_admin=True, current_agent_id=None
        )
    assert [a.id for a in ready] == [agent.id]


async def test_delegation_accepts_agent_own_dataset_binding():
    from unittest.mock import AsyncMock

    from app.services.ai.tools.agent_delegate_tool import (
        resolve_runnable_delegable_system_agents,
    )

    agent = _delegable_agent(engine_config={"dataset_ids": ["kb-1"]})
    version = type("V", (), {"tools": ["search_knowledge_base"]})()

    versions_patch, default_patch = _patch_delegation_runtime(agent, version, [])
    with versions_patch, default_patch:
        ready = await resolve_runnable_delegable_system_agents(
            AsyncMock(), [agent], user_id=1, is_admin=True, current_agent_id=None
        )

    assert [a.id for a in ready] == [agent.id]


async def test_delegation_still_requires_a_published_version():
    from unittest.mock import AsyncMock

    from app.services.ai.tools.agent_delegate_tool import (
        resolve_runnable_delegable_system_agents,
    )

    agent = _delegable_agent(engine_config={"dataset_ids": ["kb-1"]})
    versions_patch = patch(
        "app.services.ai.agent_manager.AgentManagerService._latest_published_versions_by_agent",
        AsyncMock(return_value={}),  # 没有任何已发布版本
    )
    default_patch = patch(
        "app.services.ai.knowledge_utils.load_system_default_dataset_ids",
        AsyncMock(return_value=[SYSTEM_DEFAULT_DATASET]),
    )
    with versions_patch, default_patch:
        ready = await resolve_runnable_delegable_system_agents(
            AsyncMock(), [agent], user_id=1, is_admin=True, current_agent_id=None
        )

    assert ready == []
