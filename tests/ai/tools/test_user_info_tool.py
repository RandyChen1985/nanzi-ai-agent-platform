"""Security and payload tests for the current-user profile system tool."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.tools.registry import ToolRegistry
from app.services.ai.tools.user_info_tools import get_myinfo


pytestmark = pytest.mark.no_infrastructure


def test_get_myinfo_is_implicit_read_only_and_has_no_userid_argument():
    tool_names = {getattr(tool, "name", "") for tool in ToolRegistry.get_system_implicit_tools()}
    assert "get_myinfo" in tool_names
    assert ToolRegistry._registry["get_myinfo"] is get_myinfo
    assert get_myinfo.args_schema.model_json_schema().get("properties", {}) == {}

    from app.services.ai.runtime.agentscope.tools import runtime_tool_spec_from_legacy_tool

    spec = runtime_tool_spec_from_legacy_tool(get_myinfo, source_type="system")
    assert spec.permission_scope == "read"
    assert spec.is_read_only is True


@pytest.mark.asyncio
async def test_get_myinfo_rejects_without_user_context():
    with patch(
        "app.services.ai.tools.user_info_tools.get_current_agent_context",
        return_value=None,
    ):
        result = await get_myinfo.ainvoke({})

    assert "无法识别当前用户" in result


@pytest.mark.asyncio
async def test_get_myinfo_rejects_explicit_userid_without_querying_db():
    db = AsyncMock()
    with patch(
        "app.services.ai.tools.user_info_tools.get_current_agent_context",
        return_value=SimpleNamespace(user_id=42),
    ), patch(
        "app.services.ai.tools.user_info_tools.AsyncSessionLocal",
        return_value=db,
    ):
        result = await get_myinfo.ainvoke({"userid": 99})

    assert "不接受 userid 参数" in result
    db.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_myinfo_reads_only_context_user_and_filters_secrets():
    ctx = SimpleNamespace(user_id=42)
    user = SimpleNamespace(
        id=42,
        user_name="alice",
        real_name="Alice",
        email="alice@example.com",
        role="user",
        dept_code="DEPT001",
        org_path="yovole/sh/dc1",
        extra_data=json.dumps({"phone": "13800000000", "title": "分析师"}),
        remark="测试用户",
        status=1,
        created_at=datetime(2026, 7, 28, 10, 0, 0),
        updated_at=datetime(2026, 7, 28, 11, 0, 0),
        api_key_encrypted="encrypted-secret",
        api_key_hash="hash-secret",
        password_hash="password-secret",
    )
    db = AsyncMock()
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = user
    db.execute.return_value = db_result
    db_cm = MagicMock()
    db_cm.__aenter__ = AsyncMock(return_value=db)
    db_cm.__aexit__ = AsyncMock(return_value=None)

    permissions = SimpleNamespace(
        roles=["user", "analyst"],
        permissions=SimpleNamespace(
            agents=["agent-1"],
            datasets=["dataset-1"],
            apis=[],
            metadata=[],
            menus=["menu-home"],
            elements=[],
            forbidden_tools=["exec_command"],
            forbidden_commands=[],
        ),
        details=None,
    )
    permission_service = MagicMock()
    permission_service.get_user_permissions = AsyncMock(return_value=permissions)

    with patch(
        "app.services.ai.tools.user_info_tools.get_current_agent_context",
        return_value=ctx,
    ), patch(
        "app.services.ai.tools.user_info_tools.AsyncSessionLocal",
        return_value=db_cm,
    ), patch(
        "app.services.ai.tools.user_info_tools.PermissionService",
        return_value=permission_service,
    ):
        result = await get_myinfo.ainvoke({})

    payload = json.loads(result)
    assert payload["user"]["id"] == 42
    assert payload["user"]["user_name"] == "alice"
    assert payload["user"]["email"] == "alice@example.com"
    assert payload["user"]["extra_data"] == {"phone": "13800000000", "title": "分析师"}
    assert payload["permissions"]["roles"] == ["user", "analyst"]
    assert payload["permissions"]["permissions"]["datasets"] == ["dataset-1"]
    assert "api_key_encrypted" not in result
    assert "api_key_hash" not in result
    assert "password_hash" not in result

    statement = db.execute.await_args.args[0]
    assert 42 in statement.compile().params.values()
    permission_service.get_user_permissions.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_get_myinfo_rejects_disabled_context_user():
    user = SimpleNamespace(id=42, status=0)
    db = AsyncMock()
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = user
    db.execute.return_value = db_result
    db_cm = MagicMock()
    db_cm.__aenter__ = AsyncMock(return_value=db)
    db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.services.ai.tools.user_info_tools.get_current_agent_context",
        return_value=SimpleNamespace(user_id=42),
    ), patch(
        "app.services.ai.tools.user_info_tools.AsyncSessionLocal",
        return_value=db_cm,
    ):
        result = await get_myinfo.ainvoke({})

    assert "已禁用" in result

