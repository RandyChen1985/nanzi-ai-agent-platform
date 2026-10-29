"""K8s 沙箱预置镜像的网关依赖必须与上游 + 平台补充分片保持一致。

背景：``build-k8s-sandbox-image.sh`` 会 ``uv pip install --no-deps agentscope==<版本>``，
网关 venv 的其余依赖全靠脚本自己那份清单。这份清单过去是**手工照抄**上游
``agentscope.workspace._utils._GATEWAY_BASE_REQUIREMENTS``：

- 升级到 ``agentscope==2.0.11`` 后上游把 ``mcp<2.0.0`` 换成 ``mcp>=2.0.0``，并新增
  ``httpx2``（``agentscope.mcp._mcp_client`` 直接 import 它，且用的是 2.x 才有的
  ``from mcp import Client``）。脚本没跟上 → 重建预置镜像时构建期的
  ``import agentscope.mcp`` 自检直接失败，或者网关起来后一调工具就 500；
- 更隐蔽的是清单在脚本里存了**两份**（``BASE_REQS`` 常量与 heredoc 里的字面量），
  改一处漏一处也不会有任何报错。

这里锁三件事：清单内容 = 上游 base requirements ∪ 平台 extra_pip；mcp 约束必须允许
2.x；Dockerfile 的安装行必须从 ``BASE_REQS`` 单源展开。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
K8S_DIR = ROOT / "k8s_deploy"
BUILD_SCRIPT = K8S_DIR / "build-k8s-sandbox-image.sh"


def _normalize_name(spec: str) -> str:
    """``docstring_parser>=1`` / ``httpx2 >=2.5.0`` → 规范化的分发包名。"""
    return Requirement(spec.strip()).name.lower().replace("_", "-")


def _base_reqs_from_script() -> list[str]:
    """从脚本里取出 ``BASE_REQS`` 数组（用 bash 自身展开，避免手写解析）。"""
    source = BUILD_SCRIPT.read_text(encoding="utf-8")
    start = source.index("BASE_REQS=(")
    end = source.index("\n)", start) + 2
    block = source[start:end]

    result = subprocess.run(
        ["bash", "-c", block + '\nprintf "%s\\n" "${BASE_REQS[@]}"'],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line.strip()]


def _dry_run() -> str:
    env = dict(os.environ)
    env.pop("PYPI_INDEX_URL", None)
    result = subprocess.run(
        ["bash", str(BUILD_SCRIPT), "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(K8S_DIR),
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_gateway_deps_match_upstream_base_requirements_plus_platform_extras():
    from agentscope.workspace._utils import _GATEWAY_BASE_REQUIREMENTS

    from app.services.ai.runtime.agentscope.workspace_container_mcp import (
        GATEWAY_EXTRA_PIP,
    )

    expected = {
        _normalize_name(spec)
        for spec in (*_GATEWAY_BASE_REQUIREMENTS, *GATEWAY_EXTRA_PIP)
    }
    actual = {_normalize_name(spec) for spec in _base_reqs_from_script()}

    assert actual == expected, (
        "预置镜像的网关依赖与「上游 base requirements + 平台 extra_pip」不一致；"
        f"缺失={sorted(expected - actual)} 多余={sorted(actual - expected)}"
    )


def test_gateway_mcp_requirement_allows_mcp_2x():
    """``mcp<2.0.0`` 会让 agentscope 2.0.11 的 ``from mcp import Client`` 直接 ImportError。"""
    specs = {Requirement(spec).name.lower(): Requirement(spec) for spec in _base_reqs_from_script()}

    mcp_req = next((req for name, req in specs.items() if name == "mcp"), None)
    assert mcp_req is not None, "网关依赖清单里必须有 mcp"
    assert mcp_req.specifier.contains("2.3.0"), f"mcp 约束 {mcp_req.specifier} 不允许 mcp 2.x"
    assert mcp_req.specifier.contains("2.0.0")


def test_gateway_deps_are_installed_from_a_single_source():
    """安装行必须展开 ``BASE_REQS``，不能另抄一份字面量（过去就是两份漂移）。"""
    source = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "${BASE_REQS[*]}" in source or "${BASE_REQS[@]}" in source
    # 第二份手写清单（去掉 BASE_REQS 定义后不应再出现 mcp 约束字面量）
    assert source.count('"mcp<2.0.0"') == 0
    assert "docstring_parser jinja2 aiofiles" not in source


def test_dry_run_dockerfile_installs_mcp2_and_httpx2():
    output = _dry_run()

    assert "mcp<2.0.0" not in output
    assert "httpx2" in output
    assert "mcp>=2.0.0" in output
    # agentscope 2.0.11 的网关 import 面同时需要 mcp 2.x 与 httpx2，自检必须能兜住
    assert "import agentscope.mcp" in output
