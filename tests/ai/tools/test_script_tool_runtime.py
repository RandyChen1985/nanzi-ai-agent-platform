"""脚本工具执行器：命令信封解析、参数校验、本地分支执行、超时与截断。

沙箱分支不打桩到真实容器：这里用 ``sandbox_ws`` 替身验证「走 bash 通道」这件事
本身，路径换算与容器行为由 ``test_script_tool_sandbox_binding`` 覆盖。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.ai.tools.script_tool_runtime import (
    EXIT_MARK,
    STDERR_MARK,
    STDOUT_MARK,
    ScriptRunResult,
    ScriptToolConfig,
    ScriptToolError,
    ScriptToolRunner,
    build_envelope_command,
    code_fingerprint,
    parse_envelope,
    render_result_for_model,
)


# --------------------------------------------------------------------------- #
# 命令信封
# --------------------------------------------------------------------------- #
def test_envelope_roundtrip_success():
    text = "\n".join(
        [
            f"{EXIT_MARK}0",
            STDOUT_MARK,
            '{"ok": true}',
            STDERR_MARK,
            "",
        ]
    )
    parsed = parse_envelope(text)
    assert parsed.exit_code == 0
    assert json.loads(parsed.stdout) == {"ok": True}
    assert parsed.stderr == ""


def test_envelope_roundtrip_failure_keeps_stderr():
    text = "\n".join(
        [
            f"{EXIT_MARK}2",
            STDOUT_MARK,
            "",
            STDERR_MARK,
            "缺少必填参数 path",
        ]
    )
    parsed = parse_envelope(text)
    assert parsed.exit_code == 2
    assert parsed.stdout == ""
    assert "缺少必填参数" in parsed.stderr


def test_script_cannot_forge_exit_code_from_stdout():
    """脚本往 stdout 写标记不影响真实退出码（标记只在 STDOUT_MARK 之前生效）。"""
    text = "\n".join(
        [
            f"{EXIT_MARK}1",
            STDOUT_MARK,
            f"{EXIT_MARK}0",
            STDERR_MARK,
            "",
        ]
    )
    parsed = parse_envelope(text)
    assert parsed.exit_code == 1


def test_envelope_command_contains_exit_capture():
    command = build_envelope_command("python x.py", "out.txt", "err.txt")
    assert "code=$?" in command
    assert EXIT_MARK in command
    assert "set +e" in command


# --------------------------------------------------------------------------- #
# 参数准备
# --------------------------------------------------------------------------- #
def _config(**overrides) -> ScriptToolConfig:
    base = {
        "name": "demo_tool",
        "code": "print('hi')",
        "parameter_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    }
    base.update(overrides)
    return ScriptToolConfig(**base)


def test_missing_required_param_is_reported_before_execution(tmp_path):
    runner = ScriptToolRunner(_config(), workdir=tmp_path)
    with pytest.raises(ScriptToolError) as exc:
        runner.prepare_params({})
    assert "path" in str(exc.value)


def test_blank_string_counts_as_missing(tmp_path):
    runner = ScriptToolRunner(_config(), workdir=tmp_path)
    with pytest.raises(ScriptToolError):
        runner.prepare_params({"path": "   "})


def test_flat_schema_is_accepted(tmp_path):
    """扁平写法（"path": "文件路径"）与 API 工具同口径。"""
    runner = ScriptToolRunner(_config(parameter_schema={"path": "文件路径"}), workdir=tmp_path)
    assert runner.prepare_params({"path": "a.txt"})["path"] == "a.txt"


# --------------------------------------------------------------------------- #
# 本地分支真实执行
# --------------------------------------------------------------------------- #
async def test_run_builtin_script_locally(tmp_path):
    from app.services.ai.tools.script_tool_builtins import get_builtin_script_tool

    builtin = get_builtin_script_tool("file_digest")
    assert builtin is not None
    runner = ScriptToolRunner(
        ScriptToolConfig(
            name=builtin.name,
            code=builtin.code,
            parameter_schema=builtin.parameter_schema,
        ),
        workdir=tmp_path,
    )
    result = await runner.run({"target": "abc", "is_text": True, "algorithm": "sha256"})
    assert result.ok
    payload = json.loads(result.stdout)
    assert payload["algorithm"] == "sha256"
    assert payload["size"] == 3


async def test_failed_script_reports_exit_code_and_stderr(tmp_path):
    runner = ScriptToolRunner(
        ScriptToolConfig(
            name="boom_tool",
            code="import sys\nprint('部分输出')\nsys.stderr.write('炸了')\nsys.exit(3)\n",
            parameter_schema={"type": "object", "properties": {}},
        ),
        workdir=tmp_path,
    )
    result = await runner.run({})
    assert not result.ok
    assert result.exit_code == 3
    rendered = await render_result_for_model(result, workdir=tmp_path)
    assert "退出码 3" in rendered
    assert "炸了" in rendered


async def test_timeout_is_reported(tmp_path):
    runner = ScriptToolRunner(
        ScriptToolConfig(
            name="slow_tool",
            code="import time\ntime.sleep(5)\n",
            parameter_schema={"type": "object", "properties": {}},
            timeout_seconds=1,
        ),
        workdir=tmp_path,
    )
    result = await runner.run({})
    assert result.timed_out
    assert not result.ok
    rendered = await render_result_for_model(result, workdir=tmp_path)
    assert "超时" in rendered


async def test_script_files_are_materialized_with_code_fingerprint(tmp_path):
    config = ScriptToolConfig(
        name="echo_tool",
        code="print('x')",
        parameter_schema={"type": "object", "properties": {}},
    )
    runner = ScriptToolRunner(config, workdir=tmp_path)
    script_path, params_path, _ = runner.materialize({"a": 1})
    assert script_path.name == f"echo_tool.{code_fingerprint(config.code)}.py"
    assert script_path.is_file()
    assert json.loads(params_path.read_text()) == {"a": 1}
    # 参数文件可能含业务数据：落盘即 0600
    assert (params_path.stat().st_mode & 0o777) == 0o600


# --------------------------------------------------------------------------- #
# 结果渲染
# --------------------------------------------------------------------------- #
async def test_json_stdout_is_returned_compacted(tmp_path):
    result = ScriptRunResult(
        ok=True,
        exit_code=0,
        stdout='{\n  "a": 1,\n  "b": [1, 2]\n}',
        stderr="",
        duration_ms=5,
    )
    rendered = await render_result_for_model(result, workdir=tmp_path)
    assert json.loads(rendered) == {"a": 1, "b": [1, 2]}


async def test_plain_text_stdout_is_returned_as_is(tmp_path):
    result = ScriptRunResult(ok=True, exit_code=0, stdout="hello", stderr="", duration_ms=5)
    assert await render_result_for_model(result, workdir=tmp_path) == "hello"


async def test_large_output_is_truncated(tmp_path):
    from app.services.ai.tools.script_tool_runtime import MAX_RESULT_BYTES

    result = ScriptRunResult(
        ok=True, exit_code=0, stdout="x" * (MAX_RESULT_BYTES + 1000), stderr="", duration_ms=5
    )
    rendered = await render_result_for_model(result, workdir=tmp_path)
    assert "输出已截断" in rendered


async def test_empty_output_is_explicit(tmp_path):
    result = ScriptRunResult(ok=True, exit_code=0, stdout="", stderr="", duration_ms=5)
    assert "没有输出" in await render_result_for_model(result, workdir=tmp_path)


# --------------------------------------------------------------------------- #
# 沙箱分支（替身）：必须走 bash 通道，且 cwd 是相对沙箱根的路径
# --------------------------------------------------------------------------- #
async def test_sandbox_branch_uses_bash_channel(tmp_path, monkeypatch):
    captured = {}

    class FakeSandboxBash:
        """照抄真实通道：返回 JSON，信封是其中 stdout 字段的内容。

        早期这里直接返回裸信封文本，等于把「平台假设错了通道协议」写进了测试，
        于是 docker/k8s 下每次调用都失败却测不出来。
        """

        async def __call__(self, **kwargs):
            captured.update(kwargs)
            return json.dumps(
                {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": f"{EXIT_MARK}0\n{STDOUT_MARK}\nok\n{STDERR_MARK}\n",
                    "stderr": "",
                }
            )

    async def fake_resolver(_ws):
        return FakeSandboxBash()

    import app.services.ai.runtime.agentscope.workspace as workspace_module

    monkeypatch.setattr(workspace_module, "resolve_sandbox_bash_tool", fake_resolver)

    session_dir = tmp_path / "users" / "u1" / "sessions" / "s1"
    session_dir.mkdir(parents=True)
    runner = ScriptToolRunner(
        _config(name="sandbox_demo", parameter_schema={"type": "object", "properties": {"path": {"type": "string"}}}),
        workdir=session_dir,
        sandbox_ws=SimpleNamespace(),
        sandbox_root=str(tmp_path),
    )
    result = await runner.run({"path": "a.txt"})

    assert result.ok
    assert captured["cwd"] == "users/u1/sessions/s1"
    assert ".script_tools/" in captured["command"]


async def test_sandbox_payload_is_parsed_as_json(tmp_path, monkeypatch):
    """通道返回 JSON（含失败与超时形态）时，退出码与输出必须被正确取出。"""
    from app.services.ai.tools.script_tool_runtime import (
        _parse_sandbox_payload,
        _shell_result_from_payload,
    )

    ok = _parse_sandbox_payload(
        json.dumps({"ok": True, "exit_code": 0, "stdout": "__NANZI_EXIT__:0\n__NANZI_STDOUT_START__\nhi\n", "stderr": ""})
    )
    assert ok is not None and ok["exit_code"] == 0

    failed = _parse_sandbox_payload(json.dumps({"ok": False, "exit_code": 127, "stdout": "", "stderr": "not found"}))
    assert failed is not None and failed["exit_code"] == 127

    # 超时：通道只给 error，没有退出码字段；必须识别成 timed_out，而不是普通文本
    timeout_payload = _parse_sandbox_payload(json.dumps({"ok": False, "error": "command timed out"}))
    assert timeout_payload is not None and timeout_payload["timed_out"] is True
    timeout_result = _shell_result_from_payload(timeout_payload)
    assert timeout_result.timed_out is True
    assert timeout_result.exit_code is None
    assert "timed out" in timeout_result.stderr

    assert _parse_sandbox_payload("plain text") is None
    assert _parse_sandbox_payload("{not json") is None


async def test_sandbox_envelope_missing_falls_back_to_channel_exit_code(tmp_path, monkeypatch):
    """命令在 echo 标记之前就死掉时，用通道自己的退出码，而不是「退出码未知」。"""
    from app.services.ai.tools.script_tool_runtime import _shell_result_from_payload

    result = _shell_result_from_payload(
        {"ok": False, "exit_code": 2, "stdout": "", "stderr": "bash: syntax error"}
    )
    assert result.exit_code == 2
    assert "syntax error" in result.stderr


async def test_envelope_capture_limits_fit_sandbox_stdout_window():
    """单段捕获上限之和必须远小于沙箱的尾部保留量（100KB）。

    信封把退出码写在最前面；通道只留 stdout 尾部 100KB，输出一大就先丢标记，
    表现成「脚本失败、退出码未知」。
    """
    from app.services.ai.tools.script_tool_runtime import (
        STDERR_CAPTURE_BYTES,
        STDOUT_CAPTURE_BYTES,
    )

    assert STDOUT_CAPTURE_BYTES + STDERR_CAPTURE_BYTES < 100_000
    assert STDOUT_CAPTURE_BYTES < 100_000
    # file_to_base64 默认读 48KB，base64 后约 64KB；捕获上限必须容得下这段 JSON，
    # 否则内置工具会「成功」返回一段被截断的非法 JSON。
    assert STDOUT_CAPTURE_BYTES >= 70_000


async def test_builtin_file_to_base64_at_default_limit_is_valid_json(tmp_path):
    """48KB 文件在默认 max_bytes 下必须返回完整 JSON，不能被 stdout 捕获截断。"""
    from app.services.ai.tools.script_tool_builtins import get_builtin_script_tool
    from app.services.ai.tools.script_tool_factory import config_from_builtin

    (tmp_path / "big.bin").write_bytes(b"x" * (48 * 1024))
    builtin = get_builtin_script_tool("file_to_base64")
    assert builtin is not None
    runner = ScriptToolRunner(config_from_builtin(builtin), workdir=tmp_path)

    result = await runner.run({"path": "big.bin"})

    assert result.ok, result.stderr
    payload = json.loads(result.stdout)
    assert payload["size"] == 48 * 1024
    assert len(payload["data"]) == 65536


async def test_requirements_install_creates_target_dir_before_shell(tmp_path, monkeypatch):
    """首次装依赖时 `.pylibs` 还不存在；重定向文件必须先有父目录。"""
    from app.services.ai.tools.script_tool_runtime import ShellRunResult

    runner = ScriptToolRunner(
        ScriptToolConfig(
            name="needs_dependency",
            code="print('ok')",
            requirements=("pypdf>=4.0",),
        ),
        workdir=tmp_path,
    )
    requirements_path = tmp_path / "requirements.txt"
    requirements_path.write_text("pypdf>=4.0\n", encoding="utf-8")
    commands: list[str] = []

    async def fake_run_shell(command: str, *, timeout_seconds: float):
        assert (tmp_path / ".pylibs").is_dir(), "重定向前必须创建 .pylibs"
        commands.append(command)
        return ShellRunResult(exit_code=0, stdout="", stderr="")

    monkeypatch.setattr(runner, "_run_shell", fake_run_shell)
    assert await runner.ensure_requirements(requirements_path) is None
    assert commands
    match = re.search(r"\.install\.([0-9a-f]{8})\.out", commands[0])
    assert match, commands[0]
    assert f".install.{match.group(1)}.err" in commands[0]


async def test_run_generates_fresh_run_id_for_each_call(tmp_path, monkeypatch):
    """同一个 runner 会在会话里被反复调用；参数/输出文件名必须每次不同。"""
    from app.services.ai.tools.script_tool_runtime import ShellRunResult

    runner = ScriptToolRunner(
        ScriptToolConfig(name="echo_tool", code="print('x')", parameter_schema={}),
        workdir=tmp_path,
    )
    commands: list[str] = []

    async def fake_run_shell(command: str, *, timeout_seconds: float):
        commands.append(command)
        envelope = "\n".join([f"{EXIT_MARK}0", STDOUT_MARK, "ok", STDERR_MARK, ""])
        return ShellRunResult(exit_code=0, stdout=envelope, stderr="")

    monkeypatch.setattr(runner, "_run_shell", fake_run_shell)
    assert (await runner.run({})).ok
    assert (await runner.run({})).ok
    assert len(commands) == 2
    assert commands[0] != commands[1]
    params_files = {re.search(r"\.script_tools/[^ ]+\.params\.json", c).group(0) for c in commands}
    assert len(params_files) == 2


async def test_session_outside_sandbox_root_is_rejected(tmp_path):
    session_dir = tmp_path / "outside"
    session_dir.mkdir()
    runner = ScriptToolRunner(
        _config(),
        workdir=session_dir,
        sandbox_ws=SimpleNamespace(),
        sandbox_root=str(tmp_path / "other_root"),
    )
    with pytest.raises(ScriptToolError) as exc:
        await runner.run({"path": "a.txt"})
    assert "不在沙箱工作区内" in str(exc.value)
