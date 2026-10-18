"""契约：「进行中的会话」入口双图标 —— 空闲 Radio / 有活动 Loader 旋转。

用户诉求演进（原文）：
「换成  Radio · 信号播报 吧」
→「另外，激活状态的时候，还是用 Loader · 旋转加载 吧，能体现动画效果」

所以最终是两者结合：
- 空闲（`activeRunsCount` 为 0）：Radio（信号波），**静态**；
- 有会话在跑：Loader 并**旋转**，用动效表达「正在运行」。

三处都要切换：桌面入口、移动端入口、⋮ 菜单项。其中只有两个 header 入口旋转，
菜单项保持静态（浮层里持续旋转会分散注意力）。角标仍必须留在旋转元素之外。

定位坑（吃过一次）：不能用 `v-if="activeRunsCount"` 找切换点——**角标上也有这个条件**，
搜索会被带到很远的下一个 Loader，得出错位结论。必须锚定 `<template v-if="activeRunsCount"`。
"""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
LOADER = "m21 12a9 9 0 1 1-6.219-8.56"
RADIO = "M4.9 19.1C1 15.2 1 8.8 4.9 4.9"
OLD_ACTIVITY = "M22 12h-4l-3 9L9 3l-3 9H2"
BADGE = 'class="absolute -right-1 -top-1'
EXPECTED_SWITCHES = 3   # 桌面 / 移动端 / 菜单项
EXPECTED_SPINS = 2      # 只有两个 header 入口旋转


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def _switches():
    """三处图标切换的区间 (if 分支起点, v-else 起点)。"""
    source = _source()
    out, start = [], 0
    while True:
        try:
            begin = source.index('<template v-if="activeRunsCount"', start)
        except ValueError:
            break
        els = source.index("<template v-else", begin)
        out.append((begin, els))
        start = els
    return out


def test_all_three_places_have_both_icons():
    switches = _switches()
    assert len(switches) == EXPECTED_SWITCHES, (
        f"找到 {len(switches)} 处图标切换，期望 {EXPECTED_SWITCHES}（桌面 / 移动端 / 菜单项）"
    )
    source = _source()
    for index, (begin, els) in enumerate(switches):
        assert LOADER in source[begin:els], f"第 {index + 1} 处的「有会话在跑」分支没用 Loader"
        assert RADIO in source[els : els + 1200], f"第 {index + 1} 处的空闲分支没用 Radio"


def test_only_the_two_header_entries_spin():
    """旋转只出现在两个 header 入口的「有会话在跑」分支里（菜单项静态）。

    注意：全文还有别处用 `animate-spin`，所以只统计落在切换分支内的那些。
    """
    source = _source()
    intervals = _switches()
    in_branch = [
        pos for pos in (m.start() for m in re.finditer("animate-spin", source))
        if any(begin < pos < els for begin, els in intervals)
    ]
    assert len(in_branch) == EXPECTED_SPINS, (
        f"图标切换分支里有 {len(in_branch)} 处旋转，期望 {EXPECTED_SPINS}"
    )


def test_old_activity_icon_is_gone():
    assert OLD_ACTIVITY not in _source(), "心电图脉冲图标还在"


def test_badge_is_outside_the_spinning_element():
    """角标在旋转元素之外，数字才不会跟着转。"""
    source = _source()
    spin = source.index('class="h-3.5 w-3.5 animate-spin"')  # 桌面入口那个
    badge = source.index(BADGE)
    assert spin < badge, "角标位于旋转元素之前（结构异常）"
    assert "</svg>" in source[spin:badge], "角标被放进了旋转的 svg 里，数字会跟着转"


def test_the_sfc_compiles():
    """整个 SFC 必须能被 Vue 官方编译器解析（`vue-tsc` 不查模板标签平衡）。"""
    if not (ROOT / "frontend/node_modules").exists():
        pytest.skip("前端依赖未安装")
    script = (
        "const {parse}=require('@vue/compiler-sfc');const fs=require('fs');"
        "const r=parse(fs.readFileSync('src/views/EmbedChat.vue','utf8'));"
        "if(r.errors.length){console.error(r.errors.map(String).join(String.fromCharCode(10)));process.exit(1)}"
    )
    proc = subprocess.run(["node", "-e", script], cwd=str(ROOT / "frontend"),
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"EmbedChat.vue 模板无法编译：\n{proc.stderr}"
