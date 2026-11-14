"""Private Docker and Kubernetes transport for credential-bearing scripts."""

from __future__ import annotations

import asyncio
import logging
import math
import sys
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

_ERROR_MESSAGES = {
    "provider_unavailable": "当前沙箱无法隔离执行含凭据的脚本。",
    "interpreter_missing": "凭据脚本执行镜像缺少 python 或 python3，脚本未执行。",
    "transport_failed": "凭据脚本执行通道异常，未返回脚本输出。",
    "output_limit": "凭据脚本输出超过执行通道上限。",
    "cleanup_failed": "凭据脚本私有实例清理失败，未返回脚本输出。",
}
_TIMEOUT = "含凭据的脚本执行超时。"
_OUTPUT_LIMIT = 16 * 1024 * 1024
_INSTANCE_TTL_MARGIN_SECONDS = 30
_CLEANUP_TIMEOUT_SECONDS = 10
_STDIN_CHUNK_SIZE = 64 * 1024
_POLL_INTERVAL_SECONDS = 0.2
_WORKER_NAME = "credential-script"
_INTERPRETER_PROBE = [
    "/bin/sh", "-c",
    "if command -v python >/dev/null; then printf 'python\n'; "
    "elif command -v python3 >/dev/null; then printf 'python3\n'; "
    "else printf 'missing\n'; fi",
]


class CredentialExecutionUnavailable(RuntimeError):
    """The credential execution path failed closed with a fixed diagnostic."""

    def __init__(self, code: str = "provider_unavailable") -> None:
        self.code = code
        super().__init__(_ERROR_MESSAGES[code])


def validate_credential_provider(workspace: Any) -> str:
    """Check the selected policy and the initialized provider before resolving secrets."""
    try:
        policy = getattr(workspace, "_platform_sandbox_policy", None)
        if policy == "docker":
            containers = workspace._client.containers
            if (
                _nonempty_string(workspace._image_tag)
                and callable(containers.create)
                and callable(containers.container)
            ):
                return policy
        if policy == "k8s":
            from kubernetes_asyncio.stream import WsApiClient

            core = workspace._v1
            if (
                _nonempty_string(workspace._image)
                and _nonempty_string(workspace._namespace)
                and core.api_client.configuration is not None
                and callable(core.create_namespaced_pod)
                and callable(core.read_namespaced_pod)
                and callable(core.delete_namespaced_pod)
                and callable(WsApiClient)
            ):
                return policy
    except Exception:
        raise CredentialExecutionUnavailable() from None
    raise CredentialExecutionUnavailable()


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _instance_labels(name: str) -> dict[str, str]:
    # Register the unique name with the existing orphan reconciler. It does not
    # have the format used by persisted workspace IDs and their skill directories.
    return {
        "agentscope.workspace": "true",
        "agentscope.workspace.id": name,
        "nanzi.credential-script": "true",
    }


async def _force_remove(remove: Callable[[], Awaitable[None]]) -> None:
    try:
        async with asyncio.timeout(_CLEANUP_TIMEOUT_SECONDS):
            await remove()
    except Exception as exc:
        if getattr(exc, "status", None) != 404:
            raise CredentialExecutionUnavailable("cleanup_failed") from None


async def _finish_removal(remove: Callable[[], Awaitable[None]]) -> None:
    already_cancelled = isinstance(sys.exception(), asyncio.CancelledError)
    cleanup = asyncio.create_task(_force_remove(remove))
    interrupted = None
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError as exc:
            if cleanup.cancelled():
                raise
            # Wait for bounded cleanup even when the caller cancels repeatedly.
            interrupted = exc
        except CredentialExecutionUnavailable:
            break
    try:
        cleanup.result()
    except CredentialExecutionUnavailable:
        if not already_cancelled and interrupted is None:
            raise
        logger.warning("Failed to remove a private credential script instance")
    if interrupted is not None:
        raise interrupted from None


async def run_credential_process(
    workspace: Any, *, bootstrap: str, payload: bytes, timeout_seconds: float,
) -> bytes:
    """Run a trusted bootstrap with a length-prefixed input sent over private stdin."""
    policy = validate_credential_provider(workspace)
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise TimeoutError(_TIMEOUT)
    name = f"{_WORKER_NAME}-{uuid4().hex}"
    # The Docker and Kubernetes streams do not expose a shared half-close API.
    # The length line lets the bootstrap finish reading without transport EOF.
    frame = str(len(payload)).encode("ascii") + b"\n" + payload
    deadline = asyncio.timeout(timeout_seconds)
    ttl_seconds = math.ceil(timeout_seconds) + _INSTANCE_TTL_MARGIN_SECONDS

    try:
        if policy == "docker":
            containers = workspace._client.containers
            # Construct a handle before create so a lost create response still
            # permits cleanup by the requested name.
            instance = containers.container(name)

            async def remove() -> None:
                await instance.delete(force=True, v=True)
        else:
            core, namespace = workspace._v1, workspace._namespace

            async def remove() -> None:
                await core.delete_namespaced_pod(
                    name=name, namespace=namespace, grace_period_seconds=0,
                )

        try:
            async with deadline:
                if policy == "docker":
                    return await _run_docker(workspace, containers, name, bootstrap, frame, ttl_seconds)
                return await _run_k8s(workspace, core, namespace, name, bootstrap, frame, ttl_seconds)
        finally:
            await _finish_removal(remove)
    except TimeoutError:
        if deadline.expired():
            raise TimeoutError(_TIMEOUT) from None
        raise CredentialExecutionUnavailable("transport_failed") from None
    except CredentialExecutionUnavailable:
        raise
    except Exception:
        raise CredentialExecutionUnavailable("transport_failed") from None


def _worker_command(probe: bytes, bootstrap: str) -> list[str]:
    match probe.strip():
        case b"python":
            interpreter = "python"
        case b"python3":
            interpreter = "python3"
        case b"missing":
            raise CredentialExecutionUnavailable("interpreter_missing")
        case _:
            raise CredentialExecutionUnavailable("transport_failed")
    return [interpreter, "-I", "-c", bootstrap]


class _ProcessOutput:
    def __init__(self) -> None:
        self.stdout = bytearray()
        self.size = 0

    def read(self, channel: int, data: bytes) -> None:
        self.size += len(data)
        if self.size > _OUTPUT_LIMIT:
            raise CredentialExecutionUnavailable("output_limit")
        if channel == 1:
            self.stdout.extend(data)


async def _run_docker(
    workspace: Any, containers: Any, name: str, bootstrap: str, frame: bytes,
    ttl_seconds: int,
) -> bytes:
    limits = getattr(workspace, "_nanzi_resource_limits", {})
    host_config: dict[str, Any] = {
        "AutoRemove": True,
        "NetworkMode": "bridge",
        "Privileged": False,
        "CapDrop": ["ALL"],
        "SecurityOpt": ["no-new-privileges"],
    }
    if memory := limits.get("memory_bytes"):
        host_config.update(Memory=memory, MemorySwap=memory)
    if cpus := limits.get("nano_cpus"):
        host_config["NanoCPUs"] = cpus
    if pids := limits.get("pids_limit"):
        host_config["PidsLimit"] = pids

    instance = await containers.create(
        name=name,
        config={
            "Image": workspace._image_tag,
            "Entrypoint": ["sleep"],
            "Cmd": [str(ttl_seconds)],
            "Labels": _instance_labels(name),
            "HostConfig": host_config,
        },
    )
    await instance.start()
    probe = await _docker_exec(instance, _INTERPRETER_PROBE)
    return await _docker_exec(instance, _worker_command(probe, bootstrap), frame)


async def _docker_exec(instance: Any, command: list[str], frame: bytes = b"") -> bytes:
    process = await instance.exec(command, stdin=bool(frame), stdout=True, stderr=True, tty=False)
    output = _ProcessOutput()
    async with process.start() as stream:
        for offset in range(0, len(frame), _STDIN_CHUNK_SIZE):
            await stream.write_in(frame[offset:offset + _STDIN_CHUNK_SIZE])
        while message := await stream.read_out():
            output.read(message.stream, message.data)

    while (status := await process.inspect()).get("Running"):
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    if status.get("ExitCode") != 0:
        raise CredentialExecutionUnavailable("transport_failed")
    return bytes(output.stdout)


async def _run_k8s(
    workspace: Any, core: Any, namespace: str, name: str, bootstrap: str, frame: bytes,
    ttl_seconds: int,
) -> bytes:
    from kubernetes_asyncio import client
    from kubernetes_asyncio.stream import WsApiClient

    resources = getattr(workspace, "_resources", None)
    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(name=name, namespace=namespace, labels=_instance_labels(name)),
        spec=client.V1PodSpec(
            active_deadline_seconds=ttl_seconds,
            restart_policy="Never",
            automount_service_account_token=False,
            enable_service_links=False,
            termination_grace_period_seconds=0,
            image_pull_secrets=[
                client.V1LocalObjectReference(name=secret)
                for secret in (getattr(workspace, "_image_pull_secrets", None) or ())
            ],
            node_selector=getattr(workspace, "_node_selector", None),
            tolerations=[
                client.V1Toleration(**toleration)
                for toleration in (getattr(workspace, "_tolerations", None) or ())
            ],
            containers=[
                client.V1Container(
                    name=_WORKER_NAME,
                    image=workspace._image,
                    image_pull_policy=getattr(workspace, "_image_pull_policy", None),
                    command=["sleep", str(ttl_seconds)],
                    resources=client.V1ResourceRequirements(**resources) if resources else None,
                    security_context=client.V1SecurityContext(
                        privileged=False,
                        allow_privilege_escalation=False,
                        capabilities=client.V1Capabilities(drop=["ALL"]),
                        seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
                    ),
                ),
            ],
        ),
    )
    await core.create_namespaced_pod(namespace=namespace, body=pod)
    await _wait_for_k8s_worker(core, namespace, name)

    async with WsApiClient(configuration=core.api_client.configuration) as api:
        exec_core = client.CoreV1Api(api_client=api)
        probe = await _k8s_exec(exec_core, namespace, name, _INTERPRETER_PROBE)
        return await _k8s_exec(exec_core, namespace, name, _worker_command(probe, bootstrap), frame)


async def _k8s_exec(
    core: Any, namespace: str, name: str, command: list[str], frame: bytes = b"",
) -> bytes:
    from aiohttp import WSMsgType
    from kubernetes_asyncio.stream import WsApiClient
    from kubernetes_asyncio.stream.ws_client import ERROR_CHANNEL, STDIN_CHANNEL

    output = _ProcessOutput()
    exit_code = None
    connection = await core.connect_get_namespaced_pod_exec(
        name=name, namespace=namespace, container=_WORKER_NAME,
        command=command, stdin=bool(frame), stdout=True, stderr=True, tty=False,
        _preload_content=False,
    )
    async with connection as ws:
        for offset in range(0, len(frame), _STDIN_CHUNK_SIZE):
            await ws.send_bytes(bytes([STDIN_CHANNEL]) + frame[offset:offset + _STDIN_CHUNK_SIZE])
        async for message in ws:
            if message.type != WSMsgType.BINARY or not message.data:
                raise CredentialExecutionUnavailable("transport_failed")
            channel, data = message.data[0], message.data[1:]
            output.read(channel, data)
            if channel == ERROR_CHANNEL:
                exit_code = WsApiClient.parse_error_data(data)

    if exit_code != 0:
        raise CredentialExecutionUnavailable("transport_failed")
    return bytes(output.stdout)


async def _wait_for_k8s_worker(core: Any, namespace: str, name: str) -> None:
    while True:
        pod = await core.read_namespaced_pod(name=name, namespace=namespace)
        if pod.status.phase in {"Failed", "Succeeded"}:
            raise CredentialExecutionUnavailable("transport_failed")
        if pod.status.phase == "Running" and any(
            status.name == _WORKER_NAME and status.ready
            for status in pod.status.container_statuses or ()
        ):
            return
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
