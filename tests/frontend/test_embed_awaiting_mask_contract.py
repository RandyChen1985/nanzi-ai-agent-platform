"""等待宿主凭据期间的遮罩必须盖住**整个组件**，包括会话历史侧栏。

原先这块遮罩写在「Main Chat Area」这个滚动容器里，`absolute inset-0` 只相对它定位，
于是左侧 `ChatHistorySidebar`（`relative w-72 z-10`）露在外面：登录还没完成，用户已经
能看到、也能点开历史会话。真实截图里就是这个样子——右边一片「正在获取登录信息」，
左边的历史列表却清清楚楚。

修法是把遮罩提升到根容器层级（根是 `relative`），并把它抬到 `z-[60]`：高于侧栏的
`z-10`，也高于移动端抽屉的 `z-50`。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
SIDEBAR = (ROOT / "frontend/src/components/ChatHistorySidebar.vue").read_text(encoding="utf-8")

pytestmark = pytest.mark.no_infrastructure


def _mask_open_tag() -> str:
    """遮罩的根标签块（`<div` 到 `>`，属性分了好几行）。"""
    lines = SOURCE.splitlines()
    idx = next(i for i, line in enumerate(lines) if 'data-testid="embed-awaiting-init"' in line)
    start = max(j for j in range(max(idx - 6, 0), idx + 1) if lines[j].strip().startswith("<div"))
    return "\n".join(lines[start : idx + 2])


def test_mask_sits_at_root_level():
    """缩进 4 空格 = 根容器的直接子级；写在消息区里时是 6 空格。"""
    tag = _mask_open_tag()
    first_line = tag.splitlines()[0]
    assert first_line.startswith("    <div"), f"遮罩不在根层级：{first_line!r}"
    assert not first_line.startswith("      ")


def test_mask_renders_after_chat_area():
    """挪到根级后必然出现在「Main Chat Area」之后（同在根容器下）。"""
    assert SOURCE.index('data-testid="embed-awaiting-init"') > SOURCE.index("<!-- Main Chat Area -->")


def test_mask_above_sidebar_layers():
    """z 必须高于侧栏（桌面 z-10）与移动抽屉（z-50），否则侧栏仍会露出来。"""
    assert "z-[60]" in _mask_open_tag()
    assert "relative w-72 h-full z-10" in SIDEBAR
    assert "fixed inset-x-0 bottom-0 top-0 w-full z-50" in SIDEBAR


def test_mask_still_blocks_pointer_events():
    """整层必须吃掉点击：凭据落定前不允许任何误操作。"""
    tag = _mask_open_tag()
    assert "absolute inset-0" in tag


def test_conditional_chain_stays_intact():
    """遮罩整条 v-if 链一起搬到根级：等待态 → 无权限 → 智能体错误。

    只搬首分支会让后面的 `v-else-if` 悬空（Vue 的类型收窄随之失效，
    `urlAgentAccessError` 被当成可能为 null，一次冒出 9 个 TS18047）。整条链一起搬，
    既保住优先级语义，又让三个遮罩都盖住侧栏——没权限时同样不该能翻历史会话。
    """
    assert 'v-else-if="!hasPermission"' in SOURCE
    assert 'v-else-if="urlAgentAccessError"' in SOURCE
    assert SOURCE.index('data-testid="embed-awaiting-init"') < SOURCE.index("<!-- No Permission Overlay -->")
