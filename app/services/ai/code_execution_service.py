from __future__ import annotations

import os
import asyncio
import contextlib
import signal
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from app.services.ai.runtime.workspace_access_policy import (
    WorkspaceAccessDenied,
    ensure_private_workspace_dirs,
    user_workspace_root,
    validate_execution_workspace,
)


NormalizedLanguage = Literal["python", "sh", "bash"]


class CodeExecutionValidationError(ValueError):
    """Raised when a code-execution request is outside the supported contract."""


SUPPORTED_LANGUAGES = frozenset({"python", "python3", "shell", "sh", "bash"})
DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_OUTPUT_BYTES = 100 * 1024
ExecutionStatus = Literal["succeeded", "failed", "stopped", "timed_out", "blocked"]
OutputStream = Literal["stdout", "stderr"]


def normalize_language(value: str) -> NormalizedLanguage:
    normalized = (value or "").strip().lower()
    if normalized not in SUPPORTED_LANGUAGES:
        raise CodeExecutionValidationError(f"暂不支持运行语言: {value or '未指定'}")
    if normalized in {"python", "python3"}:
        return "python"
    if normalized == "sh":
        return "sh"
    return "bash"


def build_script_command(language: str, script_path: Path) -> list[str]:
    normalized = normalize_language(language)
    path = str(script_path)
    if normalized == "python":
        return [sys.executable, "-u", path]
    if normalized == "sh":
        return ["/bin/sh", path]
    return ["/bin/bash", path]


def materialize_script(workspace: Path, language: str, code: str) -> Path:
    normalized = normalize_language(language)
    workspace_path = Path(workspace).expanduser().resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)
    suffix = ".py" if normalized == "python" else ".sh"
    fd, raw_path = tempfile.mkstemp(
        prefix=".nanzi-code-run-",
        suffix=suffix,
        dir=str(workspace_path),
        text=True,
    )
    path = Path(raw_path)
    try:
        if path.parent != workspace_path:
            raise CodeExecutionValidationError("执行脚本路径必须位于当前会话工作区内")
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(code)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise
    return path


@dataclass(frozen=True)
class ExecutionOutput:
    stream: OutputStream
    text: str
    sequence: int


@dataclass(frozen=True)
class CodeExecutionResult:
    status: ExecutionStatus
    outputs: list[ExecutionOutput] = field(default_factory=list)
    exit_code: int | None = None
    elapsed_ms: int = 0
    truncated: bool = False
    error: str | None = None


@dataclass(frozen=True)
class CodeExecutionEvent:
    name: str
    data: dict[str, Any]


@dataclass(frozen=True)
class ShellCaptureResult:
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool = False
    truncated: bool = False


async def run_shell_command_capture(
    command: str,
    *,
    cwd: Path | str | None = None,
    timeout_seconds: float = 30.0,
) -> ShellCaptureResult:
    """Run a legacy shell command through the same bounded process primitive."""
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd) if cwd is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    timed_out = False
    communicate_task = asyncio.create_task(process.communicate())
    try:
        stdout, stderr = await asyncio.wait_for(
            asyncio.shield(communicate_task), timeout=max(0.01, float(timeout_seconds))
        )
    except asyncio.TimeoutError:
        timed_out = True
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(asyncio.shield(communicate_task), timeout=1.0)
        except asyncio.TimeoutError:
            try:
                if os.name == "posix":
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            await communicate_task
        stdout, stderr = communicate_task.result()
    except BaseException:
        try:
            if process.returncode is None:
                if os.name == "posix":
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:
                    process.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass
        if not communicate_task.done():
            communicate_task.cancel()
            await asyncio.gather(communicate_task, return_exceptions=True)
        raise

    combined_bytes = len(stdout) + len(stderr)
    truncated = combined_bytes > MAX_OUTPUT_BYTES
    if truncated:
        stdout = stdout[:MAX_OUTPUT_BYTES]
        stderr = stderr[: max(0, MAX_OUTPUT_BYTES - len(stdout))]
    return ShellCaptureResult(
        stdout=stdout.decode("utf-8", errors="ignore"),
        stderr=stderr.decode("utf-8", errors="ignore"),
        exit_code=process.returncode,
        timed_out=timed_out,
        truncated=truncated,
    )


def _permission_is_blocking(decision: Any) -> bool:
    if decision is None:
        return False
    behavior = getattr(decision, "behavior", None)
    value = getattr(behavior, "value", behavior)
    return str(value).lower().endswith("deny") or str(value).lower() == "denied"


async def _policy_block_reason(
    *, language: NormalizedLanguage, code: str, workspace: Path, user_info: dict[str, Any]
) -> str | None:
    user_id = user_info.get("user_id") or user_info.get("id")
    if user_id is not None:
        from app.services.ai.runtime.agentscope.tools import (
            _enforce_command_blacklist,
            _enforce_tool_forbidden,
        )

        tool_decision = await _enforce_tool_forbidden("exec_command", user_id)
        if _permission_is_blocking(tool_decision):
            return str(getattr(tool_decision, "message", "安全策略拦截：禁止执行该代码。"))

        command_decision = await _enforce_command_blacklist(
            "exec_command", {"command": code}, user_id
        )
        if _permission_is_blocking(command_decision):
            return str(getattr(command_decision, "message", "安全策略拦截：命令被禁止。"))

    if language in {"sh", "bash"}:
        from app.services.ai.runtime.shell_deletion_policy import assess_shell_deletion

        assessment = assess_shell_deletion(code, cwd=workspace)
        if assessment.action != "pass":
            return f"安全策略拦截：{assessment.reason}"
    return None


class CodeExecutionHandle:
    def __init__(
        self,
        *,
        language: str,
        code: str,
        workspace: Path,
        workspace_root: Path | None = None,
        user_info: dict[str, Any],
        conversation_id: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.execution_id = uuid.uuid4().hex
        self.language = normalize_language(language)
        self.code = code
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace_root = Path(workspace_root).expanduser().resolve() if workspace_root else None
        self.user_info = user_info
        self.user_id = user_info.get("user_id") or user_info.get("id")
        self.conversation_id = conversation_id
        self.timeout_seconds = max(0.01, float(timeout_seconds))
        self.started = asyncio.Event()
        self._events: asyncio.Queue[CodeExecutionEvent | None] = asyncio.Queue()
        self._result_future: asyncio.Future[CodeExecutionResult] = asyncio.get_running_loop().create_future()
        self._process: asyncio.subprocess.Process | None = None
        self._script_path: Path | None = None
        self._stop_requested = False
        self._sequence = 0
        self._outputs: list[ExecutionOutput] = []
        self._output_bytes = 0
        self._truncated = False
        self._task = asyncio.create_task(self._run())

    async def _emit(self, name: str, data: dict[str, Any]) -> None:
        self._events.put_nowait(CodeExecutionEvent(name=name, data=data))

    async def _emit_output(self, stream: OutputStream, chunk: bytes) -> None:
        if not chunk:
            return
        remaining = MAX_OUTPUT_BYTES - self._output_bytes
        if remaining <= 0:
            self._truncated = True
            return
        text = chunk[:remaining].decode("utf-8", errors="replace")
        self._output_bytes += len(chunk[:remaining])
        if len(chunk) > remaining:
            self._truncated = True
        output = ExecutionOutput(stream=stream, text=text, sequence=self._sequence)
        self._sequence += 1
        self._outputs.append(output)
        await self._emit(
            "output",
            {"stream": stream, "chunk": text, "sequence": output.sequence},
        )

    async def _drain(self, stream: asyncio.StreamReader, name: OutputStream) -> None:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return
            await self._emit_output(name, chunk)

    async def _terminate_process(self) -> None:
        process = self._process
        if process is None or process.returncode is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                if os.name == "posix":
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:
                    process.kill()
            await process.wait()

    async def _run(self) -> None:
        started_at = time.perf_counter()
        result: CodeExecutionResult | None = None
        try:
            if self.user_id is not None:
                from app.services.ai.runtime.agentscope.workspace import default_workspace_root

                root = self.workspace_root or Path(default_workspace_root()).resolve()
                self.workspace = validate_execution_workspace(
                    self.workspace,
                    workspace_root=root,
                    user_info=self.user_info,
                )
                ensure_private_workspace_dirs(
                    user_workspace_root(root, self.user_info),
                    self.workspace,
                )
            else:
                self.workspace.mkdir(parents=True, exist_ok=True)

            blocked_reason = await _policy_block_reason(
                language=self.language,
                code=self.code,
                workspace=self.workspace,
                user_info=self.user_info,
            )
            if blocked_reason:
                self.started.set()
                await self._emit("error", {"code": "blocked", "message": blocked_reason})
                result = CodeExecutionResult(
                    status="blocked",
                    elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                    error=blocked_reason,
                )
                return

            if self._stop_requested:
                self.started.set()
                result = CodeExecutionResult(
                    status="stopped",
                    elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                )
                await self._emit("stopped", {"status": "stopped", "exit_code": None})
                return

            self._script_path = materialize_script(self.workspace, self.language, self.code)
            command = build_script_command(self.language, self._script_path)
            process_kwargs: dict[str, Any] = {
                "cwd": str(self.workspace),
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
            }
            if os.name == "posix":
                process_kwargs["start_new_session"] = True
            self._process = await asyncio.create_subprocess_exec(*command, **process_kwargs)
            from app.services.ai.runtime.conversation_run_registry import get_current_run_handle
            current_handle = get_current_run_handle()
            if current_handle is not None:
                current_handle.track_process(self._process)

            self.started.set()
            await self._emit(
                "started",
                {
                    "execution_id": self.execution_id,
                    "language": self.language,
                    "started_at": time.time(),
                },
            )
            assert self._process.stdout is not None
            assert self._process.stderr is not None
            drain_tasks = [
                asyncio.create_task(self._drain(self._process.stdout, "stdout")),
                asyncio.create_task(self._drain(self._process.stderr, "stderr")),
            ]
            try:
                await asyncio.wait_for(asyncio.gather(*drain_tasks), timeout=self.timeout_seconds)
                return_code = await self._process.wait()
            except asyncio.TimeoutError:
                await self._terminate_process()
                result = CodeExecutionResult(
                    status="timed_out",
                    outputs=list(self._outputs),
                    exit_code=self._process.returncode,
                    elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                    truncated=self._truncated,
                    error=f"执行超时（{self.timeout_seconds:g} 秒）",
                )
                await self._emit("timeout", {"message": result.error})
                return
            except BaseException:
                await self._terminate_process()
                raise
            finally:
                if current_handle is not None:
                    current_handle.untrack_process(self._process)
                for task in drain_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*drain_tasks, return_exceptions=True)

            status: ExecutionStatus = "stopped" if self._stop_requested else (
                "succeeded" if return_code == 0 else "failed"
            )
            result = CodeExecutionResult(
                status=status,
                outputs=list(self._outputs),
                exit_code=return_code,
                elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                truncated=self._truncated,
            )
            await self._emit(
                "stopped" if status == "stopped" else "finished",
                {
                    "status": status,
                    "exit_code": return_code,
                    "elapsed_ms": result.elapsed_ms,
                    "truncated": self._truncated,
                },
            )
        except asyncio.CancelledError:
            self._stop_requested = True
            await self._terminate_process()
            raise
        except WorkspaceAccessDenied as exc:
            self.started.set()
            result = CodeExecutionResult(
                status="blocked",
                outputs=list(self._outputs),
                elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                error=str(exc),
            )
            await self._emit("error", {"code": "workspace_access_denied", "message": str(exc)})
        except Exception as exc:
            result = CodeExecutionResult(
                status="failed",
                outputs=list(self._outputs),
                elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                truncated=self._truncated,
                error=str(exc),
            )
            await self._emit("error", {"code": "execution_error", "message": str(exc)})
        finally:
            if self._script_path is not None:
                self._script_path.unlink(missing_ok=True)
            if result is None:
                result = CodeExecutionResult(
                    status="stopped" if self._stop_requested else "failed",
                    outputs=list(self._outputs),
                    elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                    truncated=self._truncated,
                    error="执行未产生终态结果",
                )
            if not self._result_future.done():
                self._result_future.set_result(result)
            self._events.put_nowait(None)

    async def stop(self) -> bool:
        if self._task.done() or self._stop_requested:
            return False
        self._stop_requested = True
        await self._terminate_process()
        return True

    async def result(self) -> CodeExecutionResult:
        return await self._result_future

    async def events(self) -> AsyncIterator[CodeExecutionEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def collect(self) -> CodeExecutionResult:
        return await self.result()


def start_code_execution(**kwargs: Any) -> CodeExecutionHandle:
    return CodeExecutionHandle(**kwargs)


def execute_code_stream(**kwargs: Any) -> CodeExecutionHandle:
    return start_code_execution(**kwargs)


_EXECUTION_REGISTRY: dict[str, CodeExecutionHandle] = {}


def register_execution(handle: CodeExecutionHandle) -> None:
    _EXECUTION_REGISTRY[handle.execution_id] = handle


def get_execution(execution_id: str) -> CodeExecutionHandle | None:
    return _EXECUTION_REGISTRY.get(execution_id)


def unregister_execution(handle_or_id: CodeExecutionHandle | str) -> None:
    execution_id = (
        handle_or_id.execution_id if isinstance(handle_or_id, CodeExecutionHandle) else handle_or_id
    )
    _EXECUTION_REGISTRY.pop(execution_id, None)


def _same_user(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return True
    return str(left) == str(right)


async def stop_executions_for_conversation(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
) -> int:
    """Stop canvas/script runs belonging to this conversation."""
    if not conversation_id:
        return 0
    stopped = 0
    for handle in list(_EXECUTION_REGISTRY.values()):
        if handle.conversation_id != conversation_id:
            continue
        if not _same_user(handle.user_id, user_id):
            continue
        if await handle.stop():
            stopped += 1
    return stopped
