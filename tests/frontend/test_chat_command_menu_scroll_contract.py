"""Contract: 输入框快捷指令菜单键盘上下切换时滚动条自动跟随高亮项。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHAT_INPUT = ROOT / "frontend" / "src" / "components" / "embed" / "ChatInput.vue"


def test_command_menu_scroll_into_view_on_keyboard_navigation():
    text = CHAT_INPUT.read_text(encoding="utf-8")
    assert "commandListContainerRef" in text
    assert 'ref="commandListContainerRef"' in text
    assert "activeCommandIndex" in text
    assert "scrollActiveCommandIntoView" in text
    assert "scrollIntoView" in text
    assert "block: 'nearest'" in text
    assert "watch(activeCommandIndex" in text
    assert "Enter 选择 · Esc 关闭" in text
    assert "index === activeCommandIndex && !cmd.disabled" in text

