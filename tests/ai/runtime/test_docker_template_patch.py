import pytest

pytestmark = pytest.mark.no_infrastructure


def test_docker_template_patch_injects_troubleshooting_packages():
    """验证补丁成功向 AgentScope 的 Dockerfile.template 中注入排障工具链。"""
    from app.services.ai.runtime.agentscope.docker_template_patch import (
        EXTRA_SANDBOX_APT_PACKAGES,
        apply_agentscope_docker_patches,
    )
    import agentscope.workspace._docker._make_dockerfile as mk

    # 1. 应用补丁（幂等）
    applied = apply_agentscope_docker_patches()
    assert applied is True
    # 再次调用仍应安全返回 True
    assert apply_agentscope_docker_patches() is True

    # 2. 验证渲染出的 Dockerfile
    rendered = mk.render_dockerfile(base_image="python:3.11-slim")
    
    # 原有基础工具
    assert "curl" in rendered
    assert "ca-certificates" in rendered
    assert "ripgrep" in rendered

    # 新增排障工具
    for pkg in EXTRA_SANDBOX_APT_PACKAGES:
        assert pkg in rendered, f"预装工具包 {pkg} 应当存在于 Dockerfile 中"

    # 确认完整的 apt-get install 指令正确串联
    expected_segment = "curl ca-certificates ripgrep git tree iputils-ping net-tools procps telnet"
    assert expected_segment in rendered


def test_docker_build_context_tag_reflects_patch():
    """验证 prepare_build_context 产出的 Tag 基于注入后的内容生成且可重复。"""
    from app.services.ai.runtime.agentscope.docker_template_patch import (
        apply_agentscope_docker_patches,
    )
    import agentscope.workspace._docker._make_dockerfile as mk

    apply_agentscope_docker_patches()

    ctx_dir, tag, copy_files = mk.prepare_build_context(base_image="python:3.11-slim")
    try:
        assert tag.startswith("agentscope-workspace:")
        dockerfile_content = (ctx_dir / "Dockerfile").read_text(encoding="utf-8")
        assert "tree" in dockerfile_content
        assert "iputils-ping" in dockerfile_content
        assert "net-tools" in dockerfile_content
    finally:
        import shutil
        shutil.rmtree(ctx_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 沙箱内置 Node（写死常量，非配置项）
# ---------------------------------------------------------------------------

def test_sandbox_node_version_is_pinned():
    """版本写死在代码里：改这里等于改「沙箱里有什么」这个产品决策。"""
    from app.services.ai.runtime.agentscope.docker_template_patch import (
        SANDBOX_NODE_VERSION,
    )

    assert SANDBOX_NODE_VERSION, "沙箱 Node 版本不能为空，否则退化成没有 node"
    # 20 已于 2026-04 EOL；写死一个已停止维护的版本会埋下安全债
    assert int(SANDBOX_NODE_VERSION) >= 22


def test_rendered_dockerfile_installs_node_and_npm():
    """node_version 非空时，模板必须从官方 slim 镜像 COPY node/npm/npx 三个入口。"""
    import agentscope.workspace._docker._make_dockerfile as mk
    from app.services.ai.runtime.agentscope.docker_template_patch import (
        SANDBOX_NODE_VERSION,
        apply_agentscope_docker_patches,
    )

    apply_agentscope_docker_patches()
    rendered = mk.render_dockerfile(
        base_image="python:3.11-slim",
        node_version=SANDBOX_NODE_VERSION,
    )

    assert f"FROM node:{SANDBOX_NODE_VERSION}-slim AS node_stage" in rendered
    assert "COPY --from=node_stage /usr/local/bin/node /usr/local/bin/node" in rendered
    # npx 不是独立二进制，而是 npm 包内的 cli，必须由软链暴露出来
    assert "/usr/local/bin/npm" in rendered
    assert "/usr/local/bin/npx" in rendered


def test_rendered_dockerfile_omits_node_when_version_is_none():
    """空值必须完全不带 node 段——这是「关掉 node」的唯一开关。"""
    import agentscope.workspace._docker._make_dockerfile as mk

    rendered = mk.render_dockerfile(base_image="python:3.11-slim", node_version=None)

    assert "node_stage" not in rendered
    assert "/usr/local/bin/npx" not in rendered
    # 没装 node 的镜像里出现 RUN node 会直接把构建搞挂
    assert "RUN node --version" not in rendered


def test_runtime_and_prebuild_share_the_same_node_version():
    """运行时与预构建必须传同一个值：它参与镜像 tag 哈希，不一致则预构建永远命不中。"""
    import inspect

    from app.services.ai.runtime.agentscope import docker_prebuild, workspace

    runtime_src = inspect.getsource(workspace)
    prebuild_src = inspect.getsource(docker_prebuild)

    assert '"node_version": SANDBOX_NODE_VERSION' in runtime_src
    assert '"node_version": SANDBOX_NODE_VERSION' in prebuild_src
    # 任何一侧退回硬编码 None / 字面量版本都会让 tag 漂移
    assert '"node_version": None' not in runtime_src
    assert '"node_version": None' not in prebuild_src


def test_rendered_dockerfile_verifies_node_at_build_time():
    """自定义基础镜像换成非 glibc 基座时，构建期就要失败，别等到用户跑技能脚本。"""
    import agentscope.workspace._docker._make_dockerfile as mk
    from app.services.ai.runtime.agentscope.docker_template_patch import (
        SANDBOX_NODE_VERSION,
        apply_agentscope_docker_patches,
    )

    apply_agentscope_docker_patches()
    rendered = mk.render_dockerfile(
        base_image="python:3.11-slim",
        node_version=SANDBOX_NODE_VERSION,
    )

    assert "RUN node --version && npm --version && npx --version" in rendered
    # 自检必须在 COPY 之后，否则验的是基座里可能根本不存在的 node
    assert rendered.index("COPY --from=node_stage") < rendered.index("RUN node --version")


# ---------------------------------------------------------------------------
# 沙箱网关里的 agentscope 版本必须钉死（否则重建即漂移）
# ---------------------------------------------------------------------------

def _requirements_agentscope_pin() -> str:
    """从平台 requirements.txt 解析 agentscope 的 pin 版本（如 agentscope[x,y]==2.0.9）。"""
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[3] / "requirements.txt").read_text(encoding="utf-8")
    match = re.search(r"^agentscope(?:\[[^\]]*\])?==([0-9][^\s#]*)", text, re.MULTILINE)
    assert match, "requirements.txt 里应存在 agentscope 的显式 pin"
    return match.group(1)


def test_gateway_agentscope_version_is_pinned_to_runtime_version():
    """钉的版本必须等于后端实际运行的版本，不能是另写的一个字面量。"""
    import importlib.metadata

    from app.services.ai.runtime.agentscope.docker_template_patch import (
        gateway_agentscope_pin,
    )

    assert gateway_agentscope_pin() == importlib.metadata.version("agentscope")


def test_runtime_version_matches_requirements_pin():
    """运行时安装版本与仓库 pin 必须一致，否则「本机能跑、镜像里是另一个版本」。"""
    import importlib.metadata

    assert importlib.metadata.version("agentscope") == _requirements_agentscope_pin()


def test_rendered_dockerfile_pins_agentscope():
    """回归：上游装的是无版本 ``agentscope``，重建时会拉到最新版。

    最新版跟随生态改用 httpx2，而网关 venv 只有 httpx，容器 gateway 直接起不来：
        ModuleNotFoundError: No module named 'httpx2'
    """
    import agentscope.workspace._docker._make_dockerfile as mk
    from app.services.ai.runtime.agentscope.docker_template_patch import (
        apply_agentscope_docker_patches,
        gateway_agentscope_pin,
    )

    apply_agentscope_docker_patches()
    ctx_dir, _tag, copy_files = mk.prepare_build_context(base_image="python:3.11-slim")
    try:
        dockerfile = (ctx_dir / "Dockerfile").read_text(encoding="utf-8")
    finally:
        import shutil
        shutil.rmtree(ctx_dir, ignore_errors=True)

    pinned = gateway_agentscope_pin()
    assert f'"agentscope=={pinned}"' in dockerfile
    # 不允许残留无版本安装指令（那等于每次都拉最新版）
    assert '--no-deps "agentscope"' not in dockerfile
