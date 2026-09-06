import asyncio
import sys
from pathlib import Path

import pytest

from app.services.ai.code_execution_service import (
    CodeExecutionValidationError,
    build_script_command,
    execute_code_stream,
    materialize_script,
    normalize_language,
    start_code_execution,
)


pytestmark = pytest.mark.no_infrastructure


def test_normalize_language_accepts_python_and_shell_aliases():
    assert normalize_language("python3") == "python"
    assert normalize_language("bash") == "bash"
    assert normalize_language("shell") == "bash"


def test_normalize_language_rejects_unrunnable_language():
    with pytest.raises(CodeExecutionValidationError, match="暂不支持"):
        normalize_language("javascript")


def test_build_script_command_uses_fixed_interpreters_and_unbuffered_python(tmp_path: Path):
    python_path = tmp_path / "run.py"
    shell_path = tmp_path / "run.sh"

    assert build_script_command("python", python_path) == [
        sys.executable,
        "-u",
        str(python_path),
    ]
    assert build_script_command("sh", shell_path) == ["/bin/sh", str(shell_path)]


def test_materialize_script_stays_under_session_workspace(tmp_path: Path):
    path = materialize_script(tmp_path, "python", "print('ok')")

    assert path.parent == tmp_path
    assert path.suffix == ".py"
    assert path.read_text(encoding="utf-8") == "print('ok')"
    path.unlink()


@pytest.mark.asyncio
async def test_execute_code_stream_emits_stdout_stderr_and_exit_code(tmp_path: Path):
    result = await execute_code_stream(
        language="python",
        code=(
            "import sys, time\n"
            "print('out', flush=True)\n"
            "time.sleep(0.02)\n"
            "print('err', file=sys.stderr, flush=True)\n"
            "raise SystemExit(3)\n"
        ),
        workspace=tmp_path,
        user_info={},
    ).collect()

    assert [(item.stream, item.text) for item in result.outputs] == [
        ("stdout", "out\n"),
        ("stderr", "err\n"),
    ]
    assert result.status == "failed"
    assert result.exit_code == 3


@pytest.mark.asyncio
async def test_shell_policy_rejection_happens_before_process_creation(monkeypatch, tmp_path: Path):
    async def fail_if_created(*args, **kwargs):
        raise AssertionError("process must not start")

    monkeypatch.setattr(
        "app.services.ai.code_execution_service.asyncio.create_subprocess_exec",
        fail_if_created,
    )
    result = await execute_code_stream(
        language="bash",
        code="rm -rf /",
        workspace=tmp_path,
        user_info={},
    ).collect()

    assert result.status == "blocked"
    assert result.error


@pytest.mark.asyncio
async def test_other_user_workspace_is_blocked_before_process_creation(monkeypatch, tmp_path: Path):
    async def fail_if_created(*args, **kwargs):
        raise AssertionError("process must not start")

    monkeypatch.setattr(
        "app.services.ai.code_execution_service.asyncio.create_subprocess_exec",
        fail_if_created,
    )
    workspace_root = tmp_path / "agent_workspaces"
    other_workspace = workspace_root / "bob__2" / "sessions" / "conv-2"
    other_workspace.mkdir(parents=True)

    result = await execute_code_stream(
        language="python",
        code="print('no')",
        workspace=other_workspace,
        workspace_root=workspace_root,
        user_info={"user_id": 1, "user_name": "alice"},
    ).collect()

    assert result.status == "blocked"
    assert "其他用户" in (result.error or "")


@pytest.mark.asyncio
async def test_timeout_terminates_execution(tmp_path: Path):
    result = await execute_code_stream(
        language="python",
        code="import time; time.sleep(1)",
        workspace=tmp_path,
        user_info={},
        timeout_seconds=0.05,
    ).collect()

    assert result.status == "timed_out"


@pytest.mark.asyncio
async def test_stop_is_idempotent_and_reports_stopped(tmp_path: Path):
    handle = start_code_execution(
        language="python",
        code="import time; time.sleep(10)",
        workspace=tmp_path,
        user_info={},
    )
    await asyncio.wait_for(handle.started.wait(), timeout=1)

    assert await handle.stop() is True
    assert await handle.stop() is False
    assert (await handle.result()).status == "stopped"


@pytest.mark.asyncio
async def test_stop_executions_for_conversation_only_stops_matching_run(tmp_path: Path):
    from app.services.ai import code_execution_service as code_exec

    matching = start_code_execution(
        language="python",
        code="import time; time.sleep(10)",
        workspace=tmp_path,
        user_info={},
        conversation_id="conv-a",
    )
    other = start_code_execution(
        language="python",
        code="import time; time.sleep(10)",
        workspace=tmp_path,
        user_info={},
        conversation_id="conv-b",
    )
    code_exec.register_execution(matching)
    code_exec.register_execution(other)
    try:
        await asyncio.wait_for(matching.started.wait(), timeout=1)
        await asyncio.wait_for(other.started.wait(), timeout=1)
        stopped = await code_exec.stop_executions_for_conversation(
            user_id="u1",
            conversation_id="conv-a",
        )
        assert stopped == 1
        assert (await matching.result()).status == "stopped"
        assert other._task.done() is False
        assert await other.stop() is True
    finally:
        code_exec.unregister_execution(matching)
        code_exec.unregister_execution(other)


@pytest.mark.asyncio
async def test_shared_shell_capture_returns_stdout_stderr_and_exit_code(tmp_path: Path):
    from app.services.ai.code_execution_service import run_shell_command_capture

    result = await run_shell_command_capture(
        "printf 'out'; printf 'err' >&2; exit 4",
        cwd=tmp_path,
        timeout_seconds=1,
    )

    assert result.stdout == "out"
    assert result.stderr == "err"
    assert result.exit_code == 4
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_shared_shell_capture_timeout_preserves_output(tmp_path: Path):
    from app.services.ai.code_execution_service import run_shell_command_capture

    result = await run_shell_command_capture(
        "printf 'before-timeout'; sleep 5", cwd=tmp_path, timeout_seconds=0.1,
    )
    assert result.timed_out is True
    assert result.stdout == 'before-timeout'
    assert result.exit_code is not None
