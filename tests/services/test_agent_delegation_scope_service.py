"""专家团成员就地编辑：隐式新版本的契约。

最核心的一条是 ``test_only_the_member_list_changes``：保存成员**只能**动
``delegable_agent_ids``，提示词、模型、温度、其他工具、技能、欢迎语、超时
都必须原样带到新版本里。这条一旦破了，用户改个成员顺带把线上配置改掉，
而且从版本历史里还看不出来。

其余几条守的是语义分叉：空数组 ≠ 未配置、团主与 main 不入名单、工具成对补齐。
"""
import uuid

import pytest
from sqlalchemy import delete, select

from app.models.agent import AIAgent, AIAgentVersion
from app.services.ai.agent_delegation_scope_service import (
    MAX_DELEGATION_MEMBERS,
    AgentDelegationScopeService,
    DelegationScopeState,
    DelegationToolDisabledError,
    NoPublishedVersionError,
    TooManyMembersError,
    _apply_scope_to_tools,
    _normalize_member_ids,
    _read_delegation_config,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _make_agent(session, *, owner: str, tools=None, status: str = "PUBLISHED"):
    """造一个带版本的智能体；版本字段刻意填满，用来验证「其余都不能变」。"""
    agent = AIAgent(
        id=str(uuid.uuid4()),
        name=f"scope-{_uid()}",
        display_name="成员编辑测试",
        created_by=owner,
        is_system=False,
        is_enabled=True,
        engine_type="LOCAL",
    )
    session.add(agent)
    await session.flush()
    version = AIAgentVersion(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        version_number=7,
        system_prompt="你是资产管理员，只回答资产相关问题。",
        model_name="deepseek-chat",
        temperature=0.35,
        synthesis_model_name="deepseek-reasoner",
        synthesis_temperature=0.2,
        allow_model_switch=False,
        tools=tools if tools is not None else [
            "web_search",
            {"name": "sub_agent_call", "delegable_agent_ids": ["old-1", "old-2"]},
            {"name": "knowledge_search", "dataset_ids": ["ds-1"]},
        ],
        toolcall_timeout_seconds=45,
        skills_custom=True,
        skills=["skill-a", "skill-b"],
        welcome_config={"cards": [{"title": "欢迎"}]},
        status=status,
        comment="原始发布",
    )
    session.add(version)
    await session.flush()
    await session.commit()
    return agent, version


async def _cleanup(*agent_ids: str) -> None:
    from app.core.orm import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(AIAgentVersion).where(AIAgentVersion.agent_id.in_(list(agent_ids)))
        )
        await session.execute(delete(AIAgent).where(AIAgent.id.in_(list(agent_ids))))
        await session.commit()


def _user(owner: str) -> dict:
    return {"user_name": owner, "role": "user"}


async def _published(session, agent_id: str):
    row = await session.execute(
        select(AIAgentVersion)
        .where(
            AIAgentVersion.agent_id == agent_id,
            AIAgentVersion.status == "PUBLISHED",
        )
        .order_by(AIAgentVersion.version_number.desc())
    )
    return row.scalars().first()


# --------------------------------------------------------------------------
# 纯函数：工具配置的改写
# --------------------------------------------------------------------------


def test_apply_scope_only_touches_delegation_tool():
    tools = [
        "web_search",
        {"name": "sub_agent_call", "delegable_agent_ids": ["old-1"]},
        {"name": "knowledge_search", "dataset_ids": ["ds-1"]},
    ]
    result = _apply_scope_to_tools(tools, ["a", "b"], enable_tool=False)

    # 其他工具逐字不变（值相等且不是同一个对象，说明是拷贝而不是原地改）
    assert "web_search" in result
    assert {"name": "knowledge_search", "dataset_ids": ["ds-1"]} in result
    # 名单被整体替换
    single = next(t for t in result if isinstance(t, dict) and t["name"] == "sub_agent_call")
    assert single["delegable_agent_ids"] == ["a", "b"]
    # 成对补齐：只存了 sub_agent_call，batch 必须被补上
    assert any(
        (t if isinstance(t, str) else t.get("name")) == "sub_agent_batch_call"
        for t in result
    )
    # 入参本身没被改
    assert tools[1]["delegable_agent_ids"] == ["old-1"]


def test_apply_scope_clears_legacy_batch_list():
    tools = [
        {"name": "sub_agent_call", "delegable_agent_ids": ["a"]},
        {"name": "sub_agent_batch_call", "delegable_agent_ids": ["stale"]},
    ]
    result = _apply_scope_to_tools(tools, ["x"], enable_tool=False)
    batch = next(t for t in result if isinstance(t, dict) and t["name"] == "sub_agent_batch_call")
    # 名单只有一份：batch 上的历史字段必须被清掉，否则两份迟早分叉
    assert "delegable_agent_ids" not in batch


def test_apply_scope_enables_tool_only_when_asked():
    tools = ["web_search"]

    unchanged = _apply_scope_to_tools(tools, [], enable_tool=False)
    assert not any(
        (t if isinstance(t, str) else t.get("name")) == "sub_agent_call" for t in unchanged
    )

    enabled = _apply_scope_to_tools(tools, ["a"], enable_tool=True)
    names = {(t if isinstance(t, str) else t.get("name")) for t in enabled}
    assert "sub_agent_call" in names
    assert "sub_agent_batch_call" in names


def test_read_delegation_config_falls_back_to_batch():
    # 存量数据可能只配在 batch 上，必须兜底读出来，否则显示 0 个
    enabled, configured, ids = _read_delegation_config(
        [{"name": "sub_agent_batch_call", "delegable_agent_ids": ["b1"]}]
    )
    assert enabled is False
    assert configured is True
    assert ids == ["b1"]


def test_unconfigured_scope_is_not_the_same_as_empty():
    """工具在、但没配名单 = 未限制（全量可委派），不是「一个都不许」。

    这两种状态后端都是空列表，必须靠 configured 区分：否则前端只能把它显示成
    「还没有成员」，用户加一个人保存就把「全量可委派」静默收窄成那一个人。
    """
    enabled, configured, ids = _read_delegation_config([{"name": "sub_agent_call"}])
    assert enabled is True
    assert configured is False
    assert ids == []

    # 显式空列表才是「禁止委派」
    enabled2, configured2, ids2 = _read_delegation_config(
        [{"name": "sub_agent_call", "delegable_agent_ids": []}]
    )
    assert enabled2 is True
    assert configured2 is True
    assert ids2 == []


def test_explicit_empty_does_not_fall_back_to_batch():
    """显式 [] + batch 上残留历史名单：必须读成「禁止」，不能把历史名单捞回来。

    ``[] or batch_ids`` 是假值陷阱——空列表在 Python 里为假，用 or 兜底就会把
    「禁止委派」读成「允许委派 b1」，语义正好相反。
    """
    enabled, configured, ids = _read_delegation_config([
        {"name": "sub_agent_call", "delegable_agent_ids": []},
        {"name": "sub_agent_batch_call", "delegable_agent_ids": ["b1"]},
    ])
    assert enabled is True
    assert configured is True
    assert ids == []


def test_normalize_drops_lead_and_main():
    ids = _normalize_member_ids(
        ["sub-1", "lead-id", "main", "", "sub-1", " sub-2 "],
        lead_agent_id="lead-id",
    )
    # 团主自己与系统兜底入口都不该进名单；重复与空白被清掉，顺序保持
    assert ids == ["sub-1", "sub-2"]


# --------------------------------------------------------------------------
# 落库行为
# --------------------------------------------------------------------------


async def test_only_the_member_list_changes(db_session):
    owner = f"owner-{_uid()}"
    agent, source = await _make_agent(db_session, owner=owner)
    try:
        await AgentDelegationScopeService.save_scope(
            db_session,
            agent.id,
            ["new-1", "new-2"],
            enable_tool=False,
            user=_user(owner),
        )

        new_version = await _published(db_session, agent.id)
        assert new_version is not None
        # 旧版本被归档，新版本号 +1
        assert new_version.id != source.id
        assert new_version.version_number == source.version_number + 1
        await db_session.refresh(source)
        assert source.status == "ARCHIVED"

        # —— 除了名单，逐字段都该和源版本一模一样 ——
        assert new_version.system_prompt == source.system_prompt
        assert new_version.model_name == source.model_name
        assert new_version.temperature == source.temperature
        assert new_version.synthesis_model_name == source.synthesis_model_name
        assert new_version.synthesis_temperature == source.synthesis_temperature
        assert new_version.allow_model_switch == source.allow_model_switch
        assert new_version.toolcall_timeout_seconds == source.toolcall_timeout_seconds
        assert new_version.skills_custom == source.skills_custom
        assert new_version.skills == source.skills
        assert new_version.welcome_config == source.welcome_config

        # 其他工具也原样带过去
        assert "web_search" in new_version.tools
        assert {"name": "knowledge_search", "dataset_ids": ["ds-1"]} in new_version.tools

        # 唯独名单变了
        single = next(
            t for t in new_version.tools
            if isinstance(t, dict) and t.get("name") == "sub_agent_call"
        )
        assert single["delegable_agent_ids"] == ["new-1", "new-2"]
    finally:
        await _cleanup(agent.id)


async def test_empty_list_is_explicit_deny_not_null(db_session):
    owner = f"owner-{_uid()}"
    agent, _ = await _make_agent(db_session, owner=owner)
    try:
        state = await AgentDelegationScopeService.save_scope(
            db_session, agent.id, [], enable_tool=False, user=_user(owner)
        )
        assert state.delegable_agent_ids == []

        new_version = await _published(db_session, agent.id)
        single = next(
            t for t in new_version.tools
            if isinstance(t, dict) and t.get("name") == "sub_agent_call"
        )
        # 必须真的写成空列表：省略这个键会退回「未配置 = 全部可委派」，语义完全相反
        assert "delegable_agent_ids" in single
        assert single["delegable_agent_ids"] == []
    finally:
        await _cleanup(agent.id)


async def test_too_many_members_rejected(db_session):
    owner = f"owner-{_uid()}"
    agent, _ = await _make_agent(db_session, owner=owner)
    try:
        too_many = [f"sub-{i}" for i in range(MAX_DELEGATION_MEMBERS + 1)]
        with pytest.raises(TooManyMembersError):
            await AgentDelegationScopeService.save_scope(
                db_session, agent.id, too_many, enable_tool=False, user=_user(owner)
            )
        # 拒绝时不能留下半成品版本
        versions = (
            await db_session.execute(
                select(AIAgentVersion).where(AIAgentVersion.agent_id == agent.id)
            )
        ).scalars().all()
        assert len(versions) == 1
    finally:
        await _cleanup(agent.id)


async def test_no_published_version_rejected(db_session):
    owner = f"owner-{_uid()}"
    agent, _ = await _make_agent(db_session, owner=owner, status="DRAFT")
    try:
        with pytest.raises(NoPublishedVersionError):
            await AgentDelegationScopeService.save_scope(
                db_session, agent.id, ["a"], enable_tool=False, user=_user(owner)
            )
    finally:
        await _cleanup(agent.id)


async def test_read_reports_missing_conditions(db_session):
    owner = f"owner-{_uid()}"
    draft_agent, _ = await _make_agent(db_session, owner=owner, status="DRAFT")
    no_tool_agent, _ = await _make_agent(db_session, owner=owner, tools=["web_search"])
    try:
        draft_state = await AgentDelegationScopeService.read_scope(
            db_session, draft_agent.id, _user(owner)
        )
        assert isinstance(draft_state, DelegationScopeState)
        assert draft_state.exists_published is False
        assert draft_state.version_no is None

        tool_state = await AgentDelegationScopeService.read_scope(
            db_session, no_tool_agent.id, _user(owner)
        )
        # 有已发布版本，但没启用委派工具 —— 前端据此把成员区换成「一键启用」
        assert tool_state.exists_published is True
        assert tool_state.sub_agent_call_enabled is False
        assert tool_state.max_members == MAX_DELEGATION_MEMBERS
    finally:
        await _cleanup(draft_agent.id, no_tool_agent.id)


async def test_disabled_tool_requires_consent_then_enables_pair(db_session):
    owner = f"owner-{_uid()}"
    agent, source = await _make_agent(db_session, owner=owner, tools=["web_search"])
    try:
        with pytest.raises(DelegationToolDisabledError):
            await AgentDelegationScopeService.save_scope(
                db_session, agent.id, ["a"], enable_tool=False, user=_user(owner)
            )

        state = await AgentDelegationScopeService.save_scope(
            db_session, agent.id, ["a", "b"], enable_tool=True, user=_user(owner)
        )
        assert state.sub_agent_call_enabled is True
        assert state.delegable_agent_ids == ["a", "b"]

        new_version = await _published(db_session, agent.id)
        names = {(t if isinstance(t, str) else t.get("name")) for t in new_version.tools}
        assert "sub_agent_call" in names
        assert "sub_agent_batch_call" in names  # 成对补齐，否则并发委派直接不可用
        # 原有工具仍在
        assert "web_search" in names
    finally:
        await _cleanup(agent.id)


async def test_lead_agent_never_enters_the_list(db_session):
    owner = f"owner-{_uid()}"
    agent, _ = await _make_agent(db_session, owner=owner)
    try:
        state = await AgentDelegationScopeService.save_scope(
            db_session,
            agent.id,
            [agent.id, "main", "sub-1"],
            enable_tool=False,
            user=_user(owner),
        )
        # 团主自己不写进名单：它是发起方，不是委派目标
        assert state.delegable_agent_ids == ["sub-1"]
    finally:
        await _cleanup(agent.id)


async def test_non_owner_cannot_write(db_session):
    from app.services.ai.agent_delegation_scope_service import DelegationScopeError

    agent, _ = await _make_agent(db_session, owner=f"owner-{_uid()}")
    try:
        with pytest.raises(DelegationScopeError):
            await AgentDelegationScopeService.save_scope(
                db_session, agent.id, ["a"], enable_tool=False, user=_user("someone-else")
            )
    finally:
        await _cleanup(agent.id)

async def test_main_general_agent_id_is_filtered(db_session):
    """主助手按 id 也要拦住：库里主键是 sys-agent-chat，不是 "main"。

    只按 name（"main"）过滤的话，把真实主键传进名单就会漏过去，
    运行时委派到兜底入口只会撞 depth_exceeded。
    """
    from app.services.ai.agent_manager import MAIN_GENERAL_AGENT_ID

    owner = f"owner-{_uid()}"
    agent, _ = await _make_agent(db_session, owner=owner)
    try:
        state = await AgentDelegationScopeService.save_scope(
            db_session,
            agent.id,
            [MAIN_GENERAL_AGENT_ID, "main", "sub-1"],
            enable_tool=False,
            user=_user(owner),
        )
        assert state.delegable_agent_ids == ["sub-1"]
    finally:
        await _cleanup(agent.id)


async def test_version_number_uses_global_max(db_session):
    """版本号要取全智能体最大 +1，不是线上版本 +1。

    「线上 v7、草稿 v8」时用线上 +1 会再造一个 v8，版本列表排序和回滚全乱。
    """
    owner = f"owner-{_uid()}"
    agent, source = await _make_agent(db_session, owner=owner)  # 线上 v7
    # 手工加一份更晚的草稿
    db_session.add(
        AIAgentVersion(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            version_number=source.version_number + 5,
            system_prompt="草稿",
            status="DRAFT",
        )
    )
    await db_session.commit()
    try:
        await AgentDelegationScopeService.save_scope(
            db_session, agent.id, ["a"], enable_tool=False, user=_user(owner)
        )
        new_version = await _published(db_session, agent.id)
        max_no = (
            await db_session.execute(
                select(AIAgentVersion.version_number)
                .where(AIAgentVersion.agent_id == agent.id)
                .order_by(AIAgentVersion.version_number.desc())
            )
        ).scalars().all()
        assert new_version.version_number == max_no[0]
        # 版本号不能重复
        assert len(set(max_no)) == len(max_no)
    finally:
        await _cleanup(agent.id)


async def test_read_scope_rejects_invisible_agent(db_session):
    """无权读取与不存在共用同一个错误：分开返回就变成存在性探测。"""
    from app.services.ai.agent_delegation_scope_service import AgentNotVisibleError

    agent, _ = await _make_agent(db_session, owner=f"owner-{_uid()}")
    try:
        with pytest.raises(AgentNotVisibleError):
            await AgentDelegationScopeService.read_scope(
                db_session, agent.id, _user("someone-else")
            )
        with pytest.raises(AgentNotVisibleError):
            await AgentDelegationScopeService.read_scope(
                db_session, "not-a-real-id", _user("someone-else")
            )
    finally:
        await _cleanup(agent.id)


async def test_read_scope_reports_unconfigured_scope(db_session):
    """工具在但没配名单时，read 要报 scope_configured=False。"""
    owner = f"owner-{_uid()}"
    agent, _ = await _make_agent(db_session, owner=owner, tools=[{"name": "sub_agent_call"}])
    try:
        state = await AgentDelegationScopeService.read_scope(
            db_session, agent.id, _user(owner)
        )
        assert state.sub_agent_call_enabled is True
        assert state.scope_configured is False
        assert state.delegable_agent_ids == []

        # 保存一次后变成显式配置
        state2 = await AgentDelegationScopeService.save_scope(
            db_session, agent.id, ["a"], enable_tool=False, user=_user(owner)
        )
        assert state2.scope_configured is True
        assert state2.delegable_agent_ids == ["a"]
    finally:
        await _cleanup(agent.id)


async def test_publish_is_audited_and_invalidates_tool_cache(monkeypatch, db_session):
    """换掉线上基线必须留痕，并清掉工具定义缓存。"""
    from app.services.ai.agent_system_publication_service import (
        ACTION_BASELINE_PUBLISH,
        AgentSystemPublicationService,
    )
    from app.services.ai.tools.registry import ToolRegistry

    recorded = []
    cleared = []
    original_record = AgentSystemPublicationService.record_event

    async def _spy_record(session, **kwargs):
        recorded.append(kwargs)
        return await original_record(session, **kwargs)

    monkeypatch.setattr(
        AgentSystemPublicationService, "record_event", staticmethod(_spy_record)
    )
    monkeypatch.setattr(
        ToolRegistry, "clear_db_tool_cache", staticmethod(lambda: cleared.append(1))
    )

    owner = f"owner-{_uid()}"
    agent, _ = await _make_agent(db_session, owner=owner)
    try:
        await AgentDelegationScopeService.save_scope(
            db_session, agent.id, ["a"], enable_tool=False, user=_user(owner)
        )
        assert recorded, "隐式发版没有留审计事件"
        assert recorded[-1]["action"] == ACTION_BASELINE_PUBLISH
        assert cleared, "换掉线上版本后没有清工具定义缓存"
    finally:
        await _cleanup(agent.id)


def test_tools_as_json_string_is_normalized():
    """存量数据里 tools 可能是 JSON 字符串。

    直接遍历字符串会按字符拆，成员名单被读成空——而且不报错，
    只在用户发现「团里显示没人」时才暴露。
    """
    import json as _json

    from app.services.ai.agent_delegation_scope_service import _apply_scope_to_tools

    payload = _json.dumps([
        "web_search",
        {"name": "sub_agent_call", "delegable_agent_ids": ["old-1"]},
    ])
    enabled, configured, ids = _read_delegation_config(payload)
    assert enabled is True
    assert configured is True
    assert ids == ["old-1"]

    # 改写时同样要按列表处理，而不是按字符复制
    result = _apply_scope_to_tools(payload, ["a"], enable_tool=False)
    assert "web_search" in result
    assert not any(isinstance(item, str) and len(item) == 1 for item in result)


def test_invalid_json_tools_degrades_to_empty():
    """坏数据不能炸：退化成「没有工具」而不是抛异常。"""
    enabled, configured, ids = _read_delegation_config("{not json")
    assert enabled is False
    assert configured is False
    assert ids == []
