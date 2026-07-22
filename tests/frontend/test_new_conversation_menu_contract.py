"""Contract: 新会话类型菜单不应因 mouseleave / 空隙误关。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHAT_INPUT = ROOT / "frontend" / "src" / "components" / "embed" / "ChatInput.vue"


def test_new_conversation_menu_stays_open_until_click_outside():
    text = CHAT_INPUT.read_text(encoding="utf-8")
    assert "showNewConversationMenu" in text
    assert "newConversationMenuRef" in text
    assert "setNewConversationMenuRef" in text
    assert "新建项目会话" in text
    assert "selectNewConversationType('/project')" in text
    # 点击打开：禁止用 mouseleave 关菜单（空隙会导致来不及点第二项）
    assert '@mouseleave="showNewConversationMenu = false"' not in text
    assert "newConversationMenuRef.value" in text
    assert "el.contains(target)" in text or "!el.contains(target)" in text
    # 用 padding 桥接而非 margin 空隙
    assert "top-full z-[100] pt-2" in text
    assert 'top-full mt-2 z-[100]' not in text


def test_new_conversation_menu_closes_on_escape():
    text = CHAT_INPUT.read_text(encoding="utf-8")
    assert "if (showNewConversationMenu.value)" in text
