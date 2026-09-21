from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
LOGIN = ROOT / "frontend/src/views/Login.vue"
MAGNIFIER = ROOT / "frontend/src/components/login/MagnifierTitle.vue"

pytestmark = pytest.mark.no_infrastructure


def login_source():
    return LOGIN.read_text(encoding="utf-8")


def magnifier_source():
    return MAGNIFIER.read_text(encoding="utf-8")


def test_login_title_is_rendered_through_the_magnifier_component():
    source = login_source()

    assert MAGNIFIER.is_file()
    assert "import MagnifierTitle from '../components/login/MagnifierTitle.vue'" in source
    assert "<MagnifierTitle" in source
    assert ':text="slide.title"' in source
    assert "{{ slide.title }}" not in source


def test_login_magnifier_uses_the_slide_palette():
    source = login_source()

    assert ":tone=\"slide.light ? 'light' : 'dark'\"" in source


def test_magnifier_lens_only_activates_on_the_title_text_box():
    source = magnifier_source()

    # 触发区紧贴字形框（inline-block），而不是整行或整个面板
    assert "relative inline-block" in source
    assert '@mouseenter="handleMouseEnter"' in source
    assert '@mousemove="handleMouseMove"' in source
    assert '@mouseleave="handleMouseLeave"' in source


def test_magnifier_keeps_the_original_title_still():
    source = magnifier_source()

    # 放大只发生在被裁剪的副本层里，原始标题不得随鼠标位移
    assert "translate(" not in source
    assert "transform: `scale(" in source


def test_magnifier_lens_clips_a_scaled_copy_around_the_pointer():
    source = magnifier_source()

    # clip-path 与 transform-origin 必须共用同一组镜片坐标，
    # 否则放大后的字形会漂移，退化成"跟随位移"而非"放大镜"
    assert "clipPath: `circle(var(--lens-r) at var(--lens-x) var(--lens-y))`" in source
    assert "transformOrigin: 'var(--lens-x) var(--lens-y)'" in source


def test_magnifier_pointer_tracking_writes_css_variables_instead_of_rerendering():
    source = magnifier_source()

    assert "style.setProperty('--lens-x'" in source
    assert "style.setProperty('--lens-y'" in source
    assert "const lensX = ref(" not in source
    assert "const lensY = ref(" not in source


def test_magnifier_duplicate_layer_is_invisible_to_assistive_tech():
    source = magnifier_source()

    # 镜片副本与光环都不得被屏幕阅读器重复朗读，也不得抢鼠标事件
    assert source.count('aria-hidden="true"') >= 2
    assert "pointer-events-none" in source


def test_magnifier_lens_hides_the_unmagnified_title_underneath():
    source = magnifier_source()

    # 镜片内需要一层玻璃底把底下的原始文字盖住，避免重影
    assert "radial-gradient(circle at 50% 50%" in source


def test_magnifier_lens_draws_a_ring_with_glass_highlight():
    source = magnifier_source()

    assert "rounded-full border" in source
    assert "blur-" in source


def test_magnifier_disables_itself_for_reduced_motion():
    source = magnifier_source()

    assert "prefers-reduced-motion" in source
    assert 'v-if="!reducedMotion"' in source
    assert "motionMediaQuery" in source
    assert "removeEventListener" in source


def test_magnifier_radius_adapts_to_the_large_desktop_title_size():
    source = magnifier_source()

    assert "--lens-r" in source
    assert "media (min-width: 1280px)" in source


def test_login_passes_active_state_to_magnifier():
    source = login_source()

    assert ':active="currentSlide === index"' in source


def test_magnifier_supports_auto_scan_on_activation():
    source = magnifier_source()

    assert "active?: boolean" in source or "active:" in source
    assert "startAutoScan" in source or "triggerScan" in source
    assert "requestAnimationFrame" in source
    assert "cancelAnimationFrame" in source


def test_magnifier_cancels_auto_scan_on_mouse_enter():
    source = magnifier_source()

    assert "cancelAutoScan" in source or "stopScan" in source
    # 鼠标进入时必须终止扫描并把控制权切给指针
    assert "@mouseenter" in source

