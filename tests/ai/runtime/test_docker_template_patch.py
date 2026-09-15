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
