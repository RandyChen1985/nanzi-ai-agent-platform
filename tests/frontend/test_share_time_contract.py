import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/SharedConversation.vue"
UTIL = ROOT / "frontend/src/utils/shareTime.ts"
BEHAVIOR = ROOT / "tests/frontend/share_time.mjs"


def _page_source() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_share_page_renders_conversation_time():
    """分享页必须显示对话发生时间，否则收链接的人不知道这是什么时候的对话。"""
    source = _page_source()

    # 元信息行里出现「对话时间」，且用的是绝对时刻而不是相对时间
    assert "对话时间" in source
    assert "formatShareDateTime(conversationAt)" in source
    # 会话时间取第一条消息的 created_at，而不是快照的 content.created_at（那是分享创建时间）
    assert "conversationAt" in source
    assert "m.created_at" in source


def test_share_page_renders_per_turn_timestamp():
    """每轮问答要打时间戳；同一轮 user/assistant 共享一个 created_at，只在 user 侧显示一次。"""
    source = _page_source()

    assert "messageStamp(index)" in source
    # 只在 user 消息上显示，避免同一条时间出现两遍
    assert "current.role !== 'user'" in source
    # 相邻消息按时间顺序取「上一条」，用于跨天补日期
    assert "formatShareMessageStamp(current.created_at, previous?.created_at)" in source


def test_share_page_imports_absolute_time_helpers():
    """必须用绝对时间工具，不能复用 utils/date 的相对时间（「3 天前」对访客没信息量）。"""
    source = _page_source()

    assert "from '@/utils/shareTime'" in source
    assert "formatShareDateTime" in source
    assert "formatShareMessageStamp" in source
    assert "formatDate" not in source


def test_share_time_helper_behavior():
    """跑 node 行为测试，锁定时区无关的格式化口径。"""
    util_source = UTIL.read_text(encoding="utf-8")
    assert "export function formatShareMessageStamp" in util_source
    assert "export function formatShareDateTime" in util_source

    result = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(BEHAVIOR)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"时间工具行为测试失败:\n{result.stdout}\n{result.stderr}"
    assert "全部通过" in result.stdout
