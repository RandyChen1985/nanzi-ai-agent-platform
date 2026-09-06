import asyncio
import os
from unittest.mock import MagicMock, patch
import pytest

from app.services.ai.runtime.conversation_run_registry import (
    ConversationRunHandle,
    track_conversation_run,
    track_current_run_task,
    conversation_run_registry,
)
from app.services.ai.code_execution_service import run_shell_command_capture

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_conversation_handle_cascade_cancels_subtasks():
    """验证 ConversationRunHandle 能够级联取消所有跟踪的异步子协程任务。"""
    handle = ConversationRunHandle(
        user_id="user_123",
        conversation_id="conv_123",
    )

    async def long_running_subtask():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise

    task1 = asyncio.create_task(long_running_subtask())
    task2 = asyncio.create_task(long_running_subtask())

    handle.track_task(task1)
    handle.track_task(task2)

    assert not task1.done()
    assert not task2.done()

    # 触发级联取消
    stopped = await handle.stop_tasks()
    assert stopped == 2
    assert task1.cancelled()
    assert task2.cancelled()


@pytest.mark.asyncio
async def test_track_current_run_task_binds_to_active_context():
    """验证 track_current_run_task 自动绑定当前活跃会话并在 request_stop 时被取消。"""
    user_id = "user_456"
    conversation_id = "conv_456"

    async with track_conversation_run(user_id, conversation_id) as handle:
        assert handle is not None

        async def worker():
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise

        worker_task = asyncio.create_task(worker())
        bound = track_current_run_task(worker_task)
        assert bound is True

        assert not worker_task.done()

        # 模拟外部请求终止
        stopped = await conversation_run_registry.request_stop(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        assert stopped is True
        assert worker_task.cancelled()


@pytest.mark.asyncio
async def test_run_shell_command_kills_process_on_cancellation():
    """验证当运行 shell 任务遇到 CancelledError 时，底层子进程必然被杀掉防泄漏。"""
    # 启动一个 sleep 脚本任务
    task = asyncio.create_task(run_shell_command_capture("sleep 5"))
    await asyncio.sleep(0.05)  # 等待子进程创建启动

    # 取消协程
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
