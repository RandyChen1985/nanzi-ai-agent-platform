"""画布「运行」在沙箱策略下的执行契约。

背景：画布运行最初写成「后端本机子进程」，与 ``sandbox_policy`` 完全无关；
docker/k8s 部署下脚本仍跑在平台容器里，路径是宿主路径而不是沙箱逻辑根。
这些测试锁住修复后的分流行为：策略进沙箱、路径取逻辑根、e2b/ssh 明确拒绝。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from aiohttp import WSMsgType
from types import SimpleNamespace

import pytest

from app.services.ai.code_execution_service import (
    SandboxScriptResult,
    SandboxScriptUnavailable,
    build_sandbox_command,
    sandbox_session_workdir,
    start_code_execution,
)
from app.services.ai.runtime.agentscope.workspace import resolve_session_workdir


pytestmark = pytest.mark.no_infrastructure


@pytest.fixture
def canvas_workspace(tmp_path: Path) -> SimpleNamespace:
    """一个位于用户根目录下的会话工作区（与生产路径结构一致）。"""
    root = tmp_path / "agent_workspaces"
    user_info = {"user_id": 1, "user_name": "u1"}
    workspace = Path(
        resolve_session_workdir(
            root=str(root),
            user_id=1,
            user_name="u1",
            conversation_id="conv-1",
        )
    )
    return SimpleNamespace(
        root=root,
        workspace=workspace,
        user_info=user_info,
        user_root=root / "u1__1",
    )


def _start_handle(canvas_workspace: SimpleNamespace):
    return start_code_execution(
        language="python",
        code="print('hello')",
        workspace=canvas_workspace.workspace,
        workspace_root=canvas_workspace.root,
        user_info=canvas_workspace.user_info,
        conversation_id="conv-1",
    )


def _stub_policy_checks(monkeypatch) -> None:
    """隔离 DB 侧安全策略：单元测试只验证执行分流，不连真实库。"""
    from app.services.ai import code_execution_service as service

    async def no_blocked_reason(**kwargs):
        return None

    monkeypatch.setattr(service, "_policy_block_reason", no_blocked_reason)


def _forbid_local_process(monkeypatch) -> None:
    async def fail_if_created(*args, **kwargs):
        raise AssertionError("sandbox 策略下不允许回退到后端本机子进程")

    monkeypatch.setattr(
        "app.services.ai.code_execution_service.asyncio.create_subprocess_exec",
        fail_if_created,
    )


def test_sandbox_session_workdir_maps_host_session_dir_into_logical_root(canvas_workspace):
    assert (
        sandbox_session_workdir(
            workspace=canvas_workspace.workspace,
            user_root=canvas_workspace.user_root,
            sandbox_root="/workspace",
        )
        == "/workspace/sessions/conv-1"
    )


def test_sandbox_session_workdir_rejects_workspace_outside_user_root(canvas_workspace):
    with pytest.raises(ValueError, match="当前用户工作区"):
        sandbox_session_workdir(
            workspace=canvas_workspace.workspace,
            user_root=canvas_workspace.root / "other__9",
            sandbox_root="/workspace",
        )


def test_build_sandbox_command_cds_into_workdir_and_runs_unbuffered_python():
    command = build_sandbox_command(
        "python",
        workdir="/workspace/sessions/conv-1",
        script_path="/workspace/sessions/conv-1/.nanzi-code-run-abc.py",
    )

    assert command[:2] == ["/bin/bash", "-c"]
    assert command[2] == (
        "cd /workspace/sessions/conv-1 && "
        "exec python3 -u /workspace/sessions/conv-1/.nanzi-code-run-abc.py"
    )


def test_build_sandbox_command_accepts_python3_and_shell_aliases():
    python_cmd = build_sandbox_command(
        "python", workdir="/workspace/sessions/conv-1", script_path="/workspace/sessions/conv-1/a.py"
    )
    python3_cmd = build_sandbox_command(
        "python3", workdir="/workspace/sessions/conv-1", script_path="/workspace/sessions/conv-1/a.py"
    )
    shell_cmd = build_sandbox_command(
        "shell", workdir="/workspace/sessions/conv-1", script_path="/workspace/sessions/conv-1/a.sh"
    )
    sh_cmd = build_sandbox_command(
        "sh", workdir="/workspace/sessions/conv-1", script_path="/workspace/sessions/conv-1/a.sh"
    )

    assert python3_cmd == python_cmd
    assert shell_cmd == sh_cmd


def test_build_sandbox_command_quotes_paths_with_spaces():
    command = build_sandbox_command(
        "bash",
        workdir="/workspace/my dir",
        script_path="/workspace/my dir/run.sh",
    )

    assert "cd '/workspace/my dir'" in command[2]
    assert "exec /bin/bash '/workspace/my dir/run.sh'" in command[2]


@pytest.mark.asyncio
async def test_docker_policy_runs_through_sandbox_runner_with_logical_paths(
    monkeypatch, canvas_workspace
):
    from app.services.ai import code_execution_service as service

    _stub_policy_checks(monkeypatch)
    _forbid_local_process(monkeypatch)
    captured: dict = {}

    async def fake_policy() -> str:
        return "docker"

    async def fake_runner(**kwargs):
        captured.update(kwargs)
        await kwargs["on_output"]("stdout", b"hello from sandbox\n")
        return SandboxScriptResult(exit_code=0)

    monkeypatch.setattr(service, "resolve_execution_policy", fake_policy)
    monkeypatch.setattr(service, "run_sandbox_script", fake_runner)

    result = await _start_handle(canvas_workspace).collect()

    assert result.status == "succeeded"
    assert [(item.stream, item.text) for item in result.outputs] == [
        ("stdout", "hello from sandbox\n")
    ]
    assert captured["policy"] == "docker"
    assert captured["sandbox_workdir"] == "/workspace/sessions/conv-1"
    assert captured["sandbox_script_path"].startswith("/workspace/sessions/conv-1/.nanzi-code-run-")
    assert captured["sandbox_script_path"].endswith(".py")
    assert captured["conversation_id"] == "conv-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["e2b", "ssh"])
async def test_policies_without_workspace_mapping_are_blocked(monkeypatch, canvas_workspace, policy):
    from app.services.ai import code_execution_service as service

    async def fake_policy() -> str:
        return policy

    async def fail_runner(**kwargs):
        raise AssertionError("没有工作区映射的策略不得调用沙箱执行器")

    _stub_policy_checks(monkeypatch)
    _forbid_local_process(monkeypatch)
    monkeypatch.setattr(service, "resolve_execution_policy", fake_policy)
    monkeypatch.setattr(service, "run_sandbox_script", fail_runner)

    result = await _start_handle(canvas_workspace).collect()

    assert result.status == "blocked"
    assert policy in (result.error or "")
    assert "暂不支持" in (result.error or "")


@pytest.mark.asyncio
async def test_k8s_isolated_pvc_is_rejected_before_exec(monkeypatch, canvas_workspace):
    """独立 PVC 的沙箱看不到宿主会话目录，必须明确拒绝而不是让脚本报找不到文件。"""
    from app.services.ai import code_execution_sandbox as sandbox
    from app.services.ai import code_execution_service as service
    from app.services.ai.runtime.agentscope import workspace as sandbox_workspace

    class _IsolatedWorkspace:
        is_alive = True
        _nanzi_existing_pvc = None

    async def fake_policy() -> str:
        return "k8s"

    async def fake_guard(**kwargs) -> str:
        return "u1__1"

    async def fake_root(**kwargs) -> str:
        return str(canvas_workspace.root)

    async def fake_logical(policy, **kwargs):
        return "/workspace"

    async def fake_pod_identity(workspace):
        return ("sandbox", "pod-1")

    called = {"stream": False}

    async def fail_stream(**kwargs):
        called["stream"] = True
        raise AssertionError("独立 PVC 时不得进入 Pod 执行脚本")

    _stub_policy_checks(monkeypatch)
    monkeypatch.setattr(service, "resolve_execution_policy", fake_policy)
    monkeypatch.setattr(sandbox_workspace, "_k8s_runtime_guard", fake_guard)
    monkeypatch.setattr(sandbox_workspace, "resolve_workspace_root", fake_root)
    monkeypatch.setattr(sandbox_workspace, "resolve_sandbox_logical_root", fake_logical)
    monkeypatch.setattr(
        sandbox_workspace,
        "_find_cached_k8s_workspace",
        lambda *args, **kwargs: _IsolatedWorkspace(),
    )
    monkeypatch.setattr(sandbox_workspace, "_k8s_workspace_pod_identity", fake_pod_identity)
    monkeypatch.setattr(sandbox, "_stream_k8s_exec", fail_stream)

    result = await _start_handle(canvas_workspace).collect()

    assert called["stream"] is False
    assert result.status == "failed"
    assert "独立 PVC" in (result.error or "")
    assert "没有挂载进 Pod" in (result.error or "")


@pytest.mark.asyncio
async def test_sandbox_unavailable_surfaces_user_readable_error(monkeypatch, canvas_workspace):
    from app.services.ai import code_execution_service as service

    async def fake_policy() -> str:
        return "docker"

    async def unavailable_runner(**kwargs):
        raise SandboxScriptUnavailable("沙箱未启动，请先发送消息启动沙箱后再运行。")

    _stub_policy_checks(monkeypatch)
    _forbid_local_process(monkeypatch)
    monkeypatch.setattr(service, "resolve_execution_policy", fake_policy)
    monkeypatch.setattr(service, "run_sandbox_script", unavailable_runner)

    result = await _start_handle(canvas_workspace).collect()

    assert result.status == "failed"
    assert "沙箱未启动" in (result.error or "")


@pytest.mark.asyncio
async def test_local_policy_still_uses_backend_process(monkeypatch, tmp_path: Path):
    from app.services.ai import code_execution_service as service

    async def fake_policy() -> str:
        return "local"

    async def fail_runner(**kwargs):
        raise AssertionError("local 策略不得调用沙箱执行器")

    monkeypatch.setattr(service, "resolve_execution_policy", fake_policy)
    monkeypatch.setattr(service, "run_sandbox_script", fail_runner)

    result = await start_code_execution(
        language="python",
        code="print('local ok')",
        workspace=tmp_path,
        user_info={},
    ).collect()

    assert result.status == "succeeded"
    assert "local ok" in "".join(item.text for item in result.outputs)


# ---------------------------------------------------------------------------
# 沙箱内流式执行（不依赖真实 Docker daemon）
# ---------------------------------------------------------------------------


class _FakeExecMessage:
    def __init__(self, stream: int, data: bytes):
        self.stream = stream
        self.data = data


class _FakeExecStream:
    def __init__(self, messages, *, block: bool = False):
        self._messages = list(messages)
        self._block = block

    async def read_out(self):
        while True:
            if self._messages:
                return self._messages.pop(0)
            if not self._block:
                return None
            await asyncio.sleep(0.02)


class _FakeStreamContext:
    def __init__(self, stream):
        self._stream = stream

    async def __aenter__(self):
        return self._stream

    async def __aexit__(self, *exc_info):
        return False


class _FakeExec:
    def __init__(self, stream, *, exit_code: int | None = 0):
        self._stream = stream
        self.exit_code = exit_code

    def start(self, *, detach: bool = False):
        if detach:
            async def _detached():
                return b""

            return _detached()
        return _FakeStreamContext(self._stream)

    async def inspect(self):
        return {"ExitCode": self.exit_code}


class _FakeDockerContainer:
    def __init__(self, messages, *, exit_code: int | None = 0, block: bool = False):
        self.messages = messages
        self.exit_code = exit_code
        self.block = block
        self.calls: list[dict] = []

    async def exec(self, **kwargs):
        self.calls.append(kwargs)
        command = " ".join(str(part) for part in kwargs.get("cmd") or [])
        if "kill -TERM" in command:
            return _FakeExec(_FakeExecStream([]))
        return _FakeExec(
            _FakeExecStream(self.messages, block=self.block), exit_code=self.exit_code
        )


@pytest.mark.asyncio
async def test_docker_stream_forwards_stdout_stderr_chunks():
    from app.services.ai import code_execution_sandbox as sandbox

    container = _FakeDockerContainer(
        [
            _FakeExecMessage(1, b"out-1\n"),
            _FakeExecMessage(2, b"err-1\n"),
            _FakeExecMessage(1, b"out-2\n"),
        ]
    )
    output: list[tuple[str, bytes]] = []

    async def collect(stream: str, chunk: bytes) -> None:
        output.append((stream, chunk))

    result = await sandbox._stream_docker_exec(
        container,
        command=["/bin/bash", "-c", "true"],
        workdir="/workspace/sessions/conv-1",
        pid_file="/workspace/sessions/conv-1/.pid",
        timeout_seconds=5,
        on_output=collect,
        is_stopped=lambda: False,
    )

    assert result.exit_code == 0
    assert not result.timed_out and not result.stopped
    assert output == [
        ("stdout", b"out-1\n"),
        ("stderr", b"err-1\n"),
        ("stdout", b"out-2\n"),
    ]


@pytest.mark.asyncio
async def test_docker_stream_timeout_kills_pid_and_reports_timed_out():
    from app.services.ai import code_execution_sandbox as sandbox

    container = _FakeDockerContainer([], exit_code=None, block=True)

    async def collect(stream: str, chunk: bytes) -> None:
        pass

    result = await sandbox._stream_docker_exec(
        container,
        command=["/bin/bash", "-c", "true"],
        workdir="/workspace/sessions/conv-1",
        pid_file="/workspace/sessions/conv-1/.pid",
        timeout_seconds=0.05,
        on_output=collect,
        is_stopped=lambda: False,
    )

    assert result.timed_out is True
    assert any("kill -TERM" in " ".join(str(p) for p in call.get("cmd") or []) for call in container.calls)


@pytest.mark.asyncio
async def test_docker_stream_stop_request_kills_pid_and_reports_stopped():
    from app.services.ai import code_execution_sandbox as sandbox

    container = _FakeDockerContainer([], exit_code=None, block=True)

    async def collect(stream: str, chunk: bytes) -> None:
        pass

    result = await sandbox._stream_docker_exec(
        container,
        command=["/bin/bash", "-c", "true"],
        workdir="/workspace/sessions/conv-1",
        pid_file="/workspace/sessions/conv-1/.pid",
        timeout_seconds=5,
        on_output=collect,
        is_stopped=lambda: True,
    )

    assert result.stopped is True
    assert any("kill -TERM" in " ".join(str(p) for p in call.get("cmd") or []) for call in container.calls)


# ---------------------------------------------------------------------------
# K8s exec 分帧（不连真实集群：patch kubernetes_asyncio 的客户端）
# ---------------------------------------------------------------------------


class _FakeWsMessage:
    def __init__(self, type_, data):
        self.type = type_
        self.data = data


class _FakeWsResponse:
    def __init__(self, frames):
        self._frames = list(frames)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def receive(self):
        if not self._frames:
            await asyncio.sleep(3600)
        return self._frames.pop(0)


class _FakeWsApiClient:
    closed = False

    def __init__(self, *args, **kwargs):
        pass

    async def close(self):
        type(self).closed = True


def _install_fake_k8s(monkeypatch, frames):
    import kubernetes_asyncio.client as k8s_client
    import kubernetes_asyncio.config as k8s_config
    import kubernetes_asyncio.stream.ws_client as ws_client

    class _FakeCoreV1Api:
        def __init__(self, api_client=None):
            self.api_client = api_client

        async def connect_get_namespaced_pod_exec(self, **kwargs):
            if kwargs.get("_preload_content") is False:
                return _FakeWsResponse(frames)
            return _FakeWsResponse([_FakeWsMessage(WSMsgType.CLOSE, b"")])

    monkeypatch.setattr(ws_client, "WsApiClient", _FakeWsApiClient)
    monkeypatch.setattr(k8s_client, "CoreV1Api", _FakeCoreV1Api)
    monkeypatch.setattr(k8s_config, "load_incluster_config", lambda *a, **k: None)


@pytest.mark.asyncio
async def test_k8s_stream_splits_channels_and_reads_exit_code(monkeypatch):
    from app.services.ai import code_execution_sandbox as sandbox

    _install_fake_k8s(
        monkeypatch,
        [
            _FakeWsMessage(WSMsgType.BINARY, b"\x01out-1\n"),
            _FakeWsMessage(WSMsgType.BINARY, b"\x02err-1\n"),
            _FakeWsMessage(WSMsgType.BINARY, b'\x03{"status":"Success"}'),
            _FakeWsMessage(WSMsgType.CLOSE, b""),
        ],
    )
    output: list[tuple[str, bytes]] = []

    async def collect(stream: str, chunk: bytes) -> None:
        output.append((stream, chunk))

    result = await sandbox._stream_k8s_exec(
        namespace="nanzi-ai-agent",
        pod_name="as-ws-ui1--1",
        container="workspace",
        wrapped="true",
        pid_file="/workspace/sessions/conv-1/.pid",
        timeout_seconds=5,
        on_output=collect,
        is_stopped=lambda: False,
    )

    assert output == [("stdout", b"out-1\n"), ("stderr", b"err-1\n")]
    assert result.exit_code == 0
    assert not result.timed_out and not result.stopped


@pytest.mark.asyncio
async def test_k8s_stream_surfaces_nonzero_exit_code(monkeypatch):
    from app.services.ai import code_execution_sandbox as sandbox

    _install_fake_k8s(
        monkeypatch,
        [
            _FakeWsMessage(
                WSMsgType.BINARY,
                b'\x03{"status":"Failure","details":{"causes":[{"message":"2"}]}}',
            ),
            _FakeWsMessage(WSMsgType.CLOSE, b""),
        ],
    )

    async def collect(stream: str, chunk: bytes) -> None:
        pass

    result = await sandbox._stream_k8s_exec(
        namespace="nanzi-ai-agent",
        pod_name="as-ws-ui1--1",
        container="workspace",
        wrapped="false",
        pid_file="/workspace/sessions/conv-1/.pid",
        timeout_seconds=5,
        on_output=collect,
        is_stopped=lambda: False,
    )

    assert result.exit_code == 2


@pytest.mark.asyncio
async def test_k8s_stream_timeout_kills_pod_process(monkeypatch):
    from app.services.ai import code_execution_sandbox as sandbox

    _install_fake_k8s(monkeypatch, [])
    killed: list[str] = []

    async def fake_kill(core, *, namespace, pod_name, container, pid_file):
        killed.append(pid_file)

    monkeypatch.setattr(sandbox, "_kill_k8s_pod_process", fake_kill)

    async def collect(stream: str, chunk: bytes) -> None:
        pass

    result = await sandbox._stream_k8s_exec(
        namespace="nanzi-ai-agent",
        pod_name="as-ws-ui1--1",
        container="workspace",
        wrapped="sleep 600",
        pid_file="/workspace/sessions/conv-1/.pid",
        timeout_seconds=0.05,
        on_output=collect,
        is_stopped=lambda: False,
    )

    assert result.timed_out is True
    assert killed == ["/workspace/sessions/conv-1/.pid"]
