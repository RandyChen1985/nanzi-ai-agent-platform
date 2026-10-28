"""AgentScope Docker 运行时模板动态增强补丁.

由于 AgentScope 的 Dockerfile.template 仅默认安装了最基础的 curl, ca-certificates, ripgrep，
为了实现 Docker 沙箱环境的开箱即用（支持 git, tree, ping, netstat, ps, telnet 等系统排障命令），
同时避免侵入修改第三方库 site-packages 中的文件，本模块通过动态 Hook `agentscope.workspace._docker._make_dockerfile._read_template`，
在生成构建上下文时自动扩展 APT 依赖包清单。

该补丁具备完全幂等性，并会自动影响 Tag 的 SHA256 哈希计算，确保预构建与运行时镜像 Tag 100% 保持一致。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Final, Optional

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

#: 沙箱镜像内置的 Node 主版本（**写死，不开放配置**）。
#:
#: 为什么需要: 技能可以带 ``scripts/*.js``，MCP 生态里 ``npx`` 启动的服务也很常见，
#: 但沙箱基座 ``python:3.11-slim`` 里没有 node/npm/npx——脚本一跑就是
#: ``sh: 1: node: not found``。AgentScope 的 Dockerfile 模板本就有 ``node_version``
#: 开关：非空时 ``FROM node:<ver>-slim`` 把 ``node``、``npm``、``npx`` 三个二进制
#: COPY 进沙箱镜像（见 ``agentscope/workspace/_docker/Dockerfile.node_*.template``），
#: 平台此前一直传 ``None``，等于主动关掉了。
#:
#: 为什么是 22: Node 20 已于 2026-04 EOL，22 是当前维护中的 LTS。刻意不用
#: ``apt install nodejs``（Debian bookworm 只到 18，同样已 EOL，且要多扛一层 apt）。
#:
#: 为什么写死而不做成配置键: 这是个「沙箱里该有什么」的产品决策，不是一个需要
#: 按部署漂移的运行参数；做成配置只会多一个「配了没生效」的坑（预构建镜像与
#: 运行时镜像的 tag 一旦不一致，预构建就白做了）。真要换版本，改这一个常量即可。
#:
#: **注意**：该值参与沙箱镜像 tag 的 SHA256 计算，改动会让所有既有沙箱镜像
#: 标签失效、触发一次重新构建（分钟级）。k8s 策略不受影响——``K8sWorkspace``
#: 直接拉现成镜像、没有 node_version 这个概念，需要 node 只能换
#: ``sandbox_k8s_image``。
SANDBOX_NODE_VERSION: Final[str] = "22"

#: Node 装好后的构建期自检。自定义基础镜像（后台 ``sandbox_docker_base_image`` 支持
#: 「自定义镜像地址…」）如果换成 alpine / distroless 这类非 glibc 基座，COPY 进去的
#: 官方 node 二进制根本跑不起来——不加这一句，错误会一路潜伏到用户跑技能脚本时才以
#: ``sh: 1: node: not found`` / ``Exec format error`` 的形式炸出来。
NODE_VERIFY_SNIPPET: Final[str] = "RUN node --version && npm --version && npx --version"

#: 构建期 pip 源默认值。AgentScope 上游模板直接 ``uv pip install``，uv 的默认
#: index 是官方 PyPI——国内构建时这一步经常卡成整次构建的瓶颈。``k8s_deploy``
#: 那条构建路径早就默认走清华（``--default-index``），docker 这条一直没跟上，
#: 于是同一个项目里两条路径的快慢差得很明显。这里对齐到清华。
DEFAULT_PIP_INDEX: Final[str] = "https://pypi.tuna.tsinghua.edu.cn/simple"

#: 构建期 apt 源默认值（Debian。刻意用 http：换源发生在 ``apt-get install
#: ca-certificates`` **之前**，此时镜像里还没有 CA 包，https 源会直接失败）。
DEFAULT_APT_MIRROR: Final[str] = "http://mirrors.tuna.tsinghua.edu.cn"

#: 环境变量取值落在这个集合里表示「关掉加速、用官方源」（海外/内网直连场景）。
_OFF_VALUES: Final[frozenset[str]] = frozenset({"", "0", "false", "no", "off", "none", "official"})


def _resolve_build_source(env_name: str, default: str) -> Optional[str]:
    """读取构建源配置：未设置用默认值，显式关闭返回 None（= 不改模板）。

    取值顺序是「进程环境变量 → Settings（即 .env）→ 内置默认」。两条构建入口
    （手动跑 build-docker-sandbox-image.sh、后台系统设置里点预构建）加载的是同一份
    .env，所以配置只写一处就够；而 `.env` 不会被注入 os.environ，因此必须再兜一层
    Settings，否则写在 .env 里的开关会静默失效。
    """
    raw = os.environ.get(env_name)
    if raw is None:
        try:
            from app.core.config import settings

            raw = getattr(settings, env_name, None)
        except Exception:  # pragma: no cover - 配置未就绪时按未设置处理
            raw = None
    if raw is None:
        return default
    value = str(raw).strip()
    return None if value.lower() in _OFF_VALUES else value


def build_pip_index() -> Optional[str]:
    """构建期 pip index。``SANDBOX_BUILD_PIP_INDEX=off`` 时返回 None（用官方 PyPI）。"""
    return _resolve_build_source("SANDBOX_BUILD_PIP_INDEX", DEFAULT_PIP_INDEX)


def build_apt_mirror() -> Optional[str]:
    """构建期 apt 镜像站前缀。``SANDBOX_BUILD_APT_MIRROR=off`` 时返回 None。"""
    return _resolve_build_source("SANDBOX_BUILD_APT_MIRROR", DEFAULT_APT_MIRROR)


def _rewrite_build_sources(content: str) -> str:
    """把上游模板里的 apt 源与 uv 安装/索引改写成可加速的形态。

    三处改动，任何一处配置被关掉就跳过那一处：

    1. ``apt-get update`` 之前插入换源 sed（同时处理 bookworm 的 deb822
       ``debian.sources`` 与旧的 ``sources.list``，文件不存在不算失败）；
    2. ``curl astral.sh/uv/install.sh`` 换成 ``pip install uv``：省掉一次到
       GitHub release 的外网往返，且能复用同一个 pip 源；
    3. 模板的 ``ENV`` 段补 ``UV_DEFAULT_INDEX``：一处生效，``requirements.txt``
       与后面的 ``install_agentscope_block`` 两个 ``uv pip install`` 都跟着走。
    """
    pip_index = build_pip_index()
    apt_mirror = build_apt_mirror()

    if apt_mirror:
        # 上游原文：`RUN apt-get update \`
        old_head = "RUN apt-get update \\\n"
        new_head = (
            "RUN set -eux; \\\n"
            "    for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \\\n"
            '      if [ -f "$f" ]; then \\\n'
            f"        sed -i 's|http://deb.debian.org/debian|{apt_mirror}/debian|g; "
            f"s|http://security.debian.org/debian-security|{apt_mirror}/debian-security|g' \"$f\"; \\\n"
            "      fi; \\\n"
            "    done; \\\n"
            "    apt-get update \\\n"
        )
        if old_head in content:
            content = content.replace(old_head, new_head, 1)

    if pip_index:
        # 上游原文（含缩进）：
        # ` && curl -LsSf https://astral.sh/uv/install.sh \`
        # `        | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh`
        uv_old = (
            " && curl -LsSf https://astral.sh/uv/install.sh \\\n"
            "        | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh"
        )
        uv_new = f" && pip install --no-cache-dir --index-url {pip_index} uv"
        if uv_old in content:
            content = content.replace(uv_old, uv_new, 1)
            # 上游那段注释描述的是 shell installer 的做法（UV_INSTALL_DIR /
            # INSTALLER_NO_MODIFY_PATH），安装方式一换就全是误导，整段替换掉。
            stale_comment = (
                "# Install uv via Astral's official shell installer.\n"
                "# We use this rather than ``COPY --from=ghcr.io/astral-sh/uv`` to avoid\n"
                "# depending on GitHub Container Registry at build time (the GHCR blob CDN\n"
                "# has been observed to time out from some networks). ``curl`` and\n"
                "# ``ca-certificates`` are not present in ``python:*-slim`` so we apt them\n"
                "# in first, then drop the apt cache to keep the layer small.\n"
                "# ``UV_INSTALL_DIR`` redirects the installer's default ``~/.local/bin``\n"
                "# target to ``/usr/local/bin`` so ``uv`` lands directly on PATH;\n"
                "# ``INSTALLER_NO_MODIFY_PATH=1`` suppresses the installer's attempt to\n"
                "# patch shell rc files (we don't need it — PATH already covers it).\n"
            )
            fresh_comment = (
                "# 安装方式与镜像源被平台补丁改写过（docker_template_patch._rewrite_build_sources）：\n"
                "# 上游用官方 shell installer，构建时还要再去 GitHub release 拉一次\n"
                "# uv 二进制——国内网络下这是 apt 之外的第二个慢点。改成 pip 安装后，uv 与\n"
                "# requirements.txt 复用同一个 index，少一次外网往返。\n"
                f"# pip/uv 源：{pip_index}\n"
                f"# 关掉加速改用官方源：SANDBOX_BUILD_PIP_INDEX=off、SANDBOX_BUILD_APT_MIRROR=off\n"
            )
            if stale_comment in content:
                content = content.replace(stale_comment, fresh_comment, 1)

        # ENV 段补 index（{gateway_home} 此时仍是占位符，渲染时才替换）。
        env_anchor = "ENV UV_PROJECT_ENVIRONMENT="
        if env_anchor in content and "UV_DEFAULT_INDEX" not in content:
            content = content.replace(
                env_anchor,
                f"ENV UV_DEFAULT_INDEX={pip_index} \\\n    UV_PROJECT_ENVIRONMENT=",
                1,
            )

    return content


def gateway_agentscope_pin() -> str:
    """返回沙箱网关 venv 里应当安装的 agentscope 版本（即**后端自己正在用的版本**）。

    上游 ``prepare_build_context`` 生成的安装指令是不带版本的
    ``uv pip install --no-deps "agentscope"``：每次构建都拉 PyPI 上的**最新版**。
    镜像 tag 一旦因任何原因变化（例如这次加内置 node）就会触发重建，于是沙箱里
    装到的新版 agentscope 与后端 pin 的版本分叉——表现为容器 gateway 起不来：

        File "/root/.agentscope/.venv/.../agentscope/mcp/_mcp_client.py", line 12
          import httpx2
        ModuleNotFoundError: No module named 'httpx2'

    新版 agentscope 跟随生态改用 ``httpx2``，而网关 venv 的依赖清单
    （``_GATEWAY_BASE_REQUIREMENTS`` + ``GATEWAY_EXTRA_PIP``）里只有 ``httpx``。

    修复口径：把安装指令钉到**运行时实际加载的** agentscope 版本——后端用什么版本，
    沙箱就用什么版本，二者永远同源，也不需要在两个文件里各维护一个版本号。
    取不到版本时返回空串（退回上游行为），并留日志便于排查。
    """
    try:
        from importlib.metadata import version

        return str(version("agentscope")).strip()
    except Exception as exc:  # pragma: no cover - 仅在包元数据异常时触发
        logger.warning("[DockerSandbox] 读取 agentscope 版本失败，沙箱镜像将不钉版本: %s", exc)
        return ""


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
                # 源加速放在最后：它改的是 apt 段与 uv 安装段，必须在工具包注入之后，
                # 否则「curl ca-certificates ripgrep」这个锚点会被换源插入的文本打断。
                content = _rewrite_build_sources(content)
            elif name == "Dockerfile.node_copy.template":
                # 该模板只在 node_version 非空时被渲染（见上游 render_dockerfile），
                # 所以追加自检不会影响「关掉 node」的场景。
                if "node --version" not in content:
                    content = content.rstrip("\n") + "\n" + NODE_VERIFY_SNIPPET + "\n"
            return content

        mk._read_template = patched_read_template

        # 钉死 gateway venv 里的 agentscope 版本（见 gateway_agentscope_pin 的说明）。
        # prepare_build_context 内部按模块全局名调用 render_dockerfile，因此替换模块
        # 属性即可拦截；install_block 会进入 Dockerfile 文本并参与 tag 哈希，
        # 预构建与运行时因此仍然一致。
        orig_render = mk.render_dockerfile
        pinned_version = gateway_agentscope_pin()

        def patched_render_dockerfile(*args: Any, **kwargs: Any) -> str:
            block = kwargs.get("install_agentscope_block")
            if block and pinned_version:
                kwargs["install_agentscope_block"] = block.replace(
                    '"agentscope"',
                    f'"agentscope=={pinned_version}"',
                )
            return orig_render(*args, **kwargs)

        mk.render_dockerfile = patched_render_dockerfile

        _PATCH_APPLIED = True
        logger.info(
            "[DockerSandbox] 已成功应用 Dockerfile 模板扩展补丁，注入工具链: %s；"
            "网关 agentscope 版本钉死为: %s",
            ", ".join(EXTRA_SANDBOX_APT_PACKAGES),
            pinned_version or "(未钉版本)",
        )
        return True
    except Exception as exc:  # pragma: no cover
        logger.warning("[DockerSandbox] 应用 Dockerfile 模板补丁失败: %s", exc)
        return False
