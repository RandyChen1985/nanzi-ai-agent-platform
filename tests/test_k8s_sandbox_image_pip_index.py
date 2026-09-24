"""K8s 沙箱预置镜像构建脚本的 pip 源契约与端到端测试。

覆盖：内置默认清华源、PYPI_INDEX_URL 覆盖、--pip-index 优先级、
http 源自动放行、非法值拒绝、文档同步登记。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
K8S_DIR = ROOT / "k8s_deploy"
BUILD_SCRIPT = K8S_DIR / "build-k8s-sandbox-image.sh"
DEFAULT_PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
TSINGHUA_HOST = "pypi.tuna.tsinghua.edu.cn"
MIRROR_URL = "https://mirror.example.com/pypi/simple"
OFFICIAL_URL = "https://pypi.org/simple"
INTERNAL_HTTP_URL = "http://nexus.internal:8081/repository/pypi/simple"


def _run_dry_run(
    *args: str, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """执行 --dry-run，隔离外部 PYPI_INDEX_URL 干扰并只注入用例需要的环境变量。"""
    env = dict(os.environ)
    env.pop("PYPI_INDEX_URL", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(BUILD_SCRIPT), "--dry-run", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(K8S_DIR),
        timeout=180,
    )


def test_script_exposes_pip_index_option_and_tsinghua_default():
    source = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert 'DEFAULT_PIP_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"' in source
    assert 'PIP_INDEX="${PYPI_INDEX_URL:-$DEFAULT_PIP_INDEX}"' in source
    assert "--pip-index)" in source
    assert "--default-index" in source


def test_dry_run_uses_tsinghua_mirror_by_default():
    result = _run_dry_run()

    assert result.returncode == 0, result.stderr
    assert f"--default-index {DEFAULT_PIP_INDEX}" in result.stdout


def test_dry_run_honours_pypi_index_url_env():
    result = _run_dry_run(env_extra={"PYPI_INDEX_URL": MIRROR_URL})

    assert result.returncode == 0, result.stderr
    assert f"--default-index {MIRROR_URL}" in result.stdout
    assert TSINGHUA_HOST not in result.stdout


def test_dry_run_pip_index_flag_overrides_env():
    result = _run_dry_run(
        "--pip-index",
        OFFICIAL_URL,
        env_extra={"PYPI_INDEX_URL": MIRROR_URL},
    )

    assert result.returncode == 0, result.stderr
    assert f"--default-index {OFFICIAL_URL}" in result.stdout
    assert MIRROR_URL not in result.stdout


def test_dry_run_applies_index_to_both_install_steps():
    """两条 uv pip install（BASE_REQS 与 agentscope --no-deps）都必须带源。"""
    result = _run_dry_run("--pip-index", OFFICIAL_URL)

    assert result.returncode == 0, result.stderr
    assert result.stdout.count(f"--default-index {OFFICIAL_URL}") >= 2
    assert "--no-deps" in result.stdout


def test_dry_run_allows_http_index_host_automatically():
    result = _run_dry_run("--pip-index", INTERNAL_HTTP_URL)

    assert result.returncode == 0, result.stderr
    assert f"--default-index {INTERNAL_HTTP_URL}" in result.stdout
    assert "--allow-insecure-host nexus.internal:8081" in result.stdout


def test_https_index_does_not_enable_insecure_host():
    result = _run_dry_run("--pip-index", OFFICIAL_URL)

    assert result.returncode == 0, result.stderr
    assert "--allow-insecure-host" not in result.stdout


def test_rejects_index_url_with_shell_metacharacters():
    result = _run_dry_run("--pip-index", "https://pypi.org/simple && echo pwned")

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "pip 源" in combined
    # 非法值必须在生成构建上下文之前被拦下
    assert "FROM " not in result.stdout


def test_rejects_index_url_without_http_scheme():
    result = _run_dry_run("--pip-index", "pypi.tuna.tsinghua.edu.cn/simple")

    assert result.returncode != 0
    assert "http:// 或 https://" in result.stdout + result.stderr


def test_rejects_empty_index_url():
    result = _run_dry_run("--pip-index", "")

    assert result.returncode != 0
    assert "pip 源不能为空" in result.stdout + result.stderr


def test_help_and_docs_document_pip_index():
    help_result = subprocess.run(
        ["bash", str(BUILD_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        cwd=str(K8S_DIR),
        timeout=60,
    )
    assert help_result.returncode == 0
    assert "--pip-index" in help_result.stdout

    k8s_readme = (K8S_DIR / "README.md").read_text(encoding="utf-8")
    assert "--pip-index" in k8s_readme
    assert "pypi.org/simple" in k8s_readme

    sandbox_readme = (ROOT / "sandbox" / "k8s" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "--pip-index" in sandbox_readme
