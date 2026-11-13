"""执行链路轨道节点的「状态权重」契约。

背景：一条竖线配十几个同样亮的点，读者得先扫一遍才能找出「现在跑到哪」。
改了之后亮度由状态决定：跑完的退成标点，正在跑的那一行才是视觉锚点。

这里锁的是三件容易在后续改动中被悄悄推翻的事：
1. 每种形状都要带上状态类（漏一个，那一类就又变回「永远很亮」）；
2. 失败节点不能被压淡，也不能被状态类覆盖掉它唯一的彩色；
3. 呼吸动画要能被 prefers-reduced-motion 关掉。
"""

from __future__ import annotations

import re
from pathlib import Path

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "components"
    / "chat"
    / "ChatExecutionTimeline.vue"
).read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    """取出某个顶层函数的函数体文本（够用即可，不追求完整的语法解析）。"""
    match = re.search(rf"function {name}\([^)]*\)[^{{]*\{{", SOURCE)
    assert match, f"找不到函数 {name}"
    start = match.end()
    depth = 1
    cursor = start
    while cursor < len(SOURCE) and depth > 0:
        char = SOURCE[cursor]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        cursor += 1
    return SOURCE[start : cursor - 1]


def test_every_node_shape_carries_its_state_class():
    """四种形状 + todo 都要带状态类；漏一个就是「那一类永远亮着」。"""
    body = _function_body("timelineNodeClass")
    for shape in ("tl-node-todo", "tl-node-think", "tl-node-say", "tl-node-agent", "tl-node-tool"):
        line = next((line for line in body.splitlines() if shape in line), "")
        assert "${nodeStateClass(item)}" in line, f"{shape} 没有带上状态权重类：{line.strip()}"


def test_failed_node_is_never_dimmed():
    """失败节点是全链路唯一的彩色，既不能压淡，也不能被状态类覆盖。"""
    body = _function_body("timelineNodeClass")
    fail_line = next(
        (line for line in body.splitlines() if 'item.status === "error"' in line), ""
    )
    assert "tl-node-fail" in fail_line
    assert "nodeStateClass" not in fail_line, "失败节点不该参与完成态压淡"
    assert "background: rgb(239 68 68);" in SOURCE, "失败节点的红色被改掉了"


def test_done_state_is_dimmed_and_running_state_breathes():
    assert re.search(
        r"\.timeline-rail > \.tl-node-done::before \{\s*opacity: 0\.34;", SOURCE
    ), "完成态没有压淡"
    assert re.search(
        r"\.timeline-rail > \.tl-node-running::before \{[^}]*animation:", SOURCE
    ), "进行中节点缺少呼吸动画"


def test_running_animation_respects_reduced_motion():
    """呼吸动画必须能被系统的「减少动态效果」关掉，否则前庭敏感的用户会难受。"""
    blocks = SOURCE.split("@media (prefers-reduced-motion: reduce)")
    assert len(blocks) > 1, "缺少 reduced-motion 降级块"
    guarded = [
        block
        for block in blocks[1:]
        if "tl-node-running" in block[:400] and "animation: none" in block[:400]
    ]
    assert guarded, "进行中节点的动画在 reduced-motion 下没有被关掉"
