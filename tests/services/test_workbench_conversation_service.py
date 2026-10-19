"""工作台「最近会话」：全智能体覆盖、会话去重与查询语义。"""

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
        "conversation_id": "conv-a",
        "agent_id": "agent-data",
        "query": "我有哪些技能",
        "status": "success",
        "created_at": datetime(2026, 10, 15, 10, 0, 0),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _agent(agent_id: str, display_name: str, name: str = "chat-bi"):
    return SimpleNamespace(id=agent_id, name=name, display_name=display_name, avatar_url=None)


def test_conversation_item_uses_agent_display_name_as_subtitle():
    from app.services.workbench_conversation_service import conversation_items_from_rows

    items = conversation_items_from_rows(
        [_row()], {"agent-data": ("chat-bi", "数据智能助手", None)}
    )

    assert len(items) == 1
    item = items[0]
    assert item["type"] == "conversation"
    assert item["title"] == "我有哪些技能"
    # 副标题只放智能体名：成功/失败状态由 WorkbenchItemMeta 徽标承载，不重复拼文案
    assert item["subtitle"] == "数据智能助手"
    assert "ChatBI" not in item["subtitle"]
    assert item["status"] == "success"
    assert item["action"] == "open_conversation"
    assert item["target"] == {"conversation_id": "conv-a", "agent_id": "agent-data"}


def test_conversation_items_skip_missing_ids_and_dedupe_by_conversation():
    from app.services.workbench_conversation_service import conversation_items_from_rows

    items = conversation_items_from_rows(
        [
            _row(id=3, conversation_id="conv-b", query="最新一轮"),
            _row(id=2, conversation_id="conv-a"),
            _row(id=1, conversation_id=None, query="没有会话 ID"),
            _row(id=4, conversation_id="conv-b", query="重复会话"),
        ],
        {"agent-data": ("chat-bi", "数据智能助手", None)},
    )

    assert [item["target"]["conversation_id"] for item in items] == ["conv-b", "conv-a"]
    # 同一会话只保留查询顺序里第一条代表行，不重复出卡
    assert items[0]["title"] == "最新一轮"


def test_conversation_title_flattens_whitespace_and_truncates():
    from app.services.workbench_conversation_service import conversation_title

    assert conversation_title("  【TaskCenter】\n\n 自动化  规则  ") == "【TaskCenter】 自动化 规则"
    assert conversation_title("") == "未命名会话"
    long_title = conversation_title("很长的提问" * 40)
    assert long_title.endswith("…")
    assert len(long_title) == 60


def test_conversation_item_falls_back_to_unknown_agent_label():
    from app.services.workbench_conversation_service import conversation_items_from_rows

    items = conversation_items_from_rows([_row(agent_id="deleted-agent")], {})

    assert items[0]["subtitle"] == "未知智能体"


@pytest.mark.asyncio
async def test_list_recent_conversations_scopes_to_user_and_chat_scope():
    """查询必须：按用户隔离、排除 task_conv_、按会话分组、只要非空会话 ID。"""
    from app.services.workbench_conversation_service import list_recent_conversations

    statements = []
    rows = [_row(id=7)]

    async def fake_execute(stmt, *args, **kwargs):
        statements.append(stmt)
        if len(statements) == 1:
            return _Result(rows)
        return _Result([_agent("agent-data", "数据智能助手")])

    db = SimpleNamespace(execute=fake_execute)

    items = await list_recent_conversations(db, user_id=7)

    sql = _compile(statements[0])
    assert "GROUP BY coalesce(ai_agent_execution_history.conversation_id, ai_agent_execution_history.trace_id)" in sql
    assert "ai_agent_execution_history.user_id = '7'" in sql
    assert "ai_agent_execution_history.conversation_id IS NOT NULL" in sql
    # 空串/纯空白也会占一个分组名额，必须在 SQL 层排除
    assert "trim(ai_agent_execution_history.conversation_id) != ''" in sql
    assert "NOT LIKE" in sql and "task_conv_" in sql
    # 不再按 data_query 能力过滤智能体：全部智能体的普通对话都要出现
    assert "data_query" not in sql
    assert "ai_agent_execution_history.agent_id IN" not in sql
    assert items[0]["subtitle"] == "数据智能助手"


@pytest.mark.asyncio
async def test_list_recent_conversations_returns_empty_without_querying_agents():
    from app.services.workbench_conversation_service import list_recent_conversations

    calls = []

    async def fake_execute(stmt, *args, **kwargs):
        calls.append(stmt)
        return _Result([])

    items = await list_recent_conversations(SimpleNamespace(execute=fake_execute), user_id=9)

    assert items == []
    assert len(calls) == 1  # 没有会话就不必再查智能体身份
