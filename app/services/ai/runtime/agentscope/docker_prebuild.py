"""Docker 沙箱镜像预构建（预热）服务。

B 方案（docker 策略提速）的核心：docker 沙箱的镜像 tag 是内容哈希
（Dockerfile + requirements.txt + MCP gateway 脚本 + agentscope 版本 的
确定性哈希）；运行时 ``DockerWorkspace`` 首次使用会现场构建该 tag 的镜像
（分钟级），之后命中该 tag 直接复用，不再重复构建。

本模块提供运维/管理用的一次性预构建函数：提前用**与运行时完全相同**的
``prepare_build_context`` 上下文把镜像构建出来 / 复用已有缓存，并写入配置
标记 ``sandbox_docker_prebuild_done``。如果后端无法连接 Docker daemon，则返回
符合 AgentScope 规范的预构建镜像下载地址和导入说明，不伪造构建成功。
标记 ``sandbox_docker_prebuild_done``。

关键一致性约束
------------
预构建必须与 ``agentscope.workspace.DockerWorkspace`` 的镜像 tag 计算严格
一致（否则预构建的镜像对运行时毫无意义）。运行时的 ``_build_or_reuse_image``
用 ``prepare_build_context(base_image=base_image, gateway_home=GATEWAY_HOME,
container_workdir=CONTAINER_WORKDIR, node_version=self.node_version,
extra_pip=self.extra_pip)`` 生成 ``(ctx_dir, tag, copy_files)``，其中平台
docker 策略只配置了 ``base_image``（其余 4 项均为框架默认值：GATEWAY_HOME、
CONTAINER_WORKDIR、node_version=None、extra_pip=None）。因此本模块也以同样
参数调用 ``prepare_build_context``，得到完全相同的 tag 与构建上下文。
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import tarfile
from collections.abc import Awaitable, Callable
from typing import Any

from app.services.ai.runtime.agentscope.docker_template_patch import (
    apply_agentscope_docker_patches,
)

logger = logging.getLogger(__name__)

# 模块加载时即确保模板补丁已就绪
apply_agentscope_docker_patches()

PREBUILD_CONFIG_KEY = "sandbox_docker_prebuild_done"
FAQ_HELP_URL = "https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/FAQ.md"


DEFAULT_DOCKER_BASE_IMAGE = "python:3.11-slim"
BuildEventCallback = Callable[[dict[str, Any]], Awaitable[None]]


async def _emit_event(
    callback: BuildEventCallback | None,
    event_type: str,
    **payload: Any,
) -> None:
    if callback is not None:
        await callback({"type": event_type, **payload})


async def _prepare_context(base_image_override: str | None = None) -> tuple[str, str]:
    """按运行时 docker 策略相同参数生成构建上下文，返回 ``(ctx_dir, tag)``。

    ``ctx_dir`` 由调用方负责在 finally 中清理。
    """
    from app.services.config_service import ConfigService
    from agentscope.workspace._docker._make_dockerfile import (
        CONTAINER_WORKDIR,
        GATEWAY_HOME,
        prepare_build_context,
    )

    if base_image_override is not None and base_image_override.strip():
        base_image = base_image_override.strip()
    else:
        base_image = (
            await ConfigService.get("sandbox_docker_base_image", "")
        ).strip() or DEFAULT_DOCKER_BASE_IMAGE

    ctx_args: dict[str, object] = {
        "gateway_home": GATEWAY_HOME,
        "container_workdir": CONTAINER_WORKDIR,
        "node_version": None,
        "extra_pip": None,
    }
    if base_image:
        ctx_args["base_image"] = base_image

    ctx_dir, tag, _ = prepare_build_context(**ctx_args)
    # ctx_dir 为 pathlib.Path，统一成 str 便于 tarfile.add 处理。
    return str(ctx_dir), tag


async def docker_workspace_image_prebuilt(base_image: str | None = None) -> bool:
    """查询 docker 沙箱镜像是否已存在于本地（命中缓存 tag）。

    ``True`` 表示镜像已存在，运行时 docker 策略将秒级拉起；``False`` 表示首次
    使用仍需现场构建。仅做本地 inspect 判断，不触发构建。
    """
    status = await docker_workspace_prebuild_status(base_image=base_image)
    return bool(status.get("prebuilt"))


async def docker_workspace_prebuild_status(
    base_image: str | None = None,
) -> dict[str, Any]:
    """返回镜像预构建状态及 Docker 环境能力状态。"""
    try:
        import aiodocker
    except Exception:
        logger.warning("[docker_prebuild] aiodocker not installed")
        return {
            "prebuilt": False,
            "docker_available": False,
            "reason_code": "aiodocker_unavailable",
            "message": (
                "后端环境未安装 aiodocker，无法自动构建 Docker 镜像。"
                f"离线部署及常见问题排查请参考 FAQ 帮助文档：{FAQ_HELP_URL}"
            ),
            "help_url": FAQ_HELP_URL,
        }

    daemon_status = await check_docker_daemon(aiodocker)
    if not daemon_status["available"]:
        tag = await _try_prepare_context_tag(base_image)
        return {
            "prebuilt": False,
            "tag": tag,
            "docker_available": False,
            "reason_code": daemon_status.get("reason_code"),
            "message": daemon_status["message"],
            "help_url": FAQ_HELP_URL,
        }

    ctx_dir: str | None = None
    client: Any | None = None
    try:
        ctx_dir, tag = await _prepare_context(base_image)
        result: dict[str, Any] = {
            "prebuilt": False,
            "tag": tag,
            "docker_available": daemon_status["available"],
            "reason_code": daemon_status.get("reason_code"),
            "message": daemon_status["message"],
            "help_url": FAQ_HELP_URL,
        }
        client = aiodocker.Docker()
        try:
            await client.images.inspect(tag)
            result["prebuilt"] = True
            result["message"] = "Docker 沙箱镜像已预构建。"
        except Exception:
            result["message"] = "镜像尚未预构建。"
        return result
    except Exception as exc:
        logger.warning("[docker_prebuild] prebuilt check failed: %s", exc)
        return {
            "prebuilt": False,
            "docker_available": False,
            "reason_code": "prebuild_status_unavailable",
            "message": f"无法检查 Docker 沙箱镜像状态：{exc}",
            "help_url": FAQ_HELP_URL,
        }
    finally:
        if ctx_dir:
            shutil.rmtree(ctx_dir, ignore_errors=True)
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


async def prebuild_docker_workspace_image(
    *,
    base_image: str | None = None,
    force: bool = False,
    on_event: BuildEventCallback | None = None,
) -> dict[str, Any]:
    """预构建 docker 沙箱镜像（一次性的运维/预热操作）。

    流程与运行时 ``DockerWorkspace._build_or_reuse_image`` 完全一致：
    ``prepare_build_context`` -> ``images.inspect(tag)`` 命中即幂等跳过；
    未命中则 tar 打包 ``ctx_dir``（``arcname="."``）并以 ``encoding="identity"``
    ``images.build(stream=True, rm=True)`` 构建，异常转 RuntimeError（保留 stream
    尾部日志）。构建成功后写入配置标记 ``sandbox_docker_prebuild_done``。

    Args:
        base_image: 可选指定的基础镜像（覆盖系统配置）。
        force: 是否强制重新构建（跳过 inspect 缓存命中判断）。默认 ``False``
            幂等（镜像已存在则直接返回、不重复构建）。

    Returns:
        dict: 成功时包含 ``reused``、``built``、``tag``。

    Raises:
        RuntimeError: 无法生成构建上下文，或 docker build 失败（含 build stream
            尾部日志）。
    """
    await _emit_event(on_event, "phase", stage="environment", message="正在检查 Docker 环境…")
    try:
        import aiodocker
    except ImportError as exc:
        await _emit_event(
            on_event,
            "log",
            level="error",
            message=f"后端环境未安装 aiodocker: {exc}",
        )
        tag = await _try_prepare_context_tag(base_image)
        return {
            "reused": False,
            "built": False,
            "tag": tag,
            "docker_available": False,
            "reason_code": "aiodocker_unavailable",
            "message": (
                f"后端环境未安装 aiodocker ({exc})，无法自动构建 Docker 镜像。"
                f"离线部署及常见问题排查请参考 FAQ 帮助文档：{FAQ_HELP_URL}"
            ),
            "help_url": FAQ_HELP_URL,
        }

    daemon_status = await check_docker_daemon(aiodocker)
    if not daemon_status["available"]:
        await _emit_event(
            on_event,
            "log",
            level="error",
            message=daemon_status["message"],
        )
        tag = await _try_prepare_context_tag(base_image)
        return {
            "reused": False,
            "built": False,
            "tag": tag,
            "docker_available": False,
            "reason_code": daemon_status["reason_code"],
            "message": daemon_status["message"],
            "help_url": FAQ_HELP_URL,
        }

    ctx_dir: str | None = None
    client: Any | None = None
    try:
        await _emit_event(on_event, "phase", stage="prepare", message="正在准备构建上下文…")
        ctx_dir, tag = await _prepare_context(base_image)
        await _emit_event(
            on_event,
            "phase",
            stage="prepare",
            message=f"构建上下文已准备，目标 Tag：{tag}",
        )
        try:
            client = aiodocker.Docker()
        except Exception as exc:
            daemon_status = _docker_unavailable_status(exc)
            await _emit_event(on_event, "log", level="error", message=daemon_status["message"])
            return {
                "reused": False,
                "built": False,
                "tag": tag,
                "docker_available": False,
                "reason_code": daemon_status["reason_code"],
                "message": daemon_status["message"],
                "help_url": FAQ_HELP_URL,
            }

        # 命中已有镜像且非 force => 幂等跳过（与运行时 _build_or_reuse_image 相同）。
        await _emit_event(on_event, "phase", stage="cache", message="正在检查目标镜像缓存…")
        if not force and await _image_exists(client, tag):
            logger.info("[docker_prebuild] image cache hit %r, skip build", tag)
            await _mark_prebuilt(base_image)
            await _emit_event(on_event, "phase", stage="completed", message="已命中既有镜像缓存，跳过构建")
            return {"reused": True, "built": False, "tag": tag}

        logger.info("[docker_prebuild] building image %r", tag)
        await _emit_event(
            on_event,
            "phase",
            stage="build",
            message=f"开始构建 Docker 镜像：{tag}",
        )
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tf:
            tf.add(ctx_dir, arcname=".")
        tar_buf.seek(0)
        # encoding="identity" 告诉 aiodocker body 是未压缩的 tar，否则 aiodocker
        # 会对已 tar 的字节再做 gzip，daemon 会拒绝畸形流。
        build_output: list[str] = []
        try:
            async for chunk in client.images.build(
                fileobj=tar_buf,
                tag=tag,
                stream=True,
                rm=True,
                encoding="identity",
            ):
                if isinstance(chunk, dict):
                    stream_text = chunk.get("stream") or chunk.get("status")
                    if stream_text and isinstance(stream_text, str):
                        stream_text = stream_text.rstrip()
                        build_output.append(stream_text)
                        await _emit_event(on_event, "log", level="info", message=stream_text)
                    error = chunk.get("error") or chunk.get("errorDetail")
                    if error:
                        msg = error.get("message") if isinstance(error, dict) else str(error)
                        await _emit_event(on_event, "log", level="error", message=msg)
                        tail = "\n".join(build_output[-20:])
                        raise RuntimeError(f"docker build 失败: {msg}\n--- build log tail ---\n{tail}")
        except RuntimeError:
            raise
        except Exception as exc:
            tail = "\n".join(build_output[-20:])
            await _emit_event(on_event, "log", level="error", message=str(exc))
            raise RuntimeError(f"docker build 异常: {exc}\n--- build log tail ---\n{tail}") from exc

        # 构建成功，写入 prebuilt 标记并持久化 base_image（如有传入）
        await _emit_event(on_event, "phase", stage="finalize", message="Docker 构建完成，正在保存状态…")
        await _mark_prebuilt(base_image)
        logger.info("[docker_prebuild] successfully built image %r", tag)
        await _emit_event(on_event, "phase", stage="completed", message="Docker 沙箱镜像预构建完成")
        return {"reused": False, "built": True, "tag": tag}
    finally:
        if ctx_dir:
            shutil.rmtree(ctx_dir, ignore_errors=True)
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


async def _image_exists(client: Any, tag: str) -> bool:
    try:
        await client.images.inspect(tag)
        return True
    except Exception:
        return False


async def _mark_prebuilt(base_image: str | None = None) -> None:
    from app.services.config_service import ConfigService

    try:
        await ConfigService.set(
            PREBUILD_CONFIG_KEY,
            "1",
            category="sandbox",
            description="Docker 沙箱镜像预构建状态标记（内部使用，非空表示已预构建）",
        )
        if base_image and base_image.strip():
            await ConfigService.set(
                "sandbox_docker_base_image",
                base_image.strip(),
                category="sandbox",
                description="docker 策略使用的容器基础镜像（留空默认使用阿里云加速源）",
            )
    except Exception as exc:
        logger.warning("[docker_prebuild] failed to write prebuild mark: %s", exc)


async def check_docker_daemon(aiodocker: Any) -> dict[str, Any]:
    """检查当前后端是否能正常连通 Docker daemon。

    优先通过 ``Docker().version()`` 探测连通性，避免直接调 ``inspect`` 掩盖
    daemon 不可达导致的真实原因。
    """
    client: Any | None = None
    try:
        client = aiodocker.Docker()
        version = await client.version()
        return {
            "available": True,
            "reason_code": None,
            "message": "Docker daemon 可连接。",
            "version": version.get("Version") if isinstance(version, dict) else None,
        }
    except Exception as exc:
        return _docker_unavailable_status(exc)
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


def _docker_unavailable_status(exc: Exception) -> dict[str, Any]:
    """生成不泄露敏感配置的 Docker 不可用提示。"""
    logger.warning("[docker_prebuild] Docker daemon unavailable: %s", exc)
    return {
        "available": False,
        "reason_code": "docker_daemon_unavailable",
        "message": (
            "当前后端环境无法连接 Docker daemon。请确认后端容器已挂载 Docker Socket "
            f"或配置 DOCKER_HOST。离线部署及常见问题排查请参考 FAQ 帮助文档：{FAQ_HELP_URL}"
        ),
        "help_url": FAQ_HELP_URL,
        "error": str(exc),
    }


async def _try_prepare_context_tag(
    base_image_override: str | None = None,
) -> str | None:
    """尽力计算运行时 Tag；失败时不影响返回。"""
    ctx_dir: str | None = None
    try:
        ctx_dir, tag = await _prepare_context(base_image_override)
        return tag
    except Exception as exc:
        logger.warning(
            "[docker_prebuild] failed to prepare tag: %s", exc
        )
        return None
    finally:
        if ctx_dir:
            shutil.rmtree(ctx_dir, ignore_errors=True)
