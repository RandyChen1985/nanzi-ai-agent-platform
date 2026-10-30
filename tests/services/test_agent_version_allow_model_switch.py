"""智能体版本「是否允许手动切换模型」后端契约与行为测试。

历史数据（未显式配置）必须按「允许」处理，因此：
- Schema / ORM 默认值恒为 True；
- 更新接口只有请求显式携带该字段时才写库，避免旧客户端把「禁止」改回「允许」；
- /agents/allowed 与 embed-access 附带的开关缺失时同样按 True 降级。
"""

import pytest


pytestmark = pytest.mark.no_infrastructure


def test_version_schema_defaults_to_allowing_manual_switch():
    from app.schemas.agent import AIAgentVersionBase, ChatConfig

    assert AIAgentVersionBase(system_prompt="x").allow_model_switch is True
    assert AIAgentVersionBase(system_prompt="x", allow_model_switch=False).allow_model_switch is False
    assert ChatConfig(
        agent_id="a",
        agent_name="a",
        model_name="m",
        temperature=0.0,
        system_prompt="x",
        tools=[],
    ).allow_model_switch is True
    assert ChatConfig(
        agent_id="a",
        agent_name="a",
        model_name="m",
        temperature=0.0,
        system_prompt="x",
        tools=[],
        allow_model_switch=False,
    ).allow_model_switch is False


def test_agent_version_model_column_is_not_nullable_and_defaults_true():
    model_source = open("app/models/agent.py", encoding="utf-8").read()

    assert "allow_model_switch = Column(Boolean, nullable=False, default=True)" in model_source


def test_version_update_only_writes_field_when_explicitly_sent():
    manager_source = open("app/services/ai/agent_manager.py", encoding="utf-8").read()

    assert '"allow_model_switch" in data.model_fields_set' in manager_source
    assert "version.allow_model_switch = bool(data.allow_model_switch)" in manager_source
    # 创建、复制、运行时配置解析三条链路都要透传
    assert manager_source.count("allow_model_switch=bool(getattr(") >= 4


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        return _FakeResult(self._rows)


class _FakeAgent:
    def __init__(self, agent_id):
        self.id = agent_id


async def test_attach_flags_uses_latest_published_version_and_defaults_to_true():
    from app.services.ai.agent_manager import AgentManagerService

    forbidding = _FakeAgent("forbidding")
    allowing = _FakeAgent("allowing")
    missing = _FakeAgent("missing")
    # 查询按版本号倒序返回：每个 agent 首次出现即最新 PUBLISHED 版本
    session = _FakeSession([
        ("forbidding", False),
        ("forbidding", True),
        ("allowing", True),
        ("allowing", None),
    ])

    await AgentManagerService.attach_published_model_switch_flags(
        session, [forbidding, allowing, missing]
    )

    assert forbidding.allow_model_switch is False
    assert allowing.allow_model_switch is True
    # 无已发布版本 → 按允许处理
    assert missing.allow_model_switch is True


async def test_attach_flags_failure_degrades_to_allowing():
    from app.services.ai.agent_manager import AgentManagerService

    class _BrokenSession:
        async def execute(self, _stmt):
            raise RuntimeError("boom")

    agent = _FakeAgent("broken")
    await AgentManagerService.attach_published_model_switch_flags(_BrokenSession(), [agent])

    assert agent.allow_model_switch is True


def test_allowed_and_embed_access_endpoints_attach_the_flag():
    route_source = open("app/api/portal/endpoints/agents.py", encoding="utf-8").read()
    manager_source = open("app/services/ai/agent_manager.py", encoding="utf-8").read()

    assert "attach_published_model_switch_flags" in route_source
    # /allowed 与 embed-access 两个只读接口都要补齐，避免前端逐条回查
    assert route_source.count("attach_published_model_switch_flags") >= 2
    # /allowed 现在链式附加两个字段（模型切换开关 + 绑定数据集数量），不再是单条 return
    assert "attach_published_model_switch_flags(session, agents)" in route_source
    # 内部调用者（自动路由候选、MCP 绑定等）不需要该字段，附加逻辑不放进列表查询本身
    assert "attach_published_model_switch_flags(session, agents)" not in manager_source


async def test_attached_flag_serializes_into_agent_response():
    """/agents/allowed 与 embed-access 直接返回 ORM 对象，附加属性必须能被响应模型读到。"""
    from datetime import datetime

    from app.models.agent import AIAgent
    from app.schemas.agent import AIAgentResponse
    from app.services.ai.agent_manager import AgentManagerService

    agent = AIAgent(
        id="agent-under-test",
        name="agent-under-test",
        display_name="Agent Under Test",
        agent_type="GENERAL",
        engine_type="LOCAL",
        onboarding_step="COMPLETE",
        is_system=False,
        is_enabled=True,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    await AgentManagerService.attach_published_model_switch_flags(
        _FakeSession([("agent-under-test", False)]), [agent]
    )

    assert AIAgentResponse.model_validate(agent).allow_model_switch is False

    # 无已发布版本时同样能序列化，且按「允许」返回
    fresh = AIAgent(
        id="agent-without-published-version",
        name="agent-without-published-version",
        display_name="No Published Version",
        agent_type="GENERAL",
        engine_type="LOCAL",
        onboarding_step="COMPLETE",
        is_system=False,
        is_enabled=True,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    await AgentManagerService.attach_published_model_switch_flags(_FakeSession([]), [fresh])
    assert AIAgentResponse.model_validate(fresh).allow_model_switch is True


def test_agent_response_without_attached_flag_still_validates():
    """未附加开关的接口（如管理页 list_agents）必须照常序列化，字段回退为 None。"""
    from datetime import datetime

    from app.models.agent import AIAgent
    from app.schemas.agent import AIAgentResponse

    agent = AIAgent(
        id="plain-agent",
        name="plain-agent",
        display_name="Plain Agent",
        agent_type="GENERAL",
        engine_type="LOCAL",
        onboarding_step="COMPLETE",
        is_system=False,
        is_enabled=True,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    assert AIAgentResponse.model_validate(agent).allow_model_switch is None
