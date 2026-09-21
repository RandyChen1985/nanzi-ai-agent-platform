from pathlib import Path

import pytest

# 品牌资产契约需要用 Pillow 读取图片尺寸。Pillow 不是项目运行时依赖，
# 未安装时优雅跳过整个模块，避免以「收集错误」的形式让整套 tests/frontend 跑不干净。
# 需要启用该契约时安装：pip install pillow
Image = pytest.importorskip("PIL.Image", reason="缺少 Pillow，跳过品牌资产契约测试")


ROOT = Path(__file__).resolve().parents[2]


def test_index_declares_svg_png_and_apple_touch_icons_with_nanzi_title():
    index = (ROOT / "frontend/index.html").read_text(encoding="utf-8")

    assert '<link rel="icon" type="image/svg+xml" href="/favicon.svg" />' in index
    assert '<link rel="icon" type="image/png" sizes="512x512" href="/favicon.png" />' in index
    assert '<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png" />' in index
    assert "<title>NanZi·智能体平台</title>" in index


def test_public_favicon_assets_match_the_documented_nanzi_sources():
    brand_dir = ROOT / "docs/brand"

    assert (ROOT / "frontend/public/favicon.svg").read_bytes() == (
        brand_dir / "nanzi-n-icon-favicon.svg"
    ).read_bytes()
    assert (ROOT / "frontend/public/favicon.png").read_bytes() == (
        brand_dir / "nanzi-n-icon-favicon-512.png"
    ).read_bytes()


def test_runtime_default_favicon_uses_the_transparent_svg_resource():
    constants = (ROOT / "frontend/src/constants/branding.ts").read_text(encoding="utf-8")
    service = (ROOT / "app/services/branding_settings_service.py").read_text(encoding="utf-8")
    system_config = (ROOT / "frontend/src/views/SystemConfig.vue").read_text(encoding="utf-8")

    assert "export const DEFAULT_ICON_URL = '/favicon.svg'" in constants
    assert 'DEFAULT_ICON_URL = "/favicon.svg"' in service
    assert "icon_url: '/favicon.svg'" in system_config
    assert "data.icon_url || '/favicon.svg'" in system_config


def test_project_icon_pngs_keep_transparent_outer_corners():
    for relative_path in (
        "docs/brand/nanzi-n-icon.png",
        "docs/brand/nanzi-n-icon-favicon-512.png",
        "docs/brand/nanzi-n-icon-favicon.png",
        "frontend/public/logo.png",
        "frontend/public/favicon.png",
    ):
        image = Image.open(ROOT / relative_path).convert("RGBA")
        assert image.getpixel((0, 0))[3] == 0, relative_path


def test_apple_touch_icon_is_an_opaque_rounded_app_icon_export():
    public_asset = ROOT / "frontend/public/apple-touch-icon.png"
    brand_asset = ROOT / "docs/brand/nanzi-n-icon-apple-touch-180.png"

    assert public_asset.is_file()
    assert public_asset.read_bytes() == brand_asset.read_bytes()

    image = Image.open(public_asset).convert("RGBA")
    assert image.size == (180, 180)
    assert image.getpixel((0, 0))[3] == 255


def test_extended_brand_asset_pack_contains_the_documented_vi_resources():
    required_assets = (
        "docs/brand/nanzi-wordmark-on-light.svg",
        "docs/brand/nanzi-wordmark-on-dark.svg",
        "docs/brand/nanzi-n-icon-monochrome.svg",
        "docs/brand/nanzi-agent-avatar.svg",
        "docs/brand/nanzi-social-cover.svg",
        "docs/brand/nanzi-social-cover.png",
        "docs/brand/nanzi-state-empty.svg",
        "docs/brand/nanzi-state-loading.svg",
        "docs/brand/nanzi-state-error.svg",
        "docs/brand/nanzi-capability-icons.svg",
    )

    for relative_path in required_assets:
        assert (ROOT / relative_path).is_file(), relative_path

    assert Image.open(ROOT / "docs/brand/nanzi-social-cover.png").size == (1200, 630)
