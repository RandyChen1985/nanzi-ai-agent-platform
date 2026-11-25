from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Any

from app.services.ai.runtime.child_env import sandbox_child_env
from app.services.ai.runtime.conversation_run_registry import (
    get_current_run_handle,
    terminate_process_group,
)


def _subprocess_kwargs(cwd: str | None = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if cwd is not None:
        kwargs["cwd"] = cwd
    # 子进程与后端同 UID，不传 env 就等于把 DATABASE_URL / REDIS_PASSWORD /
    # ENCRYPTION_KEY 这类平台机密一并交给用户可控的 shell（一句 `env` 读走）。
    # 统一走白名单构造，HOME 指向会话工作区（见 child_env 模块 docstring）。
    kwargs["env"] = sandbox_child_env(workdir=cwd)
    if os.name == "posix":
        kwargs["start_new_session"] = True
    elif os.name == "nt":
        import subprocess

        kwargs["creationflags"] = getattr(
            subprocess,
            "CREATE_NO_WINDOW",
            0x08000000,
        )
    return kwargs


def _local_backend_cls():
    from agentscope.tool._builtin._backend import LocalBackend

    return LocalBackend


class CancellableLocalBackend(_local_backend_cls()):  # type: ignore[misc]
    """LocalBackend whose shell children die with the conversation run.

    AgentScope's LocalBackend leaves the subprocess alive if communicate() is
    cancelled. This subclass starts a new session (POSIX) and kills the process
    group on cancel/timeout.
    """

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        from agentscope.tool._builtin._backend import ExecResult

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **_subprocess_kwargs(cwd),
            )
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            return ExecResult(
                exit_code=127,
                stdout=b"",
                stderr=str(exc).encode("utf-8"),
            )

        handle = get_current_run_handle()
        if handle is not None:
            handle.track_process(process)
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
            return ExecResult(
                exit_code=process.returncode or 0,
                stdout=stdout,
                stderr=stderr,
            )
        except asyncio.TimeoutError:
            await terminate_process_group(process)
            with contextlib.suppress(Exception):
                await process.communicate()
            return ExecResult(exit_code=-1, stdout=b"", stderr=b"timed out")
        except asyncio.CancelledError:
            await terminate_process_group(process)
            raise
        finally:
            if handle is not None:
                handle.untrack_process(process)


def attach_cancellable_backend(tool: Any) -> Any:
    """Replace Bash._backend so shell children can be killed on Stop."""
    name = str(getattr(tool, "name", "") or "")
    if name != "Bash":
        return tool
    current = getattr(tool, "_backend", None)
    if isinstance(current, CancellableLocalBackend):
        return tool
    try:
        tool._backend = CancellableLocalBackend()
    except Exception:
        return tool
    return tool
