import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_theme_composable_persists_and_applies_document_theme():
    source = _read("frontend/src/composables/useAppTheme.ts")

    assert '"nanzi_app_theme"' in source
    assert '"light"' in source
    assert '"dark"' in source
    assert 'ref<AppTheme>("dark")' in source
    assert 'storedTheme === "light" ? "light" : "dark"' in source
    assert "document.documentElement.dataset.theme" in source
    assert "document.documentElement.classList.toggle" not in source
    assert "localStorage.setItem" in source
    assert "setTheme" in source


def test_dashboard_exposes_light_dark_sidebar_switcher():
    source = _read("frontend/src/views/Dashboard.vue")

    assert "useAppTheme" in source
    assert "theme" in source
    assert "亮色" in source
    assert "暗色" in source
    assert 'aria-label="切换界面主题"' in source
    assert "theme === 'light'" in source
    assert "toggleTheme" in source
    assert "border-gray-700 bg-gray-900 text-blue-300" not in source
    assert "inline-flex h-9 w-9 items-center justify-center rounded-lg border transition-colors" not in source
    # 只放宽 padding：侧边栏紧凑化把主题按钮从 p-2 收到 p-1.5，
    # 其余（相对定位 + 圆角 + 中性色 + hover 底色）仍是契约要求。
    assert re.search(
        r"\brelative p-(?:1\.5|2) rounded-lg text-gray-500 hover:bg-gray-100\b", source
    ), "主题切换按钮必须保持 relative + rounded-lg + 中性色 + hover 底色的图标按钮样式"
    assert '<svg v-else class="h-4 w-4" fill="none" stroke="currentColor"' in source
    assert "<!-- Theme Switcher -->" not in source
    assert source.index("<!-- Top Header -->") < source.index('aria-label="切换界面主题"')
    assert "flex-shrink-0 dark:bg-gray-900 dark:border-gray-800" not in source
    assert "bg-gray-100 dark:bg-gray-950 custom-scrollbar" not in source
    assert "bg-sidebar" in source
