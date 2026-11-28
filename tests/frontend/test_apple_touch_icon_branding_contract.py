"""品牌自定义 icon 同步到 Apple touch icon 的前端契约。

需求来源（用户）：系统配置里换了品牌自定义 icon，标签页图标变了，但 iPhone「添加到主屏幕」
拿到的仍是平台默认的蓝色 N。原因是 `index.html` 里 `apple-touch-icon` 硬编码成静态文件，
而品牌配置只改 `link[rel="icon"]`。

锁四件事：

1. `index.html` 仍指向固定路径 `/apple-touch-icon.png`（iOS 只认这一条），并补齐主屏 Web App 的 meta；
2. 上传品牌图时前端额外汇出一张 180×180 满版不透明 PNG 一起提交（`apple_touch_icon` 字段）；
3. 后端 `/apple-touch-icon.png` 路由排在 SPA fallback 之前，否则会被静态产物抢先返回旧图；
4. 桌面名称跟随品牌配置运行时覆盖 `apple-mobile-web-app-title`。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
INDEX_HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
SYSTEM_CONFIG = (ROOT / "frontend/src/views/SystemConfig.vue").read_text(encoding="utf-8")
USE_BRANDING = (ROOT / "frontend/src/composables/useBranding.ts").read_text(encoding="utf-8")
MAIN_PY = (ROOT / "app/main.py").read_text(encoding="utf-8")
SYSTEM_ENDPOINTS = (ROOT / "app/api/portal/endpoints/system.py").read_text(encoding="utf-8")


def test_index_keeps_fixed_apple_touch_icon_path_and_declares_home_screen_meta():
    """路径必须保持不变：iOS 抓的就是这一条，改成动态拼接等于让缓存彻底失效。"""
    assert '<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png" />' in INDEX_HTML
    assert '<meta name="apple-mobile-web-app-capable" content="yes" />' in INDEX_HTML
    assert '<meta name="mobile-web-app-capable" content="yes" />' in INDEX_HTML
    assert '<meta name="apple-mobile-web-app-status-bar-style" content="default" />' in INDEX_HTML
    assert '<meta name="apple-mobile-web-app-title" content="NanZi AI Agent" />' in INDEX_HTML
    assert '<meta name="theme-color" content="#4c4ce6" />' in INDEX_HTML


def test_upload_composes_an_opaque_180_square_for_the_home_screen_icon():
    """透明底直接当桌面图标会被 iOS 垫黑底：必须合成满版不透明方图后再上传。"""
    assert "const buildAppleTouchIconBlob = async (source: Blob): Promise<Blob | null> => {" in SYSTEM_CONFIG
    assert "const APPLE_TOUCH_ICON_SIZE = 180" in SYSTEM_CONFIG
    assert "const APPLE_TOUCH_ICON_CONTENT_RATIO = 0.72" in SYSTEM_CONFIG
    assert "const APPLE_TOUCH_ICON_GRADIENT = ['#4c4ce6', '#4464ee'] as const" in SYSTEM_CONFIG
    # 渐变铺底必须在画图之前
    assert SYSTEM_CONFIG.index("ctx.fillRect(0, 0, APPLE_TOUCH_ICON_SIZE, APPLE_TOUCH_ICON_SIZE)") < (
        SYSTEM_CONFIG.index("ctx.drawImage(")
    )
    assert "canvas.toBlob(resolve, 'image/png')" in SYSTEM_CONFIG
    assert "URL.revokeObjectURL(objectUrl)" in SYSTEM_CONFIG
    # 与 favicon 分两个字段提交：后端一个存原图、一个存桌面图
    assert "form.append('file', uploadFile)" in SYSTEM_CONFIG
    assert "form.append('apple_touch_icon'" in SYSTEM_CONFIG
    # 换了品牌图之后，桌面图标是缓存的，文案必须说清要重新添加
    assert "重新添加到主屏幕" in SYSTEM_CONFIG


def test_uploaded_full_bleed_logo_is_covered_not_letterboxed():
    """自带底板/圆角的整图按 cover 铺满，否则「渐变底 + 内缩的带底图标」会叠出双层圆角。"""
    assert "const APPLE_TOUCH_ICON_FULL_BLEED_RATIO = 0.55" in SYSTEM_CONFIG
    assert "const measureOpaqueCoverage = (image: HTMLImageElement): number => {" in SYSTEM_CONFIG
    assert "const fullBleed = measureOpaqueCoverage(image) >= APPLE_TOUCH_ICON_FULL_BLEED_RATIO" in SYSTEM_CONFIG
    # 满版走 max（cover），孤立图标走 min（contain）
    assert "Math.max(APPLE_TOUCH_ICON_SIZE / naturalWidth, APPLE_TOUCH_ICON_SIZE / naturalHeight)" in SYSTEM_CONFIG
    assert "Math.min(box / naturalWidth, box / naturalHeight)" in SYSTEM_CONFIG


def test_apple_touch_icon_route_precedes_spa_fallback():
    """路由必须排在 `/{full_path:path}` 之前：SPA fallback 会先返回 dist 里的旧静态图。"""
    assert '@app.get("/apple-touch-icon.png", include_in_schema=False)' in MAIN_PY
    assert "async def serve_apple_touch_icon():" in MAIN_PY
    assert MAIN_PY.index('@app.get("/apple-touch-icon.png"') < MAIN_PY.index('@app.get("/{full_path:path}")')
    assert "BrandingSettingsService.resolve_apple_touch_icon_path(" in MAIN_PY
    assert 'headers={"Cache-Control": "no-cache, must-revalidate"}' in MAIN_PY


def test_upload_endpoint_persists_and_clears_the_home_screen_variant():
    """上传时落盘桌面图；本次没带合成图时必须删掉上一张，否则换了品牌还挂着上一家的桌面图标。"""
    assert "apple_touch_icon: Optional[UploadFile] = File(None)" in SYSTEM_ENDPOINTS
    assert "APPLE_TOUCH_ICON_FILENAME" in SYSTEM_ENDPOINTS
    assert "if apple_touch_data is not None:" in SYSTEM_ENDPOINTS
    assert "os.remove(apple_touch_path)" in SYSTEM_ENDPOINTS
    # 还原默认图标时一并清理，避免下一次启用新品牌时抓到一个旧文件
    assert "filenames.append(APPLE_TOUCH_ICON_FILENAME)" in SYSTEM_ENDPOINTS


def test_home_screen_title_follows_branding_config():
    """桌面名称在添加那一刻读取，静态 meta 只是默认值，所以运行时也要覆盖一次。"""
    assert "function applyHomeScreenTitle() {" in USE_BRANDING
    assert 'meta.name = \'apple-mobile-web-app-title\'' in USE_BRANDING
    # 快照首帧与接口落地各调一次（一次防闪、一次纠错）
    assert USE_BRANDING.count("applyHomeScreenTitle()") == 3
