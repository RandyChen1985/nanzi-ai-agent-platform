"""EmbedChat 主题状态的契约：`config.theme` 与 `<html>.dark` 必须同源。

背景一：宿主页面（postMessage 的 `SET_THEME`）与服务端配置（`data.theme`）都会
直接调用 `applyTheme`，绕过 `setTheme`。若 `applyTheme` 不同步 `config.theme`，
设置面板的选中态就会与页面真实渲染的主题脱钩——用户看到「深色」按钮高亮、
页面却按浅色渲染，或反之。

背景二：`:global()` 包裹的 `.dark` 前缀会让**整个选择器**脱出 scoped 作用域。
Vue 编译 `:global(.dark) .gw-grid` 的产物是一条**全局** `.dark{opacity:.07}`
（连 `.gw-grid` 都丢了）。它会命中页面上任何带 `.dark` 的元素——包括 EmbedChat
根容器与 `<html>`——把整个挂件压到 7% 不透明度，用户看到的就是「深色下一片白雾、
内容隐约可见」。本文件同时锁死这类全局污染不再出现。
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = ROOT / "frontend/src"
EMBED_CHAT = FRONTEND_SRC / "views/EmbedChat.vue"
STYLE_CSS = FRONTEND_SRC / "style.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_css_comments(text: str) -> str:
    """剥掉 CSS 注释：注释里可以继续讨论这个反模式，但代码里不允许出现。"""
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _apply_theme_body() -> str:
    source = _read(EMBED_CHAT)
    start = source.index("const applyTheme = (")
    return source[start : source.index("\n};", start)]


def test_apply_theme_syncs_config_state():
    """主题的「状态」和「class」必须在同一个入口一起改。"""
    body = _apply_theme_body()
    assert "config.theme = theme" in body


def test_apply_theme_still_drives_the_dark_class():
    """反向锁：同步状态不能把原本的 class 切换弄丢。"""
    body = _apply_theme_body()
    assert 'root.classList.add("dark")' in body
    assert 'root.classList.remove("dark")' in body


def test_no_html_dark_fallback_background():
    """`html.dark` 兜底背景已被移除，且不应再加回来。

    它曾以「EmbedChat 根容器用 h-full，而 html/body/#app 没设高度，高度链断了会
    露出 body 浅色底」为由引入。该前提不成立：高度来自 EmbedLayout 的
    `position: fixed; inset: 0`——fixed 脱离文档流、直接由视口定高。实测
    html/body/#app 高度全为 0 时根容器仍铺满视口，`<html>` 背景永远被遮住，
    所以那条规则不产生任何视觉效果（挂载前白屏也救不了：当时还没有 `.dark`）。
    深色底由根容器自己的 `dark:bg-gray-900` 负责，见下一条契约。
    """
    source = _read(STYLE_CSS)
    code = _strip_css_comments(source)
    assert "html.dark {" not in code
    assert "#111827" not in code


def _root_container_block() -> str:
    """取 EmbedChat 根容器的 class / :class 声明片段。"""
    source = _read(EMBED_CHAT)
    start = source.index('class="flex h-full w-full max-w-full')
    return source[start : source.index(">", start)]


def test_root_container_owns_its_dark_background():
    """根容器的深色背景必须由它自己声明，不能只挂在 `<html>.dark` 上。

    Tailwind 的 dark 变体会编译成 `.dark\\:bg-gray-900:is(.dark *)`——**后代**
    选择器。只要 `<html>` 上没有 `.dark`（历史上存在只改状态、不改 class 的调用
    路径），根容器**自身**的 `dark:bg-gray-900` 就不命中，而它的子元素照常变浅，
    页面于是变成「白底 + 浅色文字」，也就是用户看到的「一片白雾、内容隐约可见」。
    把背景收进根容器自己的条件绑定，就与 `<html>.dark` 解耦。
    """
    block = _root_container_block()
    assert "bg-gray-900" in block
    assert "text-gray-100" in block
    # 反向锁：不得再依赖祖先选择器形态的 dark: 背景/文字
    assert "dark:bg-gray-900" not in block
    assert "dark:text-gray-100" not in block


def test_html_dark_class_follows_config_theme():
    """状态是唯一事实源：config.theme 变化必须重新对齐 `<html>.dark`。

    `applyTheme` 是命令式入口，只要有一条路径改了状态却漏掉 class（或 class 被
    别处覆盖），页面就会停在「深色状态 + 浅底」。用 watch 把映射钉成单向。
    """
    source = _read(EMBED_CHAT)
    assert 'classList.toggle("dark", theme === "dark")' in source
    assert "() => config.theme" in source


def _scoped_style_sources():
    for path in sorted(FRONTEND_SRC.rglob("*.vue")):
        yield path, _strip_css_comments(_read(path))
    for path in sorted(FRONTEND_SRC.rglob("*.css")):
        yield path, _strip_css_comments(_read(path))


def test_no_global_dark_selector_leak():
    """`.dark` 前缀绝不能用 `:global()` 包裹。

    Vue 会把 `:global(.dark) .x` 编译成一条**全局** `.dark{...}`——`.x` 连同
    scoped 属性一起丢失。它不再只作用于目标元素，而是命中**所有**带 `.dark` 的
    元素（EmbedChat 根容器、`<html>`），是「深色白雾」的直接成因。
    """
    offenders = [
        str(path.relative_to(ROOT))
        for path, code in _scoped_style_sources()
        if ":global(.dark)" in code
    ]
    assert offenders == [], f"发现全局 .dark 选择器污染：{offenders}"


def test_dark_scoped_rules_keep_their_target_selector():
    """反向锁：深色规则必须保留各自的目标类名，不能退化成裸 `.dark`。"""
    walker = _strip_css_comments(
        _read(FRONTEND_SRC / "components/embed/GeneratingWalker.vue")
    )
    assert ".dark .gw-grid {" in walker
