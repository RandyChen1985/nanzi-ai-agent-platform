"""契约：版本级超时配置卡片默认收起，展开才显示说明与输入框。

工具列表本身很长（28/75、8/99、10/10），顶部这张卡常驻三四行说明＋一句重复状态，
把首屏全吃掉了。改成默认一行：标题 + 当前生效值摘要 + 右侧开关。

收起来不能丢关键信息——摘要必须说清「跟随全局 N 秒」还是「版本级 N 秒」，
否则用户得先展开才知道这个版本到底配了什么。
"""
from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
DRAWER = ROOT / "frontend/src/components/agent/AgentVersionEditorDrawer.vue"


def _source() -> str:
    return DRAWER.read_text(encoding="utf-8")


def test_collapsed_by_default():
    assert "const timeoutCardExpanded = ref(false)" in _source(), "默认必须是收起"


def test_title_is_the_toggle_with_aria_expanded():
    source = _source()
    tag_start = source.index(':aria-expanded="timeoutCardExpanded"')
    tag = source[source.rindex("<", 0, tag_start) : source.index(">", tag_start) + 1]
    assert 'type="button"' in tag
    assert '@click="timeoutCardExpanded = !timeoutCardExpanded"' in tag
    # 可点区域只包标题与摘要，开关在右侧独立——否则点开关会顺带收起卡片
    assert "role=\"switch\"" not in tag


def test_explanations_and_stepper_are_gated_by_expanded():
    source = _source()

    notes_start = source.index('v-show="timeoutCardExpanded"')
    notes_block = source[notes_start : source.index("</div>", source.index("整轮看门狗", notes_start))]
    assert "默认使用全局配置" in notes_block
    assert "当前全局配置" in notes_block
    assert "整轮看门狗" in notes_block

    assert (
        'v-if="isAgentVersionToolcallTimeoutEnabled && timeoutCardExpanded"' in source
    ), "输入框与加减按钮也要跟着收起，否则等于没收"
    assert 'aria-label="智能体版本工具调用超时时间（秒）"' in source


def test_collapsed_summary_reports_effective_value():
    """"收起来就看不到配了什么"要防住：摘要里两种情况都要有值。"""
    source = _source()
    summary_start = source.index('v-if="!timeoutCardExpanded"')
    summary = source[summary_start : summary_start + 400]
    assert "跟随全局" in summary
    assert "版本级" in summary
    assert "globalAgentToolcallTimeout" in summary
    assert "getAgentVersionToolcallTimeoutValue()" in summary


def test_redundant_follow_global_line_hidden_when_collapsed():
    """展开时底部那句「当前跟随全局配置」保留；收起时摘要已覆盖，不重复占行。"""
    source = _source()
    marker = source.index('当前跟随全局配置（当前全局值')
    tag = source[source.rindex("<p", 0, marker) : source.index(">", marker) + 1]
    assert 'v-else-if="timeoutCardExpanded"' in tag


def test_switch_label_does_not_duplicate_the_summary():
    """开关旁不能再写「跟随全局配置」：摘要已经说了「跟随全局 N 秒」。

    否则一行里出现「· 跟随全局 300 秒   跟随全局配置」，读起来像重复，还容易被
    当成一个可点的动作标签。开关只表达开关状态，标签恒定为「自定义超时时间」。
    """
    source = _source()
    switch_label_start = source.index("自定义超时时间")
    switch_label = source[switch_label_start : switch_label_start + 40]
    assert "自定义超时时间" in switch_label
    assert "跟随全局配置" not in switch_label
    assert '{{ isAgentVersionToolcallTimeoutEnabled ? \'使用智能体专属配置\'' not in source


def test_enabling_version_level_config_auto_expands():
    """开关一开就展开：否则打开了「自定义超时时间」却看不见要填的秒数。"""
    source = _source()
    watch_start = source.index("watch(isAgentVersionToolcallTimeoutEnabled")
    watch_block = source[watch_start : watch_start + 260]
    assert "if (enabled) timeoutCardExpanded.value = true" in watch_block
