"""只用合成凭据；worker 在测试临时目录运行，不连接容器或真实服务。"""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def run_worker(tmp_path, code, *, env=None, files=None, timeout=2):
    from app.services.ai.tools.credential_script_execution import worker_source

    payload = json.dumps({"code": code, "params": {"limit": 3}, "requirements": [],
                          "env": env or {}, "files": files or {}, "timeout": timeout}).encode()
    return json.loads(_run_worker_process(worker_source(), payload, tmp_path))


def _run_worker_process(bootstrap, payload, workdir):
    process = subprocess.run(
        [sys.executable, "-I", "-c", bootstrap],
        input=str(len(payload)).encode() + b"\n" + payload,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=workdir, timeout=8,
        env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"},
    )
    assert process.returncode == 0, process.stderr
    assert not process.stderr
    return process.stdout


def test_worker_injects_only_child_and_masks_short_and_json_escaped_values(tmp_path):
    result = run_worker(tmp_path, "import os,json,sys; print(json.dumps(dict(os.environ))); print(os.environ['PWD_TOKEN'], file=sys.stderr)",
                        env={"PWD_TOKEN": "p7", "QUOTED": 'a"b\nc', "PORT": "993"})
    assert result["exit_code"] == 0
    assert "p7" not in str(result) and 'a\\"b' not in str(result)
    assert "993" not in str(result)
    assert "[REDACTED]" in result["stdout"] and "[REDACTED]" in result["stderr"]
    assert "输出已脱敏" in result["stderr"]
    assert list(tmp_path.iterdir()) == []


def test_worker_returns_clean_text_artifact_and_blocks_secret_binary_and_symlink(tmp_path):
    code = """import json, os
from pathlib import Path
Path('report.txt').write_text('mail summary', encoding='utf-8')
Path('leak.txt').write_text(os.environ['PASSWORD'])
Path('image.bin').write_bytes(b'\\x00\\xff')
Path('link.txt').symlink_to('report.txt')
print(json.dumps({'artifacts': ['report.txt','leak.txt','image.bin','link.txt','../escape.txt']}))
"""
    result = run_worker(tmp_path, code, env={"PASSWORD": "fake-short"})
    assert result["exit_code"] == 0
    assert [item["path"] for item in result["artifacts"]] == ["report.txt"]
    assert base64.b64decode(result["artifacts"][0]["data"]) == b"mail summary"
    assert result["blocked_artifacts"] == 4
    assert "fake-short" not in str(result)
    assert list(tmp_path.iterdir()) == []


def test_worker_reads_snapshot_and_json_stdin(tmp_path):
    result = run_worker(tmp_path, "import json,sys; print(open('input.txt').read()+str(json.load(sys.stdin)['limit']))",
                        files={"input.txt": base64.b64encode(b"items:").decode()})
    assert result["stdout"].strip() == "items:3"


def test_worker_timeout_clears_private_files(tmp_path):
    result = run_worker(tmp_path, "import time; time.sleep(30)", timeout=0.05)
    assert result["timed_out"] and result["exit_code"] is None
    assert list(tmp_path.iterdir()) == []


def test_worker_stops_output_flood_before_process_timeout(tmp_path):
    result = run_worker(tmp_path, "import sys,time; sys.stdout.write('x'*(5*1024*1024)); sys.stdout.flush(); time.sleep(30)")
    assert not result["timed_out"]
    assert result["stdout"] == "" and "超过" in result["stderr"]


@pytest.mark.parametrize("body", ['{"value":"\\u006eeedle"}', '["\\u006eeedle",993]', '{"\\u006eeedle":true}'])
def test_worker_cleans_all_json_escape_spellings_before_model_parsing(tmp_path, body):
    result = run_worker(tmp_path, "print(" + repr(body) + ")", env={"PASSWORD": "needle", "PORT": "993"})
    decoded = json.loads(result["stdout"])
    assert "needle" not in str(decoded) and "993" not in str(decoded)


def test_worker_blocks_json_artifact_with_escaped_credential(tmp_path):
    code = "from pathlib import Path; import json; Path('data.json').write_text(" + repr('{"value":"\\u006eeedle"}') + "); print(json.dumps({'artifacts':['data.json']}))"
    result = run_worker(tmp_path, code, env={"PASSWORD": "needle"})
    assert result["artifacts"] == [] and result["blocked_artifacts"] == 1


def test_snapshot_omits_shared_python_and_rejects_symlinks(tmp_path):
    from app.services.ai.tools.credential_script_execution import snapshot_inputs, CredentialExecutionError

    (tmp_path / ".pylibs").mkdir()
    (tmp_path / ".pylibs" / "poison.py").write_text("raise RuntimeError()")
    (tmp_path / "input.txt").write_text("business data")
    assert list(snapshot_inputs(tmp_path)) == ["input.txt"]
    (tmp_path / "outside").symlink_to("/etc/passwd")
    with pytest.raises(CredentialExecutionError):
        snapshot_inputs(tmp_path)


def test_snapshot_does_not_follow_replaced_session_root(tmp_path):
    from app.services.ai.tools.credential_script_execution import snapshot_inputs, CredentialExecutionError
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("must not be copied")
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "session").symlink_to(outside, target_is_directory=True)
    with pytest.raises(CredentialExecutionError):
        snapshot_inputs(root / "session")


def test_snapshot_rejects_link_in_relative_session_ancestors(tmp_path):
    from app.services.ai.tools.credential_script_execution import snapshot_inputs, CredentialExecutionError
    outside = tmp_path / "outside"
    (outside / "session").mkdir(parents=True)
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "sessions").symlink_to(outside, target_is_directory=True)
    with pytest.raises(CredentialExecutionError):
        snapshot_inputs(root / "sessions" / "session", sandbox_root=root)


def test_worker_blocks_artifact_equal_to_redaction_marker(tmp_path):
    code = "from pathlib import Path; import json,os; Path('out.txt').write_text(os.environ['TOKEN']); print(json.dumps({'artifacts':['out.txt']}))"
    result = run_worker(tmp_path, code, env={"TOKEN": "[REDACTED]"})
    assert result["artifacts"] == []


@pytest.mark.parametrize("value", ['{"x": "synthetic-needle"}', '"synthetic-needle"', '"x": "synthetic-needle"'])
def test_worker_hides_secret_which_is_json_or_spans_json_nodes(tmp_path, value):
    code = "import os; print(os.environ['TOKEN'])" if value.startswith(("{", '"synthetic')) else "import os; print('{'+os.environ['TOKEN']+'}')"
    result = run_worker(tmp_path, code, env={"TOKEN": value})
    assert result["stdout"] == ""
    assert "输出已隐藏" in result["stderr"]
    assert "synthetic-needle" not in result["stderr"]


def test_worker_checks_cross_node_echo_before_rewriting_artifact_paths(tmp_path):
    value = '"synthetic-left", "synthetic-right"'
    code = (
        "from pathlib import Path; import json; "
        "Path('synthetic-left').write_text('a'); "
        "print(json.dumps({'artifacts': ['synthetic-left', 'synthetic-right']}))"
    )
    result = run_worker(tmp_path, code, env={"TOKEN": value})

    assert result["stdout"] == ""
    assert "输出已隐藏" in result["stderr"]
    assert "synthetic-left" not in result["stderr"]
    assert "synthetic-right" not in result["stderr"]


def test_worker_hides_a_plain_output_if_replacement_joins_another_credential(tmp_path):
    result = run_worker(tmp_path, "print('synthetic-A' + 'tail-value')",
                        env={"SHORT": "A", "TOKEN": "synthetic-tail-value"})
    assert result["exit_code"] == 0
    assert result["stdout"] == ""
    assert "输出已隐藏" in result["stderr"]


@pytest.mark.asyncio
async def test_runner_routes_bound_credentials_without_shared_materialize(tmp_path, monkeypatch):
    from app.services.ai.tools import script_tool_runtime as runtime
    from app.services.ai.tools import credential_script_execution as execution

    monkeypatch.setattr(runtime, "list_bound_slots", AsyncMock(return_value=["env:PASSWORD"]))
    expected = runtime.ScriptRunResult(ok=True, exit_code=0, stdout="safe", stderr="", duration_ms=1)
    invoke = AsyncMock(return_value=expected)
    monkeypatch.setattr(execution, "execute_credential_script", invoke)
    runner = runtime.ScriptToolRunner(runtime.ScriptToolConfig(id="tool", name="mail", code="pass"),
                                     workdir=tmp_path, sandbox_ws=SimpleNamespace(), user_info={"id": "101"})
    result = await runner.run({})
    assert result is expected
    invoke.assert_awaited_once()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_lazy_workspace_is_prepared_before_credentials_are_read(tmp_path, monkeypatch):
    from app.services.ai.tools import credential_script_execution as execution
    from app.services.ai.tools import credential_script_providers as providers
    from app.services.ai.tools.script_tool_runtime import ScriptToolConfig, ScriptToolRunner

    order = []
    ready_workspace = SimpleNamespace(_platform_sandbox_policy="docker", _image_tag="fixture",
        _client=SimpleNamespace(containers=SimpleNamespace(create=lambda: None, container=lambda: None)))
    async def ready():
        order.append("ready")
        return ready_workspace
    async def resolve(*args, **kwargs):
        order.append("resolve")
        return {"PASSWORD": "needle"}
    async def transport(workspace, **kwargs):
        assert workspace is ready_workspace
        assert json.loads(kwargs["payload"])["env"] == {"PASSWORD": "needle"}
        return json.dumps({"exit_code": 0, "stdout": '{"v":"\\u006eeedle"}', "stderr": "needle", "artifacts": []}).encode()
    monkeypatch.setattr(execution, "apply_script_env_bindings", resolve)
    monkeypatch.setattr(providers, "run_credential_process", transport)
    runner = ScriptToolRunner(ScriptToolConfig(id="t", name="mail", code="pass", timeout_seconds=10),
                             workdir=tmp_path, sandbox_root=str(tmp_path), sandbox_ws=SimpleNamespace(ensure_ready=ready))
    result = await execution.execute_credential_script(runner, {})
    assert order == ["ready", "resolve"]
    assert result.ok and "needle" not in result.stdout + result.stderr
    assert "输出已脱敏" in result.message
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("value", "code", "stdout", "notice"), [
    (
        '\"first\", \"second\"',
        "import os; print('[' + os.environ['TOKEN'] + ']')",
        "",
        "输出已隐藏",
    ),
    (
        "p7",
        "import os, json; print(json.dumps({'value': os.environ['TOKEN']}))",
        '{"value": "[REDACTED]"}',
        "输出已脱敏",
    ),
    (
        "p7",
        "import os, json; print(json.dumps({'value': os.environ['TOKEN'], 'output_notice': 'business field'}))",
        '{"value": "[REDACTED]", "output_notice": "business field"}',
        "输出已脱敏",
    ),
    (
        "p7",
        "import os, sys; print('business'); print(os.environ['TOKEN'], file=sys.stderr)",
        "business\n",
        "输出已脱敏",
    ),
    (
        "输出已隐藏",
        "import os; print(os.environ['TOKEN'])",
        "[REDACTED]\n",
        "输出已脱敏",
    ),
    (
        "p7\n",
        "import os, sys; print('business'); sys.stderr.write(os.environ['TOKEN'] + 'p7')",
        "business\n",
        "输出已脱敏",
    ),
    ("p7", "pass", "", ""),
])
async def test_runner_surfaces_private_worker_output_notice(value, code, stdout, notice, tmp_path, monkeypatch):
    from app.services.ai.tools import credential_script_execution as execution
    from app.services.ai.tools import credential_script_providers as providers
    from app.services.ai.tools import script_tool_runtime as runtime

    monkeypatch.setattr(runtime, "list_bound_slots", AsyncMock(return_value=["env:TOKEN"]))
    monkeypatch.setattr(execution, "apply_script_env_bindings", AsyncMock(return_value={"TOKEN": value}))

    async def transport(workspace, *, bootstrap, payload, timeout_seconds):
        return _run_worker_process(bootstrap, payload, tmp_path)

    monkeypatch.setattr(providers, "run_credential_process", transport)
    workspace = SimpleNamespace(_platform_sandbox_policy="docker", _image_tag="fixture",
        _client=SimpleNamespace(containers=SimpleNamespace(create=lambda: None, container=lambda: None)))
    runner = runtime.ScriptToolRunner(
        runtime.ScriptToolConfig(id="t", name="fixture", code=code, timeout_seconds=10),
        workdir=tmp_path, sandbox_root=str(tmp_path), sandbox_ws=workspace,
    )

    result = await runner.run({})
    rendered = await runtime.render_result_for_model(result, workdir=runner.workdir)

    assert result.ok and result.stdout == stdout
    assert value not in result.stdout + result.stderr + result.message + rendered
    if notice:
        assert notice in result.message and notice in rendered
        assert "没有输出" not in rendered
        if stdout.startswith("{"):
            expected = json.loads(stdout)
            if "output_notice" in expected:
                _, _, rendered_payload = rendered.partition("\n")
                assert json.loads(rendered_payload) == expected
            else:
                assert json.loads(rendered)["value"] == "[REDACTED]"
    else:
        assert not result.message
        assert "没有输出" in rendered


@pytest.mark.asyncio
async def test_runner_uses_only_sanitized_result_when_export_rewrite_hides_stdout(tmp_path, monkeypatch):
    from app.services.ai.tools import credential_script_execution as execution
    from app.services.ai.tools import credential_script_providers as providers
    from app.services.ai.tools.script_tool_runtime import ScriptToolConfig, ScriptToolRunner

    value = '"id": "9", "tag":'
    response = {
        "exit_code": 0,
        "stdout": '{"artifacts": ["leak.txt"], "id":"9","tag":"business"}',
        "stderr": "",
        "artifacts": [{"path": "leak.txt", "data": base64.b64encode(value.encode()).decode()}],
    }
    monkeypatch.setattr(execution, "apply_script_env_bindings", AsyncMock(return_value={"TOKEN": value}))
    monkeypatch.setattr(providers, "run_credential_process", AsyncMock(return_value=json.dumps(response).encode()))
    workspace = SimpleNamespace(_platform_sandbox_policy="docker", _image_tag="fixture",
        _client=SimpleNamespace(containers=SimpleNamespace(create=lambda: None, container=lambda: None)))
    runner = ScriptToolRunner(ScriptToolConfig(id="t", name="fixture", code="pass", timeout_seconds=10),
                             workdir=tmp_path, sandbox_root=str(tmp_path), sandbox_ws=workspace)

    result = await execution.execute_credential_script(runner, {})

    assert result.ok and result.stdout == ""
    assert "输出已隐藏" in result.message and "未发布" in result.message
    assert value not in result.stderr + result.message
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    b'{"stdout": "synthetic-secret"',
    b'{"exit_code": "synthetic-secret", "stderr": "synthetic-secret"}',
    b'\xffsynthetic-secret',
])
async def test_invalid_worker_response_keeps_private_output_out_of_script_error(response, tmp_path, monkeypatch, caplog):
    from app.services.ai.tools import credential_script_execution as execution
    from app.services.ai.tools import credential_script_providers as providers
    from app.services.ai.tools.script_tool_runtime import ScriptToolConfig, ScriptToolError, ScriptToolRunner

    injected_env = {"TOKEN": "synthetic-secret"}
    monkeypatch.setattr(execution, "apply_script_env_bindings", AsyncMock(return_value=injected_env))
    monkeypatch.setattr(providers, "run_credential_process", AsyncMock(return_value=response))
    workspace = SimpleNamespace(_platform_sandbox_policy="docker", _image_tag="fixture",
        _client=SimpleNamespace(containers=SimpleNamespace(create=lambda: None, container=lambda: None)))
    runner = ScriptToolRunner(ScriptToolConfig(id="t", name="fixture", code="pass", timeout_seconds=10),
                             workdir=tmp_path, sandbox_root=str(tmp_path), sandbox_ws=workspace)

    with pytest.raises(ScriptToolError) as exc:
        await execution.execute_credential_script(runner, {})

    assert "synthetic-secret" not in str(exc.value) + caplog.text
    assert exc.value.__suppress_context__
    assert injected_env == {}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_unsupported_policy_does_not_resolve_credentials(tmp_path, monkeypatch):
    from app.services.ai.tools import credential_script_execution as execution
    from app.services.ai.tools.script_tool_runtime import ScriptToolConfig, ScriptToolRunner, ScriptToolError
    resolve = AsyncMock()
    monkeypatch.setattr(execution, "apply_script_env_bindings", resolve)
    runner = ScriptToolRunner(ScriptToolConfig(id="t", name="mail", code="pass"), workdir=tmp_path)
    with pytest.raises(ScriptToolError):
        await execution.execute_credential_script(runner, {})
    resolve.assert_not_awaited()


def test_dependency_install_never_receives_credential_environment(monkeypatch):
    from app.services.ai.tools import credential_script_worker as worker
    calls = []
    def child(command, **kwargs):
        calls.append((command, dict(kwargs["env"])))
        return 0, "ok", "", False
    monkeypatch.setattr(worker, "run_child", child)
    monkeypatch.setenv("FIXTURE_PARENT_TOKEN", "parent-only")
    worker.execute({"code": "pass", "params": {}, "files": {}, "timeout": 2,
                    "requirements": ["fixture-package==1"], "env": {"MAIL_TOKEN": "synthetic"}})
    assert len(calls) == 2
    assert "MAIL_TOKEN" not in calls[0][1]
    assert calls[1][1]["MAIL_TOKEN"] == "synthetic"
    assert all("FIXTURE_PARENT_TOKEN" not in env for _, env in calls)


def test_redaction_covers_more_than_twenty_values():
    from app.services.ai.tools.credential_script_worker import make_redactor, clean_output
    values = [f"synthetic-value-{index:03d}" for index in range(40)]
    output = clean_output(json.dumps(values), make_redactor(values))
    assert all(value not in output for value in values)


def test_worker_output_limit_counts_utf8_bytes(tmp_path):
    result = run_worker(tmp_path, "print('中'*40000)")
    assert len(result["stdout"].encode("utf-8")) <= 80 * 1024
