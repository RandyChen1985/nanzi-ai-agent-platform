"""试跑抽屉里的关键内容支持复制浮标。"""
from pathlib import Path

TESTER = Path("frontend/src/components/system/ApiToolTester.vue")
COPY_BUTTON = Path("frontend/src/components/common/CopyButton.vue")


def test_copy_button_wraps_platform_clipboard_with_feedback():
    source = COPY_BUTTON.read_text(encoding="utf-8")
    assert "copyToClipboard" in source
    assert "已复制" in source
    assert "复制失败" in source
    assert "CheckIcon" in source
    assert "ClipboardDocumentIcon" in source


def test_tester_has_copy_floats_for_request_and_response_blocks():
    source = TESTER.read_text(encoding="utf-8")
    assert "CopyButton" in source
    for label in ("最终 URL", "Query 参数", "请求体", "Headers", "响应体", "失败信息"):
        assert f'label="{label}"' in source
    assert ':text="result.request.url"' in source
    assert ':text="result.body"' in source
    assert "absolute right-2 top-2" in source
