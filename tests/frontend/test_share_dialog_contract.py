"""弹窗接线契约：有效期选项、密码可改、结果态分开复制、错误不吞。

源码契约测试：只读文件内容比对关键字，不启动浏览器、不连数据库，
因此可以用 no_infrastructure 标记跳过共享环境初始化。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure
SOURCE = "frontend/src/components/ShareConversationDialog.vue"


def _source() -> str:
    return (ROOT / SOURCE).read_text(encoding="utf-8")


def test_uses_shared_expiry_helpers_instead_of_inline_math():
    source = _source()
    assert "resolveExpiry" in source
    assert "describeExpiry" in source
    assert "generateShareSecret" in source


def test_both_access_modes_and_custom_expiry_are_offered():
    source = _source()
    assert "公开链接" in source and "密钥访问" in source
    assert "24 小时" in source and "永久" in source
    assert "自定义" in source


def test_result_state_exposes_link_and_secret_separately():
    source = _source()
    assert "复制链接" in source and "复制密码" in source
    assert "打开预览" in source
    # 一键复制分享信息 + 二维码（只编码链接）
    assert "buildShareCopyText" in source and "复制分享信息" in source
    assert "loadQrCode(created.url)" in source and "generateQRCodeDataUrl(url)" in source


def test_props_and_emits_match_plan_contract():
    """props / emit 名称是后续任务接线契约，改名会静默破坏调用方。"""
    source = _source()
    for prop in (
        "visible",
        "conversationId",
        "conversationTitle",
        "turnCount",
        "running",
        "existingActiveCount",
        "authHeaders",
    ):
        assert prop in source, prop
    for event in ("'close'", "'created'", "'toast'"):
        assert event in source, event
