"""聊天首页近 3 天推荐接口契约。"""

from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.no_infrastructure


def _request(user_id: str = "7"):
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": user_id}))


@pytest.mark.asyncio
async def test_home_context_returns_compact_recent_items(monkeypatch):
    from app.api.v1.endpoints import chat
    from app.services import workbench_conversation_service

    async def fake_recent(db, *, user_id, limit, since):
        assert user_id == 7
        assert limit == 1
        assert since is None
        return [{
            "title": "机房 PUE 异常排查",
            "subtitle": "数据智能助手",
            "occurred_at": "2026-11-06T14:20:00",
            "target": {"conversation_id": "conv-pue", "agent_id": "agent-data"},
        }]

    monkeypatch.setattr(workbench_conversation_service, "list_recent_conversations", fake_recent)
    result = await chat.get_home_context(days=0, limit=1, request=_request(), db=SimpleNamespace())

    assert result["count"] == 1
    assert result["items"][0]["conversation_id"] == "conv-pue"
    assert result["items"][0]["title"] == "机房 PUE 异常排查"
    assert "target" not in result["items"][0]


@pytest.mark.asyncio
async def test_home_recommendations_short_circuits_without_context(monkeypatch):
    from app.api.v1.endpoints import chat
    from app.services.ai import home_recommendation_service as service

    async def empty_context(db, *, user_id, days, limit):
        assert user_id == "7"
        assert days == 3
        assert limit == 5
        return []

    monkeypatch.setattr(service, "load_recent_conversation_context", empty_context)
    result = await chat.generate_home_recommendations_endpoint(
        payload=chat.HomeRecommendationRequest(variation="seed", days=3),
        request=_request(),
        db=SimpleNamespace(),
    )

    assert result == {"cards": [], "source_count": 0}


@pytest.mark.asyncio
async def test_home_recommendations_returns_cards_and_source_count(monkeypatch):
    from app.api.v1.endpoints import chat
    from app.services.ai import home_recommendation_service as service

    context = [{"title": "机房 PUE 异常排查"}]

    async def fake_context(db, *, user_id, days, limit):
        return context

    async def fake_generate(items, *, variation_seed, days):
        assert items == context
        assert variation_seed == "seed"
        assert days == 3
        return [{
            "icon": "chart",
            "title": "继续对比 PUE",
            "subtitle": "从今天的排查继续。",
            "prompt": "对比 A/B 机房最近一周 PUE。",
            "source": "来自「机房 PUE 异常排查」",
        }]

    monkeypatch.setattr(service, "load_recent_conversation_context", fake_context)
    monkeypatch.setattr(service, "generate_home_recommendations", fake_generate)
    result = await chat.generate_home_recommendations_endpoint(
        payload=chat.HomeRecommendationRequest(variation="seed", days=3),
        request=_request(),
        db=SimpleNamespace(),
    )

    assert result["source_count"] == 1
    assert result["cards"][0]["title"] == "继续对比 PUE"
