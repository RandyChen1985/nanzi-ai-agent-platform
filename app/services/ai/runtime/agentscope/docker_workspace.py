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
    **kwargs: Any,
) -> Any:
    """Instantiate an AgentScope Docker workspace with child bind mounts."""

    class _NanZiDockerWorkspace(base_workspace_class):
        def __init__(self, *, extra_bind_mounts: list[tuple[str, str, str]], **init_kwargs: Any):
            super().__init__(**init_kwargs)
            self._nanzi_extra_bind_mounts = list(extra_bind_mounts)

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
            config["HostConfig"] = host_config

            self._container = await self._client.containers.create_or_replace(
                name=f"as_ws_{self.workspace_id}",
                config=config,
            )
            await self._container.start()
            self._backend = DockerBackend(self._container, CONTAINER_WORKDIR)

    return _NanZiDockerWorkspace(
        extra_bind_mounts=extra_bind_mounts,
        **kwargs,
    )
