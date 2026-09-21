"""物理标识符（`ai_agents.name`）重名预检。

背景：用户在「新建智能体」时撞名，只拿到一条提交后的 400，无法在输入阶段发现。
预检端点必须与创建路径共用同一判定，否则会出现「预检说可用、创建却失败」。
"""

import pytest

from app.api.portal.endpoints import agents as agents_endpoint
from app.models.agent import AIAgent
from app.services.ai.agent_manager import AgentManagerService


pytestmark = pytest.mark.no_infrastructure


class FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return FakeScalars(self._rows)


class FakeSession:
    """只支持本用例需要的 select(AIAgent).where(...) 查询。"""

    def __init__(self, agents):
        self.agents = list(agents)

    async def execute(self, statement):
        params = statement.compile().params
        rows = list(self.agents)
        wanted_name = None
        excluded_id = None
        # SQLAlchemy 会把 where 条件值按顺序放进 params；这里按语义挑出目标值。
        for key, value in params.items():
            if key.startswith("name_"):
                wanted_name = value
            elif key.startswith("id_"):
                excluded_id = value
        if wanted_name is not None:
            rows = [agent for agent in rows if agent.name == wanted_name]
        if excluded_id is not None:
            rows = [agent for agent in rows if agent.id != excluded_id]
        return FakeResult(rows)


def _agent(agent_id, name, created_by="alice"):
    return AIAgent(id=agent_id, name=name, display_name=f"显示名-{name}", created_by=created_by)


@pytest.mark.asyncio
async def test_available_name_returns_available():
    session = FakeSession([_agent("a1", "sales-agent")])

    payload = await agents_endpoint.check_agent_name_availability(
        name="brand-new-agent", session=session, user={"user_name": "bob", "role": "user"}
    )

    data = payload["data"]
    assert data["available"] is True
    assert data["reason"] is None and data["message"] is None


@pytest.mark.asyncio
async def test_taken_name_is_reported_before_submitting():
    session = FakeSession([_agent("a1", "sales-agent", created_by="alice")])

    payload = await agents_endpoint.check_agent_name_availability(
        name="sales-agent", session=session, user={"user_name": "bob", "role": "user"}
    )

    data = payload["data"]
    assert data["available"] is False
    assert data["reason"] == "taken"
    # 别人占用时提示要说明「全局唯一、可能是其他人的」
    assert "已被占用" in data["message"] and "其他成员" in data["message"]


@pytest.mark.asyncio
async def test_taken_by_self_gets_actionable_hint():
    session = FakeSession([_agent("a1", "sales-agent", created_by="alice")])

    payload = await agents_endpoint.check_agent_name_availability(
        name="sales-agent", session=session, user={"user_name": "alice", "role": "user"}
    )

    assert "已被你自己创建的智能体占用" in payload["data"]["message"]


@pytest.mark.asyncio
async def test_name_check_ignores_surrounding_whitespace():
    """标识符首尾空白必须与创建路径同一口径，否则预检会漏判。"""
    session = FakeSession([_agent("a1", "sales-agent")])

    payload = await agents_endpoint.check_agent_name_availability(
        name="  sales-agent  ", session=session, user={"user_name": "bob", "role": "user"}
    )

    assert payload["data"]["name"] == "sales-agent"
    assert payload["data"]["available"] is False


@pytest.mark.asyncio
async def test_exclude_agent_id_allows_keeping_own_name():
    """改自己名字以外的字段时，不应该把自己的标识符判成冲突。"""
    session = FakeSession([_agent("a1", "sales-agent")])

    payload = await agents_endpoint.check_agent_name_availability(
        name="sales-agent",
        exclude_agent_id="a1",
        session=session,
        user={"user_name": "alice", "role": "user"},
    )

    assert payload["data"]["available"] is True


@pytest.mark.asyncio
async def test_empty_and_overlong_names_are_rejected_without_querying():
    # 空名：直接判不可用，且不查库
    empty = await agents_endpoint.check_agent_name_availability(
        name="   ", session=FakeSession([]), user={"user_name": "bob", "role": "user"}
    )
    assert empty["data"]["reason"] == "empty"

    # 超长：与 ai_agents.name 的 String(100) 对齐，提前拦住避免落库报错
    overlong = await agents_endpoint.check_agent_name_availability(
        name="x" * (agents_endpoint.AGENT_NAME_MAX_LENGTH + 1),
        session=FakeSession([]),
        user={"user_name": "bob", "role": "user"},
    )
    assert overlong["data"]["reason"] == "too_long"
    assert str(agents_endpoint.AGENT_NAME_MAX_LENGTH) in overlong["data"]["message"]


def test_conflict_detail_message_is_chinese_and_says_which_field():
    """创建路径的 400 文案必须是中文并指明「物理标识符」，不再暴露英文原文。"""
    from app.services.ai.agent_manager import _agent_name_conflict_detail

    detail = _agent_name_conflict_detail("  sales-agent ")
    assert "物理标识符" in detail
    assert "sales-agent" in detail
    assert "already exists" not in detail


@pytest.mark.asyncio
async def test_find_agent_name_conflict_shares_one_rule():
    session = FakeSession([_agent("a1", "sales-agent")])

    assert (await AgentManagerService.find_agent_name_conflict(session, "sales-agent")) is not None
    assert (await AgentManagerService.find_agent_name_conflict(session, " sales-agent ")) is not None
    assert (await AgentManagerService.find_agent_name_conflict(session, "other")) is None
    # 空名不视为冲突，避免把「未填写」当成重名
    assert (await AgentManagerService.find_agent_name_conflict(session, "   ")) is None
    # 排除自身后不再冲突
    assert (
        await AgentManagerService.find_agent_name_conflict(session, "sales-agent", exclude_agent_id="a1")
    ) is None
