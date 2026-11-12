"""「分配给我」列表的口径与只读边界（不依赖真实 DB / Redis）。

过滤发生在 SQL 里，假 session 不会真的筛行，所以：
- 可见性口径用「编译后的 SQL 片段」断言（created_by / is_enabled / 授权 id 集合）；
- 只读三字段与管理员短路用假 session 里预置的行断言（这两段在 Python 侧）。
"""

from types import SimpleNamespace

import pytest

from app.services.ai.agent_manager import AgentManagerService

pytestmark = pytest.mark.no_infrastructure


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    """只记录语句、返回固定行；不解析 SQL。"""

    def __init__(self, rows):
        self._rows = rows
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _FakeResult(self._rows)


class _FakePerms:
    def __init__(self, agents):
        self.permissions = SimpleNamespace(agents=agents)


def _agent(**overrides):
    base = dict(
        id="a-chatbi",
        name="chatbi",
        display_name="ChatBI 数据智能助手",
        description="用自然语言查数据",
        is_system=True,
        is_enabled=True,
        created_by="zhangqiming",
        is_editable=True,
        can_edit_versions=True,
        can_publish_version=True,
        sort_order=0,
        is_expert_team_lead=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _patch_perms(monkeypatch, agents):
    """把权限服务换成固定集合，避免连库。"""

    class _FakePermissionService:
        def __init__(self, _db):
            pass

        async def get_user_permissions(self, _uid):
            return _FakePerms(agents)

    monkeypatch.setattr(
        "app.services.permission_service.PermissionService", _FakePermissionService
    )


async def test_admin_gets_empty_list(monkeypatch):
    """管理员已有「全部智能体」，这里返回空，避免重复视图。"""
    _patch_perms(monkeypatch, ["a-chatbi"])
    session = _FakeSession([_agent()])
    result = await AgentManagerService.list_assigned_agents(
        session, user={"role": "admin", "user_name": "admin", "user_id": 1}
    )
    assert result == []
    assert session.statements == []


async def test_no_granted_agents_returns_empty_without_query(monkeypatch):
    """没有任何智能体授权时不查库（避免每次进页面白跑一条 SQL）。"""
    _patch_perms(monkeypatch, [])
    session = _FakeSession([])
    result = await AgentManagerService.list_assigned_agents(
        session, user={"role": "user", "user_name": "chenxiaolong", "user_id": 7}
    )
    assert result == []
    assert session.statements == []


async def test_visibility_clause_excludes_self_and_disabled(monkeypatch):
    """口径：授权集合 + 启用 + 非自己创建（含 created_by 为空的历史数据）。"""
    _patch_perms(monkeypatch, ["a-chatbi", "a-contract"])
    session = _FakeSession([])
    await AgentManagerService.list_assigned_agents(
        session, user={"role": "user", "user_name": "chenxiaolong", "user_id": 7}
    )
    assert len(session.statements) == 1
    sql = str(session.statements[0].compile(compile_kwargs={"literal_binds": True}))
    # 四个口径同时生效。is_system 这条尤其不能少：`_user_can_execute_agent` 对
    # 非系统级智能体只认 created_by == 自己，授权行即使存在也不生效；而写入侧
    # 不校验 is_system，脏授权行确实可能存在 —— 少这条就会列出点进去 403 的死链。
    assert "ai_agents.is_system = true" in sql
    assert "ai_agents.is_enabled = true" in sql
    assert "ai_agents.created_by IS NULL" in sql
    assert "ai_agents.created_by != 'chenxiaolong'" in sql
    # 授权 id 集合真的进了 IN
    assert "a-chatbi" in sql and "a-contract" in sql


async def test_returns_readonly_flags(monkeypatch):
    """三个只读字段由服务端定死——AIAgentResponse.is_editable 默认 True。"""
    _patch_perms(monkeypatch, ["a-chatbi"])
    session = _FakeSession([_agent()])
    result = await AgentManagerService.list_assigned_agents(
        session, user={"role": "user", "user_name": "chenxiaolong", "user_id": 7}
    )
    assert len(result) == 1
    agent = result[0]
    assert agent.is_editable is False
    assert agent.can_edit_versions is False
    assert agent.can_publish_version is False


async def test_non_numeric_user_id_degrades_to_empty(monkeypatch):
    """user_id 不是数字（历史脏数据）时按无授权处理，不抛异常。"""
    session = _FakeSession([_agent()])
    result = await AgentManagerService.list_assigned_agents(
        session, user={"role": "user", "user_name": "chenxiaolong", "user_id": "not-a-number"}
    )
    assert result == []
    assert session.statements == []


def test_assigned_route_registered_before_agent_id_routes():
    """`/assigned` 必须注册在 `/{agent_id}` 之前。

    FastAPI 按注册顺序匹配：放在后面的话 `GET /agents/assigned` 会被
    `/{agent_id}` 系列当成 agent_id="assigned" 吃掉。本仓库在 `/shares/*`
    与 `transfer-owner/candidates` 上已经踩过两次。
    """
    from pathlib import Path

    src = Path("app/api/portal/endpoints/agents.py").read_text(encoding="utf-8")
    assert '@router.get("/assigned"' in src or '"/assigned",' in src
    assert src.index('"/assigned"') < src.index('"/{agent_id}/embed-access"')


async def test_missing_username_returns_empty(monkeypatch):
    """身份里没有 user_name 时不返回任何行。

    `created_by != ''` 会把「别人创建的历史数据」全算成「不是我创建的」，
    虽然仍受授权集合约束，但没有依据就不要给出结果。
    """
    _patch_perms(monkeypatch, ["a-chatbi"])
    session = _FakeSession([_agent()])
    result = await AgentManagerService.list_assigned_agents(
        session, user={"role": "user", "user_name": "", "user_id": 7}
    )
    assert result == []
    assert session.statements == []
