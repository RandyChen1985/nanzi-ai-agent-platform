"""智能体中心列表的可见性口径。

普通用户只能看到系统智能体和自己创建的——``/agents/allowed``（可对话）一直是这个
口径，但管理列表曾经无条件返回全部，导致别人创建的非系统智能体出现在页面上却
既不能编辑也不能对话。
"""
import uuid

from sqlalchemy import delete

from app.core.orm import AsyncSessionLocal
from app.models.agent import AIAgent
from app.services.ai.agent_manager import AgentManagerService


async def test_non_admin_only_sees_system_and_owned_agents():
    suffix = uuid.uuid4().hex[:8]
    alice_agent_id = str(uuid.uuid4())
    bob_agent_id = str(uuid.uuid4())

    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                AIAgent(
                    id=alice_agent_id,
                    name=f"alice-private-{suffix}",
                    display_name="Alice 的私有智能体",
                    created_by="alice",
                    is_system=False,
                    is_enabled=True,
                    engine_type="LOCAL",
                ),
                AIAgent(
                    id=bob_agent_id,
                    name=f"bob-private-{suffix}",
                    display_name="Bob 的私有智能体",
                    created_by="bob",
                    is_system=False,
                    is_enabled=True,
                    engine_type="LOCAL",
                ),
            ]
        )
        await session.commit()

    try:
        async with AsyncSessionLocal() as session:
            bob_agents = await AgentManagerService.list_agents(
                session, user={"user_id": 1001, "user_name": "bob", "role": "user"}
            )
            bob_ids = {agent.id for agent in bob_agents}
            assert bob_agent_id in bob_ids
            assert alice_agent_id not in bob_ids
            # 除自己创建的外，只允许出现系统智能体
            assert all(agent.is_system or agent.created_by == "bob" for agent in bob_agents)

        async with AsyncSessionLocal() as session:
            admin_agents = await AgentManagerService.list_agents(
                session, user={"user_id": 1, "user_name": "admin", "role": "admin"}
            )
            admin_ids = {agent.id for agent in admin_agents}
            assert alice_agent_id in admin_ids
            assert bob_agent_id in admin_ids
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(AIAgent).where(AIAgent.id.in_([alice_agent_id, bob_agent_id]))
            )
            await session.commit()
