"""委派给子代理时要继承「本轮的运行参数」。

用户诉求（原话）：「委派子代理不会透传 model temperature，也改成透传」。

语义依据：输入框模型切换面板里的模型 / 温度 / 思考开关 / 推理强度是**用户为这一轮
选定的运行参数**，不是某个智能体的版本配置。此前只透传了数据集范围与无人值守标记，
于是主链路用 gpt-oss-120b、子代理仍退回自己的版本模型——卡片与「大模型调用明细」
两边说的不是一回事。这里锁住整组透传，并覆盖零值陷阱（温度 0.0 / 关思考 False）。
"""

from types import SimpleNamespace

import pytest

from app.services.ai.tools import agent_delegate_tool as delegation

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture
def captured_options(monkeypatch):
    """截获传给 AgentDispatcher.dispatch 的 execution_options。"""
    captured: dict = {}

    async def fake_dispatch(target_config, query, sub_history, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        "app.services.ai.dispatcher.AgentDispatcher.dispatch", fake_dispatch
    )
    return captured


async def _dispatch(parent_options):
    await delegation._dispatch_sub_agent_executor(
        SimpleNamespace(agent_id="sub-agent"),
        "帮我查一下",
        [],
        trace_id="trace-1",
        trace_buffer=[],
        permission_options={},
        user_info=None,
        conversation_id="conv-child",
        turn_decision=SimpleNamespace(),
        parent_execution_options=parent_options,
    )


@pytest.mark.asyncio
async def test_inherits_model_and_sampling_overrides(captured_options):
    await _dispatch(
        {
            "model": "gpt-oss-120b",
            "temperature": 0.3,
            "thinking_enable": True,
            "reasoning_effort": "high",
        }
    )

    options = captured_options["execution_options"]
    assert options["model"] == "gpt-oss-120b"
    assert options["temperature"] == 0.3
    assert options["thinking_enable"] is True
    assert options["reasoning_effort"] == "high"
    assert options["delegated_sub_agent"] is True


@pytest.mark.asyncio
async def test_inherits_falsy_overrides(captured_options):
    """温度 0.0 与「关思考」False 都是合法覆盖，不能被真假值判断吞掉。"""
    await _dispatch({"temperature": 0.0, "thinking_enable": False})

    options = captured_options["execution_options"]
    assert options["temperature"] == 0.0
    assert options["thinking_enable"] is False


@pytest.mark.asyncio
async def test_absent_overrides_are_not_forwarded(captured_options):
    await _dispatch({})

    options = captured_options["execution_options"]
    for key in ("model", "temperature", "thinking_enable", "reasoning_effort"):
        assert key not in options


@pytest.mark.asyncio
async def test_blank_model_override_is_not_forwarded(captured_options):
    await _dispatch({"model": "   ", "temperature": 0.5})

    options = captured_options["execution_options"]
    assert "model" not in options
    assert options["temperature"] == 0.5


@pytest.mark.asyncio
async def test_model_override_is_normalized(captured_options):
    await _dispatch({"model": "  gpt-oss-120b  "})

    assert captured_options["execution_options"]["model"] == "gpt-oss-120b"


@pytest.mark.asyncio
async def test_existing_inherited_keys_still_work(captured_options):
    """数据集范围与无人值守标记的既有透传不能被这次改动挤掉。"""
    scope = {"knowledge_bases": [{"id": "kb-1", "name": "知识库"}]}
    await _dispatch(
        {
            "metadata_dataset_scope": scope,
            "unattended": True,
            "model": "gpt-oss-120b",
        }
    )

    options = captured_options["execution_options"]
    assert options["metadata_dataset_scope"] == scope
    assert options["unattended"] is True
    assert options["model"] == "gpt-oss-120b"


@pytest.mark.asyncio
async def test_unrelated_parent_options_are_not_forwarded(captured_options):
    """只透传运行参数：其余请求级选项（注入上下文、浏览器会话等）不跟着走。"""
    await _dispatch(
        {
            "model": "gpt-oss-120b",
            "injected_context": {"k": "v"},
            "browser_session_id": "bs-1",
            "grounding_enabled": True,
            "ignore_ltm": True,
        }
    )

    options = captured_options["execution_options"]
    for key in ("injected_context", "browser_session_id", "grounding_enabled", "ignore_ltm"):
        assert key not in options


def test_inherited_keys_are_declared_in_one_place():
    assert delegation._INHERITED_RUNTIME_OPTION_KEYS == (
        "model",
        "temperature",
        "thinking_enable",
        "reasoning_effort",
    )
