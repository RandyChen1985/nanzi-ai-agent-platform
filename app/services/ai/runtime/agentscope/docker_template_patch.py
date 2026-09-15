"""AgentScope Docker 运行时模板动态增强补丁.

由于 AgentScope 的 Dockerfile.template 仅默认安装了最基础的 curl, ca-certificates, ripgrep，
为了实现 Docker 沙箱环境的开箱即用（支持 git, tree, ping, netstat, ps, telnet 等系统排障命令），
同时避免侵入修改第三方库 site-packages 中的文件，本模块通过动态 Hook `agentscope.workspace._docker._make_dockerfile._read_template`，
在生成构建上下文时自动扩展 APT 依赖包清单。

该补丁具备完全幂等性，并会自动影响 Tag 的 SHA256 哈希计算，确保预构建与运行时镜像 Tag 100% 保持一致。
"""

from __future__ import annotations

import logging
from typing import Final

logger = logging.getLogger(__name__)

# 开箱即用预装的 Debian 标准排障工具包列表
EXTRA_SANDBOX_APT_PACKAGES: Final[tuple[str, ...]] = (
    "git",
    "tree",
    "iputils-ping",
    "net-tools",
    "procps",
    "telnet",
)

_PATCH_APPLIED = False


def apply_agentscope_docker_patches() -> bool:
    """动态注入 AgentScope Dockerfile 模板补丁.

    Returns:
        bool: True 表示成功应用或已应用，False 表示应用失败（例如未安装 agentscope）。
    """
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return True

    try:
        import agentscope.workspace._docker._make_dockerfile as mk

        orig_read_template = mk._read_template

        def patched_read_template(name: str) -> str:
            content = orig_read_template(name)
            if name == "Dockerfile.template":
                target = "curl ca-certificates ripgrep"
                if target in content:
                    extra_str = " ".join(EXTRA_SANDBOX_APT_PACKAGES)
                    replacement = f"{target} {extra_str}"
                    content = content.replace(target, replacement, 1)
            return content

        mk._read_template = patched_read_template
        _PATCH_APPLIED = True
        logger.info(
            "[DockerSandbox] 已成功应用 Dockerfile 模板扩展补丁，注入工具链: %s",
            ", ".join(EXTRA_SANDBOX_APT_PACKAGES),
        )
        return True
    except Exception as exc:  # pragma: no cover
        logger.warning("[DockerSandbox] 应用 Dockerfile 模板补丁失败: %s", exc)
        return False
