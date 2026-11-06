"""欢迎页“近 3 天会话推荐”的上下文与卡片归一化契约。"""

from datetime import datetime
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.no_infrastructure


def _compile(stmt) -> str:
    raw = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    return " ".join(raw.split())


class _Result:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


def _row(**overrides):
    values = {
        "id": 101,
        "conversation_id": "conv-pue",
        "agent_id": "agent-data",
        "query": "帮我看看机房 PUE 最近一周的变化",
        "summary": "A 机房在周二晚间 PUE 异常升高，建议先检查冷却。",
        "created_at": datetime(2026, 11, 6, 14, 20, 0),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_recent_context_dedupes_conversations_and_keeps_latest_turn():
    from app.services.ai.home_recommendation_service import recent_context_from_rows

    items = recent_context_from_rows([
        _row(id=3, conversation_id="conv-b", query="最新一轮"),
        _row(id=2, conversation_id="conv-a"),
        _row(id=1, conversation_id=""),
        _row(id=4, conversation_id="conv-b", query="重复会话"),
    ])

    assert [item["conversation_id"] for item in items] == ["conv-b", "conv-a"]
    assert items[0]["title"] == "最新一轮"
    assert items[1]["title"].startswith("帮我看看机房 PUE")


def test_normalize_recommendations_maps_source_index_to_conversation_title():
    from app.services.ai.home_recommendation_service import normalize_home_recommendation_cards

    context = [
        {"title": "机房 PUE 异常排查"},
        {"title": "2026Q3 运维月报.md"},
    ]
    cards = normalize_home_recommendation_cards([
        {
            "icon": "chart",
            "title": "继续对比 PUE",
            "subtitle": "从今天的排查继续。",
            "prompt": "对比 A/B 机房最近一周 PUE。",
            "source_index": 1,
        },
        {
            "icon": "workspace",
            "title": "把告警转成待办",
            "subtitle": "月报里还有未闭环告警。",
            "prompt": "把月报里的告警整理成待办。",
            "source_index": 2,
        },
        {
            "icon": "not-a-real-icon",
            "title": "整理今日文件",
            "subtitle": "整理今天的工作文件。",
            "prompt": "整理今天的工作文件。",
            "source_index": 99,
        },
    ], context)

    assert [card["title"] for card in cards] == ["继续对比 PUE", "把告警转成待办", "整理今日文件"]
    assert cards[0]["source"] == "来自「机房 PUE 异常排查」"
    assert cards[1]["source"] == "来自「2026Q3 运维月报.md」"
    assert cards[2]["icon"] == "chat"
    assert cards[2]["source"] == "综合最近会话"


def test_normalize_recommendations_requires_exactly_three_cards():
    from app.services.ai.home_recommendation_service import normalize_home_recommendation_cards

    with pytest.raises(ValueError):
        normalize_home_recommendation_cards([
            {"icon": "chart", "title": "只有一张", "subtitle": "不够", "prompt": "继续", "source_index": 1}
        ], [{"title": "会话"}])


@pytest.mark.asyncio
async def test_load_recent_context_scopes_to_user_two_days_and_chat_scope():
    from app.services.ai.home_recommendation_service import load_recent_conversation_context

    statements = []

    async def fake_execute(stmt, *args, **kwargs):
        statements.append(stmt)
        return _Result([_row(id=7)])

    db = SimpleNamespace(execute=fake_execute)
    items = await load_recent_conversation_context(db, user_id="7", days=3, limit=5)

    sql = _compile(statements[0])
    assert "ai_agent_execution_history.user_id = '7'" in sql
    assert "ai_agent_execution_history.created_at >=" in sql
    assert "GROUP BY coalesce(ai_agent_execution_history.conversation_id, ai_agent_execution_history.trace_id)" in sql
    assert "ai_agent_execution_history.conversation_id IS NOT NULL" in sql
    assert "trim(ai_agent_execution_history.conversation_id) != ''" in sql
    assert "NOT LIKE" in sql and "task_conv_" in sql
    assert items[0]["conversation_id"] == "conv-pue"


@pytest.mark.asyncio
async def test_generate_recommendations_short_circuits_without_context():
    from app.services.ai.home_recommendation_service import generate_home_recommendations

    assert await generate_home_recommendations([]) == []
