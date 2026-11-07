"""NanZi-specific DockerWorkspace mount adapter.

AgentScope 2.0.6 exposes one ``host_workdir`` bind mount. NanZi keeps that
mount for the user's workspace and adds isolated child mounts for the runtime
skills snapshot and the read-only public documentation tree.
"""
from __future__ import annotations

from typing import Any

from app.services.ai.runtime.agentscope.docker_template_patch import (
    apply_agentscope_docker_patches,
)

# 确保运行时环境下的 Dockerfile 模板补丁已应用
apply_agentscope_docker_patches()


def build_docker_workspace_with_extra_binds(
    base_workspace_class: type[Any],
    *,
    extra_bind_mounts: list[tuple[str, str, str]],
    resource_limits: dict[str, int] | None = None,
    **kwargs: Any,
) -> Any:
    """Instantiate an AgentScope Docker workspace with child bind mounts.

    ``resource_limits`` 是平台下发的 cgroups 上限，键名与 ``HostConfig`` 对齐前
    的中间表示（``memory_bytes`` / ``nano_cpus`` / ``pids_limit``）；缺省或为 0
    的维度不下发，交给 Docker 默认（不限制）。
    """

    class _NanZiDockerWorkspace(base_workspace_class):
        def __init__(
            self,
            *,
            extra_bind_mounts: list[tuple[str, str, str]],
            resource_limits: dict[str, int] | None = None,
            **init_kwargs: Any,
        ):
            super().__init__(**init_kwargs)
            self._nanzi_extra_bind_mounts = list(extra_bind_mounts)
            self._nanzi_resource_limits = {
                key: int(value)
                for key, value in (resource_limits or {}).items()
                if value
            }

        async def _create_and_start_container(self) -> None:
            """Create the container with the parent and child bind mounts."""
            import os

            from agentscope.workspace._docker._docker_backend import DockerBackend
            from agentscope.workspace._docker._make_dockerfile import (
                CONTAINER_WORKDIR,
            )

            config: dict[str, Any] = {
                "Image": self._image_tag,
                "Cmd": ["sleep", "infinity"],
                "WorkingDir": CONTAINER_WORKDIR,
                "Labels": {
                    "agentscope.workspace": "true",
                    "agentscope.workspace.id": self.workspace_id,
                },
            }
            if self.env:
                config["Env"] = [f"{key}={value}" for key, value in self.env.items()]

            host_config: dict[str, Any] = {}
            binds: list[str] = []
            if self.host_workdir is not None:
                os.makedirs(self.host_workdir, exist_ok=True)
                binds.append(
                    f"{os.path.abspath(self.host_workdir)}:{CONTAINER_WORKDIR}:rw",
                )
            for source, target, mode in self._nanzi_extra_bind_mounts:
                if mode != "ro":
                    os.makedirs(source, exist_ok=True)
                binds.append(f"{os.path.abspath(source)}:{target}:{mode}")
            if binds:
                host_config["Binds"] = binds

            # 资源上限：Docker 沙箱是宿主机（DooD 下与平台共用内核与内存）上的兄弟
            # 容器，不设 cgroups 限制时，一行死循环 / 内存爆破 / fork bomb 就能把宿主
            # 机打进 OOM，由内核随机挑选牺牲品（可能是平台进程、MySQL 或 Redis）。
            # 因此默认必须有上限，但允许通过配置项按部署规格调整（见
            # ``_resolve_docker_sandbox_resource_limits``）。
            limits = self._nanzi_resource_limits
            memory_bytes = limits.get("memory_bytes")
            if memory_bytes:
                host_config["Memory"] = memory_bytes
                # 与 Memory 相等 = 不给 swap 留余量：否则内存打满后容器会继续吃
                # 宿主机 swap，整机卡死而不是单容器被杀。
                host_config["MemorySwap"] = memory_bytes
            if limits.get("nano_cpus"):
                host_config["NanoCPUs"] = limits["nano_cpus"]
            if limits.get("pids_limit"):
                # 阻断 fork bomb：默认值要能容纳 pip/uv 的并发编译，又要挡得住无限 fork。
                host_config["PidsLimit"] = limits["pids_limit"]

            config["HostConfig"] = host_config

            self._container = await self._client.containers.create_or_replace(
                name=f"as_ws_{self.workspace_id}",
                config=config,
            )
            await self._container.start()
            self._backend = DockerBackend(self._container, CONTAINER_WORKDIR)

    return _NanZiDockerWorkspace(
        extra_bind_mounts=extra_bind_mounts,
        resource_limits=resource_limits,
        **kwargs,
    )
