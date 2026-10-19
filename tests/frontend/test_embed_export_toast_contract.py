"""会话导出只保留一个结果 toast。

导出开始时给一个不自动消失的进度 toast；完成/失败/空结果时先移除它，再显示结果，
否则用户会同时看到「正在导出对话记录...」和「导出成功（N 条消息）」两个浮层。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def _export_body() -> str:
    source = _source()
    start = source.index("const handleExportChatFromSidebar = async")
    end = source.index("const handleGlobalKeydown", start)
    return source[start:end]


def test_export_loading_toast_is_removed_before_result_toast():
    source = _source()
    assert "const removeToast = toast.removeToast;" in source

    body = _export_body()
    assert 'const loadingToastId = showToast("正在导出对话记录...", "info", 0);' in body
    # 空结果、成功、失败三条收尾路径都要清掉进度 toast
    assert body.count("removeToast(loadingToastId)") >= 3

    success_at = body.index("showToast(`导出成功")
    assert body.rindex("removeToast(loadingToastId)", 0, success_at) > 0
    # 不允许再出现不带 id、无法主动关闭的进度提示
    assert 'showToast("正在导出对话记录...", "info");' not in body
