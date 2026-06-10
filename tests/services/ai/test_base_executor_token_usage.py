from types import SimpleNamespace

from app.schemas.agent import ChatConfig
from app.services.ai.executors.base import BaseExecutor


class _StubExecutor(BaseExecutor):
    async def execute(self, history):
        yield {}


def test_record_agent_scope_model_call_appends_model_call_step():
    config = ChatConfig(
        agent_id="id",
        agent_name="DataAgent",
        model_name="test-model",
        temperature=0.0,
        system_prompt="sys",
        tools=[],
    )
    runner = _StubExecutor(config=config, trace_id="trace-1", trace_buffer=[])
    event = SimpleNamespace(
        reply_id="reply-1",
        input_tokens=93952,
        output_tokens=1286,
        model_name="qwen-test",
    )
    state = {"model_call_started_at": {"reply-1": 0.0}}

    runner._record_agent_scope_model_call(event, state=state, native_model=SimpleNamespace(model="fallback-model"))

    assert len(runner.trace_buffer) == 1
    step = runner.trace_buffer[0]
    assert step.event_type == "model_call"
    assert step.prompt_tokens == 93952
    assert step.completion_tokens == 1286
    assert step.total_tokens == 95238


def test_record_llm_token_usage_skips_zero_usage():
    config = ChatConfig(
        agent_id="id",
        agent_name="DataAgent",
        model_name="test-model",
        temperature=0.0,
        system_prompt="sys",
        tools=[],
    )
    runner = _StubExecutor(config=config, trace_id="trace-2", trace_buffer=[])
    runner.record_llm_token_usage(prompt_tokens=0, completion_tokens=0)
    assert runner.trace_buffer == []
