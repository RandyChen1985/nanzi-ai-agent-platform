"""登录页 Canvas 主题背景引擎与预览模式契约。"""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
LOGIN = ROOT / "frontend/src/views/Login.vue"
BACKDROP = ROOT / "frontend/src/components/login/LoginThemeBackdrop.vue"
RENDERER = ROOT / "frontend/src/utils/loginThemeRenderers.ts"

pytestmark = pytest.mark.no_infrastructure


def test_login_renders_theme_backdrop_and_preview_mode():
    source = LOGIN.read_text(encoding="utf-8")

    assert BACKDROP.is_file()
    assert RENDERER.is_file()
    assert "import LoginThemeBackdrop from '../components/login/LoginThemeBackdrop.vue'" in source
    assert "<LoginThemeBackdrop" in source
    assert "route.query.preview" in source
    assert "applyPreviewTheme" in source


def test_backdrop_respects_reduced_motion_and_cleans_up_animation():
    source = BACKDROP.read_text(encoding="utf-8")

    assert "prefers-reduced-motion" in source
    assert "requestAnimationFrame" in source
    assert "cancelAnimationFrame" in source
    assert "removeEventListener" in source
    assert "pointer-events-none" in source
    assert 'aria-hidden="true"' in source


def test_renderer_implements_all_8_themes():
    source = RENDERER.read_text(encoding="utf-8")

    for branch in (
        "theme === 'f1'",
        "theme === 'f2'",
        "theme === 'f3'",
        "theme === 'f4'",
        "theme === 'g'",
        "theme === 'h'",
        "theme === 'i'",
    ):
        assert branch in source
    assert "F3_SATELLITES" in source
