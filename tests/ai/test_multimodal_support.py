from unittest.mock import AsyncMock

import pytest

from app.services.ai.agent_prompts import AgentServicePrompts
from app.services.ai.multimodal_support import (
    format_execution_error,
    format_vision_sidecar_block,
    history_contains_images,
    inject_vision_sidecar,
    is_multimodal_api_error,
    last_user_message_has_images,
    resolve_default_multimodal_model_name,
    run_multimodal_gate,
)


IMAGE_TURN = [
    {
        "role": "user",
        "content": "看看这张图",
        "files": [
            {
                "url": "/static/uploads/a.png",
                "filename": "a.png",
                "ext": "png",
            }
        ],
    }
]


def test_history_contains_images():
    history = [
        {
            "role": "user",
            "content": "看看这张图",
            "files": [
                {
                    "type": "local_file",
                    "url": "/app/data/uploads/a.png",
                    "filename": "a.png",
                    "ext": ".png",
                }
            ],
        }
    ]
    assert history_contains_images(history) is True
    assert history_contains_images([{"role": "user", "content": "hi"}]) is False


def test_last_user_message_has_images_ignores_history():
    history = [
        {
            "role": "user",
            "content": "old image",
            "files": [{"url": "/static/uploads/a.png", "filename": "a.png", "ext": "png"}],
        },
        {"role": "assistant", "content": "failed"},
        {"role": "user", "content": "follow up text only"},
    ]
    assert history_contains_images(history) is True
    assert last_user_message_has_images(history) is False


def test_is_multimodal_api_error():
    raw = (
        "Error code: 400 - {'error': {'message': "
        "'DeepSeek-V4-Flash is not a multimodal model', 'type': 'BadRequestError'}}"
    )
    assert is_multimodal_api_error(raw) is True
    assert is_multimodal_api_error("connection timeout") is False


def test_format_execution_error_multimodal():
    raw = "'DeepSeek-V4-Flash' is not a multimodal model"
    msg = format_execution_error(raw)
    assert "不支持图片理解" in msg
    assert "DeepSeek-V4-Flash" in msg
    assert "[系统错误]" not in msg


def test_format_execution_error_context_window():
    raw = """Error code: 400 - {'error': {'message': "Requested token count exceeds the model's maximum context length of 65536 tokens. You requested a total of 67298 tokens: 50914 tokens from the input messages and 16384 tokens for the completion. Please reduce the number of tokens in the input messages or the completion to fit within the limit.", 'type': 'bad_response_status_code'}}"""

    msg = format_execution_error(raw)

    assert "输入内容过长" in msg
    assert "65,536" in msg
    assert "50,914" in msg
    assert "16,384" in msg
    assert "67,298" in msg
    assert "1,762" in msg
    assert "请减少输入内容，或分批发送" in msg
    assert "Requested token count exceeds" not in msg


def test_format_execution_error_generic():
    msg = format_execution_error("connection reset")
    assert "[系统错误]" in msg
    assert "connection reset" in msg


def test_multimodal_sidecar_notice_starts_as_standalone_markdown_blockquote():
    notice = AgentServicePrompts.multimodal_sidecar_notice("text-model", "qwen-vl")

    combined = "整理今日热搜汇总报告" + notice

    assert "整理今日热搜汇总报告\n\n> ℹ️" in combined


def test_inject_vision_sidecar_is_idempotent():
    message = {"role": "user", "content": "看图"}
    first = inject_vision_sidecar(message, "qwen-vl", "图上写着 42")
    second = inject_vision_sidecar(message, "qwen-vl", "不应再次写入")
    assert first == second
    assert "图上写着 42" in first
    assert "不应再次写入" not in first
    assert format_vision_sidecar_block("qwen-vl", "图上写着 42") in first
    assert "\n\n---\n\n" in first


@pytest.mark.asyncio
async def test_resolve_default_multimodal_model_name(monkeypatch):
    async def fake_get(key, default=None):
        return "qwen-vl" if key == "multimodal_model_name" else default

    async def fake_supports(name):
        return name == "qwen-vl"

    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_get)
    monkeypatch.setattr(
        "app.services.ai.multimodal_support.model_supports_multimodal",
        fake_supports,
    )
    assert await resolve_default_multimodal_model_name() == "qwen-vl"

    async def fake_get_empty(key, default=None):
        return ""

    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_get_empty)
    assert await resolve_default_multimodal_model_name() is None


@pytest.mark.asyncio
async def test_run_multimodal_gate_skips_when_model_supports_vision(monkeypatch):
    async def fake_supports(_name):
        return True

    monkeypatch.setattr(
        "app.services.ai.multimodal_support.model_supports_multimodal",
        fake_supports,
    )
    events = [chunk async for chunk in run_multimodal_gate(list(IMAGE_TURN), "qwen-vl")]
    assert events == []


@pytest.mark.asyncio
async def test_run_multimodal_gate_degrades_when_default_unconfigured(monkeypatch):
    async def fake_supports(_name):
        return False

    async def fake_default():
        return None

    monkeypatch.setattr(
        "app.services.ai.multimodal_support.model_supports_multimodal",
        fake_supports,
    )
    monkeypatch.setattr(
        "app.services.ai.multimodal_support.resolve_default_multimodal_model_name",
        fake_default,
    )
    events = [chunk async for chunk in run_multimodal_gate(list(IMAGE_TURN), "text-model")]
    assert len(events) == 1
    assert events[0]["status"] == "error"
    assert "不支持图片理解" in events[0]["content"]
    assert "默认多模态模型" in events[0]["content"]


@pytest.mark.asyncio
async def test_run_multimodal_gate_injects_sidecar_and_notice(monkeypatch):
    history = [
        {
            "role": "user",
            "content": "看看这张图",
            "files": [{"url": "/static/uploads/a.png", "filename": "a.png", "ext": "png"}],
        }
    ]

    async def fake_supports(_name):
        return False

    async def fake_default():
        return "qwen-vl"

    async def fake_describe(last_user, vision_model):
        assert last_user["content"] == "看看这张图"
        assert vision_model == "qwen-vl"
        return "图上写着销售额 42"

    persist = AsyncMock()
    monkeypatch.setattr(
        "app.services.ai.multimodal_support.model_supports_multimodal",
        fake_supports,
    )
    monkeypatch.setattr(
        "app.services.ai.multimodal_support.resolve_default_multimodal_model_name",
        fake_default,
    )
    monkeypatch.setattr(
        "app.services.ai.multimodal_support.describe_images_with_vision_model",
        fake_describe,
    )
    monkeypatch.setattr(
        "app.services.ai.multimodal_support._persist_vision_sidecar",
        persist,
    )

    events = [
        chunk
        async for chunk in run_multimodal_gate(
            history,
            "text-model",
            user_id="u1",
            conversation_id="c1",
        )
    ]
    assert [event.get("status") for event in events] == ["pending", "success", "success"]
    assert events[0]["type"] == "log"
    assert events[1]["id"] == events[0]["id"]
    assert "已自动使用系统默认多模态模型" in events[-1]["content"]
    assert "qwen-vl" in events[-1]["content"]
    assert "<vision_sidecar" in history[0]["content"]
    assert "销售额 42" in history[0]["content"]
    persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_multimodal_gate_degrades_when_vision_call_fails(monkeypatch):
    history = [
        {
            "role": "user",
            "content": "看看这张图",
            "files": [{"url": "/static/uploads/a.png", "filename": "a.png", "ext": "png"}],
        }
    ]

    async def fake_supports(_name):
        return False

    async def fake_default():
        return "qwen-vl"

    async def fake_describe(_last_user, _vision_model):
        raise RuntimeError("upstream 400")

    monkeypatch.setattr(
        "app.services.ai.multimodal_support.model_supports_multimodal",
        fake_supports,
    )
    monkeypatch.setattr(
        "app.services.ai.multimodal_support.resolve_default_multimodal_model_name",
        fake_default,
    )
    monkeypatch.setattr(
        "app.services.ai.multimodal_support.describe_images_with_vision_model",
        fake_describe,
    )

    events = [chunk async for chunk in run_multimodal_gate(history, "text-model")]
    assert events[-1]["status"] == "error"
    assert "图片自动解析失败" in events[-1]["content"]
    assert "qwen-vl" in events[-1]["content"]
    assert "<vision_sidecar" not in history[0]["content"]


@pytest.mark.asyncio
async def test_run_multimodal_gate_skips_when_sidecar_already_present(monkeypatch):
    history = [
        {
            "role": "user",
            "content": "看图\n\n<vision_sidecar model=\"qwen-vl\">已解析</vision_sidecar>",
            "files": [{"url": "/static/uploads/a.png", "filename": "a.png", "ext": "png"}],
        }
    ]
    describe = AsyncMock(side_effect=AssertionError("must not re-parse"))
    monkeypatch.setattr(
        "app.services.ai.multimodal_support.describe_images_with_vision_model",
        describe,
    )
    events = [chunk async for chunk in run_multimodal_gate(history, "text-model")]
    assert events == []
    describe.assert_not_called()


# —— 闸门判据必须覆盖「所有会收到这批图片的模型」 ——
#
# 用户报过一次「选了能识图的合成模型，发图却没反应」：闸门只按合成模型判定，
# 放行后图片被直发给不识图的主链路模型。下面三条锁住新口径。


def test_resolve_gate_model_names_includes_primary_and_synthesis():
    from types import SimpleNamespace

    from app.services.ai.multimodal_support import resolve_gate_model_names

    assert resolve_gate_model_names(
        SimpleNamespace(model_name="text-primary", synthesis_model_name="vision-synth")
    ) == ["text-primary", "vision-synth"]


def test_resolve_gate_model_names_prefers_session_override_for_primary():
    from types import SimpleNamespace

    from app.core.context import set_execution_options_context
    from app.services.ai.multimodal_support import resolve_gate_model_names

    set_execution_options_context({"model": "picked-by-user"})
    try:
        names = resolve_gate_model_names(
            SimpleNamespace(model_name="version-default", synthesis_model_name=None)
        )
    finally:
        set_execution_options_context({})

    assert names == ["picked-by-user"]


def test_resolve_gate_model_names_dedupes_and_skips_blank():
    from types import SimpleNamespace

    from app.services.ai.multimodal_support import resolve_gate_model_names

    assert resolve_gate_model_names(
        SimpleNamespace(model_name="same", synthesis_model_name=" same ")
    ) == ["same"]
    assert resolve_gate_model_names(
        SimpleNamespace(model_name=None, synthesis_model_name="")
    ) == []


@pytest.mark.asyncio
async def test_run_multimodal_gate_triggers_when_only_synthesis_model_lacks_vision(monkeypatch):
    """主链路能识图、合成模型不能：图片仍不能原样发出去（合成阶段会看到它）。"""

    async def fake_supports(name):
        return name == "vision-primary"

    async def fake_default_vision():
        return "qwen-vl"

    monkeypatch.setattr(
        "app.services.ai.multimodal_support.model_supports_multimodal", fake_supports
    )
    monkeypatch.setattr(
        "app.services.ai.multimodal_support.resolve_default_multimodal_model_name",
        fake_default_vision,
    )
    monkeypatch.setattr(
        "app.services.ai.multimodal_support.describe_images_with_vision_model",
        AsyncMock(return_value="一张图"),
    )
    monkeypatch.setattr(
        "app.services.ai.multimodal_support._persist_vision_sidecar", AsyncMock()
    )

    events = [
        chunk
        async for chunk in run_multimodal_gate(
            list(IMAGE_TURN), ["vision-primary", "text-synthesis"]
        )
    ]

    assert any("text-synthesis" in str(event.get("details", "")) for event in events), (
        "应点名不支持识图的那个模型，而不是先撞上的主链路模型"
    )
