import pytest


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_context_usage_endpoint_returns_shared_estimate_and_effective_sandbox_runtime(monkeypatch):
    from app.api.v1.endpoints.chat import get_conversation_context_usage

    expected = {
        "estimated_current_tokens": 0,
        "estimated_remaining_tokens": 40960,
        "context_messages": 0,
        "token_budget": 40960,
        "physical_window": 65536,
        "history_budget": 40960,
        "completion_reserve_tokens": 16384,
        "request_input_budget": 49152,
        "usage_percentage": 0.0,
    }

    async def fake_estimate_context_usage(**kwargs):
        assert kwargs["user_id"] == "user-1"
        assert kwargs["conversation_id"] == "conversation-1"
        assert kwargs["empty_history_is_zero"] is True
        # 浮标是唯一的校准开启点（展示层）
        assert kwargs["calibrate"] is True
        return expected

    async def fake_config_get(key, default=None):
        assert key == "sandbox_policy"
        assert default == "local"
        return "docker"

    monkeypatch.setattr(
        "app.api.v1.endpoints.chat.estimate_context_usage",
        fake_estimate_context_usage,
    )
    monkeypatch.setattr("app.api.v1.endpoints.chat.get_env", lambda: "docker")
    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_config_get)

    response = await get_conversation_context_usage(
        conversation_id="conversation-1",
        model_id=None,
        user_info={"user_id": "user-1"},
        db=None,
    )

    assert response.data == {
        **expected,
        "sandbox_policy": "docker",
        "sandbox_runtime_env": "docker",
        # 端点现在同时回传沙箱预热开关与空闲超时（docker 策略下的默认值）。
        "sandbox_auto_warm": True,
        "sandbox_idle_minutes": None,
    }
