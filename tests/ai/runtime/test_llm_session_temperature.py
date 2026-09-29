"""`get_llm_async` 也必须认会话级温度覆盖。

同一个输入框温度开关在主链路（`AgentConfigProvider.get_configured_llm`）生效，
但在 `get_llm_async` 这条底层通道上此前被完全忽略，
于是「同一个开关在不同链路行为不一致」。
"""

from unittest.mock import AsyncMock

import pytest

from app.core.context import set_debug_context

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture(autouse=True)
def _clean_debug_context():
    set_debug_context({})
    yield
    set_debug_context({})


def _patch_config(monkeypatch, values: dict) -> None:
    from app.core.llm import client as client_module

    async def fake_get(key):
        return values.get(key)

    monkeypatch.setattr(
        client_module.ConfigServiceProxy, "get", staticmethod(fake_get)
    )
    monkeypatch.setattr(
        client_module, "_lookup_ai_model_record", AsyncMock(return_value=None)
    )


@pytest.mark.asyncio
async def test_get_llm_async_uses_session_temperature_override(monkeypatch):
    from app.core.llm import client as client_module

    _patch_config(
        monkeypatch,
        {
            "llm_model_name": "deepseek-chat",
            "llm_api_key": "sk-test",
            "llm_base_url": "https://llm.example.com/v1",
            "llm_temperature": "0.0",
        },
    )
    set_debug_context({"temperature": 0.3})

    handle = await client_module.get_llm_async(streaming=False)

    assert handle.temperature == 0.3


@pytest.mark.asyncio
async def test_get_llm_async_keeps_explicit_temperature_over_session_override(monkeypatch):
    from app.core.llm import client as client_module

    _patch_config(
        monkeypatch,
        {
            "llm_model_name": "deepseek-chat",
            "llm_api_key": "sk-test",
            "llm_base_url": "https://llm.example.com/v1",
            "llm_temperature": "0.0",
        },
    )
    set_debug_context({"temperature": 0.3})

    handle = await client_module.get_llm_async(streaming=False, temperature=0.9)

    assert handle.temperature == 0.9


@pytest.mark.asyncio
async def test_get_llm_async_falls_back_to_database_temperature_without_override(monkeypatch):
    from app.core.llm import client as client_module

    _patch_config(
        monkeypatch,
        {
            "llm_model_name": "deepseek-chat",
            "llm_api_key": "sk-test",
            "llm_base_url": "https://llm.example.com/v1",
            "llm_temperature": "0.4",
        },
    )

    handle = await client_module.get_llm_async(streaming=False)

    assert handle.temperature == 0.4


@pytest.mark.asyncio
async def test_get_llm_async_can_opt_out_of_session_temperature(monkeypatch):
    """内部链路（意图识别等）不该被用户的采样温度开关波及。"""
    from app.core.llm import client as client_module

    _patch_config(
        monkeypatch,
        {
            "llm_model_name": "deepseek-chat",
            "llm_api_key": "sk-test",
            "llm_base_url": "https://llm.example.com/v1",
            "llm_temperature": "0.4",
        },
    )
    set_debug_context({"temperature": 0.3})

    handle = await client_module.get_llm_async(
        streaming=False, ignore_session_temperature=True
    )

    assert handle.temperature == 0.4

