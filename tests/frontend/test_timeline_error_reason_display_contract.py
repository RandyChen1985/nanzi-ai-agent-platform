"""时间线「错误原因」的展示契约：不给用户看框架内部标记。

背景：整轮被取消时，AgentScope 会给没跑完的工具补一条
`<system-reminder>The tool call has been interrupted by the user.</system-reminder>`
作为工具结果。后端把这段文本提成 `error_reason` 后，前端原来直接 `{{ x.error_reason }}`
渲染，用户看到的就是一串 XML 标签，而且「被用户中断」的归因在取消来自断流/看门狗时
也是错的。

后端已按 interrupted 状态改写成人话；这里守住前端这一层兜底——历史消息里已经存下的
脏文本同样要洗干净，不能只靠新数据。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TIMELINE_PATH = ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue"
TIMELINE = TIMELINE_PATH.read_text(encoding="utf-8")

pytestmark = pytest.mark.no_infrastructure

# 四个层级的工具卡片都各自渲染一次错误原因。
_DIRECT_RENDERINGS = (
    "{{ child.error_reason }}",
    "{{ subStep.error_reason }}",
    "{{ item.error_reason }}",
    "{{ nestedStep.error_reason }}",
)


def _formatter_body() -> str:
    start = TIMELINE.index("const formatErrorReason")
    open_index = TIMELINE.index("{", TIMELINE.index("=>", start))
    depth = 0
    for index in range(open_index, len(TIMELINE)):
        char = TIMELINE[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return TIMELINE[start : index + 1]
    raise AssertionError("formatErrorReason 的花括号未配平")


def test_error_reason_only_renders_through_the_formatter():
    for direct in _DIRECT_RENDERINGS:
        assert direct not in TIMELINE, f"错误原因必须过 formatErrorReason，不能直出：{direct}"
    assert TIMELINE.count("{{ formatErrorReason(") >= 4


def test_error_reason_visibility_also_uses_the_formatter():
    """`v-if` 也要用同一条判据，否则洗成空串时会留下一个空的红色提示框。"""
    for field in ("child", "subStep", "item", "nestedStep"):
        assert f'v-if="formatErrorReason({field}.error_reason)"' in TIMELINE, field


def test_formatter_strips_agentscope_markup_and_translates_interruption():
    body = _formatter_body()
    assert "system-reminder" in body, "要剥掉 AgentScope 的内部标记"
    assert "interrupted by the user" in body, "要能识别框架的中断原文（含历史脏数据）"
    assert "INTERRUPTED_REASON_TEXT" in body, "中断文案要统一走常量"
    assert 'const INTERRUPTED_REASON_TEXT = "本轮已取消，工具未返回结果"' in TIMELINE, (
        "中断要换成人话，且不再甩锅给用户"
    )
