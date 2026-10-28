"""分享文案与二维码的契约：一键复制、二维码只编码链接、两处入口一致。"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

UTIL = "frontend/src/utils/shareCopy.ts"
DIALOG = "frontend/src/components/ShareConversationDialog.vue"
PANEL = "frontend/src/components/SharedLinksPanel.vue"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_copy_text_is_a_pure_utility():
    """文案拼装必须是零依赖纯函数：node 能直接跑，才谈得上行为测试。"""
    source = _read(UTIL)
    assert "buildShareCopyText" in source
    for banned in ("import axios", "fetch(", "document.", "window."):
        assert banned not in source, banned


def test_dialog_offers_one_click_copy_and_qrcode():
    source = _read(DIALOG)
    # 一次复制「主题 + 链接 + 密码」，不再让用户分两次复制
    assert "buildShareCopyText" in source
    assert "复制分享信息" in source
    assert "copyShareInfo" in source
    # 二维码复用既有 qrcode 工具，不引第三方在线接口（分享链接是敏感凭据）
    assert "generateQRCodeDataUrl" in source
    assert "qrDataUrl" in source
    assert "api.qrserver" not in source and "chart.googleapis" not in source
    # 二维码只编码链接：出现 secret 参与编码就是泄露
    # 传给二维码生成器的是纯链接（loadQrCode 的入参），不是拼好的文案
    assert "loadQrCode(created.url)" in source
    assert "generateQRCodeDataUrl(url)" in source
    assert "generateQRCodeDataUrl(buildShareCopyText" not in source


def test_list_row_also_copies_the_whole_blurb():
    source = _read(PANEL)
    assert "buildShareCopyText" in source
    assert "复制分享信息" in source
    # 密码仍要能单独复制（有的人习惯只发密码）
    assert "复制密码" in source


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行行为测试")
def test_share_copy_behaviour():
    script = Path("tests/frontend/share_copy.mjs")
    assert script.exists(), "行为测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "会话分享文案测试全部通过" in proc.stdout
