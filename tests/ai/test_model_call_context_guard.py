from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.runtime.agentscope.middleware import ModelCallStatsMiddleware


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_model_call_guard_clamps_completion_to_remaining_context():
    parameters = SimpleNamespace(max_tokens=32768)
    current_model = SimpleNamespace(
        model="deepseek-v3.2",
        context_size=65536,
        parameters=parameters,
        count_tokens=AsyncMock(return_value=65000),
    )
    observed = []

    async def next_handler(**kwargs):
        del kwargs
        observed.append(parameters.max_tokens)
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=65000, output_tokens=536),
            content=[],
        )

    middleware = ModelCallStatsMiddleware(
        user_id="u1",
        conversation_id="c1",
        agent_name="main",
        physical_window=65536,
        history_budget=24576,
        completion_reserve=32768,
    )

    with patch(
        "app.services.ai.runtime.agentscope.middleware._append_stat_to_redis",
        new=AsyncMock(),
    ):
        await middleware.on_model_call(
            agent=SimpleNamespace(),
            input_kwargs={
                "current_model": current_model,
                "messages": [],
                "tools": [],
            },
            next_handler=next_handler,
        )

    assert observed == [536]
    assert parameters.max_tokens == 32768


@pytest.mark.asyncio
async def test_model_call_guard_skips_clamp_when_remaining_below_floor():
    """剩余空间小于下限时不钳制，并留下显式告警。

    钳到 1..127 token 只会产出"空回复/半截话"，比让供应商直接拒绝更难排查；这里选择
    暴露问题而不是制造坏输出。
    """
    parameters = SimpleNamespace(max_tokens=32768)
    current_model = SimpleNamespace(
        model="deepseek-v3.2",
        context_size=65536,
        parameters=parameters,
        count_tokens=AsyncMock(return_value=65500),  # available = 36 < 128
    )
    observed = []

    async def next_handler(**kwargs):
        del kwargs
        observed.append(parameters.max_tokens)
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=65500, output_tokens=10),
            content=[],
        )

    middleware = ModelCallStatsMiddleware(
        user_id="u1",
        conversation_id="c1",
        agent_name="main",
        physical_window=65536,
        history_budget=24576,
        completion_reserve=32768,
    )

    with patch(
        "app.services.ai.runtime.agentscope.middleware._append_stat_to_redis",
        new=AsyncMock(),
    ), patch(
        "app.services.ai.runtime.agentscope.middleware.logger"
    ) as mock_logger:
        await middleware.on_model_call(
            agent=SimpleNamespace(),
            input_kwargs={
                "current_model": current_model,
                "messages": [],
                "tools": [],
            },
            next_handler=next_handler,
        )

    # 未被钳制：仍发出原始的 32768
    assert observed == [32768]
    assert any(
        "too small to produce a usable reply" in str(call.args[0])
        for call in mock_logger.error.call_args_list
    ), "剩余空间过小时必须留下显式告警"


@pytest.mark.asyncio
async def test_model_call_guard_logs_error_when_input_exceeds_window():
    """输入已经超出窗口时钳不动，但必须显式告警（否则线上只看到供应商 400）。"""
    parameters = SimpleNamespace(max_tokens=32768)
    current_model = SimpleNamespace(
        model="deepseek-v3.2",
        context_size=65536,
        parameters=parameters,
        count_tokens=AsyncMock(return_value=70000),  # available < 0
    )

    async def next_handler(**kwargs):
        del kwargs
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=70000, output_tokens=0),
            content=[],
        )

    middleware = ModelCallStatsMiddleware(
        user_id="u1",
        conversation_id="c1",
        agent_name="main",
        physical_window=65536,
        history_budget=24576,
        completion_reserve=32768,
    )

    with patch(
        "app.services.ai.runtime.agentscope.middleware._append_stat_to_redis",
        new=AsyncMock(),
    ), patch(
        "app.services.ai.runtime.agentscope.middleware.logger"
    ) as mock_logger:
        await middleware.on_model_call(
            agent=SimpleNamespace(),
            input_kwargs={
                "current_model": current_model,
                "messages": [],
                "tools": [],
            },
            next_handler=next_handler,
        )

    assert parameters.max_tokens == 32768  # 未被改坏
    assert any(
        "Input exceeds model context window" in str(call.args[0])
        for call in mock_logger.error.call_args_list
    ), "输入超窗必须留下显式告警"


@pytest.mark.asyncio
async def test_model_call_guard_still_clamps_above_the_floor():
    """刚好大于下限时仍要正常钳制（不要把正常保护一并挡掉）。"""
    parameters = SimpleNamespace(max_tokens=32768)
    current_model = SimpleNamespace(
        model="deepseek-v3.2",
        context_size=65536,
        parameters=parameters,
        count_tokens=AsyncMock(return_value=65000),  # available = 536 >= 128
    )
    observed = []

    async def next_handler(**kwargs):
        del kwargs
        observed.append(parameters.max_tokens)
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=65000, output_tokens=536),
            content=[],
        )

    middleware = ModelCallStatsMiddleware(
        user_id="u1",
        conversation_id="c1",
        agent_name="main",
        physical_window=65536,
        history_budget=24576,
        completion_reserve=32768,
    )

    with patch(
        "app.services.ai.runtime.agentscope.middleware._append_stat_to_redis",
        new=AsyncMock(),
    ):
        await middleware.on_model_call(
            agent=SimpleNamespace(),
            input_kwargs={
                "current_model": current_model,
                "messages": [],
                "tools": [],
            },
            next_handler=next_handler,
        )

    assert observed == [536]


def test_contains_compaction_detects_plain_string_content():
    """content 为纯 str 时也必须能识别摘录标记（此前走 block 分支永远取不到值）。"""
    from app.services.ai.context_compaction import COMPACTION_MARKER
    from app.services.ai.runtime.agentscope.middleware import _contains_compaction

    marker_text = f"{COMPACTION_MARKER}\n以下是更早轮次对话的要点。"
    assert _contains_compaction([SimpleNamespace(content=marker_text)]) is True
    assert _contains_compaction([SimpleNamespace(content=[marker_text])]) is True
    assert _contains_compaction([SimpleNamespace(content="与摘录无关的普通文本")]) is False
