"""委派子代理的模型调用明细归属与 trace 步骤编号。

背景：
- 主消息「用量」来自共享 trace_buffer，本身已包含子代理消耗；
- 明细若留在 child_session_* 的 stats 键下，前端弹窗（按主会话 + 主 trace 过滤）
  永远查不到，导致「总量含子代理、明细不含」的口径差；
- 子代理与主代理共用 trace_buffer 但各自是独立执行器实例，只按实例自增会让
  同一 trace 出现重复 step_number，审计页排序错乱。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import json

import pytest

from app.core.context import AgentContext, set_agent_context

pytestmark = pytest.mark.no_infrastructure


class FakeCountingModel:
    model = "fake-model"

    async def count_tokens(self, *, messages, tools):
        roles = [getattr(message, "role", "") for message in messages or []]
        if not messages and tools:
            return 11
        if roles and all(role == "system" for role in roles):
            return 3
        if messages:
            return 7 if "system" not in roles else 21
        return 0


async def _invoke_middleware(middleware):
    """跑一次 on_model_call，返回所有写入调用 [(redis_key, record), ...]。"""

    async def next_handler(**kwargs):
        del kwargs
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=21, output_tokens=2),
            content=[],
        )

    scheduled = []
    with patch(
        "app.services.ai.runtime.agentscope.middleware._append_stat_to_redis",
        new=AsyncMock(),
    ) as append_stat, patch(
        "app.services.ai.runtime.agentscope.middleware.asyncio.ensure_future",
        side_effect=lambda coroutine: scheduled.append(coroutine),
    ):
        await middleware.on_model_call(
            agent=SimpleNamespace(),
            input_kwargs={
                "current_model": FakeCountingModel(),
                "messages": [SimpleNamespace(role="system")],
                "tools": [{"type": "function", "function": {"name": "search"}}],
            },
            next_handler=next_handler,
        )
        while scheduled:
            await scheduled.pop(0)
    return [call.args for call in append_stat.await_args_list]


@pytest.mark.asyncio
async def test_subagent_middleware_writes_into_parent_stats_key_with_tag():
    from app.services.ai.runtime.agentscope.middleware import ModelCallStatsMiddleware

    middleware = ModelCallStatsMiddleware(
        user_id="u1",
        conversation_id="child_session_subrun_1",
        agent_name="chat-bi",
        trace_id="sub_trace_1",
        stats_conversation_id="parent-conv",
        record_trace_id="parent-trace",
        subagent_meta={
            "agent_name": "chat-bi",
            "run_id": "subrun_1",
            "child_trace_id": "sub_trace_1",
            "parent_trace_id": "parent-trace",
            "child_session_id": "child_session_subrun_1",
        },
    )

    calls = await _invoke_middleware(middleware)

    # 双写：子代理自己的键（内部 session_status 读它）+ 父会话键（前端明细查它）。
    assert len(calls) == 2
    keys = [key for key, _ in calls]
    assert any(
        key.endswith(":u1:child_session_subrun_1:model_call_stats") for key in keys
    )
    assert any(key.endswith(":u1:parent-conv:model_call_stats") for key in keys)

    key, record = calls[-1]
    assert key.endswith(":u1:parent-conv:model_call_stats")
    assert record["conversation_id"] == "parent-conv"
    assert record["trace_id"] == "parent-trace"
    assert record["parent_trace_id"] == "parent-trace"
    assert record["child_session_id"] == "child_session_subrun_1"
    assert record["subagent"] == {
        "agent_name": "chat-bi",
        "run_id": "subrun_1",
        "child_trace_id": "sub_trace_1",
        "parent_trace_id": "parent-trace",
        "child_session_id": "child_session_subrun_1",
    }


@pytest.mark.asyncio
async def test_main_agent_middleware_keeps_own_key_without_subagent_tag():
    from app.services.ai.runtime.agentscope.middleware import ModelCallStatsMiddleware

    middleware = ModelCallStatsMiddleware(
        user_id="u1",
        conversation_id="parent-conv",
        agent_name="main",
        trace_id="parent-trace",
    )

    calls = await _invoke_middleware(middleware)

    # 主代理只写自己的键，不双写、不带子代理标记。
    assert len(calls) == 1
    key, record = calls[0]
    assert key.endswith(":u1:parent-conv:model_call_stats")
    assert record["conversation_id"] == "parent-conv"
    assert record["trace_id"] == "parent-trace"
    assert "subagent" not in record
    assert "child_session_id" not in record


def test_build_runtime_middlewares_routes_subagent_stats_to_parent():
    from app.services.ai.runtime.agentscope.agent_runtime import build_runtime_middlewares
    from app.services.ai.runtime.agentscope.middleware import ModelCallStatsMiddleware

    set_agent_context(
        AgentContext(
            agent_id="child-agent",
            agent_name="chat-bi",
            conversation_id="child_session_subrun_1",
            child_session_id="child_session_subrun_1",
            parent_conversation_id="parent-conv",
            parent_trace_id="parent-trace",
            delegation_depth=1,
            delegation_run_id="subrun_1",
            user_id=7,
        )
    )
    try:
        middlewares = build_runtime_middlewares(
            user_id=7,
            conversation_id="child_session_subrun_1",
            agent_name="chat-bi",
            trace_id="sub_trace_1",
        )
    finally:
        set_agent_context(None)

    stats = next(m for m in middlewares if isinstance(m, ModelCallStatsMiddleware))
    assert stats._stats_conversation_id == "parent-conv"
    assert stats._record_trace_id == "parent-trace"
    assert stats._subagent_meta == {
        "agent_name": "chat-bi",
        "run_id": "subrun_1",
        "child_trace_id": "sub_trace_1",
        "parent_trace_id": "parent-trace",
        "child_session_id": "child_session_subrun_1",
    }


def test_build_runtime_middlewares_keeps_main_agent_stats_on_own_conversation():
    from app.services.ai.runtime.agentscope.agent_runtime import build_runtime_middlewares
    from app.services.ai.runtime.agentscope.middleware import ModelCallStatsMiddleware

    set_agent_context(
        AgentContext(
            agent_id="main-agent",
            agent_name="main",
            conversation_id="parent-conv",
            parent_conversation_id=None,
            delegation_depth=0,
            user_id=7,
        )
    )
    try:
        middlewares = build_runtime_middlewares(
            user_id=7,
            conversation_id="parent-conv",
            agent_name="main",
            trace_id="parent-trace",
        )
    finally:
        set_agent_context(None)

    stats = next(m for m in middlewares if isinstance(m, ModelCallStatsMiddleware))
    assert stats._stats_conversation_id == "parent-conv"
    assert stats._record_trace_id == "parent-trace"
    assert stats._subagent_meta is None


class _FakeListRedis:
    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    async def lrange(self, key, start, end):
        self.calls.append((key, start, end))
        return list(self._rows)


class _SyncFakeListRedis:
    def __init__(self, rows):
        self._rows = rows

    def lrange(self, key, start, end):
        return list(self._rows)


@pytest.mark.asyncio
async def test_read_latest_stats_record_skips_subagent_tail():
    from app.services.ai.runtime.agentscope.middleware import read_latest_stats_record

    main_record = {"agent_name": "main", "input_tokens": 100}
    subagent_record = {
        "agent_name": "chat-bi",
        "input_tokens": 999,
        "subagent": {"run_id": "subrun_1"},
    }
    redis = _FakeListRedis(
        [json.dumps(main_record), json.dumps(subagent_record)]
    )

    skipped = await read_latest_stats_record(redis, "k", skip_subagent=True)
    latest = await read_latest_stats_record(redis, "k", skip_subagent=False)

    assert skipped == main_record
    assert latest == subagent_record
    assert all(end == -1 and start < 0 for _, start, end in redis.calls)


@pytest.mark.asyncio
async def test_read_latest_stats_record_works_with_sync_fake_redis():
    from app.services.ai.runtime.agentscope.middleware import read_latest_stats_record

    redis = _SyncFakeListRedis([{"agent_name": "main"}])
    assert await read_latest_stats_record(redis, "k") == {"agent_name": "main"}


def _build_step_executor(*, agent_name, trace_buffer, trace_id="t1"):
    from app.schemas.agent import ChatConfig
    from app.services.ai.executors.base import BaseExecutor

    class _StubExecutor(BaseExecutor):
        async def execute(self, history):
            del history
            if False:  # pragma: no cover - 让函数成为 async generator
                yield {}

    config = ChatConfig(
        agent_id=f"agent-{agent_name}",
        agent_name=agent_name,
        model_name="fake-model",
        temperature=0.0,
        system_prompt="",
        tools=[],
    )
    return _StubExecutor(
        config=config,
        trace_id=trace_id,
        trace_buffer=trace_buffer,
    )


def test_increment_step_is_monotonic_across_shared_trace_buffer():
    shared_buffer = []
    main_executor = _build_step_executor(agent_name="main", trace_buffer=shared_buffer)
    sub_executor = _build_step_executor(agent_name="chat-bi", trace_buffer=shared_buffer)

    main_executor.record_llm_token_usage(prompt_tokens=10, completion_tokens=2)
    sub_executor.record_llm_token_usage(prompt_tokens=20, completion_tokens=4)
    main_executor.record_llm_token_usage(prompt_tokens=30, completion_tokens=6)

    step_numbers = [step.step_number for step in shared_buffer]
    assert step_numbers == [1, 2, 3]
    assert len(set(step_numbers)) == len(step_numbers)
