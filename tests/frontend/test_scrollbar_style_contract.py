"""全站滚动条的唯一口径（`frontend/src/style.css`）。

历史包袱：几十个滚动容器都挂着 `custom-scrollbar`，但样式是各组件自己 scoped
定义的——宽度 4/5/6px 不等、滑块颜色一人一种，另有一大批只写了类名没写样式，
直接退回浏览器原生滚动条。原生滚动条有一整条浅灰轨道：浅色主题下融进白底看不
出来，切到深色侧栏（#0b0f19）就变成右侧一根「大块灰」，还常显占位把内容挤窄。

现在收敛成 `style.css` 里唯一一份，口径是「平时完全不显示，滚动时闪一下、
停手约 0.6 秒淡出」（`utils/scrollbarReveal.ts` 在 scroll 事件上给容器打
`data-scroll-active`；CSS 没有「正在滚动」这个状态，用 `:hover` 反而会在
侧栏这类常驻区域一直显示）。这里守住三件事：
1. 那份样式本身的几个关键约束别被改回去（轨道透明、默认滑块透明、深浅两套、
   标准属性只给 Firefox）；
2. 触发靠 `data-scroll-active` 而不是 `:hover`，且这个标记由 main.ts 唯一安装；
3. 不许再有组件自己定义 `.custom-scrollbar`——否则 scoped 特异性更高，
   全局口径会被悄悄覆盖，又回到一人一种的老路。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
STYLE = ROOT / "frontend/src/style.css"
SRC = ROOT / "frontend/src"

_PREFIXES = (
    "",
    ".dark ",
    "html.dark ",
    "html[data-theme='light'] ",
    "html[data-theme='dark'] ",
)


def _global_style() -> str:
    return STYLE.read_text(encoding="utf-8")


def _scrollbar_block() -> str:
    """只取 style.css 里滚动条那一段，避免断言命中别的规则。"""
    source = _global_style()
    start = source.index("统一滚动条")
    return source[start:]


def test_thumb_hidden_until_scrolling() -> None:
    """平时完全不显示：滑块默认透明，滚动时才亮起来。

    只把 `::-webkit-scrollbar-thumb` 设成透明是不够的——不写滚动那一支就永远
    看不见滚动条；反过来只写宽度也不起作用。两条都得有。
    """
    block = _scrollbar_block()

    assert ".custom-scrollbar::-webkit-scrollbar-thumb" in block
    assert "background-color: transparent" in block
    # 触发条件必须是「正在滚动」的标记，不能是 :hover（侧栏里等于常显）
    assert ".custom-scrollbar[data-scroll-active]::-webkit-scrollbar-thumb" in block
    assert ".custom-scrollbar:hover::-webkit-scrollbar-thumb" not in block
    # 拖动中的滑块要比滚动态更实一点，否则按住时看不出自己在拖
    assert ".custom-scrollbar[data-scroll-active]::-webkit-scrollbar-thumb:hover" in block


def test_track_is_transparent_so_no_grey_slab() -> None:
    """轨道必须透明——「大块灰」的本体就是原生轨道的浅色底。"""
    block = _scrollbar_block()
    assert ".custom-scrollbar::-webkit-scrollbar-track" in block
    track = block.split(".custom-scrollbar::-webkit-scrollbar-track", 1)[1].split("}", 1)[0]
    assert "transparent" in track


def test_dark_and_light_have_separate_thumb_colors() -> None:
    """深色底用半透明白，浅色底用半透明深灰，否则深底上的灰块同样扎眼。"""
    block = _scrollbar_block()

    # 深色：两条入口都覆盖——主站走 <html data-theme>，嵌入页走 <html class="dark">
    assert "html.dark .custom-scrollbar[data-scroll-active]::-webkit-scrollbar-thumb" in block
    assert "html[data-theme='dark'] .custom-scrollbar[data-scroll-active]::-webkit-scrollbar-thumb" in block
    dark_rules = [
        line
        for line in block.splitlines()
        if "rgba(255, 255, 255" in line
    ]
    assert dark_rules, "深色底缺少半透明白滑块配色"
    # 浅色：默认分支就是浅色，不能是深色（否则白底上看不见）
    assert "rgba(100, 116, 139" in block


def test_standard_properties_are_firefox_only() -> None:
    """`scrollbar-width` / `scrollbar-color` 只能出现在 Firefox 分支里。

    Chromium 121 起，元素上一旦设了这两个标准属性，就会整段忽略
    `::-webkit-scrollbar`，退回没有圆角的 `thin`——6px 细条直接失效。
    """

    source = _global_style()
    supports_at = source.index("@supports (-moz-appearance: none)")

    for prop in ("scrollbar-width:", "scrollbar-color:"):
        first = source.index(prop)
        assert first > supports_at, f"{prop} 出现在 Firefox 分支之外"


def test_no_component_redefines_custom_scrollbar() -> None:
    """任何组件都不得再自己定义 `.custom-scrollbar`。

    scoped 样式带 `[data-v-xxx]`，特异性高于全局那份；一旦哪个组件又抄一份，
    这个页面就会停在旧口径（常显、灰块），而且从全局文件里根本看不出来。
    """
    rule = re.compile(r"([^{}]*?)\{", re.S)
    comment = re.compile(r"/\*.*?\*/", re.S)
    offenders: list[str] = []

    for path in sorted(SRC.rglob("*.vue")):
        source = path.read_text(encoding="utf-8")
        for style_block in re.findall(r"<style[^>]*>(.*?)</style>", source, re.S):
            for selector in rule.findall(style_block):
                clean = " ".join(comment.sub("", selector).split())
                if clean.startswith("@"):
                    continue
                if any(
                    clean == prefix + ".custom-scrollbar"
                    or clean.startswith(prefix + ".custom-scrollbar:")
                    for prefix in _PREFIXES
                ):
                    offenders.append(f"{path.relative_to(ROOT)}: {clean}")

    assert not offenders, "以下组件又自己写了 .custom-scrollbar，请改用全局口径：\n" + "\n".join(offenders)


def test_reveal_is_installed_exactly_once() -> None:
    """显隐驱动由 main.ts 唯一安装。

    装在组件里会随路由/弹窗增删叠加出多份监听（同一容器多个定时器，收不起来）；
    而漏装则整站滚动条永不出现——两头都不好查，所以钉住「就在入口装一次」。
    """
    main_ts = (SRC / "main.ts").read_text(encoding="utf-8")
    assert "installScrollbarReveal" in main_ts
    assert main_ts.count("installScrollbarReveal()") == 1

    # 除 main.ts 外不许再有别处调用安装函数
    offenders = [
        str(path.relative_to(ROOT))
        for path in SRC.rglob("*")
        if path.is_file()
        and path.suffix in {".ts", ".vue"}
        and path.name != "main.ts"
        and path.name != "scrollbarReveal.ts"
        and "installScrollbarReveal(" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, "显隐驱动只允许在 main.ts 安装：\n" + "\n".join(offenders)


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行滚动条显隐行为测试")
def test_reveal_behaviour_via_node() -> None:
    """行为本身（打标记 / 600ms 撤掉 / 连续滚动续期 / 可卸载）用 node 跑。"""
    script = ROOT / "tests/frontend/scrollbar_reveal.mjs"
    assert script.exists(), "行为测试脚本缺失"

    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "all assertions passed" in proc.stdout
