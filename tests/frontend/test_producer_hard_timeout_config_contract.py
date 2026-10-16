"""契约：producer 看门狗在系统配置页必须用数字控件。

背景：看门狗配置项（agent_chat_producer_hard_timeout_seconds）加入后落到了通用文本输入
分支上，可以随手输入「jhkj」——后端 400 会拦住（`validate_producer_hard_timeout`），
但用户要先撞一次保存失败才知道范围，体验上等于没有约束。

本页对数字类配置项既有的模式是「一个 key 一个 v-else-if 分支 + 一组 helper」：
agent_max_toolcall_timeout、agent_tool_loop_global_limit、llm_request_read_timeout
都是这么做的。看门狗照同一模式渲染，并**紧跟在 agent_max_toolcall_timeout 之后**——
两者是「整轮总时长」与「单个工具」的关系，并排才看得出那个冲突。

与工具超时的唯一区别：下界是 0（0 表示关闭看门狗），不是 1。
"""
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
SYSTEM_CONFIG = ROOT / "frontend/src/views/SystemConfig.vue"

WATCHDOG_KEY = "agent_chat_producer_hard_timeout_seconds"


def _source() -> str:
    return SYSTEM_CONFIG.read_text(encoding="utf-8")


def _watchdog_branch() -> str:
    """截取看门狗的模板分支：从它开始到下一个 v-else-if 分支为止。"""
    source = _source()
    start = source.index(f'v-else-if="item.key === \'{WATCHDOG_KEY}\'"')
    end = source.index('v-else-if="item.key === \'llm_request_read_timeout\'"', start)
    return source[start:end]


def test_watchdog_uses_a_number_input_not_a_free_text_field():
    branch = _watchdog_branch()

    assert 'type="number"' in branch
    assert 'inputmode="numeric"' in branch
    # 关键回归：绝不能退回自由文本框
    assert 'type="text"' not in branch


def test_watchdog_accepts_zero_because_zero_disables_the_watchdog():
    branch = _watchdog_branch()

    # 与工具超时（min=1）的唯一区别：0 是合法值，表示关闭
    assert ':min="PRODUCER_HARD_TIMEOUT_MIN"' in branch
    assert ':max="PRODUCER_HARD_TIMEOUT_MAX"' in branch
    assert 'step="1"' in branch


def test_watchdog_branch_has_stepper_buttons_and_input_guards():
    branch = _watchdog_branch()

    assert 'aria-label="减少看门狗超时时间"' in branch
    assert 'aria-label="增加看门狗超时时间"' in branch
    assert "adjustProducerHardTimeout(item, -1)" in branch
    assert "adjustProducerHardTimeout(item, 1)" in branch
    assert '@keydown="handleProducerHardTimeoutKeydown"' in branch
    assert '@input="handleProducerHardTimeoutInput(item, $event)"' in branch
    assert '@blur="normalizeProducerHardTimeoutInput(item)"' in branch
    assert "秒" in branch


def test_watchdog_helpers_exist_with_zero_as_lower_bound():
    source = _source()

    for name in (
        "getProducerHardTimeoutValue",
        "adjustProducerHardTimeout",
        "handleProducerHardTimeoutKeydown",
        "handleProducerHardTimeoutInput",
        "normalizeProducerHardTimeoutInput",
    ):
        assert f"const {name}" in source

    # 边界必须是 0-3600 且默认 600，与后端 producer_watchdog 模块一致
    assert "PRODUCER_HARD_TIMEOUT_MIN = 0" in source
    assert "PRODUCER_HARD_TIMEOUT_MAX = 3600" in source
    assert "PRODUCER_HARD_TIMEOUT_DEFAULT = 600" in source


def test_watchdog_value_is_normalized_on_load():
    """脏值（例如手工塞进 DB 的中文）在加载时要归一成合法数字。"""
    source = _source()

    assert f"item.key === '{WATCHDOG_KEY}'" in source
    assert "String(getProducerHardTimeoutValue(item))" in source


def test_watchdog_is_ordered_right_after_the_toolcall_timeout():
    """两者是「整轮 vs 单个工具」的关系，并排才看得出冲突。"""
    source = _source()
    order_block = source[source.index("const order = [", source.index("if (category === 'agent')")):]

    assert order_block.index("'agent_max_toolcall_timeout'") < order_block.index(f"'{WATCHDOG_KEY}'")


def test_generic_input_branch_is_still_the_fallback():
    """没登记的键仍走通用输入分支，别把其它配置项弄丢。"""
    source = _source()
    start = source.index(f'v-else-if="item.key === \'{WATCHDOG_KEY}\'"')

    assert "v-else" in source[start:]
