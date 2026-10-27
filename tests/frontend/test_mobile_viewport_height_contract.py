"""移动端定高布局必须跟随 visualViewport，而不是 100vh。

## 现象

chat 页在手机上「底部被地址栏/键盘压住」：输入框露出一半，或者干脆被键盘盖住。

## 原因

整页是「定高 + 内部滚动」：Dashboard 根 `h-screen`（100vh）、`/embed/*` 用
`position: fixed; inset: 0`。这两个都取**大视口**高度，而移动端有两种情况更矮：

1. 地址栏展开（iOS Safari / 微信内置浏览器）：真实可视区 < 100vh；
2. 软键盘弹出：100vh 与 100dvh 都**不会**跟着缩。

所以高度必须来自 `window.visualViewport.height`（唯一能同时反映两者的量），
落成 CSS 变量 `--app-height`，由定高布局消费。

## 约束

- 变量由 `composables/useAppViewportHeight.ts` 统一维护，只在 App.vue 注册一次；
- 写样式要 rAF 合并（键盘弹出会连发几十个 resize），卸载要解绑；
- `visualViewport` 缺失时退回 `innerHeight`，CSS 侧退回 `100dvh`，再退回 `100vh`；
- 消费方只限「定高 + 内部滚动」的布局，普通文档流页面用了会裁掉内容。
"""

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure

COMPOSABLE = "frontend/src/composables/useAppViewportHeight.ts"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _normalized(path: str) -> str:
    return re.sub(r"\s+", " ", _read(path))


def test_composable_reads_visual_viewport_with_fallback():
    source = _read(COMPOSABLE)

    assert "window.visualViewport?.height ?? window.innerHeight" in source, (
        "高度必须以 visualViewport 为准、innerHeight 兜底——只用 innerHeight 键盘弹出时不会缩"
    )
    assert 'const CSS_VAR = "--app-height"' in source
    assert "setProperty(CSS_VAR" in source


def test_composable_is_throttled_and_cleaned_up():
    source = _read(COMPOSABLE)

    # 键盘弹出会连发几十个 resize，必须合并到一帧
    assert "requestAnimationFrame" in source
    assert "cancelAnimationFrame" in source
    # 监听与解绑必须成对，否则路由来回切会累积监听器
    for event in ("resize", "scroll"):
        assert f'.addEventListener("{event}", schedule)' in source
        assert f'.removeEventListener("{event}", schedule)' in source
    for event in ("resize", "orientationchange"):
        assert f'window.addEventListener("{event}", schedule)' in source
        assert f'window.removeEventListener("{event}", schedule)' in source


def test_composable_resets_document_scroll():
    """iOS 键盘弹出会把文档整体上推，定高布局必须拉回原点，否则顶部变负、底部更靠下。"""
    source = _read(COMPOSABLE)

    assert "window.scrollX !== 0 || window.scrollY !== 0" in source
    assert "window.scrollTo(0, 0)" in source


def test_desktop_is_left_completely_untouched():
    """Web 桌面端必须完全不受影响。

    桌面没有软键盘、没有会伸缩的地址栏，`visualViewport.height` 就等于 `100vh`；
    而桌面用户会在长页面里正常滚动，「顺手把文档拉回原点」会变成「页面莫名跳回顶部」。
    所以：窄屏判定、高度未变早退、桌面清空变量（回落到 CSS 的 100dvh/100vh）。
    """
    source = _read(COMPOSABLE)

    assert 'const MOBILE_QUERY = "(max-width: 1023px)"' in source
    assert "matchMedia(MOBILE_QUERY).matches" in source
    # 桌面分支要移除变量，让消费方回落到 100dvh / 100vh（= 改动前的行为）
    assert "removeProperty(CSS_VAR)" in source
    # 高度没变就不做任何事（避免无意义重排 + 滚动补偿）
    assert "height === lastHeight" in source
    # scrollTo 必须在窄屏判定之后，不能被桌面路径执行到
    assert source.index("window.scrollTo(0, 0)") > source.index("matchMedia(MOBILE_QUERY).matches")


def test_registered_once_in_app_root():
    source = _normalized("frontend/src/App.vue")

    assert "useAppViewportHeight()" in source
    assert "import { useAppViewportHeight }" in source
    # 只在根组件注册一次，页面里重复注册会互相覆盖。（只查调用，注释里提到文件名是允许的）
    for path in ("frontend/src/views/Dashboard.vue", "frontend/src/layouts/EmbedLayout.vue"):
        assert "useAppViewportHeight()" not in _normalized(path), f"{path} 重复注册了视口高度"


def test_chat_page_height_follows_the_variable():
    """平台 chat 页：Dashboard 根必须走 --app-height，h-screen 只作为兜底。"""
    source = _normalized("frontend/src/views/Dashboard.vue")

    assert "var(--app-height, 100dvh)" in source
    root_at = source.index("var(--app-height, 100dvh)")
    # h-screen 兜底类必须还在（老浏览器不认 var/dvh 时靠它）
    nearby = source[max(0, root_at - 300) : root_at]
    assert "h-screen" in nearby


def test_embed_layout_height_follows_the_variable():
    source = _read("frontend/src/layouts/EmbedLayout.vue")
    code = re.sub(r"\s+", " ", source)

    assert "height: var(--app-height, 100dvh)" in code
    # 100vh 兜底写在前面（旧浏览器解析不了后一条时保留这条）
    assert code.index("height: 100vh;") < code.index("height: var(--app-height, 100dvh)")
    # bottom: 0 会把高度重新钉在大视口，必须去掉（注释里提到它是允许的，只查真实声明）
    assert not re.search(r"bottom:\s*0\s*;", code), "EmbedLayout 不能用 bottom:0 定高，那会绕开 --app-height"


def test_viewport_meta_lets_the_keyboard_resize_the_page():
    source = _read("frontend/index.html")

    assert "interactive-widget=resizes-content" in source, (
        "缺 interactive-widget=resizes-content 时，Android 上软键盘弹出不会压缩布局视口，"
        "底部输入框会被键盘盖住"
    )
