"""契约：「进行中的会话」入口双图标 —— 空闲 Radio / 有活动 Loader 旋转。

用户诉求演进（原文）：
「换成  Radio · 信号播报 吧」
→「另外，激活状态的时候，还是用 Loader · 旋转加载 吧，能体现动画效果」
→「移动端下，咋挨那么远，中间有这么多空白」（选方案 A：移动端图标并入副标题行）

最终形态：
- **副标题行末尾**一个图标（桌面与移动端共用，紧跟「准备就绪」/「正在处理您的请求...」），
  空闲 Radio 静态、有会话在跑 Loader 旋转；
- **⋮ 菜单项**里同一个双图标（静态，不旋转），给手机端一个从菜单进入的入口；
- 角标必须留在旋转元素之外，数字不跟着转。

移动端不再有独立的图标按钮：它原本占 44px 触控热区，与同样 44px 的「更多」按钮并排，
16px 的图标被两圈 padding 撑开约 36px 视觉空白，看起来「离得远」。并进副标题行后，
那段空白彻底消失，header 右侧只剩「更多」。

定位坑（吃过一次）：不能用 `v-if="activeRunsCount"` 找切换点——**角标上也有这个条件**，
搜索会被带到很远的下一个 Loader。必须锚定 `<template v-if="activeRunsCount"`。
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
EXPECTED_SWITCHES = 2   # 副标题行图标（桌面+移动共用）/ ⋮ 菜单项
EXPECTED_SPINS = 1      # 只有副标题行那个图标旋转


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def _switches():
    """两处图标切换的区间 (if 分支起点, v-else 起点)。"""
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


def test_both_switch_points_have_radio_and_loader():
    switches = _switches()
    assert len(switches) == EXPECTED_SWITCHES, (
        f"找到 {len(switches)} 处图标切换，期望 {EXPECTED_SWITCHES}（副标题行 / ⋮ 菜单项）"
    )
    source = _source()
    for index, (begin, els) in enumerate(switches):
        assert LOADER in source[begin:els], f"第 {index + 1} 处的「有会话在跑」分支没用 Loader"
        assert RADIO in source[els : els + 1200], f"第 {index + 1} 处的空闲分支没用 Radio"


def test_only_the_subtitle_icon_spins():
    """旋转只出现在副标题行那个图标里（菜单项静态）。"""
    source = _source()
    intervals = _switches()
    in_branch = [
        pos for pos in (m.start() for m in re.finditer("animate-spin", source))
        if any(begin < pos < els for begin, els in intervals)
    ]
    assert len(in_branch) == EXPECTED_SPINS, (
        f"图标切换分支里有 {len(in_branch)} 处旋转，期望 {EXPECTED_SPINS}"
    )


def test_the_icon_is_shared_by_desktop_and_mobile():
    """副标题行的图标不能再用 `!isMobile` 排除移动端。"""
    source = _source()
    head = source.index('ref="activeRunsRef"')
    tag = source[source.rindex("<div", 0, head) : source.index(">", head)]
    assert "!isMobile" not in tag, "副标题行的图标仍在排除移动端"


def test_mobile_has_no_separate_entry_button():
    """移动端原来那个独立图标按钮必须删掉，否则右侧又多一处空白。

    用「触发入口只能有一处」来判断：按钮回来就会变成两处。
    （不能靠注释判断——移动端 modal 的注释与按钮注释前缀相同。）
    """
    source = _source()
    hits = source.count("@click=\"toggleActiveRuns\"")
    assert hits == 1, f"`toggleActiveRuns` 触发点有 {hits} 处，期望 1 处（只剩副标题行）"
    assert "移动端「进行中的会话」：从「更多」进入" in source, "移动端 modal 变体被误删"


def test_no_text_leaks_out_of_comment_blocks():
    """`-->` 之后如果直接跟着文字，那段文字会被渲染到页面上。

    本轮踩过：把三行说明插在 `-->` 之后，结果整段说明出现在 header 副标题行里。
    `@vue/compiler-sfc` 与 `vue-tsc` 都不会报这个错（对它们来说那就是合法文本节点），
    只有真机才看得出来，所以必须用契约兜住。
    """
    lines = _source().split("\n")
    leaks = []
    for i, line in enumerate(lines):
        if "-->" not in line:
            continue
        tail = line[line.index("-->") + 3 :].strip()
        if tail and not tail.startswith("<"):
            leaks.append((i + 1, tail[:40]))
        elif not tail and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if nxt and not nxt.startswith("<"):
                leaks.append((i + 2, nxt[:40]))
    assert not leaks, f"注释块外有裸露文字，会被渲染到页面：{leaks[:3]}"


def test_old_activity_icon_is_gone():
    assert OLD_ACTIVITY not in _source(), "心电图脉冲图标还在"


def test_badge_is_outside_the_spinning_element():
    """角标在旋转元素之外，数字才不会跟着转。"""
    source = _source()
    spin = source.index('class="h-3.5 w-3.5 animate-spin"')
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
