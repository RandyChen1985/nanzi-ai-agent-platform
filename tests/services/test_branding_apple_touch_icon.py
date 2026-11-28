"""Apple touch icon 分流判定契约。

需求来源（用户）：改了品牌自定义 icon 之后，iPhone「添加到主屏幕」拿到的仍是平台默认的蓝色 N。
根因是两条链路没接上——`index.html` 里的 `apple-touch-icon` 是硬编码静态文件，
品牌自定义只改 `link[rel="icon"]`（浏览器标签页），iOS 在「添加到主屏幕」时只认前者。

这里锁住后端的分流判定：只有「品牌已启用 + icon_url 指向上传文件 + 合成图存在」
三条同时成立才返回自定义图，其余情况一律回默认图。
`icon_url` 被手填成外链时**刻意不回退到外链**：那张图无法保证满版不透明，
iOS 会给透明四角垫黑底，桌面图标就成了黑边方块。
"""

from pathlib import Path

import pytest

from app.services.branding_settings_service import BrandingSettingsService


pytestmark = pytest.mark.no_infrastructure


def _make_files(tmp_path: Path, *, custom: bool, default: bool) -> tuple[str, str]:
    custom_path = tmp_path / "icon-apple-touch.png"
    default_path = tmp_path / "apple-touch-icon.png"
    if custom:
        custom_path.write_bytes(b"custom")
    if default:
        default_path.write_bytes(b"default")
    return str(custom_path), str(default_path)


def test_uploaded_branding_icon_wins_when_branding_enabled(tmp_path):
    custom_path, default_path = _make_files(tmp_path, custom=True, default=True)

    resolved = BrandingSettingsService.resolve_apple_touch_icon_path(
        enabled=True,
        icon_url="/branding/icon.png?t=1700000000",
        custom_icon_path=custom_path,
        default_icon_path=default_path,
    )

    assert resolved == custom_path


def test_disabled_branding_falls_back_to_default_even_if_file_exists(tmp_path):
    custom_path, default_path = _make_files(tmp_path, custom=True, default=True)

    resolved = BrandingSettingsService.resolve_apple_touch_icon_path(
        enabled=False,
        icon_url="/branding/icon.png?t=1700000000",
        custom_icon_path=custom_path,
        default_icon_path=default_path,
    )

    assert resolved == default_path


def test_external_or_default_icon_url_falls_back_to_default(tmp_path):
    custom_path, default_path = _make_files(tmp_path, custom=True, default=True)

    for icon_url in ("/favicon.svg", "https://cdn.example.com/logo.png", ""):
        resolved = BrandingSettingsService.resolve_apple_touch_icon_path(
            enabled=True,
            icon_url=icon_url,
            custom_icon_path=custom_path,
            default_icon_path=default_path,
        )
        assert resolved == default_path, icon_url


def test_missing_composed_icon_falls_back_to_default(tmp_path):
    custom_path, default_path = _make_files(tmp_path, custom=False, default=True)

    resolved = BrandingSettingsService.resolve_apple_touch_icon_path(
        enabled=True,
        icon_url="/branding/icon.png?t=1700000000",
        custom_icon_path=custom_path,
        default_icon_path=default_path,
    )

    assert resolved == default_path


def test_returns_none_when_neither_asset_exists(tmp_path):
    custom_path, default_path = _make_files(tmp_path, custom=False, default=False)

    assert (
        BrandingSettingsService.resolve_apple_touch_icon_path(
            enabled=True,
            icon_url="/branding/icon.png",
            custom_icon_path=custom_path,
            default_icon_path=default_path,
        )
        is None
    )
