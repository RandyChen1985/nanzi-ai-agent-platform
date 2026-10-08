"""前端构建内存上限契约。

背景：接入 @file-viewer 后，`@file-viewer/vite-plugin` 会自动把 `preset-standard`
注入 bundle，它静态引入 word / pdf / spreadsheet / pptx / ofd / archive 等渲染器，
产出约 4.4MB 的 chunk（含 pdf.worker 1.0MB、pptx.worker 0.6MB），构建峰值内存随之
超过原先的 3048MB。容器内表现为：

    FATAL ERROR: Ineffective mark-compacts near heap limit
    Allocation failed - JavaScript heap out of memory    (exit 134)

这不是编译错误，而是内存预算不足——所以必须在构建配置层面守住下限，而不是靠
「构建失败后再手动调大」。
"""

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure

# 与原 3048 相比留出余量；4096 也是 docker/_build_common.sh 宿主机预构建所用的值
MIN_HEAP_MB = 4096


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _heap_ceiling_mb(source: str) -> int:
    """取出源码里第一个 --max-old-space-size=N 的 N。"""
    match = re.search(r"--max-old-space-size=(\d+)", source)
    assert match, "未找到 --max-old-space-size 配置"
    return int(match.group(1))


def test_container_frontend_build_heap_ceiling_is_raised():
    """容器内 vite build 的堆上限不得低于 4096MB。"""
    dockerfile = _source("docker/Dockerfile")
    ceiling = _heap_ceiling_mb(dockerfile)

    assert ceiling >= MIN_HEAP_MB, (
        f"容器内前端构建堆上限为 {ceiling}MB，低于 {MIN_HEAP_MB}MB；"
        "接入 file-viewer 后构建峰值已超过 3048MB，会以 exit 134（OOM）失败"
    )


def test_container_ceiling_not_below_host_prebuild_ceiling():
    """容器内上限不得低于宿主机预构建，否则会出现「宿主机能过、容器 OOM」。"""
    container = _heap_ceiling_mb(_source("docker/Dockerfile"))
    host = _heap_ceiling_mb(_source("docker/_build_common.sh"))

    assert container >= host, (
        f"容器内上限 {container}MB 低于宿主机预构建的 {host}MB，"
        "两条构建路径行为不一致，容器路径会先于宿主机失败"
    )


def test_dockerfile_keeps_prebuild_escape_hatch():
    """保留 PREBUILD_FRONTEND=1 逃生舱：内存紧张的构建机可改在宿主机构建。"""
    dockerfile = _source("docker/Dockerfile")

    assert "ARG PREBUILD_FRONTEND=0" in dockerfile
    assert '$PREBUILD_FRONTEND" = "1"' in dockerfile
    assert "dist/index.html" in dockerfile

    # 构建脚本仍需在缺 node/npm 时给出明确报错，而不是静默回落到容器内构建
    common = _source("docker/_build_common.sh")
    assert "build_frontend_on_host" in common
    assert "未找到 node/npm" in common
