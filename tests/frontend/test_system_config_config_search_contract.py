"""系统配置页「参数搜索」下拉的契约。

真实缺陷（vue-tsc 抓到的 TS2339）：模板里写的是

    @blur="setTimeout(() => (configSearchOpen = false), 150)"

Vue 模板表达式的作用域是组件实例代理 `_ctx`，而模板白名单里没有 `setTimeout`
（只有 Math / JSON / Date / RegExp / console 这类），于是这段被编译成
`_ctx.setTimeout(...)` —— 失焦瞬间抛 `TypeError: _ctx.setTimeout is not a function`，
`configSearchOpen` 反而保持 true：搜索下拉永远关不掉，控制台还每次报一条错。

修法是把定时器收回脚本层（`closeConfigSearchLater`），模板只调方法。这里钉住
三件事：模板不再直接摸浏览器 API、脚本里确实用 `window.setTimeout`、延迟值仍是
150ms（小于这个值时「点选项」会被 blur 抢先关掉而点空）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SYSTEM_CONFIG = ROOT / "frontend/src/views/SystemConfig.vue"

pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return SYSTEM_CONFIG.read_text(encoding="utf-8")


def _template_only() -> str:
    """只取模板段：脚本里解释这个坑的注释本身也含 `@blur="setTimeout(` 字样。"""
    return _source().split("<template>", 1)[1]


def test_template_never_calls_browser_timers_directly():
    """模板表达式里不许出现 setTimeout / setInterval 等实例上不存在的全局。"""
    source = _template_only()

    for call in ("setTimeout(", "setInterval(", "requestAnimationFrame("):
        assert f'@blur="{call}' not in source, (
            f"模板里直接调用了 {call}：它不在 Vue 模板白名单，会被编译成 _ctx.{call}"
        )
        assert f'@click="{call}' not in source


def test_blur_handler_is_a_script_method():
    """失焦收起走脚本方法，方法里用 window.setTimeout 并保持 150ms 延迟。"""
    source = _source()

    assert '@blur="closeConfigSearchLater"' in source, "失焦应绑定脚本方法"

    start = source.index("const closeConfigSearchLater")
    body = source[start : start + 400]
    assert "window.setTimeout(" in body, "定时器要在脚本层用 window. 前缀调用"
    assert "configSearchOpen.value = false" in body, "回调要真正收起下拉"
    # 值太小会在「点选项」之前就把下拉关掉；太大则失焦后残留太久
    assert "150" in body


def test_search_results_are_gated_by_the_open_flag():
    """下拉是否渲染仍由 configSearchOpen 决定（别再退回只看 query）。"""
    source = _source()
    assert 'v-if="configSearchOpen && configSearchQuery"' in source
