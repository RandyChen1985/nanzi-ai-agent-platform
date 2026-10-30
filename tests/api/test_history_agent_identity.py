"""历史消息智能体署名：三级回落与「不套可见性过滤」的契约。

背景：`ai_agent_execution_history` 原本只落 `agent_id`，展示名靠 `/chat/history`
实时查 `ai_agents` 回填。而那次查询走 `AgentManagerService.list_agents`，本身带着
可见性过滤——非管理员只能看到自己创建的智能体：

    if not is_admin and agent.created_by != username:
        continue

于是普通用户打开「主助手」的历史会话时，`agent_map` 里根本没有主助手，三个身份
字段全被设成 None，前端 `msg.agentName || msg.agentDisplayName` 为假，胶囊走
`opacity-0` 分支——界面上「谁回答的」那一条直接变成空白，而头像因为回落到全局
默认形象，看起来一切正常。实测同一份数据：管理员查到 `('main', '主助手(Main)')`，
普通用户查到 `None`。

修复是三级回落，越接近「当时」的越优先：

    行内快照（V201/V101 之后写入的行）
      → Redis 快照（存量行，写入时 memory_service 存了名字）
        → ai_agents 直查（不套可见性）

这里盯的就是这套优先级和最后那一跳不受权限影响。
"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints.chat import _resolve_agent_identity
from app.services.ai.agent_manager import load_agent_identity_map

ROOT = Path(__file__).resolve().parents[2]


def _item(**kwargs) -> SimpleNamespace:
    base = {
        "agent_id": "sys-agent-chat",
        "trace_id": "trace-1",
        "agent_name": None,
        "agent_display_name": None,
        "agent_avatar_url": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------- 优先级


def test_embedded_snapshot_wins_over_everything():
    """新写入的行自带快照，即使智能体后来改名也不该被覆盖。"""

    item = _item(agent_name="main", agent_display_name="主助手(Main)")
    agent_map = {"sys-agent-chat": ("main", "改过的新名字", "/new.svg")}
    redis_identity = {"trace-1": {"agent_name": "main", "agent_display_name": "更早的名字"}}

    out = _resolve_agent_identity(item, agent_map, redis_identity)

    assert out["agent_display_name"] == "主助手(Main)"
    # 头像没进快照列，仍然要实时查，否则藏了头像再显示回来会丢图
    assert out["agent_avatar_url"] == "/new.svg"


def test_redis_snapshot_beats_current_agent_name():
    """存量行没有快照列，Redis 里那份是写入时刻的，比「现在叫什么」更接近事实。"""

    item = _item()
    agent_map = {"sys-agent-chat": ("main", "改过的新名字", "/a.svg")}
    redis_identity = {"trace-1": {"agent_name": "main", "agent_display_name": "主助手(Main)"}}

    out = _resolve_agent_identity(item, agent_map, redis_identity)

    assert out["agent_display_name"] == "主助手(Main)"
    assert out["agent_avatar_url"] == "/a.svg"


def test_falls_back_to_agents_table_when_redis_has_no_entry():
    item = _item()
    agent_map = {"sys-agent-chat": ("main", "主助手(Main)", "/a.svg")}

    out = _resolve_agent_identity(item, agent_map, {})

    assert out["agent_name"] == "main"
    assert out["agent_display_name"] == "主助手(Main)"
    assert out["agent_avatar_url"] == "/a.svg"


def test_redis_missing_only_display_keeps_name_from_agents_table():
    """Redis 那条消息可能只存了其中一个字段，不能整体覆盖成空。"""

    item = _item()
    agent_map = {"sys-agent-chat": ("main", None, None)}
    redis_identity = {"trace-1": {"agent_name": None, "agent_display_name": "主助手(Main)"}}

    out = _resolve_agent_identity(item, agent_map, redis_identity)

    assert out["agent_name"] == "main"
    assert out["agent_display_name"] == "主助手(Main)"


def test_all_sources_empty_yields_none_instead_of_crashing():
    """三级全空（智能体已删除 + 存量行 + Redis 过期）时给 None，前端走兜底文案。"""

    out = _resolve_agent_identity(_item(agent_id="ghost"), {}, {})

    assert out == {
        "agent_name": None,
        "agent_display_name": None,
        "agent_avatar_url": None,
    }


# ---------------------------------------------------------------- 不套可见性


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """只记录 SQL，不连库。"""

    def __init__(self, rows):
        self._rows = rows
        self.statements: list[str] = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        return _FakeResult(self._rows)


@pytest.mark.asyncio
async def test_identity_lookup_does_not_filter_by_creator():
    """这条是本次修复的核心：按 id 直查，不允许再带上 created_by 过滤。

    旧实现走 `list_agents`，普通用户会被 `created_by != username` 过滤掉别人创建的
    智能体——主助手的 created_by 是 admin，于是普通用户看不到它的名字。
    """

    session = _FakeSession([("sys-agent-chat", "main", "主助手(Main)", "/a.svg")])

    identity = await load_agent_identity_map(session, ["sys-agent-chat"])

    assert identity == {"sys-agent-chat": ("main", "主助手(Main)", "/a.svg")}
    sql = " ".join(session.statements).lower()
    assert "created_by" not in sql, "身份回填不得按创建者过滤，否则历史署名会随权限消失"
    assert "is_enabled" not in sql, "停用的智能体也要能显示历史署名"


@pytest.mark.asyncio
async def test_identity_lookup_short_circuits_on_empty_ids():
    session = _FakeSession([])

    assert await load_agent_identity_map(session, [None, "", "  "]) == {}
    assert session.statements == [], "没有 id 时不该发查询"


# ---------------------------------------------------------------- 写入侧


def test_save_history_accepts_identity_snapshot():
    """写入侧必须能收下快照，否则新数据仍然只有 agent_id。"""

    from app.services.ai.audit import AuditManager

    params = inspect.signature(AuditManager.save_history).parameters

    assert "agent_name" in params
    assert "agent_display_name" in params


def test_history_model_declares_snapshot_columns():
    from app.models.audit import AgentExecutionHistory

    columns = AgentExecutionHistory.__table__.columns

    assert "agent_name" in columns
    assert "agent_display_name" in columns
    # 存量行没有快照，必须允许 NULL，否则迁移会卡在已有数据上
    assert columns["agent_name"].nullable is True
    assert columns["agent_display_name"].nullable is True


def test_both_dialect_migrations_exist_and_match():
    """两套迁移必须成对出现：MySQL 用 db-prod/，PG 用 db-prod-pg/，编号各自自增。"""

    mysql = ROOT / "db-prod/V201-add-history-agent-snapshot.sql"
    pg = ROOT / "db-prod-pg/V101-add-history-agent-snapshot.sql"

    assert mysql.exists()
    assert pg.exists()
    for statement in (mysql.read_text(encoding="utf-8"), pg.read_text(encoding="utf-8")):
        assert "agent_name" in statement
        assert "agent_display_name" in statement
        assert "ai_agent_execution_history" in statement
