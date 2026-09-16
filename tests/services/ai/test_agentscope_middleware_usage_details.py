"""测试 agentscope middleware._extract_usage_details 的多供应商缓存字段解析。

覆盖 middleware（前端「大模型调用明细指标」实际采集路径）对 usage 缓存字段的
归一化能力，确保与 executors.common.extract_tokens_from_message 对齐，
不丢失供应商返回的真实缓存命中数据。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch as _patch

import pytest

from app.services.ai.runtime.agentscope.middleware import _extract_usage_details


def test_extract_details_reads_top_level_cached_tokens_from_usage():
    """应解析 __proto__ 之外的顶层 cached_tokens（部分 OpenAI 兼容网关形态）。"""
    usage = SimpleNamespace(prompt_tokens=352280, completion_tokens=2276, cached_tokens=351232)

    in_tokens, out_tokens, cache_tokens, source = _extract_usage_details(usage)

    assert in_tokens == 352280
    assert out_tokens == 2276
    assert cache_tokens == 351232
    assert source == "cached_tokens"


def test_extract_details_reads_top_level_cached_tokens_from_dict():
    """应解析 dict 形态的顶层 cached_tokens。"""
    usage = {"prompt_tokens": 352280, "completion_tokens": 2276, "cached_tokens": 351232}

    in_tokens, out_tokens, cache_tokens, source = _extract_usage_details(usage)

    assert cache_tokens == 351232
    assert source == "cached_tokens"


def test_extract_details_reads_input_token_details_cache_read():
    """应解析 Anthropic 风格 input_token_details.cache_read。"""
    usage = SimpleNamespace(
        input_tokens=352280,
        output_tokens=2276,
        input_token_details={"cache_read": 351232},
    )

    in_tokens, out_tokens, cache_tokens, source = _extract_usage_details(usage)

    assert in_tokens == 352280
    assert out_tokens == 2276
    assert cache_tokens == 351232
    assert source == "input_token_details.cache_read"


def test_extract_details_reads_input_token_details_cache_read_from_dict():
    """应解析 dict 形态的 input_token_details.cache_read。"""
    usage = {"input_tokens": 352280, "output_tokens": 2276, "input_token_details": {"cache_read": 351232}}

    in_tokens, out_tokens, cache_tokens, source = _extract_usage_details(usage)

    assert cache_tokens == 351232
    assert source == "input_token_details.cache_read"


def test_extract_details_keeps_existing_openai_prompt_tokens_details():
    """回归：已有 prompt_tokens_details.cached_tokens 形态仍应正常解析。"""
    usage = SimpleNamespace(
        prompt_tokens=352280,
        completion_tokens=2276,
        prompt_tokens_details={"cached_tokens": 351232},
    )

    _, _, cache_tokens, source = _extract_usage_details(usage)

    assert cache_tokens == 351232
    assert source == "openai_prompt_tokens_details"


def test_extract_details_keeps_existing_cache_input_tokens():
    """回归：已有 AgentScope cache_input_tokens 形态仍应正常解析。"""
    usage = SimpleNamespace(input_tokens=352280, output_tokens=2276, cache_input_tokens=351232)

    _, _, cache_tokens, source = _extract_usage_details(usage)

    assert cache_tokens == 351232
    assert source == "agentscope_usage"


def test_extract_details_keeps_existing_cache_read_input_tokens():
    """回归：已有 cache_read_input_tokens 形态仍应正常解析。"""
    usage = SimpleNamespace(input_tokens=352280, output_tokens=2276, cache_read_input_tokens=351232)

    _, _, cache_tokens, source = _extract_usage_details(usage)

    assert cache_tokens == 351232
    assert source == "cache_read_input_tokens"


def test_extract_details_explicit_zero_cache_is_kept():
    """缓存字段显式为 0 时应返回 0 而非误用其他分支。"""
    usage = SimpleNamespace(input_tokens=100, output_tokens=10, cache_input_tokens=0)

    in_tokens, out_tokens, cache_tokens, source = _extract_usage_details(usage)

    assert cache_tokens == 0
    assert source == "agentscope_usage"


def test_extract_details_without_cache_returns_zero():
    """供应商未返回任何缓存字段时，cache 应为 0 且不报错。"""
    usage = SimpleNamespace(prompt_tokens=352280, completion_tokens=2276)

    in_tokens, out_tokens, cache_tokens, source = _extract_usage_details(usage)

    assert cache_tokens == 0
    assert source == "agentscope_usage"



# ---------------------------------------------------------------------------
# record 组装：uncached_input_tokens = input_tokens - cache_input_tokens
# 非流式 / 流式两条路径都要产出未缓存/缓存读取两栏，且不破坏 input_tokens 的 gross 语义。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_stream_record_exposes_uncached_and_cache_fields():
    """非流式 record 应同时包含未缓存输入与缓存读取两个口径字段。"""
    from app.services.ai.runtime.agentscope.middleware import ModelCallStatsMiddleware

    middleware = ModelCallStatsMiddleware(user_id="u1", conversation_id="c1", agent_name="main")

    class _CountModel:
        model = "fake-model"
        context_size = 1000

        def __init__(self):
            self.parameters = SimpleNamespace(max_tokens=100)

        async def count_tokens(self, *, messages, tools):
            return 352280

    async def next_handler(**kwargs):
        del kwargs
        return SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=352280,
                output_tokens=2276,
                cache_read_input_tokens=351232,
            ),
            content=[],
        )

    scheduled = []
    with _patch(
        "app.services.ai.runtime.agentscope.middleware._append_stat_to_redis",
        new=AsyncMock(),
    ) as append_stat, _patch(
        "app.services.ai.runtime.agentscope.middleware.asyncio.ensure_future",
        side_effect=lambda coroutine: scheduled.append(coroutine),
    ):
        await middleware.on_model_call(
            agent=SimpleNamespace(),
            input_kwargs={
                "current_model": _CountModel(),
                "messages": [SimpleNamespace(role="system")],
                "tools": [],
            },
            next_handler=next_handler,
        )
        await scheduled[0]

    record = append_stat.await_args.args[1]
    # soft 断言保持既有口径不破坏
    assert record["input_tokens"] == 352280  # gross 总输入（含缓存）
    assert record["cache_input_tokens"] == 351232  # 缓存读取
    assert record["uncached_input_tokens"] == 1048  # 未缓存输入 = gross - cache


@pytest.mark.asyncio
async def test_non_stream_record_uncached_never_negative_without_cache():
    """无缓存命中时 uncached_input_tokens 应等于 input_tokens，且不为负。"""
    from app.services.ai.runtime.agentscope.middleware import ModelCallStatsMiddleware

    middleware = ModelCallStatsMiddleware(user_id="u1", conversation_id="c1", agent_name="main")

    class _CountModel:
        model = "fake-model"
        context_size = 1000

        def __init__(self):
            self.parameters = SimpleNamespace(max_tokens=100)

        async def count_tokens(self, *, messages, tools):
            return 100

    async def next_handler(**kwargs):
        del kwargs
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=100, output_tokens=10),
            content=[],
        )

    scheduled = []
    with _patch(
        "app.services.ai.runtime.agentscope.middleware._append_stat_to_redis",
        new=AsyncMock(),
    ) as append_stat, _patch(
        "app.services.ai.runtime.agentscope.middleware.asyncio.ensure_future",
        side_effect=lambda coroutine: scheduled.append(coroutine),
    ):
        await middleware.on_model_call(
            agent=SimpleNamespace(),
            input_kwargs={
                "current_model": _CountModel(),
                "messages": [SimpleNamespace(role="system")],
                "tools": [],
            },
            next_handler=next_handler,
        )
        await scheduled[0]

    record = append_stat.await_args.args[1]
    assert record["input_tokens"] == 100
    assert record["cache_input_tokens"] == 0
    assert record["uncached_input_tokens"] == 100
