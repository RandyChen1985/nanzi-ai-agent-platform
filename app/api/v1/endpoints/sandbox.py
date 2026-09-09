"""沙箱管理（运维/管理）端点。

当前提供 docker 策略沙箱镜像的预构建（预热）以及 E2B/SSH 连接测试能力：

- ``GET  /admin/sandbox/docker/prebuild-status``：查询 docker 沙箱镜像是否已预构建
  以及当前后端是否具备自动构建能力（仅本地 inspect，不触发构建）。
- ``POST /admin/sandbox/docker/prebuild``：执行一次镜像预构建，使运行时
  docker 沙箱首次创建时不需现场构建（分钟级）而直接命中缓存（秒级）。
- ``POST /sandbox/docker/workspace/ensure``：当前登录用户手动启动或复用自己的
  Docker 工作区容器，不执行用户命令。
- ``GET  /sandbox/docker/workspace/status``：只读查询当前登录用户的 Docker
  工作区容器状态，不触发初始化。
- ``POST /admin/sandbox/{e2b|ssh}/test-connection``：按管理页面当前填写的配置
  初始化一次真实沙箱，完成连通性/初始化检查后立即释放资源。

这些端点都挂在 ``v1_secured`` 下（继承 ``require_api_key`` +
``verify_v1_api_access``）。镜像预构建和 E2B/SSH 连通性测试额外校验
``role == "admin"``；用户工作区 ensure 接口只使用当前登录用户身份。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Any, AsyncGenerator, Dict, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.dependencies import require_api_key
from app.schemas.response import StandardResponse
from app.services.ai.runtime.agentscope.k8s_workspace import K8sSandboxUnavailableError
from app.services.ai.runtime.agentscope.docker_prebuild import (
    docker_workspace_prebuild_status,
    prebuild_docker_workspace_image,
)
from app.services.ai.runtime.agentscope.workspace import (
    DockerSandboxUnavailableError,
    build_sandbox_workspace_for_test,
    docker_workspace_status as docker_workspace_status_runtime,
    docker_workspace_runtime_metadata,
    ensure_docker_workspace as ensure_docker_workspace_runtime,
    stop_docker_workspace as stop_docker_workspace_runtime,
    restart_docker_workspace as restart_docker_workspace_runtime,
    exec_docker_workspace_command as exec_docker_workspace_command_runtime,
    ensure_k8s_workspace as ensure_k8s_workspace_runtime,
    exec_k8s_workspace_command as exec_k8s_workspace_command_runtime,
    k8s_workspace_status as k8s_workspace_status_runtime,
    restart_k8s_workspace as restart_k8s_workspace_runtime,
    stop_k8s_workspace as stop_k8s_workspace_runtime,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _require_admin(user_info: Dict[str, Any]) -> None:
    """普通 API 用户无权执行沙箱管理操作。"""
    role = user_info.get("role") or user_info.get("user_role")
    if role != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可执行沙箱管理操作")


def _sse_event(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get(
    "/admin/sandbox/docker/prebuild-status",
    response_model=StandardResponse[Dict[str, Any]],
    summary="查询 Docker 沙箱镜像是否已预构建",
)
async def get_docker_prebuild_status(
    base_image: str | None = Query(None, description="临时指定的 Docker 基础镜像，为空则使用系统配置"),
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """查询 Docker 镜像缓存和自动构建能力，离线时返回手动下载信息。"""
    _require_admin(user_info)
    status = await docker_workspace_prebuild_status(base_image=base_image)
    return StandardResponse(
        data=status,
        message=status.get("message") or "success",
    )


@router.post(
    "/admin/sandbox/docker/prebuild",
    response_model=StandardResponse[Dict[str, Any]],
    summary="预构建 Docker 沙箱镜像（预热）",
)
async def trigger_docker_prebuild(
    base_image: str | None = Query(None, description="临时指定的 Docker 基础镜像，为空则使用系统配置"),
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """强制构建（或复用已存在）Docker 沙箱基础镜像。

    流程与运行时 ``DockerWorkspace._build_or_reuse_image`` 完全一致，tag 也完全
    一致；幂等：镜像已存在则直接返回 ``reused=True`` 不重复构建。构建成功后写入
    配置标记 ``sandbox_docker_prebuild_done``。

    注意：这是一个**同步**长耗时操作（首次构建分钟级），会阻塞当前请求直到完成。
    如果后端无法访问 Docker daemon，则返回 ``action=manual_download`` 和镜像导入
    信息，不会把环境不支持误报成构建失败。
    """
    _require_admin(user_info)
    try:
        result = await prebuild_docker_workspace_image(
            base_image=base_image,
            force=False,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return StandardResponse(
        data=result,
        message=result.get("message")
        if result.get("action") == "manual_download"
        else "success",
    )


@router.post(
    "/admin/sandbox/docker/prebuild/stream",
    summary="实时预构建 Docker 沙箱镜像（管理员）",
)
async def stream_docker_prebuild(
    request: Request,
    base_image: str | None = Query(None, description="临时指定的 Docker 基础镜像，为空则使用系统配置"),
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """以 SSE 转发管理员 Docker 预构建的阶段、原始日志和最终结果。"""
    _require_admin(user_info)

    events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def publish(event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "log")
        payload = {key: value for key, value in event.items() if key != "type"}
        await events.put({"event": event_type, "data": payload})

    async def run_prebuild() -> None:
        try:
            result = await prebuild_docker_workspace_image(
                base_image=base_image,
                force=False,
                on_event=publish,
            )
            await events.put({"event": "result", "data": result})
        except asyncio.CancelledError:
            raise
        except RuntimeError as exc:
            await events.put(
                {
                    "event": "error",
                    "data": {
                        "reason_code": "docker_build_failed",
                        "message": str(exc),
                    },
                }
            )
        except Exception as exc:  # pragma: no cover - defensive endpoint boundary
            logger.exception("Docker 预构建 SSE 任务异常")
            await events.put(
                {
                    "event": "error",
                    "data": {
                        "reason_code": "docker_prebuild_stream_failed",
                        "message": str(exc),
                    },
                }
            )
        finally:
            await events.put({"event": "done", "data": {}})

    task = asyncio.create_task(run_prebuild())

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            while True:
                if await request.is_disconnected():
                    task.cancel()
                    return
                try:
                    event = await asyncio.wait_for(events.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                yield _sse_event(event["event"], event["data"])
                if event["event"] == "done":
                    await task
                    break
        finally:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class DockerWorkspaceEnsureRequest(BaseModel):
    """当前用户手动启动 Docker 工作区所需的会话标识。"""

    conversation_id: str


class DockerWorkspaceExecRequest(BaseModel):
    """当前用户在 Docker 容器内执行命令请求。"""

    conversation_id: str
    command: str
    workdir: str | None = None


@router.post(
    "/sandbox/docker/workspace/ensure",
    response_model=StandardResponse[Dict[str, Any]],
    summary="启动或复用当前用户的 Docker 沙箱容器",
)
async def ensure_docker_workspace_endpoint(
    body: DockerWorkspaceEnsureRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """只确保当前用户容器运行，不在容器内执行用户命令。"""
    conversation_id = body.conversation_id.strip()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id 不能为空")

    try:
        workspace = await ensure_docker_workspace_runtime(
            user_id=user_info.get("user_id") or user_info.get("id"),
            user_name=user_info.get("user_name") or user_info.get("username"),
            user_info=user_info,
            conversation_id=conversation_id,
        )
    except DockerSandboxUnavailableError as exc:
        status_code = (
            409
            if exc.reason_code == "docker_policy_not_effective"
            else 503
        )
        raise HTTPException(
            status_code=status_code,
            detail={
                "reason_code": exc.reason_code,
                "message": exc.user_message,
            },
        ) from exc

    return StandardResponse(
        data=docker_workspace_runtime_metadata(workspace),
        message="Docker 沙箱容器已运行。",
    )


@router.post(
    "/sandbox/docker/workspace/stop",
    response_model=StandardResponse[Dict[str, Any]],
    summary="停止当前用户的 Docker 沙箱容器",
)
async def stop_docker_workspace_endpoint(
    body: DockerWorkspaceEnsureRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """停止当前用户的 Docker 沙箱容器并清理缓存。"""
    conversation_id = body.conversation_id.strip()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id 不能为空")

    try:
        result = await stop_docker_workspace_runtime(
            user_id=user_info.get("user_id") or user_info.get("id"),
            user_name=user_info.get("user_name") or user_info.get("username"),
            user_info=user_info,
            conversation_id=conversation_id,
        )
    except DockerSandboxUnavailableError as exc:
        status_code = 409 if exc.reason_code == "docker_policy_not_effective" else 503
        raise HTTPException(
            status_code=status_code,
            detail={
                "reason_code": exc.reason_code,
                "message": exc.user_message,
            },
        ) from exc

    return StandardResponse(
        data=result,
        message="Docker 沙箱容器已停止。",
    )


@router.post(
    "/sandbox/docker/workspace/restart",
    response_model=StandardResponse[Dict[str, Any]],
    summary="重启当前用户的 Docker 沙箱容器（删除旧容器并拉起新容器）",
)
async def restart_docker_workspace_endpoint(
    body: DockerWorkspaceEnsureRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """删除旧 Docker 容器并重新拉起全新的容器。"""
    conversation_id = body.conversation_id.strip()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id 不能为空")

    try:
        result = await restart_docker_workspace_runtime(
            user_id=user_info.get("user_id") or user_info.get("id"),
            user_name=user_info.get("user_name") or user_info.get("username"),
            user_info=user_info,
            conversation_id=conversation_id,
        )
    except DockerSandboxUnavailableError as exc:
        status_code = 409 if exc.reason_code == "docker_policy_not_effective" else 503
        raise HTTPException(
            status_code=status_code,
            detail={
                "reason_code": exc.reason_code,
                "message": exc.user_message,
            },
        ) from exc

    return StandardResponse(
        data=result,
        message="Docker 沙箱容器已重启完成。",
    )


@router.post(
    "/sandbox/docker/workspace/exec",
    response_model=StandardResponse[Dict[str, Any]],
    summary="在当前用户的 Docker 沙箱容器中执行终端命令",
)
async def exec_docker_workspace_endpoint(
    body: DockerWorkspaceExecRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """执行交互终端命令并返回输出。"""
    conversation_id = body.conversation_id.strip()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id 不能为空")

    try:
        result = await exec_docker_workspace_command_runtime(
            user_id=user_info.get("user_id") or user_info.get("id"),
            user_name=user_info.get("user_name") or user_info.get("username"),
            user_info=user_info,
            conversation_id=conversation_id,
            command=body.command,
            workdir=body.workdir,
        )
    except DockerSandboxUnavailableError as exc:
        status_code = 409 if exc.reason_code in ("docker_policy_not_effective", "docker_container_not_running") else 503
        raise HTTPException(
            status_code=status_code,
            detail={
                "reason_code": exc.reason_code,
                "message": exc.user_message,
            },
        ) from exc

    return StandardResponse(
        data=result,
        message="命令执行完成。",
    )


@router.get(
    "/sandbox/docker/workspace/status",
    response_model=StandardResponse[Dict[str, Any]],
    summary="查询当前用户的 Docker 沙箱容器状态",
)
async def get_docker_workspace_status_endpoint(
    conversation_id: str = Query(..., min_length=1),
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """只查询当前用户容器，不触发 DockerWorkspace 初始化。"""
    conversation_id = conversation_id.strip()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id 不能为空")

    try:
        status = await docker_workspace_status_runtime(
            user_id=user_info.get("user_id") or user_info.get("id"),
            user_name=user_info.get("user_name") or user_info.get("username"),
            user_info=user_info,
            conversation_id=conversation_id,
        )
    except DockerSandboxUnavailableError as exc:
        status_code = 409 if exc.reason_code == "docker_policy_not_effective" else 503
        raise HTTPException(
            status_code=status_code,
            detail={
                "reason_code": exc.reason_code,
                "message": exc.user_message,
            },
        ) from exc

    return StandardResponse(
        data=status,
        message="Docker 沙箱状态查询完成。",
    )


@router.post(
    "/sandbox/k8s/workspace/ensure",
    response_model=StandardResponse[Dict[str, Any]],
    summary="启动或复用当前用户的 Kubernetes 沙箱 Pod",
)
async def ensure_k8s_workspace_endpoint(
    body: DockerWorkspaceEnsureRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """只确保当前用户 Pod 运行，不在 Pod 内执行用户命令。"""
    conversation_id = body.conversation_id.strip()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id 不能为空")

    try:
        result = await ensure_k8s_workspace_runtime(
            user_id=user_info.get("user_id") or user_info.get("id"),
            user_name=user_info.get("user_name") or user_info.get("username"),
            user_info=user_info,
            conversation_id=conversation_id,
        )
    except K8sSandboxUnavailableError as exc:
        status_code = 409 if exc.reason_code == "k8s_policy_not_effective" else 503
        raise HTTPException(
            status_code=status_code,
            detail={
                "reason_code": exc.reason_code,
                "message": exc.user_message,
            },
        ) from exc

    return StandardResponse(
        data=result,
        message="Kubernetes 沙箱 Pod 已运行。",
    )


@router.post(
    "/sandbox/k8s/workspace/stop",
    response_model=StandardResponse[Dict[str, Any]],
    summary="停止当前用户的 Kubernetes 沙箱 Pod",
)
async def stop_k8s_workspace_endpoint(
    body: DockerWorkspaceEnsureRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """停止当前用户的 Kubernetes 沙箱 Pod 并清理缓存。"""
    conversation_id = body.conversation_id.strip()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id 不能为空")

    try:
        result = await stop_k8s_workspace_runtime(
            user_id=user_info.get("user_id") or user_info.get("id"),
            user_name=user_info.get("user_name") or user_info.get("username"),
            user_info=user_info,
            conversation_id=conversation_id,
        )
    except K8sSandboxUnavailableError as exc:
        status_code = 409 if exc.reason_code == "k8s_policy_not_effective" else 503
        raise HTTPException(
            status_code=status_code,
            detail={
                "reason_code": exc.reason_code,
                "message": exc.user_message,
            },
        ) from exc

    return StandardResponse(
        data=result,
        message="Kubernetes 沙箱 Pod 已停止。",
    )


@router.post(
    "/sandbox/k8s/workspace/restart",
    response_model=StandardResponse[Dict[str, Any]],
    summary="重启当前用户的 Kubernetes 沙箱 Pod（销毁旧 Pod 并拉起新 Pod）",
)
async def restart_k8s_workspace_endpoint(
    body: DockerWorkspaceEnsureRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """销毁旧 Kubernetes 沙箱 Pod 并重新拉起全新的 Pod。"""
    conversation_id = body.conversation_id.strip()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id 不能为空")

    try:
        result = await restart_k8s_workspace_runtime(
            user_id=user_info.get("user_id") or user_info.get("id"),
            user_name=user_info.get("user_name") or user_info.get("username"),
            user_info=user_info,
            conversation_id=conversation_id,
        )
    except K8sSandboxUnavailableError as exc:
        status_code = 409 if exc.reason_code == "k8s_policy_not_effective" else 503
        raise HTTPException(
            status_code=status_code,
            detail={
                "reason_code": exc.reason_code,
                "message": exc.user_message,
            },
        ) from exc

    return StandardResponse(
        data=result,
        message="Kubernetes 沙箱 Pod 已重启完成。",
    )


@router.get(
    "/sandbox/k8s/workspace/status",
    response_model=StandardResponse[Dict[str, Any]],
    summary="查询当前用户的 Kubernetes 沙箱 Pod 状态",
)
async def get_k8s_workspace_status_endpoint(
    conversation_id: str = Query(..., min_length=1),
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """只查询当前用户 Pod，不触发 K8s 沙箱初始化。"""
    conversation_id = conversation_id.strip()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id 不能为空")

    try:
        status = await k8s_workspace_status_runtime(
            user_id=user_info.get("user_id") or user_info.get("id"),
            user_name=user_info.get("user_name") or user_info.get("username"),
            user_info=user_info,
            conversation_id=conversation_id,
        )
    except K8sSandboxUnavailableError as exc:
        status_code = 409 if exc.reason_code == "k8s_policy_not_effective" else 503
        raise HTTPException(
            status_code=status_code,
            detail={
                "reason_code": exc.reason_code,
                "message": exc.user_message,
            },
        ) from exc

    return StandardResponse(
        data=status,
        message="Kubernetes 沙箱状态查询完成。",
    )


@router.post(
    "/sandbox/k8s/workspace/exec",
    response_model=StandardResponse[Dict[str, Any]],
    summary="在当前用户的 Kubernetes 沙箱 Pod 中执行终端命令",
)
async def exec_k8s_workspace_endpoint(
    body: DockerWorkspaceExecRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """执行交互终端命令并返回输出（等价 kubectl exec，逐条命令）。"""
    conversation_id = body.conversation_id.strip()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id 不能为空")

    try:
        result = await exec_k8s_workspace_command_runtime(
            user_id=user_info.get("user_id") or user_info.get("id"),
            user_name=user_info.get("user_name") or user_info.get("username"),
            user_info=user_info,
            conversation_id=conversation_id,
            command=body.command,
            workdir=body.workdir,
        )
    except K8sSandboxUnavailableError as exc:
        status_code = 409 if exc.reason_code in ("k8s_policy_not_effective", "k8s_pod_not_found") else 503
        raise HTTPException(
            status_code=status_code,
            detail={
                "reason_code": exc.reason_code,
                "message": exc.user_message,
            },
        ) from exc

    return StandardResponse(
        data=result,
        message="命令执行完成。",
    )


class SandboxConnectionTestRequest(BaseModel):
    """E2B/SSH 连接测试所需的临时配置覆盖值。

    密钥字段允许接收管理页面的脱敏值；运行时会识别 ``****`` 并回退到服务端
    已保存的真实配置，避免页面在未修改密钥时把脱敏文本当成凭据。
    """

    sandbox_e2b_api_key: str = ""
    sandbox_e2b_template: str = ""
    sandbox_e2b_timeout_seconds: str = "300"
    sandbox_ssh_host: str = ""
    sandbox_ssh_port: str = "22"
    sandbox_ssh_user: str = ""
    sandbox_ssh_auth_type: str = "password"
    sandbox_ssh_password: str = ""
    sandbox_ssh_private_key: str = ""
    sandbox_ssh_remote_workdir: str = "/workspace"


@router.post(
    "/admin/sandbox/{policy}/test-connection",
    response_model=StandardResponse[Dict[str, Any]],
    summary="测试 E2B/SSH 沙箱连接",
)
async def test_sandbox_connection(
    policy: Literal["e2b", "ssh"],
    body: SandboxConnectionTestRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """使用当前页面配置做一次真实初始化测试，并在结束后释放沙箱。"""
    _require_admin(user_info)
    workspace = None
    try:
        workspace = await build_sandbox_workspace_for_test(
            policy,
            body.model_dump(),
        )
        return StandardResponse(
            data={"policy": policy, "connected": True},
            message=f"{policy.upper()} 沙箱连接测试成功",
        )
    except Exception as exc:
        # 不记录请求体，避免 API Key、密码或私钥进入日志；异常文本仅用于向管理员
        # 说明远端返回的可诊断错误。
        detail = str(exc).strip() or "远端返回未知错误"
        logger.warning(
            "沙箱连接测试失败 policy=%s error_type=%s",
            policy,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail=f"{policy.upper()} 沙箱连接测试失败：{detail}",
        ) from exc
    finally:
        if workspace is not None:
            close = getattr(workspace, "close", None)
            if callable(close):
                try:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:
                    logger.warning(
                        "沙箱连接测试资源释放失败 policy=%s error_type=%s",
                        policy,
                        type(exc).__name__,
                    )


@router.post(
    "/admin/sandbox/k8s/check-rbac",
    response_model=StandardResponse[Dict[str, Any]],
    summary="校验 Kubernetes 集群连接与沙箱命名空间 RBAC 权限",
)
async def check_k8s_rbac_endpoint(
    namespace: str | None = Query(None, description="可选指定待探测的沙箱命名空间，留空则读取系统配置"),
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    """校验当前平台 Pod 是否具备连接 Kubernetes API Server 并创建/管理沙箱 Pod 的权限。"""
    _require_admin(user_info)
    from app.services.ai.runtime.agentscope.k8s_workspace import check_k8s_rbac_status

    result = await check_k8s_rbac_status(namespace=namespace)
    if "details" not in result and "can_create_pods" in result:
        result["details"] = {
            "pods_create": result.get("can_create_pods", False),
            "pvc_create": result.get("can_create_pvcs", False),
        }
    if "suggestion" not in result and "remedy" in result:
        result["suggestion"] = result.get("remedy")
    return StandardResponse(
        data=result,
        message=result.get("message") or "success",
    )

