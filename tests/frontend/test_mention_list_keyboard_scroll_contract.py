"""Contract: @ 专家提及列表键盘上下导航时高亮选中与滚动跟随。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MENTION_LIST = ROOT / "frontend" / "src" / "components" / "agent" / "MentionList.vue"


def test_mention_list_keyboard_scroll_and_highlight():
    text = MENTION_LIST.read_text(encoding="utf-8")
    assert "data-mention-index" in text
    assert "selectedIndex" in text
    assert "querySelector" in text
    assert "scrollIntoView" in text
    assert "block: 'nearest'" in text
    assert "ring-1 ring-primary/20" in text
    assert "border-primary/30" in text
    assert "handleKeydown" in text
    assert "defineExpose({ handleKeydown })" in text
