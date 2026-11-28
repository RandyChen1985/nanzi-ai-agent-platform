"""品牌 logo 首帧不闪默认图标（First Paint Branding）。

## 现象

刷新页面时，左上角 logo 先出现一次默认图标（`/favicon.svg`），几十毫秒后跳成管理员
配置的自定义 logo，肉眼就是「闪一下」。

## 根因

`useBranding()` 里 `branding` 的初值是 `DEFAULT_BRANDING`，真正的品牌配置要等
`GET /api/portal/auth/branding` 返回才知道。所以首帧一定先按默认值渲染。

## 修复约定

1. 首帧读本机快照（`localStorage`）里的上次品牌，刷新时直接画正确 logo；
2. 接口回来后无条件覆盖并回写快照——管理员改 logo / 关品牌功能，下一次渲染即纠正；
3. 没有快照时（首次访问、清缓存）组件不画默认图标，改用同尺寸占位，等
   `loadBranding()` 落地再画：`brandingReady` 就是这道判据；
4. 接口失败保留快照，不把已经画对的品牌改回默认值；
5. 有快照时立刻设好 tab favicon，避免标签页图标同样闪一次。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
USE_BRANDING = ROOT / "frontend/src/composables/useBranding.ts"
LOGIN = ROOT / "frontend/src/views/Login.vue"


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


def test_branding_starts_from_local_snapshot() -> None:
    source = _read(USE_BRANDING)

    assert "const BRANDING_CACHE_KEY = 'nanzi_public_branding'" in source
    assert "function readCachedBranding(" in source
    assert "const cachedBranding = readCachedBranding()" in source
    # 初值来自快照，缺快照才退回默认品牌
    assert "const branding = ref<PublicBranding>({ ...(cachedBranding ?? DEFAULT_BRANDING) })" in source
    # 有快照 = 首帧即可画；无快照要等一次接口
    assert "const brandingReady = ref<boolean>(cachedBranding !== null)" in source


def test_loaded_branding_is_written_back_to_snapshot() -> None:
    source = _read(USE_BRANDING)

    assert "function writeCachedBranding(" in source
    assert "branding.value = { ...DEFAULT_BRANDING, ...data }" in source
    assert "writeCachedBranding(branding.value)" in source
    # 请求失败时不能把已画对的品牌改回默认值，也不能丢掉快照
    fetch = source[source.index("export async function loadBranding(") :]
    catch_body = fetch[fetch.index("} catch {") : fetch.index("} finally {")]
    assert "branding.value = " not in catch_body, "失败分支不得覆盖已就绪的品牌"
    assert "localStorage" not in catch_body


def test_ready_flag_gates_first_paint_and_favicon_uses_snapshot() -> None:
    source = _read(USE_BRANDING)

    assert "brandingReady.value = true" in source
    # 有快照时模块加载阶段就把 favicon 设好（标签页图标不闪）；
    # 同一次快照里一并覆盖主屏 Web App 的桌面名称（apple-mobile-web-app-title）
    assert "if (cachedBranding) {" in source
    snapshot_block = source[source.index("if (cachedBranding) {"):]
    snapshot_block = snapshot_block[: snapshot_block.index("}") + 1]
    assert "applyFavicon(cachedBranding.icon_url)" in snapshot_block
    assert "applyHomeScreenTitle()" in snapshot_block
    # 对外暴露，供页面用 v-if 判据
    assert "brandingReady," in source


def test_login_page_does_not_paint_default_logo_first() -> None:
    """两处品牌位（桌面左侧 lockup + 移动端头部）都改用 brandingReady 判据。"""
    view = _read(LOGIN)

    assert "const { branding, loadBranding, brandingReady } = useBranding()" in view
    # 不再有条件无条件渲染 logo —— 两处都必须带 v-if
    assert view.count(':src="iconUrl"') == 2
    assert view.count('v-if="brandingReady"') >= 3
    # 未就绪时用同尺寸占位，避免布局跳动
    assert "animate-pulse" in view
    assert "品牌配置未就绪" in view
