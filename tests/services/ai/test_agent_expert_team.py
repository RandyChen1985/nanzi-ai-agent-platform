"""专家团：主专家标记、成员派生的口径。

核心约定：**成员不落库**，取的是已发布版本 sub_agent_call 的调度范围。
所以「团里有几个人」和「调度实际能调几个」天然是同一份数据，不会出现两套真相。

另一个必须守住的区分：``team_member_count`` 为 ``None`` 表示**未配置调度范围**
（此时全部有权限的系统级都能被调），不等于 0 个成员。把两者混成一个数，列表页
就会把「没设范围」说成「团是空的」。
"""
import uuid

import pytest

from app.core.orm import AsyncSessionLocal
from app.models.agent import AIAgent, AIAgentVersion
from app.schemas.agent import AIAgentBase, ChatConfig
from app.services.ai.agent_manager import AgentManagerService


def _agent(*, name: str, created_by: str, is_expert_team_lead: bool, team_profile=None) -> AIAgent:
    return AIAgent(
        id=str(uuid.uuid4()),
        name=name,
        display_name=f"{name}-display",
        description="职责描述",
        created_by=created_by,
        is_system=False,
        is_enabled=True,
        engine_type="LOCAL",
        is_expert_team_lead=is_expert_team_lead,
        team_profile=team_profile,
    )


def _published_version(*, agent_id: str, tools) -> AIAgentVersion:
    return AIAgentVersion(
        id=str(uuid.uuid4()),
        agent_id=agent_id,
        version_number=1,
        system_prompt="你是专家团主专家",
        tools=tools,
        status="PUBLISHED",
    )


def test_resolve_team_members_distinguishes_unconfigured_from_empty():
    from types import SimpleNamespace

    assert AgentManagerService._resolve_team_members(None) is None
    # 没装委派工具 → 未配置，不是「0 个成员」
    assert AgentManagerService._resolve_team_members(SimpleNamespace(tools=["execute_sql_query"])) is None
    # 显式清空 → 0 个成员（团确实是空的，要求补成员）
    assert AgentManagerService._resolve_team_members(
        SimpleNamespace(tools=[{"name": "sub_agent_call", "delegable_agent_ids": []}])
    ) == []
    assert AgentManagerService._resolve_team_members(
        SimpleNamespace(tools=[{"name": "sub_agent_call", "delegable_agent_ids": ["a", "b", "c"]}])
    ) == ["a", "b", "c"]
    # 存量：历史上只配在批量委派上也照样生效（名单只有一份，batch 字段兜底读取）
    assert AgentManagerService._resolve_team_members(
        SimpleNamespace(tools=[{"name": "sub_agent_batch_call", "delegable_agent_ids": ["x"]}])
    ) == ["x"]


async def test_list_agents_fills_team_fields_for_lead_only():
    suffix = uuid.uuid4().hex[:8]
    lead = _agent(
        name=f"team-lead-{suffix}",
        created_by="alice",
        is_expert_team_lead=True,
        team_profile={"name": "数据分析专家团", "intro": "从取数到口径的一条龙团队"},
    )
    plain = _agent(name=f"plain-{suffix}", created_by="alice", is_expert_team_lead=False)

    async with AsyncSessionLocal() as session:
        session.add_all([lead, plain])
        await session.commit()
        session.add(
            _published_version(
                agent_id=lead.id,
                tools=[{"name": "sub_agent_call", "delegable_agent_ids": ["m1", "m2"]}],
            )
        )
        await session.commit()

    try:
        async with AsyncSessionLocal() as session:
            agents = await AgentManagerService.list_agents(session, user={"role": "admin"})
        by_id = {agent.id: agent for agent in agents}

        lead_row = by_id[lead.id]
        assert lead_row.team_name == "数据分析专家团"
        assert lead_row.team_member_count == 2
        assert lead_row.team_member_ids == ["m1", "m2"]

        # 非团主专家不带这些字段：列表页据此不显示「专家团」标记
        plain_row = by_id[plain.id]
        assert plain_row.team_name is None
        assert plain_row.team_member_count is None
        assert plain_row.team_member_ids is None
    finally:
        async with AsyncSessionLocal() as session:
            await session.delete(await session.get(AIAgent, lead.id))
            await session.delete(await session.get(AIAgent, plain.id))
            await session.commit()


async def test_team_name_falls_back_to_display_name_and_unconfigured_scope_is_null():
    suffix = uuid.uuid4().hex[:8]
    # 开了标记但没写团名、也没配调度范围
    lead = _agent(name=f"bare-lead-{suffix}", created_by="alice", is_expert_team_lead=True)

    async with AsyncSessionLocal() as session:
        session.add(lead)
        await session.commit()
        session.add(_published_version(agent_id=lead.id, tools=["execute_sql_query"]))
        await session.commit()

    try:
        async with AsyncSessionLocal() as session:
            agents = await AgentManagerService.list_agents(session, user={"role": "admin"})
        row = next(agent for agent in agents if agent.id == lead.id)
        assert row.team_name == f"bare-lead-{suffix}-display"
        # 未配置调度范围 = None，不是 0
        assert row.team_member_count is None
        assert row.team_member_ids is None
    finally:
        async with AsyncSessionLocal() as session:
            await session.delete(await session.get(AIAgent, lead.id))
            await session.commit()


def test_team_profile_normalizes_blank_and_trims():
    blank = AIAgentBase(
        name="t1",
        display_name="团一",
        agent_type="GENERAL",
        team_profile={"name": "   ", "intro": ""},
    )
    assert blank.team_profile is None

    trimmed = AIAgentBase(
        name="t2",
        display_name="团二",
        agent_type="GENERAL",
        team_profile={"name": "  数据分析团  ", "intro": "  一句话介绍  "},
    )
    assert trimmed.team_profile == {"name": "数据分析团", "intro": "一句话介绍"}

    # 前端可能把 null 直接塞进开关字段，列是 NOT NULL，必须归一成 False
    assert AIAgentBase(
        name="t3", display_name="团三", agent_type="GENERAL", is_expert_team_lead=None
    ).is_expert_team_lead is False


async def test_duplicate_does_not_inherit_team_lead_flag():
    """复制一份不应产生第二个同名团；团名/介绍作为文案带过去，省得重填。"""
    suffix = uuid.uuid4().hex[:8]
    source = _agent(
        name=f"dup-src-{suffix}",
        created_by="alice",
        is_expert_team_lead=True,
        team_profile={"name": "被复制的团", "intro": "介绍"},
    )

    async with AsyncSessionLocal() as session:
        session.add(source)
        await session.commit()
        session.add(_published_version(agent_id=source.id, tools=[]))
        await session.commit()

    created_id = None
    try:
        async with AsyncSessionLocal() as session:
            from app.schemas.agent import AgentDuplicateRequest

            copy = await AgentManagerService.duplicate_agent(
                session,
                source.id,
                AgentDuplicateRequest(name=f"dup-copy-{suffix}", display_name="复制品"),
                user={"role": "admin", "user_name": "alice"},
            )
            created_id = copy.id
            assert copy.is_expert_team_lead is False
            assert copy.team_profile == {"name": "被复制的团", "intro": "介绍"}
    finally:
        async with AsyncSessionLocal() as session:
            for agent_id in (created_id, source.id):
                if not agent_id:
                    continue
                row = await session.get(AIAgent, agent_id)
                if row is not None:
                    await session.delete(row)
            await session.commit()


async def test_allowed_agents_exposes_team_name_for_badge():
    """/agents/allowed 是聊天页「专家中心」抽屉的数据源。

    抽屉要在团主专家上打 👥 TEAM 标记，所以这个接口也必须带上团名——否则 tooltip
    只能回落成智能体展示名，用户看不出这是哪个团。
    """
    suffix = uuid.uuid4().hex[:8]
    lead = _agent(
        name=f"allowed-lead-{suffix}",
        created_by="alice",
        is_expert_team_lead=True,
        team_profile={"name": "销售专家团", "intro": "介绍"},
    )

    async with AsyncSessionLocal() as session:
        session.add(lead)
        await session.commit()

    try:
        async with AsyncSessionLocal() as session:
            rows = await AgentManagerService.list_allowed_agents(
                session, user={"role": "admin", "user_name": "admin"}
            )
            # 团名/成员数由 attach_team_metadata 补齐（接口层负责调用）
            rows = await AgentManagerService.attach_team_metadata(session, rows)
        row = next(agent for agent in rows if agent.id == lead.id)
        assert row.is_expert_team_lead is True
        assert row.team_name == "销售专家团"
        # 没有已发布版本 → 成员数未知（None），不是 0
        assert row.team_member_count is None
    finally:
        async with AsyncSessionLocal() as session:
            await session.delete(await session.get(AIAgent, lead.id))
            await session.commit()


# ---------------- 团队身份段：进提示词，但不进父级路由 ----------------

from types import SimpleNamespace

from app.services.ai.agent_roster import (
    TEAM_DELEGATION_BOUNDARY_HINT,
    build_sub_agents_context,
    build_team_identity_context,
)


def _member(name: str, display: str, desc: str, caps: list[str]) -> SimpleNamespace:
    return SimpleNamespace(name=name, display_name=display, description=desc, capabilities=caps)


def test_team_identity_is_injected_before_the_roster():
    """团介绍里的流程约束要进提示词，且必须排在成员清单之前（先身份、再名单）。"""
    context = build_sub_agents_context(
        [_member("metadata-specialist", "元数据专家", "解析 DDL 与业务口径", ["DDL"])],
        team_profile={
            "name": "数据分析专家团",
            "intro": "先问元数据专家确认口径，再让数据助手取数。",
        },
    )
    assert context is not None
    assert context.startswith("## 你的团队：数据分析专家团")
    assert "先问元数据专家确认口径" in context
    assert context.index("你的团队") < context.index("可委派子智能体清单")
    assert "`metadata-specialist`" in context
    # 有成员才指挥它去调
    assert "把子任务交给对应成员" in context
    assert TEAM_DELEGATION_BOUNDARY_HINT in context


def test_team_identity_without_members_still_renders():
    """写好了团介绍但还没配调度范围：只注入身份段，不出现空清单。"""
    context = build_sub_agents_context([], team_profile={"name": "数据分析专家团"})
    assert context is not None
    assert "## 你的团队：数据分析专家团" in context
    assert "可委派子智能体清单" not in context
    # 没有成员时不能诱导它去调工具，否则必然撞 depth_exceeded
    assert "把子任务交给对应成员" not in context


def test_no_team_profile_keeps_plain_roster():
    """普通智能体（非团主专家）拿到的清单一个字都不该变。"""
    context = build_sub_agents_context(
        [_member("chat-bi", "数据智能助手", "取数与报表", ["SQL"])],
        team_profile=None,
    )
    assert context is not None
    assert context.startswith("## 可委派子智能体清单")
    assert "你的团队" not in context


def test_blank_team_profile_produces_nothing():
    assert build_team_identity_context(None, has_members=True) is None
    assert build_team_identity_context({}, has_members=True) is None
    # 全空白等同于没写
    assert build_team_identity_context({"name": "   ", "intro": ""}, has_members=True) is None


def test_team_identity_keeps_delegation_boundary():
    """深度上限是 1：被委派而来的团主专家不能再往下调，提示词必须写清。"""
    context = build_team_identity_context({"name": "销售支撑专家团"}, has_members=True)
    assert context is not None
    assert "无法再向下委派" in context
    assert "`sub_agent_call`" in context


def test_delegated_team_lead_is_told_it_can_only_use_its_own_ability():
    """团主专家同时是另一个团的子专家时（深度 1），身份段必须改口。

    默认那句「需要成员专长时用 sub_agent_call 交给成员」在深度 1 是**做不到**的：
    深度上限 1，再调只会拿到 depth_exceeded。若提示词不改，模型会照着去试，
    先白失败一次再降级作答——用户看到的是「它试了一下，然后自己答了」。
    """
    profile = {"name": "销售支撑专家团", "intro": "先问元数据专家，再问指标口径。"}
    delegated = build_team_identity_context(profile, has_members=True, delegation_depth=1)

    assert delegated is not None
    # 不再推它去委派
    assert "不要调用 `sub_agent_call`" in delegated
    assert "只能用你自己的能力作答" in delegated
    # 介绍里的流程仍要给它，用来判断该由谁做，但不能说「必须遵守」
    assert "供你判断这类问题该交给哪位成员" in delegated
    assert "遇到对应场景必须遵守" not in delegated


def test_handoff_is_not_conflated_with_doing_it_yourself():
    """团介绍写「不查不写」时，团主会退化成「请你自己去那边办」。

    真实反馈：某团主明明配好了 4 位子专家，却回「总入口不查设备明细，我不替你代查，
    你进入【资产查询】后把这段话发给我」。它把「我不动手」读成了「我不委派」。
    提示词必须把界线写死：转交是本职，不是越权。
    """
    context = build_team_identity_context(
        {"name": "资产档案专家", "intro": "资产运维总入口：识别意图并转到查询、写操作或模板导出。不查不写。"},
        has_members=True,
        delegation_depth=0,
    )
    assert context is not None
    assert "「交给成员办」不算「你代为执行」" in context
    assert "不查不写" in context
    assert "不要退化成让用户自己去找人" in context


def test_explicit_no_delegation_members_keep_their_rule():
    """写操作类成员常写「必须直选，禁止委派」：这条只对那位成员生效，不能被泛化。"""
    context = build_team_identity_context({"name": "资产档案专家"}, has_members=True)
    assert context is not None
    assert "必须直选 / 禁止委派" in context
    assert "这条只对那一位生效" in context


def test_team_without_members_does_not_promise_a_handoff():
    """开了团主开关、写了介绍但调度范围还没配：不能承诺「我帮你转给某位专家」。"""
    context = build_team_identity_context(
        {"name": "资产档案专家", "intro": "识别意图并转到对应专家。"},
        has_members=False,
    )
    assert context is not None
    assert "还没有可调用的成员" in context
    assert "不要承诺「我帮你转给某位专家」" in context
    assert "「交给成员办」不算「你代为执行」" not in context


def test_top_level_team_lead_still_gets_the_delegate_instruction():
    """深度 0（用户直接选定这个团）时行为不变：该怎么调度成员还怎么写。"""
    # 必须带 intro：只有写了团队介绍才会出现「遇到对应场景必须遵守」那句引导
    profile = {"name": "销售支撑专家团", "intro": "先确认口径再取数。"}
    top = build_team_identity_context(profile, has_members=True, delegation_depth=0)

    assert top is not None
    assert "用 `sub_agent_call` 把子任务交给对应成员" in top
    # 条件句兜底（「如果你本身就是被委派的…」）在任何深度都保留，这里只要求
    # 「确定句」不出现——它会把一个真正的主专家误判成被委派的
    assert "只能用你自己的能力作答" not in top
    assert "遇到对应场景必须遵守" in top


def test_unknown_depth_falls_back_to_conditional_boundary():
    """读不到深度时（默认 0 但成员场景）保留条件句兜底，不能凭空断言「你是被委派的」。"""
    context = build_team_identity_context({"name": "销售支撑专家团"}, has_members=True)
    assert context is not None
    assert "如果你本身就是被其他智能体委派来处理某个子任务的" in context


def test_chat_config_roundtrip_keeps_team_profile():
    """runner 会用 ChatConfig(**config) 重建配置，团身份不能在这条路上丢。"""
    config = ChatConfig(
        agent_id="agent-1",
        agent_name="ds-lead",
        system_prompt="p",
        tools=[],
        model_name="m",
        temperature=0.1,
        is_expert_team_lead=True,
        team_profile={"name": "数据分析专家团", "intro": "先确认口径再取数"},
    )
    rebuilt = ChatConfig(**config.model_dump())
    assert rebuilt.is_expert_team_lead is True
    assert rebuilt.team_profile == {"name": "数据分析专家团", "intro": "先确认口径再取数"}


@pytest.mark.asyncio
async def test_active_config_carries_team_profile_from_db():
    """链路验证：库里的团身份要能一路带到执行期配置，否则提示词段永远是空的。"""
    from app.services.ai.agent_manager import AgentManagerService

    lead = _agent(
        name=f"team-lead-{uuid.uuid4().hex[:8]}",
        created_by="admin",
        is_expert_team_lead=True,
        team_profile={"name": "数据分析专家团", "intro": "先确认口径再取数"},
    )
    version = AIAgentVersion(
        id=str(uuid.uuid4()),
        agent_id=lead.id,
        version_number=1,
        status="PUBLISHED",
        model_name="qwen",
        temperature=0.3,
        system_prompt="你是数据分析专家团的主专家。",
        tools=[],
    )
    async with AsyncSessionLocal() as session:
        session.add(lead)
        await session.flush()
        session.add(version)
        await session.commit()
        try:
            config = await AgentManagerService.get_active_agent_config(
                session, agent_id=lead.id
            )
        finally:
            await session.delete(await session.get(AIAgent, lead.id))
            await session.commit()

    assert config is not None
    assert config.is_expert_team_lead is True
    assert config.team_profile == {"name": "数据分析专家团", "intro": "先确认口径再取数"}
