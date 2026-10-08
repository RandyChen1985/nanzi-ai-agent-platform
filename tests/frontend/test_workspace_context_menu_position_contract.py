"""工作空间浏览器右键菜单的落点契约（静态源码断言）。

现象：在文件列表靠下的位置右键，菜单从点击点向下展开，超出视口底部的部分被裁掉
（「新建文件夹」以下看不见）。菜单本身是 ``fixed`` + ``Teleport to body``，不会被抽屉
裁切，问题纯粹是**溢出视口**。

要求：按菜单**实测尺寸**决定落点——下方放不下就向上翻转，右侧放不下就向左收，
最后夹在视口内；而不是直接把点击坐标当落点。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DRAWER = Path("frontend/src/components/embed/WorkspaceBrowserDrawer.vue")


def _source() -> str:
    if not DRAWER.exists():
        pytest.fail(f"缺少文件：{DRAWER}")
    return DRAWER.read_text(encoding="utf-8")


def _body(source: str, marker: str) -> str:
    """取函数体：优先从 `=> {` 起算，否则从第一个 `{` 起算。

    注意不能见到第一个 `{` 就用——`const openContextMenu = (item?: { path: string }) => {`
    的参数类型注解里也有花括号，会把参数类型当成函数体。
    """
    start = source.index(marker)
    arrow = source.find("=> {", start)
    brace = source.index("{", arrow + 3) if arrow != -1 else source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace : index + 1]
    raise AssertionError(f"未找到平衡块：{marker}")


def test_menu_position_comes_from_measured_placement():
    """落点必须来自校正后的位置，不能直接用点击坐标。"""
    source = _source()
    assert "contextMenuPosition" in source
    style_line = next(line for line in source.splitlines() if "contextMenuPosition.left" in line)
    assert "contextMenu.x" not in style_line, style_line
    assert "contextMenu.y" not in style_line, style_line


def test_menu_is_measured_before_placing():
    body = _body(_source(), "const adjustContextMenuPosition")
    assert "getBoundingClientRect" in body
    assert "contextMenuRef" in body


def test_menu_flips_upwards_when_bottom_is_not_enough():
    body = _body(_source(), "const adjustContextMenuPosition")
    assert "window.innerHeight" in body
    # 向上翻转：用点击点减去菜单高度
    assert re.search(r"top\s*=\s*contextMenu\.value\.y\s*-\s*height", body), body


def test_menu_stays_horizontal_inside_viewport():
    body = _body(_source(), "const adjustContextMenuPosition")
    assert "window.innerWidth" in body
    assert re.search(r"left\s*=\s*Math\.max\([^)]*window\.innerWidth", body), body


def test_menu_is_repositioned_right_after_opening():
    body = _body(_source(), "const openContextMenu")
    assert "contextMenuPosition.value" in body
    assert "nextTick(adjustContextMenuPosition)" in body


def test_menu_is_still_tethered_to_the_mouse_point():
    """向上翻转只是兜底：够放的时候仍应贴着点击点。"""
    body = _body(_source(), "const openContextMenu")
    assert "event.clientX" in body and "event.clientY" in body
    source = _source()
    assert 'ref="contextMenuRef"' in source
    assert "fixed z-[130]" in source
    assert '<Teleport to="body">' in source


def test_menu_height_is_capped_to_the_viewport():
    """只翻转还不够：菜单本身高于剩余空间时，翻上去照样露不全。

    因此先把高度限制在可视区之内（超出时菜单内部滚动），再决定翻不翻。
    """
    source = _source()
    assert "contextMenuMaxHeight" in source
    body = _body(source, "const adjustContextMenuPosition")
    assert "element.scrollHeight" in body, "应按内容真实高度计算"
    assert re.search(r"Math\.min\(element\.scrollHeight,\s*available\)", body), body
    assert re.search(r"window\.innerHeight\s*-\s*margin\s*\*\s*2", body), body

    style_line = next(line for line in source.splitlines() if "contextMenuMaxHeight}px" in line)
    assert "maxHeight" in style_line, style_line
    # 注意：源码里存在多个 fixed 菜单（如快速导航），必须锚定到右键菜单本身
    anchor = source.index('v-if="contextMenu"')
    menu_tag = source[source.rindex("<div", 0, anchor) : source.index(">", anchor)]
    assert "overflow-y-auto" in menu_tag, menu_tag


def test_menu_still_flips_after_being_capped():
    """限高之后仍要翻转，且翻转用的高度必须是限高后的高度（否则又会溢出）。"""
    body = _body(_source(), "const adjustContextMenuPosition")
    assert re.search(r"top\s*=\s*contextMenu\.value\.y\s*-\s*height", body), body
    flip_branch = body[body.index("if (top + height > window.innerHeight - margin)") :]
    assert "height" in flip_branch
