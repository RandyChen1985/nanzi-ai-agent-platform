"""Contract tests with mocked Docker and Kubernetes clients."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from aiohttp import WSMsgType
from kubernetes_asyncio.client import ApiClient
from kubernetes_asyncio.stream import WsApiClient

from app.services.ai.tools import credential_script_providers as providers


BOOTSTRAP = "print('trusted bootstrap')"
PAYLOAD = b'{"credential":"synthetic-secret"}'
STDIN_FRAME = b"33\n" + PAYLOAD


def _workspace(policy, monkeypatch):
    workspace = SimpleNamespace(
        _platform_sandbox_policy=policy,
        _container=Mock(),
        env={"LEGACY": "parent-env-secret"},
    )
    if policy == "docker":
        stream = MagicMock()
        stream.__aenter__.return_value = stream
        stream.write_in = AsyncMock()
        stream.read_out = AsyncMock(side_effect=[
            SimpleNamespace(stream=2, data=b"hidden stderr"),
            SimpleNamespace(stream=1, data=b"output"),
            None,
        ])
        command = SimpleNamespace(start=Mock(return_value=stream),
                                  inspect=AsyncMock(return_value={"Running": False, "ExitCode": 0}))
        private_container = SimpleNamespace(start=AsyncMock(), exec=AsyncMock(return_value=command))
        containers = SimpleNamespace(
            create=AsyncMock(return_value=private_container),
            container=Mock(return_value=SimpleNamespace(delete=AsyncMock())),
        )
        workspace._client = SimpleNamespace(containers=containers)
        workspace._image_tag = "worker:test"
        workspace._nanzi_resource_limits = {"memory_bytes": 1024, "nano_cpus": 1000, "pids_limit": 32}
        return workspace, SimpleNamespace(
            create=containers.create, delete=containers.container.return_value.delete,
            remove_handle=containers.container, exec=private_container.exec, send=stream.write_in,
            read=stream.read_out, inspect=command.inspect,
        )

    status = SimpleNamespace(phase="Running", container_statuses=[
        SimpleNamespace(name="credential-script", ready=True),
    ])
    workspace._v1 = SimpleNamespace(
        api_client=SimpleNamespace(configuration=object()),
        create_namespaced_pod=AsyncMock(),
        delete_namespaced_pod=AsyncMock(),
        read_namespaced_pod=AsyncMock(return_value=SimpleNamespace(status=status)),
    )
    workspace._namespace = "test-namespace"
    workspace._image = "worker:test"
    workspace._image_pull_policy = "IfNotPresent"
    workspace._resources = {"limits": {"cpu": "2"}}
    workspace._node_selector = {"sandbox": "true"}
    workspace._tolerations = []
    workspace._image_pull_secrets = ["image-pull"]
    ws = MagicMock()
    ws.send_bytes = AsyncMock()
    ws.__aiter__.return_value = [
        SimpleNamespace(type=WSMsgType.BINARY, data=b"\x02hidden stderr"),
        SimpleNamespace(type=WSMsgType.BINARY, data=b"\x01output"),
        SimpleNamespace(type=WSMsgType.BINARY, data=b'\x03{"status":"Success"}'),
    ]
    conn = MagicMock()
    conn.__aenter__.return_value = ws
    exec_api = AsyncMock(return_value=conn)
    monkeypatch.setattr(
        "kubernetes_asyncio.client.CoreV1Api",
        Mock(return_value=SimpleNamespace(connect_get_namespaced_pod_exec=exec_api)),
    )
    ws_client = Mock(return_value=MagicMock(), parse_error_data=WsApiClient.parse_error_data)
    monkeypatch.setattr("kubernetes_asyncio.stream.WsApiClient", ws_client)
    return workspace, SimpleNamespace(
        create=workspace._v1.create_namespaced_pod,
        delete=workspace._v1.delete_namespaced_pod,
        exec=exec_api,
        send=ws.send_bytes,
        read=workspace._v1.read_namespaced_pod,
        websocket=ws,
    )


async def _run(workspace, *, timeout=2):
    return await providers.run_credential_process(
        workspace, bootstrap=BOOTSTRAP, payload=PAYLOAD, timeout_seconds=timeout,
    )


@pytest.mark.parametrize("policy", ["local", "ssh", "k8s", "docker", None])
def test_unavailable_provider_fails_closed(policy):
    with pytest.raises(providers.CredentialExecutionUnavailable):
        providers.validate_credential_provider(SimpleNamespace(_platform_sandbox_policy=policy))


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["docker", "k8s"])
async def test_private_instance_sends_payload_only_over_stdin(policy, monkeypatch):
    workspace, calls = _workspace(policy, monkeypatch)
    assert providers.validate_credential_provider(workspace) == policy
    assert await _run(workspace) == b"output"
    assert PAYLOAD not in repr(calls.create.call_args).encode()
    assert PAYLOAD not in repr(calls.exec.call_args).encode()
    assert "parent-env-secret" not in repr(calls.create.call_args)
    assert workspace._container.mock_calls == []

    if policy == "docker":
        config = calls.create.call_args.kwargs["config"]
        host = config["HostConfig"]
        assert config["Image"] == "worker:test"
        assert host["Memory"] == host["MemorySwap"] == 1024
        assert host["NanoCPUs"] == 1000 and host["PidsLimit"] == 32
        assert not {"Env", "Volumes"} & config.keys()
        assert not {"Binds", "VolumesFrom", "Mounts", "PidMode"} & host.keys()
        assert host["NetworkMode"] != "host"
        assert not host.get("Privileged")
        name = calls.create.call_args.kwargs["name"]
        calls.remove_handle.assert_called_once_with(name)
        calls.exec.assert_awaited_once_with(
            ["python", "-I", "-c", BOOTSTRAP], stdin=True, stdout=True, stderr=True, tty=False,
        )
        calls.send.assert_awaited_once_with(STDIN_FRAME)
        calls.delete.assert_awaited_once_with(force=True, v=True)
    else:
        async with ApiClient() as api:
            pod = api.sanitize_for_serialization(calls.create.call_args.kwargs["body"])
        spec = pod["spec"]
        worker = spec["containers"][0]
        assert worker["image"] == "worker:test"
        assert worker["resources"]["limits"] == {"cpu": "2"}
        assert worker["imagePullPolicy"] == "IfNotPresent"
        assert not spec["automountServiceAccountToken"]
        assert not spec["enableServiceLinks"]
        assert not spec.get("hostPID") and not spec.get("hostNetwork")
        assert "volumes" not in spec and "volumeMounts" not in worker
        assert "env" not in worker and not worker["securityContext"]["privileged"]
        assert spec["activeDeadlineSeconds"] >= 2
        name = pod["metadata"]["name"]
        assert calls.exec.call_args.kwargs["name"] == name
        assert calls.exec.call_args.kwargs["command"] == ["python", "-I", "-c", BOOTSTRAP]
        assert calls.exec.call_args.kwargs["stdin"]
        assert calls.exec.call_args.kwargs["_preload_content"] is False
        calls.send.assert_awaited_once_with(b"\x00" + STDIN_FRAME)
        calls.delete.assert_awaited_once_with(
            name=name, namespace="test-namespace", grace_period_seconds=0,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["docker", "k8s"])
@pytest.mark.parametrize("finish", ["timeout", "cancel"])
async def test_interrupted_create_still_forces_delete(policy, finish, monkeypatch):
    workspace, calls = _workspace(policy, monkeypatch)
    entered = asyncio.Event()

    async def stall_create(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    calls.create.side_effect = stall_create
    task = asyncio.create_task(_run(workspace, timeout=0.025 if finish == "timeout" else 2))
    await entered.wait()
    if finish == "cancel":
        task.cancel()
    with pytest.raises(TimeoutError if finish == "timeout" else asyncio.CancelledError):
        await task
    calls.delete.assert_awaited_once()
    calls.exec.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["docker", "k8s"])
async def test_cleanup_failure_fails_closed(policy, monkeypatch):
    workspace, calls = _workspace(policy, monkeypatch)
    calls.delete.side_effect = RuntimeError("synthetic-secret")
    with pytest.raises(providers.CredentialExecutionUnavailable) as exc:
        await _run(workspace)
    assert "synthetic-secret" not in str(exc.value)
    assert exc.value.__suppress_context__


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["docker", "k8s"])
async def test_output_limit_applies_to_stderr(policy, monkeypatch):
    workspace, calls = _workspace(policy, monkeypatch)
    oversized = b"x" * (16 * 1024 * 1024 + 1)
    if policy == "docker":
        calls.read.side_effect = [SimpleNamespace(stream=2, data=oversized), None]
    else:
        calls.websocket.__aiter__.return_value = [
            SimpleNamespace(type=WSMsgType.BINARY, data=b"\x02" + oversized),
        ]
    with pytest.raises(providers.CredentialExecutionUnavailable):
        await _run(workspace)
    calls.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_k8s_exec_waits_for_container_ready(monkeypatch):
    workspace, calls = _workspace("k8s", monkeypatch)
    ready_pod = calls.read.return_value
    not_ready = SimpleNamespace(status=SimpleNamespace(
        phase="Running", container_statuses=[SimpleNamespace(name="credential-script", ready=False)],
    ))

    async def read_pod(**kwargs):
        calls.exec.assert_not_awaited()
        return not_ready if calls.read.await_count == 1 else ready_pod

    calls.read.side_effect = read_pod
    assert await _run(workspace) == b"output"
    assert calls.read.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["docker", "k8s"])
async def test_concurrent_calls_delete_their_own_private_instances(policy, monkeypatch):
    workspace, calls = _workspace(policy, monkeypatch)
    both_creating = asyncio.Event()

    async def create(**kwargs):
        if calls.create.await_count == 2:
            both_creating.set()
        await both_creating.wait()
        return calls.create.return_value

    calls.create.side_effect = create
    if policy == "docker":
        per_task_reads = {}

        async def read():
            task = asyncio.current_task()
            if per_task_reads.get(task):
                return None
            per_task_reads[task] = True
            return SimpleNamespace(stream=1, data=b"output")

        calls.read.side_effect = read

    assert await asyncio.gather(_run(workspace), _run(workspace)) == [b"output", b"output"]
    if policy == "docker":
        names = {call.kwargs["name"] for call in calls.create.await_args_list}
        removed = {call.args[0] for call in calls.remove_handle.call_args_list}
    else:
        names = {call.kwargs["body"].metadata.name for call in calls.create.await_args_list}
        removed = {call.kwargs["name"] for call in calls.delete.await_args_list}
    assert len(names) == 2
    assert removed == names


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["docker", "k8s"])
async def test_cancel_propagates_even_when_delete_fails(policy, monkeypatch, caplog):
    workspace, calls = _workspace(policy, monkeypatch)
    entered = asyncio.Event()

    async def stall(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    calls.create.side_effect = stall
    calls.delete.side_effect = RuntimeError("synthetic-secret")
    task = asyncio.create_task(_run(workspace))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    calls.delete.assert_awaited_once()
    assert "synthetic-secret" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["docker", "k8s"])
async def test_instance_ttl_outlasts_execution_timeout(policy, monkeypatch):
    workspace, calls = _workspace(policy, monkeypatch)
    assert await _run(workspace, timeout=1200) == b"output"
    if policy == "docker":
        ttl = int(calls.create.call_args.kwargs["config"]["Cmd"][0])
    else:
        ttl = calls.create.call_args.kwargs["body"].spec.active_deadline_seconds
    assert ttl > 1200
