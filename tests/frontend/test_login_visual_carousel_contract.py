from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def login_source():
    return (ROOT / "frontend/src/views/Login.vue").read_text(encoding="utf-8")


def test_login_visual_carousel_contracts():
    source = login_source()
    for text in (
        "key: 'b'",
        "key: 'a'",
        "key: 'c'",
        "NanZi · 智能体平台",
        "Your Intelligent Agent Platform",
    ):
        assert text in source
    for text in (
        "branding.value.enabled",
        "branding.value.product_name",
        "branding.value.login_subtitle",
        "sessionStorage",
        "Math.random()",
        "7000",
        "clearInterval",
        "prefers-reduced-motion",
        "pauseSlideTimer",
        "resumeSlideTimer",
        "aria-current",
        ':src="iconUrl"',
    ):
        assert text in source
    assert "nanzi-wordmark-on-light.svg" not in source
    assert "nanzi-wordmark-on-dark.svg" not in source


def test_login_visual_carousel_starts_with_third_slide_and_ends_with_first():
    source = login_source()

    assert source.index("key: 'c'") < source.index("key: 'b'")
    assert source.index("key: 'b'") < source.index("key: 'a'")


def test_login_subtitle_defaults_are_consistent_across_branding_configuration():
    constants = (ROOT / "frontend/src/constants/branding.ts").read_text(encoding="utf-8")
    service = (ROOT / "app/services/branding_settings_service.py").read_text(encoding="utf-8")
    system_config = (ROOT / "frontend/src/views/SystemConfig.vue").read_text(encoding="utf-8")

    assert "DEFAULT_LOGIN_SUBTITLE = 'Your Intelligent Agent Platform'" in constants
    assert 'DEFAULT_LOGIN_SUBTITLE = "Your Intelligent Agent Platform"' in service
    assert "login_subtitle: 'Your Intelligent Agent Platform'" in system_config
    assert "placeholder=\"Your Intelligent Agent Platform\"" in system_config


def test_login_visual_brand_lockup_is_anchored_to_the_panel_top_left():
    source = login_source()

    assert "absolute top-10 left-10" in source
    assert "xl:top-12 xl:left-12" in source
    assert "flex items-center justify-center gap-3 mb-10" not in source


def test_login_panel_has_a_compact_small_desktop_layout():
    source = login_source()

    assert "lg:w-[420px] xl:w-[460px]" in source
    assert "px-6 lg:px-7 xl:px-10" in source
    assert "mb-6 xl:mb-10" in source
    assert "mb-5 xl:mb-8" in source
    assert "space-y-4 xl:space-y-6" in source
    assert "mt-8 pt-5 xl:mt-12 xl:pt-8" in source


def test_login_large_desktop_keeps_the_primary_title_on_one_line():
    source = login_source()

    assert "lg:w-[420px] xl:w-[460px]" in source
    assert "max-w-4xl xl:max-w-[1100px]" in source


def test_login_visual_content_does_not_move_with_mouse():
    source = login_source()

    assert "window.addEventListener('mousemove', handleMouseMove)" not in source
    assert "const mouseX = ref(0)" not in source
    assert "const mouseY = ref(0)" not in source
    assert ':style="{ transform: `translate(${mouseX}px, ${mouseY}px)` }"' not in source


def test_login_execution_slide_uses_a_dark_tech_palette():
    source = login_source()

    assert "gradient: 'from-blue-500/30 via-blue-950/60 to-[#0b1830]'" in source
    assert "accent: 'text-blue-300'" in source
    assert "glow: 'bg-cyan-400/10'" in source
    assert "bg: 'bg-[#0b1830]'" in source
    assert "light: false" in source
    assert "bg: 'bg-[#eff6ff]'" not in source


def test_login_mouse_follow_effect_is_a_background_light_plus_a_non_moving_lens():
    source = login_source()

    assert "const mouseLightX = ref(50)" in source
    assert "const mouseLightY = ref(50)" in source
    assert "const handleVisualMouseMove = (event: MouseEvent)" in source
    assert '@mousemove="handleVisualMouseMove"' in source
    assert '@mouseleave="handleVisualMouseLeave"' in source
    assert "transition-[left,top] duration-300 ease-out motion-reduce:transition-none" in source
    assert "pointer-events-none" in source
    assert ':style="{ left: `${mouseLightX}%`, top: `${mouseLightY}%`' in source
    assert "radial-gradient(circle, rgba(96, 165, 250, 0.24)" in source

    # 鼠标跟随效果只有两个：背景光晕，以及标题上"原始文字静止"的放大镜镜片。
    # 放大镜的契约由 test_login_visual_magnifier_contract.py 锁定。
    assert "<MagnifierTitle" in source
    assert "translate(${mouseX}px, ${mouseY}px)" not in source
