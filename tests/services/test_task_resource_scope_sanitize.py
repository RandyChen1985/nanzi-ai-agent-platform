"""定时任务 resource_scope 服务端收敛契约。

任务 config 由客户端自由提交，必须像会话资源范围一样在落库前按任务所有者的
授权目录收敛，避免通过 resource_scope 注入越权的数据集 / 知识库 / 技能 / MCP。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import tasks as tasks_endpoint

pytestmark = pytest.mark.no_infrastructure


def _authorized_scope():
    return {
        "project_name": "",
        "datasets": [{"id": "ds-allowed", "name": "允许的数据集"}],
        "knowledge_bases": [],
        "skills": [],
        "mcp_tools": [],
    }


def _empty_scope():
    return {
        "project_name": "",
        "datasets": [],
        "knowledge_bases": [],
        "skills": [],
        "mcp_tools": [],
    }


@pytest.mark.asyncio
async def test_sanitize_drops_unauthorized_scope_items():
    db = AsyncMock()
    owner = {"user_id": 7, "user_name": "alice", "role": "user"}
    raw_config = {
        "approval_mode": "allow",
        "resource_scope": {
            "datasets": [{"id": "ds-allowed"}, {"id": "ds-forbidden"}],
            "knowledge_bases": [{"id": "kb-forbidden"}],
        },
    }

    with patch.object(
        tasks_endpoint,
        "normalize_resource_scope_for_user",
        AsyncMock(return_value=_authorized_scope()),
    ) as normalizer:
        sanitized = await tasks_endpoint._sanitize_task_config(db, owner, raw_config)

    normalizer.assert_awaited_once()
    assert normalizer.await_args.args[1] is owner
    assert sanitized["resource_scope"]["datasets"] == [{"id": "ds-allowed", "name": "允许的数据集"}]
    assert sanitized["resource_scope"]["knowledge_bases"] == []
    assert sanitized["approval_mode"] == "allow"
    # 不得就地修改调用方传入的 config
    assert raw_config["resource_scope"]["knowledge_bases"] == [{"id": "kb-forbidden"}]


@pytest.mark.asyncio
async def test_sanitize_removes_scope_when_nothing_authorized():
    db = AsyncMock()
    owner = {"user_id": 7, "user_name": "alice", "role": "user"}
    raw_config = {"resource_scope": {"skills": [{"id": "not-mine"}]}, "model": "deepseek-v3"}

    with patch.object(
        tasks_endpoint,
        "normalize_resource_scope_for_user",
        AsyncMock(return_value=_empty_scope()),
    ):
        sanitized = await tasks_endpoint._sanitize_task_config(db, owner, raw_config)

    assert "resource_scope" not in sanitized
    assert sanitized["model"] == "deepseek-v3"


@pytest.mark.asyncio
async def test_sanitize_skips_config_without_scope():
    db = AsyncMock()
    owner = {"user_id": 7, "user_name": "alice", "role": "user"}

    with patch.object(
        tasks_endpoint, "normalize_resource_scope_for_user", AsyncMock()
    ) as normalizer:
        assert await tasks_endpoint._sanitize_task_config(db, owner, None) is None
        assert await tasks_endpoint._sanitize_task_config(
            db, owner, {"approval_mode": "ask"}
        ) == {"approval_mode": "ask"}

    normalizer.assert_not_awaited()


@pytest.mark.asyncio
async def test_sanitize_normalizes_task_reasoning_overrides():
    db = AsyncMock()
    owner = {"user_id": 7, "user_name": "alice", "role": "user"}

    sanitized = await tasks_endpoint._sanitize_task_config(
        db,
        owner,
        {
            "thinking_enable": False,
            "reasoning_effort": "high",
            "untrusted": {"nested": True},
        },
    )

    assert sanitized == {
        "thinking_enable": False,
        "untrusted": {"nested": True},
    }


@pytest.mark.asyncio
async def test_create_task_persists_sanitized_config():
    db = AsyncMock()
    created = MagicMock()

    with patch.object(
        tasks_endpoint, "_task_owner_info", AsyncMock(return_value={"user_id": 7})
    ), patch.object(
        tasks_endpoint,
        "_sanitize_task_config",
        AsyncMock(return_value={"resource_scope": _authorized_scope()}),
    ), patch.object(
        # 智能体绑定校验由 test_task_agent_validation.py 覆盖，这里聚焦范围收敛。
        tasks_endpoint, "_ensure_agent_selectable", AsyncMock()
    ), patch.object(
        tasks_endpoint.TaskCenterService, "create_task", AsyncMock(return_value=created)
    ) as create, patch.object(
        tasks_endpoint.TaskResponse, "from_orm", MagicMock(return_value=None)
    ):
        await tasks_endpoint.create_task(
            tasks_endpoint.TaskCreate(
                name="巡检",
                agent_id="agent-1",
                cron_expr="0 8 * * *",
                prompt="跑一下",
                config={"resource_scope": {"datasets": [{"id": "ds-forbidden"}]}},
            ),
            user_info={"user_id": 7, "role": "user"},
            db=db,
        )

    assert create.await_args.kwargs["config"] == {"resource_scope": _authorized_scope()}


@pytest.mark.asyncio
async def test_update_task_validates_against_task_owner_not_caller():
    """管理员代改他人任务时，范围要按任务所有者校验，不能按管理员权限放行。"""
    db = AsyncMock()
    task = MagicMock()
    task.user_id = 42

    with patch.object(
        tasks_endpoint.TaskCenterService, "get_task", AsyncMock(return_value=task)
    ), patch.object(
        tasks_endpoint, "_task_owner_info", AsyncMock(return_value={"user_id": 42})
    ) as owner_info, patch.object(
        tasks_endpoint, "_sanitize_task_config", AsyncMock(return_value={})
    ), patch.object(
        tasks_endpoint.TaskCenterService, "update_task", AsyncMock(return_value=task)
    ) as update, patch.object(
        tasks_endpoint.TaskResponse, "from_orm", MagicMock(return_value=None)
    ):
        await tasks_endpoint.update_task(
            1,
            tasks_endpoint.TaskUpdate(config={"resource_scope": {"datasets": [{"id": "x"}]}}),
            user_info={"user_id": 1, "role": "admin"},
            db=db,
        )

    assert owner_info.await_args.args[1] == 42
    assert update.await_args.args[2]["config"] == {}


@pytest.mark.asyncio
async def test_chat_conversation_scope_reuses_shared_normalizer():
    """会话与任务共用同一套收敛逻辑，避免两侧权限语义漂移。"""
    from app.api.v1.endpoints import chat as chat_endpoint

    db = AsyncMock()
    with patch.object(
        chat_endpoint,
        "normalize_resource_scope_for_user",
        AsyncMock(return_value=_empty_scope()),
    ) as normalizer:
        await chat_endpoint._normalize_conversation_resource_scope(db, {"user_id": 7}, {})

    normalizer.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_task_without_config_does_not_touch_scope():
    db = AsyncMock()
    task = MagicMock()
    task.user_id = 7

    with patch.object(
        tasks_endpoint.TaskCenterService, "get_task", AsyncMock(return_value=task)
    ), patch.object(
        tasks_endpoint, "_sanitize_task_config", AsyncMock()
    ) as sanitize, patch.object(
        tasks_endpoint.TaskCenterService, "update_task", AsyncMock(return_value=task)
    ), patch.object(
        tasks_endpoint.TaskResponse, "from_orm", MagicMock(return_value=None)
    ):
        await tasks_endpoint.update_task(
            1,
            tasks_endpoint.TaskUpdate(status=0),
            user_info={"user_id": 7, "role": "user"},
            db=db,
        )

    sanitize.assert_not_awaited()


@pytest.mark.asyncio
async def test_sanitize_keeps_trimmed_task_constraints():
    db = AsyncMock()
    owner = {"user_id": 7, "user_name": "alice", "role": "user"}

    sanitized = await tasks_endpoint._sanitize_task_config(
        db, owner, {"task_constraints": "  禁止图表；用列表呈现。  "}
    )

    assert sanitized["task_constraints"] == "禁止图表；用列表呈现。"


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, "", "   ", "\n\t "])
async def test_sanitize_drops_blank_task_constraints(raw):
    db = AsyncMock()
    owner = {"user_id": 7, "user_name": "alice", "role": "user"}

    sanitized = await tasks_endpoint._sanitize_task_config(
        db, owner, {"task_constraints": raw, "approval_mode": "allow"}
    )

    assert "task_constraints" not in sanitized
    assert sanitized["approval_mode"] == "allow"


@pytest.mark.asyncio
async def test_sanitize_rejects_overlong_task_constraints():
    db = AsyncMock()
    owner = {"user_id": 7, "user_name": "alice", "role": "user"}
    overlong = "禁" * (tasks_endpoint.TASK_CONSTRAINTS_MAX_CHARS + 1)

    with pytest.raises(HTTPException) as exc_info:
        await tasks_endpoint._sanitize_task_config(db, owner, {"task_constraints": overlong})

    assert exc_info.value.status_code == 400
    assert str(tasks_endpoint.TASK_CONSTRAINTS_MAX_CHARS) in exc_info.value.detail


@pytest.mark.asyncio
async def test_sanitize_accepts_max_length_task_constraints():
    db = AsyncMock()
    owner = {"user_id": 7, "user_name": "alice", "role": "user"}
    at_limit = "禁" * tasks_endpoint.TASK_CONSTRAINTS_MAX_CHARS

    sanitized = await tasks_endpoint._sanitize_task_config(db, owner, {"task_constraints": at_limit})

    assert len(sanitized["task_constraints"]) == tasks_endpoint.TASK_CONSTRAINTS_MAX_CHARS
