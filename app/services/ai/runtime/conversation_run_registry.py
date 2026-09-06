from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

from app.services.ai.conversation_identity import require_user_id

_current_run: ContextVar[ConversationRunHandle | None] = ContextVar(
    "conversation_run_handle",
    default=None,
)


def _run_key(user_id: str | int | None, conversation_id: str) -> tuple[str, str]:
    uid = require_user_id(user_id)
    return uid, conversation_id


async def terminate_process_group(process: Any, *, grace_seconds: float = 1.0) -> None:
    """SIGTERM then SIGKILL a subprocess, using the process group when possible."""
    if process is None:
        return
    returncode = getattr(process, "returncode", None)
    if returncode is not None:
        return
    pid = getattr(process, "pid", None)
    try:
        if os.name == "posix" and pid:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        else:
            terminate = getattr(process, "terminate", None)
            if terminate is not None:
                terminate()
    except (ProcessLookupError, PermissionError, OSError):
        return
    wait = getattr(process, "wait", None)
    if wait is None:
        return
    try:
        await asyncio.wait_for(wait(), timeout=grace_seconds)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            if os.name == "posix" and pid:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            else:
                kill = getattr(process, "kill", None)
                if kill is not None:
                    kill()
        with contextlib.suppress(Exception):
            await wait()
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            if os.name == "posix" and pid:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            else:
                kill = getattr(process, "kill", None)
                if kill is not None:
                    kill()
        raise


@dataclass
class ConversationRunHandle:
    user_id: str
    conversation_id: str
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[Any] | None = None
    _processes: set[Any] = field(default_factory=set)
    _tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    persistence_task: asyncio.Task[Any] | None = None
    finished: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def track_process(self, process: Any) -> None:
        if process is not None:
            self._processes.add(process)

    def untrack_process(self, process: Any) -> None:
        self._processes.discard(process)

    def track_task(self, task: asyncio.Task[Any]) -> None:
        if task is not None and not task.done():
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    def untrack_task(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)

    async def stop_children(self) -> int:
        stopped = 0
        for process in list(self._processes):
            await terminate_process_group(process)
            self._processes.discard(process)
            stopped += 1
        return stopped

    async def stop_tasks(self) -> int:
        stopped = 0
        current = asyncio.current_task()
        pending: list[asyncio.Task[Any]] = []
        for task in list(self._tasks):
            if task is not current and not task.done():
                task.cancel()
                pending.append(task)
                stopped += 1
            self._tasks.discard(task)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return stopped

    async def request_stop(self, *, cancel_task: bool = True) -> None:
        self.cancel_event.set()
        await self.stop_children()
        await self.stop_tasks()
        task = self.task
        if not cancel_task or task is None or task.done():
            return
        current = asyncio.current_task()
        if task is not current:
            task.cancel()

    async def wait_finished(self, timeout: float = 10.0) -> None:
        """等待取消任务完成收尾，尤其是后台历史持久化。"""
        try:
            await asyncio.wait_for(self.finished.wait(), timeout=max(0.1, timeout))
        except asyncio.TimeoutError:
            logger.warning(
                "[ConversationRunRegistry] timed out waiting for run finalization conversation=%s",
                self.conversation_id,
            )


class ConversationRunRegistry:
    """In-process map of the active generation task per conversation."""

    def __init__(self) -> None:
        self._runs: dict[tuple[str, str], ConversationRunHandle] = {}

    def clear(self) -> None:
        self._runs.clear()

    def get(
        self,
        user_id: str | int | None,
        conversation_id: str | None,
    ) -> ConversationRunHandle | None:
        if not conversation_id:
            return None
        return self._runs.get(_run_key(user_id, conversation_id))

    def register(
        self,
        user_id: str | int | None,
        conversation_id: str,
        *,
        task: asyncio.Task[Any] | None = None,
    ) -> ConversationRunHandle:
        handle = ConversationRunHandle(
            user_id=_run_key(user_id, conversation_id)[0],
            conversation_id=conversation_id,
            task=task or asyncio.current_task(),
        )
        self._runs[_run_key(user_id, conversation_id)] = handle
        return handle

    def unregister(self, handle: ConversationRunHandle | None) -> None:
        if handle is None:
            return
        key = _run_key(handle.user_id, handle.conversation_id)
        if self._runs.get(key) is handle:
            self._runs.pop(key, None)

    async def request_stop(
        self,
        *,
        user_id: str | int | None,
        conversation_id: str | None,
    ) -> bool:
        handle = self.get(user_id, conversation_id)
        if handle is None:
            return False
        await handle.request_stop(cancel_task=True)
        logger.info(
            "[ConversationRunRegistry] stop requested conversation=%s user=%s",
            conversation_id,
            user_id,
        )
        return True


conversation_run_registry = ConversationRunRegistry()


def get_current_run_handle() -> ConversationRunHandle | None:
    return _current_run.get()


@asynccontextmanager
async def track_conversation_run(
    user_id: str | int | None,
    conversation_id: str | None,
) -> AsyncIterator[ConversationRunHandle | None]:
    """Bind the current asyncio task as the cancellable run for this conversation."""
    if not conversation_id:
        yield None
        return
    handle = conversation_run_registry.register(user_id, conversation_id)
    token = _current_run.set(handle)
    try:
        yield handle
    finally:
        _current_run.reset(token)
        if handle.persistence_task is not None:
            try:
                await asyncio.shield(handle.persistence_task)
            except asyncio.CancelledError:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.shield(handle.persistence_task)
            except Exception:
                logger.exception(
                    "[ConversationRunRegistry] persistence finalization failed conversation=%s",
                    handle.conversation_id,
                )
        with contextlib.suppress(Exception):
            await handle.stop_children()
        with contextlib.suppress(Exception):
            await handle.stop_tasks()
        handle.finished.set()
        conversation_run_registry.unregister(handle)


def track_current_run_task(task: asyncio.Task[Any]) -> bool:
    """若当前协程运行在某会话上下文中，将 task 绑定至该会话生命周期，支持级联取消。"""
    handle = get_current_run_handle()
    if handle is not None:
        handle.track_task(task)
        return True
    return False
