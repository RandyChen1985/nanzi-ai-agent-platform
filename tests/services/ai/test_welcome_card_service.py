from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.welcome_card_service import (
    get_runtime_welcome_cards,
    generate_welcome_cards,
    normalize_welcome_config,
    safe_welcome_config,
)


pytestmark = pytest.mark.no_infrastructure


def test_enabled_welcome_config_keeps_three_clickable_cards():
    config = normalize_welcome_config({
        "enabled": True,
        "mode": "manual",
        "generation_requirement": "面向园区能耗运营人员",
        "cards": [
            {"icon": "chart", "title": "查看能耗趋势", "subtitle": "分析近 30 天 PUE 变化", "prompt": "查看近 30 天 PUE 趋势"},
            {"icon": "alert", "title": "排查异常", "subtitle": "定位今日异常能耗", "prompt": "今天有哪些能耗异常？"},
            {"icon": "report", "title": "生成日报", "subtitle": "汇总今日核心指标", "prompt": "生成今日能耗运营日报"},
        ],
    })

    assert config["enabled"] is True
    assert config["mode"] == "manual"
    assert len(config["cards"]) == 3
    assert config["cards"][0]["prompt"] == "查看近 30 天 PUE 趋势"


def test_enabled_welcome_config_rejects_incomplete_cards():
    with pytest.raises(ValueError, match="3 张"):
        normalize_welcome_config({"enabled": True, "mode": "manual", "cards": []})


def test_disabled_welcome_config_falls_back_to_static_capabilities():
    assert normalize_welcome_config(None) == {
        "enabled": False,
        "mode": "manual",
        "generation_requirement": "",
        "cards": [],
    }


def test_invalid_persisted_welcome_config_falls_back_to_static_capabilities():
    assert safe_welcome_config({"enabled": True, "cards": []})["enabled"] is False


def test_ai_welcome_config_does_not_require_or_persist_fixed_cards():
    config = normalize_welcome_config({
        "enabled": True,
        "mode": "ai",
        "generation_requirement": "面向园区运营人员",
        "cards": [{"icon": "chat", "title": "旧卡片", "subtitle": "旧说明", "prompt": "旧问题"}],
    })

    assert config["enabled"] is True
    assert config["mode"] == "ai"
    assert config["cards"] == []


@pytest.mark.asyncio
async def test_runtime_ai_welcome_cards_are_cached_for_five_minutes():
    class FakeRedis:
        def __init__(self):
            self.values = {}
            self.ttl = None

        async def get(self, key):
            return self.values.get(key)

        async def set(self, key, value, ex=None):
            self.values[key] = value
            self.ttl = ex

    config = MagicMock(
        agent_id="agent-1",
        agent_version="v2",
        agent_name="energy-agent",
        agent_display_name="能耗助手",
        system_prompt="分析园区能耗",
        engine_type="LOCAL",
        welcome_config={"enabled": True, "mode": "ai", "generation_requirement": "面向运营人员", "cards": []},
    )
    redis = FakeRedis()
    generated = [
        {"icon": "chart", "title": "趋势", "subtitle": "查看趋势", "prompt": "查看最近趋势"},
        {"icon": "alert", "title": "异常", "subtitle": "排查异常", "prompt": "有哪些异常？"},
        {"icon": "report", "title": "报告", "subtitle": "生成报告", "prompt": "生成日报"},
    ]

    with patch("app.services.ai.welcome_card_service.get_redis", new=AsyncMock(return_value=redis)), patch(
        "app.services.ai.welcome_card_service.generate_welcome_cards", new=AsyncMock(return_value=generated)
    ) as generate:
        assert await get_runtime_welcome_cards(config) == generated
        assert await get_runtime_welcome_cards(config) == generated

    generate.assert_awaited_once()
    assert redis.ttl == 300


@pytest.mark.asyncio
async def test_generate_welcome_cards_returns_three_valid_cards():
    client = MagicMock()
    client.generate_text = AsyncMock(return_value='''
    {"cards":[
      {"icon":"chart","title":"查看趋势","subtitle":"分析最近指标变化","prompt":"查看最近 30 天的趋势"},
      {"icon":"alert","title":"排查异常","subtitle":"定位需要关注的问题","prompt":"今天有哪些异常需要处理？"},
      {"icon":"report","title":"生成报告","subtitle":"汇总当前业务结果","prompt":"生成今天的业务摘要"}
    ]}
    ''')

    with patch("app.services.ai.welcome_card_service.AgentConfigProvider.get_configured_llm", new=AsyncMock(return_value=object())), patch(
        "app.services.ai.welcome_card_service.chat_client_from_handle", return_value=client
    ):
        cards = await generate_welcome_cards(
            name="energy-agent",
            display_name="能耗助手",
            description="分析园区能耗",
            system_prompt="帮助运营人员分析能耗",
            generation_requirement="面向运营人员",
        )

    assert len(cards) == 3
    assert cards[0]["title"] == "查看趋势"
    assert cards[1]["prompt"] == "今天有哪些异常需要处理？"
