"""A1：工作区准备进度卡（workspace:prepare）的行为契约。"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner


pytestmark = pytest.mark.no_infrastructure


def _runner() -> AssistantAgentRunner:
    """只测进度旁路：跳过 __init__（它需要完整 config 与模型装配）。"""
    return object.__new__(AssistantAgentRunner)


def test_workspace_prepare_log_id_does_not_collide_with_sandbox_prewarm():
    """A1 的卡不能复用 workspace:sandbox。

    那个 id 由 LazySandboxWorkspaceProxy.ensure_ready 在首次 Bash 时改写状态，
    共用会让两张卡互相覆盖标题与 status。
    """
    assert AssistantAgentRunner.WORKSPACE_PREPARE_LOG_ID == "workspace:prepare"
    assert not AssistantAgentRunner.WORKSPACE_PREPARE_LOG_ID.startswith("workspace:sandbox")


def test_emit_workspace_prepare_log_is_noop_without_event_queue():
    """没有 event_queue（离线单测 / 非流式旁路）时必须静默跳过，绝不能抛。"""
    runner = _runner()

    with patch("app.core.context.get_current_agent_context", return_value=None):
        asyncio.run(runner._emit_workspace_prepare_log(status="pending"))

    with patch(
        "app.core.context.get_current_agent_context",
        return_value=SimpleNamespace(event_queue=None),
    ):
        asyncio.run(runner._emit_workspace_prepare_log(status="success"))


def test_emit_workspace_prepare_log_pushes_pending_then_success():
    runner = _runner()
    queue: asyncio.Queue = asyncio.Queue()

    async def _run():
        with patch(
            "app.core.context.get_current_agent_context",
            return_value=SimpleNamespace(event_queue=queue),
        ):
            await runner._emit_workspace_prepare_log(status="pending")
            await runner._emit_workspace_prepare_log(
                status="success",
                started_at=time.monotonic() - 0.25,
                details="工作区已就绪。",
            )
        return queue.get_nowait(), queue.get_nowait()

    pending, done = asyncio.run(_run())

    assert pending["type"] == "log"
    assert pending["id"] == "workspace:prepare"
    assert pending["parent_id"] == "preparation:auth_context_capability"
    assert pending["title"] == "工作区准备"
    assert pending["status"] == "pending"
    assert "execution_time_ms" not in pending

    # 同 id 才会被前端 merge 成一张卡（而不是并排两张）
    assert done["id"] == pending["id"]
    assert done["status"] == "success"
    assert done["details"] == "工作区已就绪。"
    assert done["execution_time_ms"] >= 200


def test_emit_workspace_prepare_log_ignores_delegated_subagents():
    """子代理不发这张卡：它们的事件已归到「委派智能体」容器，共用 id 会互相覆盖。"""
    runner = _runner()
    queue: asyncio.Queue = asyncio.Queue()

    async def _run():
        with patch(
            "app.core.context.get_current_agent_context",
            return_value=SimpleNamespace(
                event_queue=queue,
                delegation_depth=1,
                parent_conversation_id="main-conversation",
            ),
        ):
            await runner._emit_workspace_prepare_log(status="pending")
        return queue.empty()

    assert asyncio.run(_run()) is True
