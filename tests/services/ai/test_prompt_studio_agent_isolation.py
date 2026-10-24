"""提示词工坊操作智能体提示词时的隔离与完整性。

这个页面前身是绕过权限体系的：它用裸 SQL 直接 UPDATE / INSERT
``ai_agent_versions``，于是所有权、冻结、字段完整性全部失效。具体后果：

* 任何登录账号都能读到全公司所有智能体的系统提示词（含生产基线）——列表接口
  的 SQL 里连 ``WHERE created_by`` 都没有；
* 有 ``element:prompts:edit`` 的人可以改**别人的**草稿，而且不留痕、不写事件；
* 新建版本时只写 ``system_prompt``，``temperature`` 等字段落回数据库默认值
  （实测 ``temperature`` 默认 0），于是「提示词一样、效果却变了」。

下面锁的就是这三件事。
"""
import uuid

import pytest
from sqlalchemy import delete, select

from app.models.agent import AIAgent, AIAgentVersion
from app.services.ai.agent_manager import AgentManagerService
from app.services.ai.prompt_ops.prompt_service import PromptService
from app.schemas.prompt import PromptSource


async def _make_agent(session, *, owner: str, prompt: str = "原始提示词", temperature: float = 0.8):
    agent = AIAgent(
        id=str(uuid.uuid4()),
        name=f"ps-{uuid.uuid4().hex[:8]}",
        display_name="提示词工坊测试智能体",
        created_by=owner,
        is_enabled=True,
        engine_type="LOCAL",
    )
    session.add(agent)
    await session.flush()
    session.add(
        AIAgentVersion(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            version_number=1,
            system_prompt=prompt,
            model_name="deepseek-chat",
            temperature=temperature,
            status="PUBLISHED",
        )
    )
    await session.flush()
    return agent


async def _cleanup(session, *agent_ids: str):
    if not agent_ids:
        return
    await session.execute(delete(AIAgentVersion).where(AIAgentVersion.agent_id.in_(list(agent_ids))))
    await session.execute(delete(AIAgent).where(AIAgent.id.in_(list(agent_ids))))
    await session.commit()


@pytest.mark.asyncio
async def test_saving_other_owners_prompt_is_rejected(db_session):
    """不是自己的智能体，改提示词必须被拒——且不能被伪装成「无变化」。"""
    owner = f"psowner-{uuid.uuid4().hex[:6]}"
    agent = await _make_agent(db_session, owner=owner)
    agent_id = agent.id
    await db_session.commit()

    intruder = {"role": "user", "user_name": "someone-else", "user_id": 999001, "id": 999001}

    try:
        with pytest.raises(PermissionError):
            await AgentManagerService.save_agent_prompt(
                db_session, agent_id, "恶意篡改", user=intruder
            )

        # 库里的内容不能变
        rows = await db_session.execute(
            select(AIAgentVersion.system_prompt).where(AIAgentVersion.agent_id == agent_id)
        )
        assert rows.scalars().first() == "原始提示词"
    finally:
        await _cleanup(db_session, agent_id)


@pytest.mark.asyncio
async def test_owner_can_save_and_new_version_keeps_other_fields(db_session):
    """所有者能改；新建草稿版本时必须继承温度等字段，而不是落回默认值。"""
    owner = f"psowner-{uuid.uuid4().hex[:6]}"
    agent = await _make_agent(db_session, owner=owner, temperature=0.8)
    agent_id = agent.id
    await db_session.commit()

    ctx = {"role": "user", "user_name": owner, "user_id": 999002, "id": 999002}

    try:
        version_id = await AgentManagerService.save_agent_prompt(
            db_session, agent_id, "新提示词", user=ctx
        )
        await db_session.commit()

        assert version_id, "应当产生一个可发布的草稿版本"

        rows = await db_session.execute(
            select(AIAgentVersion).where(AIAgentVersion.id == version_id)
        )
        draft = rows.scalars().first()
        assert draft.system_prompt == "新提示词"
        assert draft.status == "DRAFT"
        assert draft.temperature == 0.8, \
            "新建版本必须继承原温度；写死 system_prompt 会落回 DB 默认值 0"
        assert draft.model_name == "deepseek-chat"
    finally:
        await _cleanup(db_session, agent_id)


@pytest.mark.asyncio
async def test_saving_unchanged_prompt_reports_no_change(db_session):
    """内容没变时不制造「保存成功」，也不生成多余版本。"""
    owner = f"psowner-{uuid.uuid4().hex[:6]}"
    agent = await _make_agent(db_session, owner=owner, prompt="同样的内容")
    agent_id = agent.id
    await db_session.commit()

    ctx = {"role": "user", "user_name": owner, "user_id": 999003, "id": 999003}

    try:
        # 先落一个草稿，再原样保存一次
        first = await AgentManagerService.save_agent_prompt(
            db_session, agent_id, "草稿内容", user=ctx
        )
        await db_session.commit()
        assert first

        second = await AgentManagerService.save_agent_prompt(
            db_session, agent_id, "草稿内容", user=ctx
        )
        assert second is None, "内容未变不应产生新版本"
    finally:
        await _cleanup(db_session, agent_id)


@pytest.mark.asyncio
async def test_prompt_list_hides_agents_the_user_cannot_see(db_session):
    """列表只返回自己创建的（管理员除外）——否则任何账号都能看到全公司提示词。"""
    other_owner = f"psother-{uuid.uuid4().hex[:6]}"
    agent = await _make_agent(db_session, owner=other_owner)
    agent_id = agent.id
    await db_session.commit()

    try:
        outsider = {"role": "user", "user_name": "nobody", "user_id": 999004, "id": 999004}
        prompts = await PromptService.get_all_prompts(outsider)
        assert all(
            p.id != f"agent_{agent_id}" for p in prompts
        ), "无权查看的智能体不应出现在提示词列表里"

        admin = {"role": "admin", "user_name": "admin", "user_id": 1, "id": 1}
        admin_prompts = await PromptService.get_all_prompts(admin)
        assert any(
            p.id == f"agent_{agent_id}" for p in admin_prompts
        ), "管理员应当能看到全部"
    finally:
        await _cleanup(db_session, agent_id)


@pytest.mark.asyncio
async def test_system_agents_never_show_up_in_prompt_studio(db_session):
    """系统级智能体不进提示词工坊——哪怕当前用户被授权使用它。

    工坊能改草稿又能一键发布，等于给「申请-审核-上线」流程开了后门；
    系统级的迭代统一走智能体中心。
    """
    owner = f"pssys-{uuid.uuid4().hex[:6]}"
    agent = await _make_agent(db_session, owner=owner)
    agent.is_system = True
    agent_id = agent.id
    await db_session.commit()

    try:
        # 连所有者本人都看不到
        prompts = await PromptService.get_all_prompts(
            {"role": "user", "user_name": owner, "user_id": 999006, "id": 999006}
        )
        assert all(p.id != f"agent_{agent_id}" for p in prompts)

        # 管理员仍然可以看到并维护
        admin_prompts = await PromptService.get_all_prompts(
            {"role": "admin", "user_name": "admin", "user_id": 1, "id": 1}
        )
        assert any(p.id == f"agent_{agent_id}" for p in admin_prompts)
    finally:
        await _cleanup(db_session, agent_id)


@pytest.mark.asyncio
async def test_prompt_detail_is_guarded_by_visibility(db_session):
    """拿到别人的 agent_id 也不能读提示词全文。"""
    other_owner = f"psother-{uuid.uuid4().hex[:6]}"
    agent = await _make_agent(db_session, owner=other_owner, prompt="机密提示词")
    agent_id = agent.id
    await db_session.commit()

    try:
        outsider = {"role": "user", "user_name": "nobody", "user_id": 999005, "id": 999005}
        with pytest.raises(PermissionError):
            await PromptService.get_prompt_detail(
                PromptSource.AGENT, f"agent_{agent_id}", None, outsider
            )
    finally:
        await _cleanup(db_session, agent_id)
