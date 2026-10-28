"""K8s 沙箱预置镜像内置 Node 的契约。

背景：技能的 ``scripts/*.js`` 与 ``npx`` 启动的 MCP 服务都依赖 node/npm/npx，
而 ``python:*-slim`` 基座并不自带（Debian bookworm 源里的 nodejs 只到 18、已 EOL）。
docker 策略走 AgentScope 的 ``node_version``（见
``docker_template_patch.SANDBOX_NODE_VERSION``）；k8s 策略的 ``K8sWorkspace``
直接拉现成镜像、没有这个开关，所以只能在预置镜像的 Dockerfile 里写死。

两种策略必须同版本——否则同一个技能「docker 沙箱能跑、k8s 沙箱跑不了」。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
K8S_DIR = ROOT / "k8s_deploy"
BUILD_SCRIPT = K8S_DIR / "build-k8s-sandbox-image.sh"


def _dry_run(*args: str) -> str:
    env = dict(os.environ)
    env.pop("PYPI_INDEX_URL", None)
    result = subprocess.run(
        ["bash", str(BUILD_SCRIPT), "--dry-run", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(K8S_DIR),
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_node_version_matches_docker_policy():
    """两侧版本必须同源：一边 22 一边 20 会立刻造成策略间行为不一致。"""
    from app.services.ai.runtime.agentscope.docker_template_patch import (
        SANDBOX_NODE_VERSION,
    )

    source = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert f'SANDBOX_NODE_VERSION="{SANDBOX_NODE_VERSION}"' in source


def test_dry_run_dockerfile_installs_node_npm_npx():
    output = _dry_run()

    from app.services.ai.runtime.agentscope.docker_template_patch import (
        SANDBOX_NODE_VERSION,
    )

    assert f"FROM node:{SANDBOX_NODE_VERSION}-slim AS node_stage" in output
    assert "COPY --from=node_stage /usr/local/bin/node /usr/local/bin/node" in output
    assert "COPY --from=node_stage /usr/local/lib/node_modules /usr/local/lib/node_modules" in output
    # npx 不是独立二进制，而是 npm 包里的 cli，必须显式软链到 /usr/local/bin
    assert "npm/bin/npm-cli.js /usr/local/bin/npm" in output
    assert "npm/bin/npx-cli.js /usr/local/bin/npx" in output


def test_dry_run_verifies_node_at_build_time():
    """构建期自检：换到非 glibc 基座（如 alpine）要当场失败，而不是运行时才发现。"""
    output = _dry_run()

    assert "node --version" in output
    assert "npm --version" in output


def test_node_copy_precedes_use_and_follows_base_image():
    """顺序校验：node 阶段必须在最前，COPY 必须落在 FROM base 之后。"""
    output = _dry_run()
    node_stage_at = output.index("FROM node:")
    base_at = output.index("FROM python:3.11-slim")
    copy_at = output.index("COPY --from=node_stage")
    assert node_stage_at < base_at < copy_at
