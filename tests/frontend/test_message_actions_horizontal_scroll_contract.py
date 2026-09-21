from pathlib import Path


ROOT = Path(__file__).parents[2]
EMBED = ROOT / "frontend/src/views/EmbedChat.vue"
ACTION_MENU = ROOT / "frontend/src/components/chat/MessageActionMenus.vue"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_embed_message_actions_scroll_without_compressing_items() -> None:
    source = _source(EMBED)
    assert 'class="flex min-w-0 max-w-full flex-nowrap items-center space-x-2 overflow-x-auto' in source
    assert 'class="flex shrink-0 items-center space-x-1"' in source
    assert '<div class="hidden sm:block shrink-0">' in source
    # 间距值由 space-x-1.5 回调为 space-x-1，仅锚定「桌面端专属操作组」这一稳定特征。
    assert 'class="hidden sm:flex shrink-0 items-center' in source
    assert 'space-x-1 ml-auto' not in source


def test_message_action_menu_keeps_root_and_more_action_intrinsic_width() -> None:
    source = _source(ACTION_MENU)
    assert '<div ref="root" class="flex shrink-0 items-center gap-1.5">' in source
    assert 'class="flex min-h-8 shrink-0 items-center gap-1 whitespace-nowrap rounded-md px-2 py-1 text-[11px] text-gray-500' in source
    assert 'class="flex min-h-8 shrink-0 items-center gap-1 whitespace-nowrap rounded-md px-2 py-1 text-[11px] font-medium' in source


def test_more_menu_escapes_horizontal_scroll_clipping_context() -> None:
    source = _source(ACTION_MENU)
    assert '<Teleport to="body">' in source
    assert 'ref="moreButton"' in source
    assert 'ref="moreMenu"' in source
    assert 'position: "fixed"' in source
    assert 'window.addEventListener("scroll", repositionMoreMenu, true)' in source
