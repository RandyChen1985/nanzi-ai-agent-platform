"""k8s_deploy/install.sh 镜像候选版本识别（解析 + 版本比较）契约测试。

覆盖本次修复的两个真实缺陷：
1. 旧实现用 ``awk -F: '{print $2}'`` 解析运行镜像，遇到 ``registry:5000/repo/name:tag``
   会切出 ``5000/repo/name`` 当 Tag，导致「当前运行中」判定失效；
2. 旧实现把「任意非当前 Tag」都标成「候选新版本」，并按字典序取最后一个作默认值，
   于是 ``1.0.15.0`` 会盖过 ``1.0.16.0`` / ``1.0.17.0``，把降级当成升级推荐。

测试方式：把脚本中纯函数（``parse_image_ref`` / ``version_gt`` / ``sort_versions_asc``）
抽到 bash 里 source 执行，避免触发脚本顶层的交互安装流程。
"""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = ROOT / "k8s_deploy" / "install.sh"

pytestmark = pytest.mark.no_infrastructure

pytest.importorskip("shutil")


def _bash() -> str:
    bash = shutil.which("bash")
    assert bash, "契约测试需要 bash"
    return bash


def _run_function(body: str) -> str:
    """source install.sh 的函数定义（不含顶层流程）后执行 body。"""
    source = INSTALL_SH.read_text(encoding="utf-8")
    start = source.index("parse_image_ref() {")
    end = source.index("# 统一的资源下发/演练函数")
    helpers = source[start:end]
    script = f"set -eu\n{helpers}\n{body}\n"
    completed = subprocess.run(
        [_bash(), "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_install_sh_parses_repository_with_registry_port():
    out = _run_function(
        'parse_image_ref "registry.example.com:5000/nanzi/nanzi-ai-agent:1.0.16.0"\n'
        'printf "%s\\n%s\\n" "$IMAGE_REF_REPO" "$IMAGE_REF_TAG"'
    )
    repo, tag = out.splitlines()
    assert repo == "registry.example.com:5000/nanzi/nanzi-ai-agent"
    assert tag == "1.0.16.0"


def test_install_sh_parses_plain_reference_and_digest():
    out = _run_function(
        'parse_image_ref "nanzi-ai-agent:1.0.16.0"\n'
        'printf "%s|%s\\n" "$IMAGE_REF_REPO" "$IMAGE_REF_TAG"\n'
        'parse_image_ref "nanzi-ai-agent@sha256:abcdef"\n'
        'printf "%s|%s\\n" "$IMAGE_REF_REPO" "$IMAGE_REF_TAG"'
    )
    assert out.splitlines() == ["nanzi-ai-agent|1.0.16.0", "nanzi-ai-agent|latest"]


@pytest.mark.parametrize(
    ("newer", "older"),
    [
        ("1.0.17.0", "1.0.16.0"),
        ("1.0.10.0", "1.0.9.0"),
        ("1.0.16.0", "1.0.16"),
        ("2.0.0.0", "1.99.99.0"),
    ],
)
def test_install_sh_version_gt_is_numeric_per_segment(newer, older):
    assert _run_function(f'version_gt "{newer}" "{older}" && echo gt || echo le').strip() == "gt"
    assert _run_function(f'version_gt "{older}" "{newer}" && echo gt || echo le').strip() == "le"


def test_install_sh_version_gt_treats_alias_tags_as_incomparable():
    # latest / main 这类纯别名不可与数字版本比较，避免被误判为「更新」
    for left, right in (("latest", "1.0.16.0"), ("1.0.16.0", "latest"), ("latest", "latest")):
        assert _run_function(f'version_gt "{left}" "{right}" && echo gt || echo le').strip() == "le"


def test_install_sh_sort_versions_asc_orders_numerically():
    out = _run_function(
        'printf "1.0.16.0\\n1.0.9.0\\n1.0.10.0\\n1.0.15.0\\n" | sort_versions_asc'
    )
    assert out.split() == ["1.0.9.0", "1.0.10.0", "1.0.15.0", "1.0.16.0"]


def test_install_sh_has_no_legacy_tag_detection_patterns():
    source = INSTALL_SH.read_text(encoding="utf-8")
    # 旧实现：按列号取 Tag + 字典序去重 + 循环覆盖推荐值
    assert "awk -F: '{print $NF}'" not in source
    assert "detected_nanzi_tags" not in source
    assert "recommended_tag=\"$t\"" not in source
    # 统一探测入口在两个流程中都被复用
    assert source.count("probe_nanzi_images") >= 3
    assert source.count("recommend_upgrade_tag") >= 3
    # 降级保护必须存在
    assert "is_downgrade_tag" in source
