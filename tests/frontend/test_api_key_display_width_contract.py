"""API Key 展示容器宽度契约（源码级）。

背景：`nzi_` 前缀让 API Key 从 43 字符增至 47 字符，原先 `max-w-md`（448px）的
重置弹窗装不下等宽文本，密钥会折行。这里锁定该弹窗的宽度，避免 Key 格式再变长时
又被挤折。

宽度估算：`max-w-lg` = 512px，扣除 `p-6`（48px）、复制按钮与间距（约 80px）、
code 的 `p-2`（16px）后约 368px；47 字符在 `text-xs`（12px）等宽字体下约 338px，
留有余量。`break-all` 作为超长兜底保留。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
USERS_VUE = ROOT / "frontend/src/views/Users.vue"

pytestmark = pytest.mark.no_infrastructure


def _regenerate_dialog() -> str:
    """截取「重置 API Key」弹窗片段。"""
    source = USERS_VUE.read_text(encoding="utf-8")
    return source.split("<!-- Regenerate Key Dialog -->", 1)[1].split(
        "<!-- Disable 2FA Dialog -->", 1
    )[0]


def test_regenerate_dialog_is_wide_enough_for_prefixed_key():
    """重置弹窗必须容得下 47 字符的 `nzi_` 前缀 Key，不折行。"""
    dialog = _regenerate_dialog()

    assert "max-w-lg" in dialog
    assert "max-w-md" not in dialog


def test_key_text_keeps_overflow_fallback():
    """密钥文本仍保留 `select-all`（便于整段选中）与 `break-all`（超长兜底）。"""
    dialog = _regenerate_dialog()

    assert "select-all" in dialog
    assert "break-all" in dialog
