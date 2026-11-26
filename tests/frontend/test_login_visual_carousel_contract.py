"""登录页 8 套视觉主题、品牌配置兼容与个人中心风格切换契约。"""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def login_source() -> str:
    return (ROOT / "frontend/src/views/Login.vue").read_text(encoding="utf-8")


def themes_source() -> str:
    return (ROOT / "frontend/src/constants/loginThemes.ts").read_text(encoding="utf-8")


def composable_source() -> str:
    return (ROOT / "frontend/src/composables/useLoginTheme.ts").read_text(encoding="utf-8")


def personal_source() -> str:
    return (ROOT / "frontend/src/views/PersonalCenter.vue").read_text(encoding="utf-8")


def selector_source() -> str:
    return (ROOT / "frontend/src/components/personal/LoginThemeSelector.vue").read_text(encoding="utf-8")


def test_login_supports_all_8_themes_with_f1_default():
    themes = themes_source()
    assert "export const DEFAULT_LOGIN_THEME: LoginThemeId = 'f1'" in themes
    for theme_id in ("'f1'", "'f2'", "'f3'", "'f4'", "'g'", "'h'", "'i'", "'j'"):
        assert theme_id in themes


def test_login_keeps_branding_configuration_and_subtitle_defaults():
    source = login_source()
    for text in (
        "NanZi · 智能体平台",
        "Your Intelligent Agent Platform",
        "branding.value.enabled",
        "branding.value.product_name",
        "branding.value.login_subtitle",
        ':src="iconUrl"',
    ):
        assert text in source
    assert "nanzi-wordmark-on-light.svg" not in source
    assert "nanzi-wordmark-on-dark.svg" not in source


def test_login_subtitle_defaults_are_consistent_across_branding_configuration():
    constants = (ROOT / "frontend/src/constants/branding.ts").read_text(encoding="utf-8")
    service = (ROOT / "app/services/branding_settings_service.py").read_text(encoding="utf-8")
    system_config = (ROOT / "frontend/src/views/SystemConfig.vue").read_text(encoding="utf-8")

    assert "DEFAULT_LOGIN_SUBTITLE = 'Your Intelligent Agent Platform'" in constants
    assert 'DEFAULT_LOGIN_SUBTITLE = "Your Intelligent Agent Platform"' in service
    assert "login_subtitle: 'Your Intelligent Agent Platform'" in system_config
    assert "placeholder=\"Your Intelligent Agent Platform\"" in system_config


def test_login_panel_has_a_compact_small_desktop_layout():
    source = login_source()

    assert "lg:w-[420px] xl:w-[460px]" in source
    assert "px-6 lg:px-7 xl:px-10" in source


def test_login_theme_persists_in_redis_and_local_cache_mirror():
    composable = composable_source()
    ui_mode = (ROOT / "frontend/src/composables/useUiMode.ts").read_text(encoding="utf-8")
    cleanup = (ROOT / "frontend/src/utils/sessionCleanup.ts").read_text(encoding="utf-8")

    assert "/api/portal/portal-prefs/login-theme" in composable
    assert "/api/portal/portal-prefs" in composable
    assert "LOGIN_THEME_STORAGE_KEY" in composable
    assert "syncLoginThemeFromPrefs" in ui_mode
    # 登出清理不应清掉设备级登录页皮肤镜像，否则退出后立即跳回默认
    assert "nanzi_login_theme" not in cleanup


def test_personal_center_exposes_login_theme_tab_and_selector():
    personal = personal_source()
    selector = selector_source()

    assert 'data-user-sub-tab="login-theme"' in personal
    assert "登录页风格" in personal
    assert "<LoginThemeSelector" in personal
    assert 'data-user-panel="login-theme"' in selector
    assert "/login?preview=" in selector
