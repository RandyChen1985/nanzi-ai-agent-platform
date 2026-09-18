import json

import pytest


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_context_usage_service_returns_budget_for_an_empty_conversation(monkeypatch):
    from app.services.ai.context_usage import estimate_context_usage

    async def fake_get_history(user_id, conversation_id, limit=None, offset=0):
        assert user_id == "user-1"
        assert conversation_id == "conversation-1"
        return []

    async def fake_config_get(key, default=None):
        return {
            "agent_context_max_tokens": "65536",
        }.get(key, default)

    monkeypatch.setattr(
        "app.services.ai.context_usage.memory_service.get_history",
        fake_get_history,
    )
    monkeypatch.setattr(
        "app.services.ai.context_usage.ConfigService.get",
        fake_config_get,
    )

    usage = await estimate_context_usage(
        user_id="user-1",
        conversation_id="conversation-1",
        runtime_model_info={
            "source": "runtime_override",
            "context_size": 65536,
            "completion_reserve_tokens": 16384,
            "prompt_overhead_reservation_tokens": 8192,
        },
        empty_history_is_zero=True,
    )

    assert usage["estimated_current_tokens"] == 0
    assert usage["context_messages"] == 0
    assert usage["physical_window"] == 65536
    assert usage["history_budget"] == 40960
    assert usage["request_input_budget"] == 49152
    assert usage["usage_percentage"] == 0.0


@pytest.mark.asyncio
async def test_context_usage_aggregates_session_breakdown_without_repeating_runtime_overhead(monkeypatch):
    from app.services.ai.context_usage import estimate_context_usage

    async def fake_get_history(user_id, conversation_id, limit=None, offset=0):
        del user_id, conversation_id, limit, offset
        return [{"role": "user", "content": "hello"}]

    async def fake_config_get(key, default=None):
        return {
            "agent_context_max_tokens": "100",
        }.get(key, default)

    # overhead 现在是模块常量（不是系统配置项）：这里按需覆盖它，让预算算术确定。
    monkeypatch.setattr(
        "app.services.ai.context_usage.CONTEXT_OVERHEAD_RESERVATION_TOKENS", 20
    )

    class FakeRedis:
        async def lrange(self, key, start, end):
            assert key.endswith(":user-1:conversation-1:model_call_stats")
            assert (start, end) == (-1, -1)
            return [
                json.dumps(
                    {
                        "context_breakdown": {
                            "system_prompt_tokens": 3,
                            "tools_tokens": 11,
                            "conversation_tokens": 7,
                            "total_tokens": 21,
                            "estimated": True,
                            "source": "agentscope_count_tokens",
                        }
                    }
                )
            ]

    async def fake_get_redis():
        return FakeRedis()

    monkeypatch.setattr(
        "app.services.ai.context_usage.memory_service.get_history",
        fake_get_history,
    )
    monkeypatch.setattr(
        "app.services.ai.context_usage.ConfigService.get",
        fake_config_get,
    )
    monkeypatch.setattr(
        "app.services.ai.context_usage.get_redis",
        fake_get_redis,
    )
    monkeypatch.setattr(
        "app.services.ai.context_usage.estimate_text_tokens",
        lambda value: 40 if value == "hello" else 0,
    )

    usage = await estimate_context_usage(
        user_id="user-1",
        conversation_id="conversation-1",
        runtime_model_info={
            "source": "runtime_override",
            "context_size": 100,
            "completion_reserve_tokens": 20,
        },
    )

    assert usage["estimated_current_tokens"] == 54
    assert usage["estimated_remaining_tokens"] == 26
    assert usage["usage_percentage"] == 67.5
    assert usage["context_breakdown"] == {
        "system_prompt_tokens": 3,
        "tools_tokens": 11,
        "conversation_tokens": 40,
        "total_tokens": 54,
        "estimated": True,
        "source": "session_history_plus_latest_runtime_context",
    }


@pytest.mark.asyncio
async def test_context_usage_reads_effective_history_after_manual_compaction(monkeypatch):
    from app.services.ai.context_usage import estimate_context_usage

    async def fail_get_history(*args, **kwargs):
        raise AssertionError("统计不应直接读取未压缩的原始历史")

    async def fake_get_effective_history(user_id, conversation_id):
        assert (user_id, conversation_id) == ("user-1", "conversation-1")
        return [{"role": "user", "content": "压缩后的内容"}]

    async def fake_config_get(key, default=None):
        return {
            "agent_context_max_tokens": "100",
        }.get(key, default)

    # overhead 现在是模块常量（不是系统配置项）：这里按需覆盖它，让预算算术确定。
    monkeypatch.setattr(
        "app.services.ai.context_usage.CONTEXT_OVERHEAD_RESERVATION_TOKENS", 20
    )

    monkeypatch.setattr(
        "app.services.ai.context_usage.memory_service.get_history",
        fail_get_history,
    )
    monkeypatch.setattr(
        "app.services.ai.context_usage.memory_service.get_effective_context_history",
        fake_get_effective_history,
    )
    monkeypatch.setattr(
        "app.services.ai.context_usage.ConfigService.get",
        fake_config_get,
    )
    monkeypatch.setattr(
        "app.services.ai.context_usage.estimate_text_tokens",
        lambda value: 12 if value == "压缩后的内容" else 0,
    )

    usage = await estimate_context_usage(
        user_id="user-1",
        conversation_id="conversation-1",
        runtime_model_info={"context_size": 100, "completion_reserve_tokens": 20},
    )

    assert usage["estimated_current_tokens"] == 12
    assert usage["context_messages"] == 1
