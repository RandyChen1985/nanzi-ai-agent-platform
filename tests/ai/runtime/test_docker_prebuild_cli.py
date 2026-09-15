import io
import sys
from pathlib import Path
import pytest

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_dry_run_cli_outputs_troubleshooting_packages(monkeypatch, capsys):
    """测试 --dry-run 演练模式正常生成 Dockerfile 并输出完整排障工具链。"""
    from sandbox.docker import prebuild_docker_sandbox

    await prebuild_docker_sandbox.dry_run_cli(base_image="python:3.11-slim")

    captured = capsys.readouterr()
    output = captured.out

    assert "Dry-Run 演练模式" in output
    assert "agentscope-workspace:" in output
    assert "git" in output
    assert "tree" in output
    assert "iputils-ping" in output
    assert "net-tools" in output
    assert "procps" in output
    assert "telnet" in output
    assert "requirements.txt" in output
    assert "_mcp_gateway_app.py" in output
    assert "Dry-Run 演练完成！未向 Docker Daemon 发起任何构建任务。" in output


def test_script_reorganization_and_forwarding_compatibility():
    """测试新目录脚本结构完整性与向后兼容性。"""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    docker_dir = repo_root / "sandbox" / "docker"
    root_script = repo_root / "prebuild-sandbox.sh"
    new_sh = docker_dir / "prebuild-sandbox.sh"
    new_py = docker_dir / "prebuild_docker_sandbox.py"
    compat_py = repo_root / "scripts" / "prebuild_docker_sandbox.py"
    readme = docker_dir / "README.md"

    k8s_dir = repo_root / "sandbox" / "k8s"
    k8s_sh = k8s_dir / "build-k8s-sandbox-image.sh"
    k8s_readme = k8s_dir / "README.md"

    assert new_sh.exists(), "sandbox/docker/ 下 shell 脚本应当存在"
    assert new_py.exists(), "sandbox/docker/ 下 python 脚本应当存在"
    assert readme.exists(), "README 说明文档应当存在"
    assert k8s_sh.exists(), "sandbox/k8s/ 下快捷构建脚本应当存在"
    assert k8s_readme.exists(), "sandbox/k8s/ 下 README 说明文档应当存在"
    assert compat_py.exists(), "scripts 目录兼容转发 python 脚本应当存在"
    assert not root_script.exists(), "根目录下的 prebuild-sandbox.sh 应当已被移除，统归集至 sandbox/docker"
