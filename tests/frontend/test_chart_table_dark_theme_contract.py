"""深色模式下表格与图表的渲染契约。

用户截图反馈「深色下表看得见一半、图表是一块白板」。两者的成因不同，因此分别锁：

- **表格**：底色与文字色都走 `--md-table-*` 变量并已按 `.dark` 换过，但
  `tbody tr:nth-child(even)` 的斑马纹是硬编码亮色（`#fafbfd`），深色下不一致——
  偶数行成了「白底 + 浅色字」，正是截图里几乎看不见的那几行。
- **图表**：ECharts 画在 canvas 上，Tailwind 的 `dark:` 变体完全管不到它。卡片容器
  曾只有 `bg-white`，option 里的标题/轴标签也是深色常量，所以深色下要么是一块白板、
  要么改成深底后变成「深底深字」。图表必须显式跟随主题重算 option。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = ROOT / "frontend/src"
EMBED_CHAT = FRONTEND_SRC / "views/EmbedChat.vue"
MESSAGE_RENDERER = FRONTEND_SRC / "components/MessageRenderer.vue"
CANVAS_RENDERER = FRONTEND_SRC / "components/embed/CanvasMarkdownRenderer.vue"
DARK_FLAG = FRONTEND_SRC / "composables/useDarkThemeFlag.ts"
CHART_RENDERER = FRONTEND_SRC / "utils/chartRenderer.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_embedded_markdown_zebra_stripe_has_dark_variant():
    """深色斑马纹必须显式声明，否则偶数行会退回亮色底。"""
    source = _read(EMBED_CHAT)
    assert ".dark :deep(.markdown-body tbody tr:nth-child(even))" in source
    assert ".dark :deep(.markdown-body tbody tr:hover)" in source
    # 亮色规则必须保留（浅色模式不受影响）
    assert ":deep(.markdown-body tbody tr:nth-child(even)) {" in source


@pytest.mark.parametrize(
    ("path", "fragment"),
    [
        # 图表卡片本身（注意别被右上角切换按钮的 bg-white/90 蒙混过去）
        (MESSAGE_RENDERER, "h-64 bg-white dark:bg-gray-800 rounded-lg border border-gray-100 dark:border-gray-700"),
        (CANVAS_RENDERER, "canvas-markdown-chart my-4 w-full rounded-xl border border-gray-100 dark:border-gray-700 bg-white dark:bg-gray-800"),
    ],
)
def test_chart_card_follows_dark_theme(path: Path, fragment: str):
    """图表卡片容器必须同时声明亮/暗底色与描边。"""
    assert fragment in _read(path)


@pytest.mark.parametrize("path", [MESSAGE_RENDERER, CANVAS_RENDERER])
def test_chart_rerenders_on_theme_switch(path: Path):
    """切主题时 option 是整组替换的，默认 merge 会留下旧主题的颜色。"""
    source = _read(path)
    assert ':update-options="{ notMerge: true }"' in source
    assert "useDarkThemeFlag" in source
    assert "applyChartDarkTheme" in source


def test_dark_theme_flag_observes_html_class():
    """主题是改 `<html>` 的 class，必须用 MutationObserver 才能感知切换。"""
    source = _read(DARK_FLAG)
    assert "MutationObserver" in source
    assert 'classList.contains("dark")' in source
    assert 'attributeFilter: ["class"]' in source


def test_chart_renderer_exposes_dark_projection():
    """纯函数形式的深色投影（可被行为测试直接调用）。"""
    source = _read(CHART_RENDERER)
    assert "export function applyChartDarkTheme" in source
    # 不能无差别覆盖：AI 显式指定的品牌色要保留
    assert "normalizeDarkReadableTextColor" in source
