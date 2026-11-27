"""聊天日志空回复状态文案契约测试。

确保 ChatLogs.vue 的 `emptyReplyView` 对挂起/拒批状态给出准确说明，
不再与真正的「模型静默未返回」混为一谈。
"""

from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _extract_empty_reply_view_body() -> str:
    source = (ROOT / "frontend/src/views/ChatLogs.vue").read_text(encoding="utf-8")
    match = re.search(
        r"const emptyReplyView\s*=\s*\(turn:\s*ConversationTurn\)\s*=>\s*\{(?P<body>.*?)\n\}",
        source,
        flags=re.DOTALL,
    )
    assert match is not None, "未找到 ChatLogs.vue 中的 emptyReplyView 定义"
    return match.group("body")


@pytest.mark.parametrize(
    ("status", "expected_title"),
    [
        ("cancelled", "该轮已取消，未产生回复"),
        ("interrupted", "该轮已中断，未产生回复"),
        ("error", "该轮执行失败，未产生回复"),
        ("awaiting_permission", "该轮正在等待工具授权，尚未产生回复"),
        ("awaiting_external_execution", "该轮正在等待外部执行结果，尚未产生回复"),
        ("awaiting_user", "该轮正在等待用户交互，尚未产生回复"),
        ("rejected", "该轮工具授权已被拒绝，未产生回复"),
    ],
)
def test_chat_logs_empty_reply_view_handles_terminal_and_suspended_statuses(
    status: str,
    expected_title: str,
):
    body = _extract_empty_reply_view_body()
    assert "const status = (turn.status || '').trim().toLowerCase()" in body
    assert f"status === '{status}'" in body
    assert expected_title in body


def test_chat_logs_empty_reply_view_keeps_default_fallback():
    body = _extract_empty_reply_view_body()
    assert "title: '该轮未产生回复'" in body
    assert "模型没有返回任何内容，可在「轨迹」页签中确认执行情况。" in body
