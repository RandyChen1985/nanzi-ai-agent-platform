from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

EMBED_CHAT = "frontend/src/views/EmbedChat.vue"
AGENT_DEBUG = "frontend/src/views/AgentDebug.vue"
WORKSPACE_DRAWER = "frontend/src/components/embed/WorkspaceBrowserDrawer.vue"
MEMORY_DRAWER = "frontend/src/components/embed/MemoryBrowserDrawer.vue"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_pinned_dock_offsets_are_pixel_values_not_dynamic_tailwind_classes():
    """钉住偏移必须走内联 px。

    Tailwind 只做静态扫描：`right-[${rem}rem]` 这种运行时拼接的类名不会出现在
    构建产物里，多面板并排时偏移直接失效、面板叠在同一位置。
    """
    for relative_path in (EMBED_CHAT, AGENT_DEBUG):
        source = _read(relative_path)
        assert "right-[${" not in source, relative_path
        assert "pinned-dock-class" not in source, relative_path
        assert ":pinned-dock-right=" in source, relative_path


def test_drawers_render_pinned_offset_through_inline_style():
    for relative_path in (WORKSPACE_DRAWER, MEMORY_DRAWER):
        source = _read(relative_path)
        assert "pinnedDockRight?: number" in source, relative_path
        assert "pinnedDockClass" not in source, relative_path
        assert 'pinnedContainerStyle = computed' in source, relative_path
        assert ':style="pinnedContainerStyle"' in source, relative_path
        assert "'fixed inset-y-0 right-0 max-w-full flex pointer-events-none'" in source, relative_path


def test_embed_chat_waits_for_panels_through_the_capped_layout_helper():
    """让位宽度必须由纯函数封顶，不能再是无上限的宽度累加。"""
    source = _read(EMBED_CHAT)
    assert "computePinnedDrawerLayout(" in source
    assert "pinnedDrawerLayout.value.marginRightPx" in source
    assert "totalPinnedDrawerPx" not in source
    # 旧写法把总和直接 min 到 100vw，容器窄时对话区会被压成 0 宽。
    assert "min(${px}px, 100vw)" not in source


def test_embed_chat_releases_dropped_panels_instead_of_overlapping_chat():
    source = _read(EMBED_CHAT)
    start = source.index("空间不足时把被挤掉的面板自动取消钉住")
    body = source[start : start + 1400]
    assert "pinnedDrawerLayout.value.droppedKeys" in body
    for key in ("webPreview", "browser", "canvas", "memory", "workspace", "knowledge", "portal"):
        assert f'["{key}"' in body, key
    assert "已取消钉住" in body
