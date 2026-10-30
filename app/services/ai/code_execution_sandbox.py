"""画布「运行」的沙箱执行后端（docker / k8s）。

画布运行最初实现成「后端本机子进程」，与 ``sandbox_policy`` 无关：docker/k8s
部署下脚本仍跑在平台容器里，报错里出现的也是宿主路径
（``/app/data/agent_workspaces/...``），而不是沙箱逻辑根（``/workspace/...``）。
本模块补上这一层分流：

- docker / k8s：宿主用户根以 bind mount 挂到逻辑根，会话目录在沙箱内是
  ``<逻辑根>/sessions/<conversation_id>``；脚本先落在宿主会话目录（容器内同一
  文件），再在容器/Pod 内执行，stdout/stderr 逐块回传；
- e2b / ssh：只把技能复制进沙箱，没有「宿主工作区 → 沙箱路径」的映射，脚本
  文件沙箱看不到，因此调用方必须明确拒绝，不做「看起来能跑」的降级。

执行期间带沙箱活跃心跳：长脚本超过 ``sandbox_idle_time`` 不会被 reaper 回收。
停止/超时通过容器内 pid 文件 kill 主进程（``exec`` 后 pid 不变）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shlex
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

SANDBOX_PYTHON = "python3"
#: 逻辑根里可以执行画布脚本的策略（宿主工作区已挂载进沙箱）。
MAPPED_SANDBOX_POLICIES = frozenset({"docker", "k8s"})
#: 没有宿主工作区映射的策略：脚本文件无法被沙箱看到，必须明确拒绝。
UNMAPPED_SANDBOX_POLICIES = frozenset({"e2b", "ssh"})

OutputCallback = Callable[[str, bytes], Awaitable[None]]
StopCheck = Callable[[], bool]

#: 流读取结束的哨兵（与 ``None`` 区分：``None`` 可能来自消息本身）。
_STREAM_END = object()


class SandboxScriptUnavailable(RuntimeError):
    """沙箱暂时不可用；``str(exc)`` 直接作为用户可见文案。"""


@dataclass(frozen=True)
class SandboxScriptResult:
    exit_code: int | None = None
    timed_out: bool = False
    stopped: bool = False


def unmapped_policy_message(policy: str) -> str:
    """没有工作区映射的策略的用户可见文案（与脚本工具同口径）。"""
    return (
        f"当前沙箱策略（{policy or '未知'}）暂不支持在画布中运行脚本："
        "该策略没有「会话工作区 → 沙箱路径」的映射，脚本无法定位工作区文件。"
        "请改用 local、docker 或 k8s 策略。"
    )


def sandbox_session_workdir(*, workspace: Path, user_root: Path, sandbox_root: str) -> str:
    """把宿主会话目录映射成沙箱逻辑路径（``/workspace/sessions/<cid>``）。

    docker/k8s 挂载的是**用户根** ``<root>/<user_key>`` → ``<逻辑根>``，不是把
    会话目录单独挂到逻辑根；换算必须相对用户根，否则会多/少一层 ``sessions``。
    """
    root = Path(sandbox_root).expanduser()
    session_dir = Path(workspace).expanduser().resolve(strict=False)
    user_base = Path(user_root).expanduser().resolve(strict=False)
    try:
        relative = session_dir.relative_to(user_base)
    except ValueError as exc:
        raise ValueError(
            "会话工作区不在当前用户工作区内，无法换算沙箱路径"
        ) from exc
    return (root / relative).as_posix() or root.as_posix()


def build_sandbox_command(
    language: str,
    *,
    workdir: str,
    script_path: str,
    pid_file: str | None = None,
) -> list[str]:
    """构造沙箱内执行脚本的命令（``bash -c``，固定解释器，不读 shebang）。"""
    normalized = str(language or "").strip().lower()
    if normalized in {"python", "python3"}:
        run = f"exec {SANDBOX_PYTHON} -u {shlex.quote(script_path)}"
    elif normalized in {"sh", "shell"}:
        run = f"exec /bin/sh {shlex.quote(script_path)}"
    else:
        run = f"exec /bin/bash {shlex.quote(script_path)}"
    prefix = f"echo $$ > {shlex.quote(pid_file)}; " if pid_file else ""
    return ["/bin/bash", "-c", f"{prefix}cd {shlex.quote(workdir)} && {run}"]


async def run_sandbox_script(
    *,
    policy: str,
    language: str,
    sandbox_script_path: str,
    sandbox_workdir: str,
    user_info: dict[str, Any],
    conversation_id: str,
    timeout_seconds: float,
    on_output: OutputCallback,
    is_stopped: StopCheck,
) -> SandboxScriptResult:
    """在沙箱内执行 ``sandbox_script_path``，逐块回传 stdout/stderr。

    ``sandbox_script_path`` 必须是容器/Pod 内可见的路径（由
    :func:`sandbox_session_workdir` 换算）。
    """
    normalized = str(policy or "").strip().lower()
    if normalized == "docker":
        return await _run_docker_script(
            language=language,
            sandbox_script_path=sandbox_script_path,
            sandbox_workdir=sandbox_workdir,
            user_info=user_info,
            conversation_id=conversation_id,
            timeout_seconds=timeout_seconds,
            on_output=on_output,
            is_stopped=is_stopped,
        )
    if normalized == "k8s":
        return await _run_k8s_script(
            language=language,
            sandbox_script_path=sandbox_script_path,
            sandbox_workdir=sandbox_workdir,
            user_info=user_info,
            conversation_id=conversation_id,
            timeout_seconds=timeout_seconds,
            on_output=on_output,
            is_stopped=is_stopped,
        )
    raise SandboxScriptUnavailable(unmapped_policy_message(normalized))


def _sandbox_identity(user_info: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": user_info.get("user_id") or user_info.get("id"),
        "user_name": user_info.get("user_name") or user_info.get("username"),
        "user_info": user_info,
    }


# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------


async def _run_docker_script(
    *,
    language: str,
    sandbox_script_path: str,
    sandbox_workdir: str,
    user_info: dict[str, Any],
    conversation_id: str,
    timeout_seconds: float,
    on_output: OutputCallback,
    is_stopped: StopCheck,
) -> SandboxScriptResult:
    try:
        import aiodocker
    except Exception as exc:  # pragma: no cover - 依赖缺失时给出可读文案
        raise SandboxScriptUnavailable(
            "当前后端未安装 aiodocker，无法使用 Docker 沙箱执行脚本。"
        ) from exc

    from app.services.ai.runtime.agentscope import workspace as sandbox_workspace

    identity = _sandbox_identity(user_info)
    user_key = sandbox_workspace._resolve_sandbox_user_key(**identity)
    if not user_key:
        raise SandboxScriptUnavailable("缺少当前用户身份，无法访问 Docker 沙箱。")

    root = await sandbox_workspace.resolve_workspace_root()
    client = aiodocker.Docker()
    try:
        container, _name = await sandbox_workspace._docker_first_existing_container(
            client,
            user_key=user_key,
            root=root,
            conversation_id=conversation_id,
        )
        if container is None:
            raise SandboxScriptUnavailable(
                "未找到运行中的 Docker 沙箱容器，请先发送一条消息启动沙箱后再运行脚本。"
            )
        details = await container.show()
        state = details.get("State") if isinstance(details, dict) else {}
        if not state.get("Running"):
            raise SandboxScriptUnavailable(
                "Docker 沙箱容器未运行，请先发送一条消息启动沙箱后再运行脚本。"
            )

        pid_file = f"{sandbox_workdir}/.nanzi-code-run-{uuid.uuid4().hex}.pid"
        command = build_sandbox_command(
            language,
            workdir=sandbox_workdir,
            script_path=sandbox_script_path,
            pid_file=pid_file,
        )
        async with sandbox_workspace._sandbox_activity_heartbeat(
            sandbox_user_key=user_key,
            policy="docker",
        ):
            return await _stream_docker_exec(
                container,
                command=command,
                workdir=sandbox_workdir,
                pid_file=pid_file,
                timeout_seconds=timeout_seconds,
                on_output=on_output,
                is_stopped=is_stopped,
            )
    finally:
        with contextlib.suppress(Exception):
            await client.close()


async def _stream_docker_exec(
    container: Any,
    *,
    command: list[str],
    workdir: str,
    pid_file: str,
    timeout_seconds: float,
    on_output: OutputCallback,
    is_stopped: StopCheck,
) -> SandboxScriptResult:
    exec_obj = await container.exec(
        cmd=command,
        workdir=workdir,
        stdout=True,
        stderr=True,
        stdin=False,
        tty=False,
    )
    deadline = time.monotonic() + max(0.01, float(timeout_seconds))
    timed_out = False
    stopped = False
    stream_error: str | None = None
    queue: "asyncio.Queue[Any]" = asyncio.Queue()

    async with exec_obj.start(detach=False) as stream:
        # 读取放到独立 task 里：主循环只带超时地从队列取结果。直接在循环里对
        # ``read_out()`` 反复 ``wait_for`` 会反复取消 aiohttp 的 receive，留下
        # 半消费的帧；取消一次（任务级）才安全。
        drain_task = asyncio.create_task(_drain_docker_stream(stream, queue))
        try:
            while True:
                if is_stopped():
                    stopped = True
                    await _kill_docker_exec(container, pid_file)
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    await _kill_docker_exec(container, pid_file)
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=min(remaining, 0.5))
                except asyncio.TimeoutError:
                    continue
                if item is _STREAM_END:
                    break
                if isinstance(item, Exception):
                    stream_error = str(item) or item.__class__.__name__
                    logger.warning("[code-execution] docker exec stream error: %s", item)
                    break
                stream_name = "stderr" if getattr(item, "stream", 1) == 2 else "stdout"
                raw = getattr(item, "data", item)
                if isinstance(raw, str):
                    raw = raw.encode("utf-8", errors="replace")
                elif not isinstance(raw, (bytes, bytearray)):
                    raw = str(raw or "").encode("utf-8", errors="replace")
                await on_output(stream_name, bytes(raw))
        finally:
            drain_task.cancel()
            await asyncio.gather(drain_task, return_exceptions=True)

    exit_code: int | None = None
    with contextlib.suppress(Exception):
        inspect_info = await exec_obj.inspect()
        if isinstance(inspect_info, dict):
            raw_exit = inspect_info.get("ExitCode")
            if isinstance(raw_exit, int) and not isinstance(raw_exit, bool):
                exit_code = raw_exit
    if stream_error and exit_code is None and not stopped and not timed_out:
        raise SandboxScriptUnavailable(
            f"Docker 沙箱执行流中断，无法确认脚本退出状态，请重试：{stream_error}"
        )
    return SandboxScriptResult(
        exit_code=exit_code,
        timed_out=timed_out,
        stopped=stopped,
    )


async def _drain_docker_stream(stream: Any, queue: "asyncio.Queue[Any]") -> None:
    """把 docker exec 的流式消息转成队列条目；结束时放入哨兵。"""
    try:
        while True:
            message = await stream.read_out()
            if message is None:
                return
            queue.put_nowait(message)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - 交给主循环转成用户可读错误
        queue.put_nowait(exc)
    finally:
        queue.put_nowait(_STREAM_END)


async def _kill_docker_exec(container: Any, pid_file: str) -> None:
    """kill 容器内脚本主进程并清理 pid 文件（尽力而为，不向上抛错）。"""
    quoted = shlex.quote(pid_file)
    # 先等 pid 文件落盘（``echo $$`` 在命令最前面，正常 <100ms）：用户刚点运行就点
    # 停止时文件可能还没写，直接 cat 会拿到空值、kill 落空，脚本继续在沙箱里跑。
    kill_script = (
        f"for _i in 1 2 3 4 5 6 7 8 9 10; do [ -s {quoted} ] && break; sleep 0.1; done; "
        f"pid=$(cat {quoted} 2>/dev/null); "
        f'[ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null; '
        f'sleep 0.2; [ -n "$pid" ] && kill -KILL "$pid" 2>/dev/null; '
        f"rm -f {quoted}; true"
    )
    with contextlib.suppress(Exception):
        exec_obj = await container.exec(
            cmd=["/bin/bash", "-c", kill_script],
            stdout=False,
            stderr=False,
            stdin=False,
            tty=False,
        )
        await exec_obj.start(detach=True)


# ---------------------------------------------------------------------------
# Kubernetes
# ---------------------------------------------------------------------------


async def _run_k8s_script(
    *,
    language: str,
    sandbox_script_path: str,
    sandbox_workdir: str,
    user_info: dict[str, Any],
    conversation_id: str,
    timeout_seconds: float,
    on_output: OutputCallback,
    is_stopped: StopCheck,
) -> SandboxScriptResult:
    from app.services.ai.runtime.agentscope import workspace as sandbox_workspace
    from app.services.ai.runtime.agentscope.k8s_workspace import (
        K8S_SANDBOX_CONTAINER,
        K8sSandboxUnavailableError,
        resolve_sandbox_namespace,
    )

    identity = _sandbox_identity(user_info)
    try:
        user_key = await sandbox_workspace._k8s_runtime_guard(
            **identity,
            conversation_id=conversation_id,
            operation="exec",
        )
    except K8sSandboxUnavailableError as exc:
        raise SandboxScriptUnavailable(
            getattr(exc, "user_message", None) or str(exc)
        ) from exc

    root = await sandbox_workspace.resolve_workspace_root()
    workspace = sandbox_workspace._find_cached_k8s_workspace(
        root, user_key, conversation_id=conversation_id
    )
    # 独立 PVC 模式下 Pod 里没有挂载宿主用户工作区（/workspace 是空的卷），
    # 脚本文件传不进去；此时必须在进 Pod 前拒绝，否则用户只会看到
    # 「python: can't open file」这种把部署配置问题误读成脚本问题。
    if workspace is not None and not getattr(workspace, "_nanzi_existing_pvc", None):
        raise SandboxScriptUnavailable(
            "当前 K8s 沙箱使用独立 PVC，会话工作区没有挂载进 Pod，画布脚本无法访问工作区文件。"
            "请让管理员配置 sandbox_k8s_existing_pvc 以共享平台数据卷，或改用 docker 沙箱策略。"
        )
    if workspace is not None and getattr(workspace, "is_alive", True):
        namespace, pod_name = await sandbox_workspace._k8s_workspace_pod_identity(workspace)
    else:
        namespace, pod_name = await sandbox_workspace._k8s_named_pod_identity(user_key)
    if not pod_name:
        raise SandboxScriptUnavailable(
            "未找到运行中的 Kubernetes 沙箱 Pod，请先发送一条消息启动沙箱后再运行脚本。"
        )
    namespace = namespace or resolve_sandbox_namespace("")

    pid_file = f"{sandbox_workdir}/.nanzi-code-run-{uuid.uuid4().hex}.pid"
    command = build_sandbox_command(
        language,
        workdir=sandbox_workdir,
        script_path=sandbox_script_path,
        pid_file=pid_file,
    )
    wrapped = command[2]

    async with sandbox_workspace._sandbox_activity_heartbeat(
        sandbox_user_key=user_key,
        policy="k8s",
    ):
        return await _stream_k8s_exec(
            namespace=namespace,
            pod_name=pod_name,
            container=K8S_SANDBOX_CONTAINER,
            wrapped=wrapped,
            pid_file=pid_file,
            timeout_seconds=timeout_seconds,
            on_output=on_output,
            is_stopped=is_stopped,
        )


async def _stream_k8s_exec(
    *,
    namespace: str,
    pod_name: str,
    container: str,
    wrapped: str,
    pid_file: str,
    timeout_seconds: float,
    on_output: OutputCallback,
    is_stopped: StopCheck,
) -> SandboxScriptResult:
    from kubernetes_asyncio import client as k8s_client
    from kubernetes_asyncio import config as k8s_async_config
    from kubernetes_asyncio.stream.ws_client import (
        ERROR_CHANNEL,
        STDERR_CHANNEL,
        STDOUT_CHANNEL,
        WsApiClient,
    )

    try:
        k8s_async_config.load_incluster_config()
    except Exception:
        try:
            await k8s_async_config.load_kube_config()
        except Exception as exc:
            raise SandboxScriptUnavailable(
                "当前后端无法连接 Kubernetes 集群以执行沙箱脚本，请检查集群连接与 RBAC。"
            ) from exc

    api_client = WsApiClient()
    deadline = time.monotonic() + max(0.01, float(timeout_seconds))
    timed_out = False
    stopped = False
    exit_code: int | None = None
    stream_error: str | None = None
    try:
        core = k8s_client.CoreV1Api(api_client=api_client)
        resp = await core.connect_get_namespaced_pod_exec(
            name=pod_name,
            namespace=namespace,
            container=container,
            command=["/bin/bash", "-lc", wrapped],
            stdin=False,
            stdout=True,
            stderr=True,
            tty=False,
            _preload_content=False,
        )
        async with resp as ws:
            queue: "asyncio.Queue[Any]" = asyncio.Queue()
            drain_task = asyncio.create_task(_drain_k8s_stream(ws, queue))
            try:
                while True:
                    if is_stopped():
                        stopped = True
                        await _kill_k8s_pod_process(
                            core, namespace=namespace, pod_name=pod_name, container=container,
                            pid_file=pid_file,
                        )
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        await _kill_k8s_pod_process(
                            core, namespace=namespace, pod_name=pod_name, container=container,
                            pid_file=pid_file,
                        )
                        break
                    try:
                        item = await asyncio.wait_for(
                            queue.get(), timeout=min(remaining, 0.5)
                        )
                    except asyncio.TimeoutError:
                        continue
                    if item is _STREAM_END:
                        break
                    if isinstance(item, Exception):
                        stream_error = str(item) or item.__class__.__name__
                        logger.warning("[code-execution] k8s exec stream error: %s", item)
                        break
                    raw = getattr(item, "data", None)
                    if raw is None:
                        continue
                    if isinstance(raw, str):
                        raw = raw.encode("utf-8", errors="replace")
                    if not raw:
                        continue
                    channel = raw[0]
                    payload = raw[1:]
                    if channel == STDERR_CHANNEL:
                        await on_output("stderr", payload)
                    elif channel == STDOUT_CHANNEL:
                        await on_output("stdout", payload)
                    elif channel == ERROR_CHANNEL:
                        exit_code = _k8s_error_channel_exit_code(payload)
            finally:
                drain_task.cancel()
                await asyncio.gather(drain_task, return_exceptions=True)
    finally:
        with contextlib.suppress(Exception):
            await api_client.close()
    if stream_error and exit_code is None and not stopped and not timed_out:
        raise SandboxScriptUnavailable(
            f"K8s 沙箱执行流中断，无法确认脚本退出状态，请重试：{stream_error}"
        )
    return SandboxScriptResult(
        exit_code=exit_code,
        timed_out=timed_out,
        stopped=stopped,
    )


async def _drain_k8s_stream(ws: Any, queue: "asyncio.Queue[Any]") -> None:
    """把 k8s exec 的 websocket 帧转成队列条目；结束/断开时放入哨兵。"""
    from aiohttp import WSMsgType

    try:
        while True:
            message = await ws.receive()
            if message is None:
                return
            if message.type in (
                WSMsgType.CLOSE,
                WSMsgType.CLOSING,
                WSMsgType.CLOSED,
                WSMsgType.ERROR,
            ):
                return
            queue.put_nowait(message)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - 交给主循环转成用户可读错误
        queue.put_nowait(exc)
    finally:
        queue.put_nowait(_STREAM_END)


def _k8s_error_channel_exit_code(payload: bytes) -> int | None:
    """解析 exec 的 error channel：``{"status": ...}``，失败时带退出码。"""
    try:
        data = json.loads(payload.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("status") == "Success":
        return 0
    details = data.get("details") if isinstance(data.get("details"), dict) else {}
    causes = details.get("causes") if isinstance(details.get("causes"), list) else []
    for cause in causes:
        if not isinstance(cause, dict):
            continue
        try:
            return int(str(cause.get("message")))
        except (TypeError, ValueError):
            continue
    return None


async def _kill_k8s_pod_process(
    core: Any,
    *,
    namespace: str,
    pod_name: str,
    container: str,
    pid_file: str,
) -> None:
    """在 Pod 内 kill 脚本主进程（尽力而为）。"""
    quoted = shlex.quote(pid_file)
    # 先等 pid 文件落盘（``echo $$`` 在命令最前面，正常 <100ms）：用户刚点运行就点
    # 停止时文件可能还没写，直接 cat 会拿到空值、kill 落空，脚本继续在沙箱里跑。
    kill_script = (
        f"for _i in 1 2 3 4 5 6 7 8 9 10; do [ -s {quoted} ] && break; sleep 0.1; done; "
        f"pid=$(cat {quoted} 2>/dev/null); "
        f'[ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null; '
        f'sleep 0.2; [ -n "$pid" ] && kill -KILL "$pid" 2>/dev/null; '
        f"rm -f {quoted}; true"
    )
    with contextlib.suppress(Exception):
        await core.connect_get_namespaced_pod_exec(
            name=pod_name,
            namespace=namespace,
            container=container,
            command=["/bin/bash", "-lc", kill_script],
            stdin=False,
            stdout=False,
            stderr=False,
            tty=False,
        )
