"""Tests for list_accessible_datasets / list_accessible_knowledge_bases system tools."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.tools.registry import ToolRegistry

pytestmark = pytest.mark.no_infrastructure


def test_resource_catalog_tools_are_system_implicit():
    from app.services.ai.runtime.agentscope.tools import (
        READ_ONLY_TOOL_NAMES,
        infer_runtime_permission_scope,
    )

    tool_names = {getattr(tool, "name", "") for tool in ToolRegistry.get_system_implicit_tools()}
    assert "list_accessible_datasets" in tool_names
    assert "list_accessible_knowledge_bases" in tool_names
    assert "list_available_agents" in tool_names
    assert "list_accessible_datasets" in READ_ONLY_TOOL_NAMES
    assert "list_accessible_knowledge_bases" in READ_ONLY_TOOL_NAMES
    assert "list_available_agents" in READ_ONLY_TOOL_NAMES
    assert infer_runtime_permission_scope("list_accessible_datasets", "system") == "read"
    assert infer_runtime_permission_scope("list_accessible_knowledge_bases", "system") == "read"
    assert infer_runtime_permission_scope("list_available_agents", "system") == "read"
    assert ToolRegistry._registry["list_accessible_datasets"].name == "list_accessible_datasets"
    description = str(getattr(ToolRegistry._registry["list_accessible_datasets"], "description", "") or "")
    assert "已启用" in description
    assert "未启用" in description
    assert (
        ToolRegistry._registry["list_accessible_knowledge_bases"].name
        == "list_accessible_knowledge_bases"
    )
    assert (
        ToolRegistry._registry["list_available_agents"].name
        == "list_available_agents"
    )


@pytest.mark.asyncio
async def test_list_accessible_datasets_requires_user_context():
    from app.services.ai.tools.resource_catalog_tools import list_accessible_datasets

    with patch(
        "app.services.ai.tools.resource_catalog_tools.get_current_agent_context",
        return_value=None,
    ):
        result = await list_accessible_datasets.ainvoke({})

    assert "无法识别当前用户" in result or "User context" in result


@pytest.mark.asyncio
async def test_list_accessible_datasets_returns_lightweight_fields():
    from app.services.ai.tools.resource_catalog_tools import list_accessible_datasets

    ctx = MagicMock()
    ctx.user_id = 7
    ctx.is_admin = False

    row = SimpleNamespace(
        id=11,
        name="orders",
        display_name="客户订单",
        description="订单主数据备注",
        status=1,
    )

    db = AsyncMock()
    db_cm = MagicMock()
    db_cm.__aenter__ = AsyncMock(return_value=db)
    db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.services.ai.tools.resource_catalog_tools.get_current_agent_context",
        return_value=ctx,
    ), patch(
        "app.services.ai.tools.resource_catalog_tools.AsyncSessionLocal",
        return_value=db_cm,
    ), patch(
        "app.services.ai.tools.resource_catalog_tools.MetadataService.list_accessible_dataset_options",
        AsyncMock(return_value=[row]),
    ) as mock_list:
        result = await list_accessible_datasets.ainvoke({})

    payload = json.loads(result)
    assert payload["count"] == 1
    assert payload["items"] == [
        {
            "id": 11,
            "name": "orders",
            "display_name": "客户订单",
            "description": "订单主数据备注",
            "status": 1,
        }
    ]
    mock_list.assert_awaited_once()
    kwargs = mock_list.await_args.kwargs
    assert kwargs["user_id"] == 7
    assert kwargs["is_admin"] is False
    assert kwargs["status"] == 1


@pytest.mark.asyncio
async def test_list_accessible_knowledge_bases_filters_by_access():
    from app.services.ai.tools.resource_catalog_tools import list_accessible_knowledge_bases

    ctx = MagicMock()
    ctx.user_id = 7
    ctx.is_admin = False
    ctx.user_dimensions = {"user_name": "alice"}

    kb_allowed = SimpleNamespace(
        ragflow_dataset_id="kb-1",
        name="运维手册",
        description="机房 SOP",
        notes="内部备注",
        visibility="team",
        owner="ops",
    )
    kb_denied = SimpleNamespace(
        ragflow_dataset_id="kb-2",
        name="机密库",
        description="x",
        notes="y",
        visibility="private",
        owner="sec",
    )

    db = AsyncMock()
    db_cm = MagicMock()
    db_cm.__aenter__ = AsyncMock(return_value=db)
    db_cm.__aexit__ = AsyncMock(return_value=None)

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [kb_allowed, kb_denied]
    db.execute = AsyncMock(return_value=execute_result)

    perm = MagicMock()
    perm.get_knowledge_base_access = AsyncMock(
        return_value={
            "is_admin": False,
            "accessible_ids": {"kb-1"},
            "writable_ids": set(),
        }
    )

    with patch(
        "app.services.ai.tools.resource_catalog_tools.get_current_agent_context",
        return_value=ctx,
    ), patch(
        "app.services.ai.tools.resource_catalog_tools.AsyncSessionLocal",
        return_value=db_cm,
    ), patch(
        "app.services.ai.tools.resource_catalog_tools.PermissionService",
        return_value=perm,
    ):
        result = await list_accessible_knowledge_bases.ainvoke({})

    payload = json.loads(result)
    assert payload["count"] == 1
    assert payload["items"] == [
        {
            "ragflow_dataset_id": "kb-1",
            "name": "运维手册",
            "description": "机房 SOP",
            "notes": "内部备注",
            "visibility": "team",
            "owner": "ops",
        }
    ]
    perm.get_knowledge_base_access.assert_awaited_once_with(7, "alice")


@pytest.mark.asyncio
async def test_list_available_agents_requires_user_context():
    from app.services.ai.tools.resource_catalog_tools import list_available_agents

    with patch(
        "app.services.ai.tools.resource_catalog_tools.get_current_agent_context",
        return_value=None,
    ):
        result = await list_available_agents.ainvoke({})

    assert "无法识别当前用户" in result


@pytest.mark.asyncio
async def test_list_available_agents_returns_lightweight_fields():
    from app.services.ai.tools.resource_catalog_tools import list_available_agents

    ctx = MagicMock()
    ctx.user_id = 7
    ctx.is_admin = False
    ctx.agent_id = "agent_current"

    agent_target = SimpleNamespace(
        id="agent_target",
        name="data_expert",
        display_name="数据分析专家",
        description="负责数据查询与分析",
        capabilities=["text2sql", "chart"],
    )

    db = AsyncMock()
    scalars_result = MagicMock()
    scalars_result.scalars.return_value.all.return_value = [agent_target]
    db.execute = AsyncMock(return_value=scalars_result)
    db_cm = MagicMock()
    db_cm.__aenter__ = AsyncMock(return_value=db)
    db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.services.ai.tools.resource_catalog_tools.get_current_agent_context",
        return_value=ctx,
    ), patch(
        "app.services.ai.tools.resource_catalog_tools.AsyncSessionLocal",
        return_value=db_cm,
    ), patch(
        # 目录口径不得复用 UI 可见性列表：list_agents 在 tool 侧拿不到 user，
        # 会把 created_by 非空的候选（含全部系统智能体）剃成空列表。
        "app.services.ai.agent_manager.AgentManagerService.list_agents",
        AsyncMock(return_value=[]),
    ) as mock_list_agents, patch(
        "app.services.ai.tools.agent_delegate_tool.resolve_runnable_delegable_system_agents",
        AsyncMock(return_value=[agent_target]),
    ) as mock_resolve_delegable:
        result = await list_available_agents.ainvoke({})

    payload = json.loads(result)
    assert payload["scope"] == "delegable_system_agents"
    assert payload["count"] == 1
    assert "note" not in payload
    assert payload["items"] == [
        {
            "agent_name": "data_expert",
            "display_name": "数据分析专家",
            "description": "负责数据查询与分析",
            "capabilities": ["text2sql", "chart"],
            "is_current": False,
        }
    ]
    mock_list_agents.assert_not_awaited()
    mock_resolve_delegable.assert_awaited_once_with(
        db,
        [agent_target],
        user_id=7,
        is_admin=False,
        current_agent_id=None,
    )


@pytest.mark.asyncio
async def test_list_available_agents_empty_catalog_without_permitted_system_agents():
    """未被分配任何系统级智能体时返回空目录，是正确的授权口径而非缺陷。

    本工具只服务于「委派」场景，而委派本身只允许系统级智能体
    （``sub_agent_call`` 的候选 SQL 即 ``is_system=True``）。用户自建智能体
    即使由 owner 自动授权，也不进入该目录。

    空目录必须自带 note：否则模型容易把「没有可委派的系统级智能体」转述成
    「你没有任何智能体」，与智能体中心里用户自建的条目直接冲突。
    """
    from app.services.ai.tools.resource_catalog_tools import list_available_agents

    ctx = MagicMock()
    ctx.user_id = 7
    ctx.is_admin = False
    ctx.agent_id = "agent_current"

    db = AsyncMock()
    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=empty_result)
    db_cm = MagicMock()
    db_cm.__aenter__ = AsyncMock(return_value=db)
    db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.services.ai.tools.resource_catalog_tools.get_current_agent_context",
        return_value=ctx,
    ), patch(
        "app.services.ai.tools.resource_catalog_tools.AsyncSessionLocal",
        return_value=db_cm,
    ), patch(
        "app.services.ai.tools.agent_delegate_tool.resolve_runnable_delegable_system_agents",
        AsyncMock(return_value=[]),
    ):
        result = await list_available_agents.ainvoke({})

    payload = json.loads(result)
    assert payload["scope"] == "delegable_system_agents"
    assert payload["items"] == []
    assert payload["count"] == 0
    assert "没有被分配" in payload["note"]
    assert "自己创建" in payload["note"]


@pytest.mark.asyncio
async def test_list_available_agents_empty_catalog_note_for_admin():
    """管理员没有「未分配」问题，空目录的 note 不能说成「你没有被分配」。"""
    from app.services.ai.tools.resource_catalog_tools import list_available_agents

    ctx = MagicMock()
    ctx.user_id = 1
    ctx.is_admin = True
    ctx.agent_id = "agent_current"

    db = AsyncMock()
    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=empty_result)
    db_cm = MagicMock()
    db_cm.__aenter__ = AsyncMock(return_value=db)
    db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.services.ai.tools.resource_catalog_tools.get_current_agent_context",
        return_value=ctx,
    ), patch(
        "app.services.ai.tools.resource_catalog_tools.AsyncSessionLocal",
        return_value=db_cm,
    ), patch(
        "app.services.ai.tools.agent_delegate_tool.resolve_runnable_delegable_system_agents",
        AsyncMock(return_value=[]),
    ):
        result = await list_available_agents.ainvoke({})

    payload = json.loads(result)
    assert payload["count"] == 0
    assert "没有可委派的系统级智能体" in payload["note"]
    assert "没有被分配" not in payload["note"]

