"""LLM 请求超时配置的回归测试。

关注两点：
1. 配置解析/校验的边界行为（非法值回退默认，管理端保存时拒绝越界）；
2. 超时**确实注入到了 SDK 客户端**，且覆盖了 OpenAI SDK 默认的 600s。
"""

import openai
import pytest

from app.services.ai.runtime.agentscope.models import (
    AgentScopeModelConfig,
    create_openai_chat_model,
)
from app.services.ai.runtime.agentscope.request_timeout import (
    DEFAULT_LLM_REQUEST_CONNECT_TIMEOUT,
    DEFAULT_LLM_REQUEST_POOL_TIMEOUT,
    DEFAULT_LLM_REQUEST_READ_TIMEOUT,
    DEFAULT_LLM_REQUEST_WRITE_TIMEOUT,
    LLM_REQUEST_READ_TIMEOUT_KEY,
    MAX_LLM_REQUEST_READ_TIMEOUT,
    MIN_LLM_REQUEST_READ_TIMEOUT,
    build_llm_request_timeout,
    load_llm_request_read_timeout,
    parse_llm_request_read_timeout,
    validate_llm_request_read_timeout,
)

pytestmark = pytest.mark.no_infrastructure


def _config(**overrides) -> AgentScopeModelConfig:
    payload = {
        "api_key": "test-key",
        "base_url": "https://example.invalid/v1",
        "model": "test-model",
    }
    payload.update(overrides)
    return AgentScopeModelConfig(**payload)


# --------------------------------------------------------------------------
# 解析与校验
# --------------------------------------------------------------------------


def test_parse_returns_default_for_missing_value():
    assert parse_llm_request_read_timeout(None) == DEFAULT_LLM_REQUEST_READ_TIMEOUT
    assert parse_llm_request_read_timeout("") == DEFAULT_LLM_REQUEST_READ_TIMEOUT


@pytest.mark.parametrize("value", ["abc", True, False, [], {}, "NaN", "inf"])
def test_parse_falls_back_for_non_numeric_values(value):
    assert parse_llm_request_read_timeout(value) == DEFAULT_LLM_REQUEST_READ_TIMEOUT


@pytest.mark.parametrize(
    "value",
    [
        str(MIN_LLM_REQUEST_READ_TIMEOUT - 1),
        str(MAX_LLM_REQUEST_READ_TIMEOUT + 1),
        "0",
        "-30",
    ],
)
def test_parse_falls_back_for_out_of_range_values(value):
    """越界一律回退默认值，避免把模型调用改成必然超时。"""
    assert parse_llm_request_read_timeout(value) == DEFAULT_LLM_REQUEST_READ_TIMEOUT


@pytest.mark.parametrize("value", ["30", "60", "180", "300", 120.5])
def test_parse_accepts_values_within_range(value):
    assert parse_llm_request_read_timeout(value) == float(value)


@pytest.mark.parametrize("value", ["29", "301", "0", "-1", "abc", True, ""])
def test_validate_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        validate_llm_request_read_timeout(value)


@pytest.mark.parametrize("value", ["30", "180", "300"])
def test_validate_accepts_boundary_values(value):
    validate_llm_request_read_timeout(value)


# --------------------------------------------------------------------------
# Timeout 构造
# --------------------------------------------------------------------------


def test_build_timeout_sets_all_four_fields():
    """httpx 的 Timeout 要求四项显式给全，否则会抛 ValueError。"""
    timeout = build_llm_request_timeout(120)
    assert timeout.connect == DEFAULT_LLM_REQUEST_CONNECT_TIMEOUT
    assert timeout.read == 120.0
    assert timeout.write == DEFAULT_LLM_REQUEST_WRITE_TIMEOUT
    assert timeout.pool == DEFAULT_LLM_REQUEST_POOL_TIMEOUT


def test_build_timeout_without_argument_uses_platform_default():
    assert build_llm_request_timeout().read == DEFAULT_LLM_REQUEST_READ_TIMEOUT


# --------------------------------------------------------------------------
# 配置读取入口
# --------------------------------------------------------------------------


async def test_load_read_timeout_returns_configured_value(monkeypatch):
    from app.services import config_service

    async def fake_get(key, default=None):
        return "90" if key == LLM_REQUEST_READ_TIMEOUT_KEY else default

    monkeypatch.setattr(config_service.ConfigService, "get", staticmethod(fake_get))

    assert await load_llm_request_read_timeout() == 90.0


async def test_load_read_timeout_falls_back_when_config_read_fails(monkeypatch):
    """配置读取异常不得阻断模型调用，应回退到平台默认值。"""
    from app.services import config_service

    async def boom(key, default=None):
        raise RuntimeError("config backend unavailable")

    monkeypatch.setattr(config_service.ConfigService, "get", staticmethod(boom))

    assert await load_llm_request_read_timeout() == DEFAULT_LLM_REQUEST_READ_TIMEOUT


# --------------------------------------------------------------------------
# 注入到真实 SDK 客户端
# --------------------------------------------------------------------------


async def test_model_client_receives_configured_read_timeout():
    model = create_openai_chat_model(_config(read_timeout=90.0))
    try:
        assert model.client.timeout.read == 90.0
        assert model.client.timeout.connect == DEFAULT_LLM_REQUEST_CONNECT_TIMEOUT
    finally:
        await model.client.close()


async def test_model_client_overrides_sdk_default_timeout():
    """未显式配置时也必须落到平台默认值，而不是 SDK 的 600s。"""
    model = create_openai_chat_model(_config())
    try:
        assert model.client.timeout.read == DEFAULT_LLM_REQUEST_READ_TIMEOUT
        assert model.client.timeout.read != openai.DEFAULT_TIMEOUT.read
    finally:
        await model.client.close()


async def test_model_client_sdk_retries_still_disabled():
    """timeout 的注入不能影响「SDK 重试关闭」这一既有约定。"""
    model = create_openai_chat_model(_config(read_timeout=120.0))
    try:
        assert model.max_retries == 3
        assert model.client.max_retries == 0
    finally:
        await model.client.close()


# --------------------------------------------------------------------------
# 主链路贯通：AgentConfigProvider.get_configured_llm
# --------------------------------------------------------------------------


def _patch_config_provider(monkeypatch, llm_config, captured):
    from types import SimpleNamespace

    from app.services.ai import config as ai_config

    async def fake_get_all_from_db():
        return llm_config

    async def fake_resolve_runtime_model_info(config=None, model_override=None):
        return SimpleNamespace(
            effective_model_id="configured-model",
            api_key=None,
            base_url=None,
            provider=None,
            context_size=None,
            max_output_tokens=None,
            thinking_enable=False,
            thinking_capable=False,
            reasoning_effort=None,
        )

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(
        ai_config.ConfigService, "get_all_from_db", staticmethod(fake_get_all_from_db)
    )
    monkeypatch.setattr(
        ai_config, "resolve_runtime_model_info", fake_resolve_runtime_model_info
    )
    monkeypatch.setattr(ai_config, "get_llm", fake_get_llm)


async def test_configured_llm_forwards_configured_read_timeout(monkeypatch):
    """Agent 对话主路径必须把库里的 read timeout 透传给模型工厂。"""
    captured = {}
    _patch_config_provider(
        monkeypatch,
        {
            "llm_api_key": {"value": "configured-key"},
            "llm_base_url": {"value": "https://configured.example/v1"},
            "llm_request_read_timeout": {"value": "120"},
        },
        captured,
    )

    from app.services.ai.config import AgentConfigProvider

    await AgentConfigProvider.get_configured_llm(streaming=False)

    assert captured["read_timeout"] == 120.0


async def test_configured_llm_falls_back_when_read_timeout_missing(monkeypatch):
    """迁移未执行 / 配置缺失时，主路径也必须落到平台默认值而非 SDK 的 600s。"""
    captured = {}
    _patch_config_provider(
        monkeypatch,
        {"llm_api_key": {"value": "configured-key"}},
        captured,
    )

    from app.services.ai.config import AgentConfigProvider

    await AgentConfigProvider.get_configured_llm(streaming=False)

    assert captured["read_timeout"] == DEFAULT_LLM_REQUEST_READ_TIMEOUT
