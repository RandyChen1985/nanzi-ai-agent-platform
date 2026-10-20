"""图标目录对账：前端 `constants/menuIcons.ts` 与后端 `menu_seed.ALLOWED_ICON_KEYS`。

两边一旦分叉，后果是「菜单管理里能选到这个图标，侧栏却渲染成兜底方块」——
不会报错，只是看起来像坏了。因此把它固化成契约。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.menu_seed import ALLOWED_ICON_KEYS

SOURCE = Path("frontend/src/constants/menuIcons.ts")
pytestmark = pytest.mark.no_infrastructure


def _frontend_keys() -> set[str]:
    source = SOURCE.read_text(encoding="utf-8")
    block = source[
        source.index("export const MENU_ICON_COMPONENTS") : source.index("export const MENU_ICON_KEYS")
    ]
    return set(re.findall(r"^  ([a-z_0-9]+): \w+Icon,", block, re.M))


def test_every_seed_icon_has_a_frontend_svg():
    missing = set(ALLOWED_ICON_KEYS) - _frontend_keys()
    assert not missing, f"这些图标后端允许但前端没有绘制：{sorted(missing)}"


def test_frontend_has_no_extra_icons_the_backend_would_reject():
    extra = _frontend_keys() - set(ALLOWED_ICON_KEYS)
    assert not extra, f"这些图标前端可选但后端会拒绝：{sorted(extra)}"


def test_every_icon_has_a_chinese_label_for_the_picker():
    """选择器上要显示中文名：key 本身（`chat_bubble_left_right`）看不出图形含义。"""
    source = SOURCE.read_text(encoding="utf-8")
    labels_block = source[source.index("export const MENU_ICON_LABELS"):]
    labels = set(re.findall(r"^  ([a-z_]+): '", labels_block, re.M))

    assert labels == _frontend_keys(), (
        f"缺少中文名的图标：{sorted(_frontend_keys() - labels)}；"
        f"多余的中文名：{sorted(labels - _frontend_keys())}"
    )


def test_icon_keys_are_sorted_for_stable_dropdown_order():
    source = SOURCE.read_text(encoding="utf-8")

    assert "export const MENU_ICON_KEYS = Object.keys(MENU_ICON_COMPONENTS).sort()" in source


def test_icon_component_falls_back_instead_of_rendering_nothing():
    """未知图标（历史数据、或后端新增但前端未发版）必须画个占位，不能留空白。"""
    source = Path("frontend/src/components/menu/MenuIcon.vue").read_text(encoding="utf-8")

    assert "|| Bars3Icon" in source
    assert "MENU_ICON_COMPONENTS[(props.name || '').trim()]" in source


def test_picker_categories_cover_every_icon_exactly_once():
    """选择器按用途分组展示，分组漏项会让图标在最常用的入口里消失。"""
    source = SOURCE.read_text(encoding="utf-8")
    block = source[source.index("export const MENU_ICON_CATEGORIES"):]

    grouped = re.findall(r"keys: \[([^\]]+)\]", block)
    assert grouped, "选择器分组为空"
    listed = [key for group in grouped for key in re.findall(r"'([a-z_0-9]+)'", group)]

    assert len(listed) == len(set(listed)), f"图标在多个分组里重复出现：{sorted({k for k in listed if listed.count(k) > 1})}"
    assert set(listed) == _frontend_keys(), (
        f"分组漏掉的图标：{sorted(_frontend_keys() - set(listed))}；"
        f"分组里多出的图标：{sorted(set(listed) - _frontend_keys())}"
    )


def test_icon_catalog_stays_comfortably_large():
    """图标太少时菜单只能全用同一个，用户要找的是"能不能表达这个菜单"。"""
    assert len(_frontend_keys()) >= 50


def test_every_menu_icon_call_site_sets_its_own_size():
    """图标组件本身不带尺寸：heroicons 的 svg 没有 width/height。

    漏传尺寸时内联 SVG 会按 300x150 铺开，侧栏直接被挤变形——这类问题在
    `vue-tsc` 里看不出来，只能靠契约盯住调用点。
    """
    root = Path("frontend/src")
    offenders: list[str] = []
    for path in sorted(root.rglob("*.vue")):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "<MenuIcon " not in line:
                continue
            if "h-" in line or ":class" in line:
                continue
            offenders.append(f"{path}:{line_no}")

    assert not offenders, "以下 MenuIcon 调用没给尺寸：" + "、".join(offenders)

    # 走动态 class 的那处，确认尺寸写在计算属性里
    sidebar = (root / "components/menu/SidebarMenuLink.vue").read_text(encoding="utf-8")
    assert "const size = 'h-[18px] w-[18px]'" in sidebar
