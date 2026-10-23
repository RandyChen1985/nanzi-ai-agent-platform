"""智能体中心列表的可见性口径。

普通用户只看得到**自己创建的**智能体——别人创建的不出现，无论是否系统级。
系统智能体由管理员维护，普通用户既管不了，也不该看到它的提示词与工具配置：
复制接口会把整份配置带走，看到即等于可复刻。

按 ``created_by`` 而不是 ``is_system`` 判定：自己创建的智能体被管理员提升为系统级后，
``created_by`` 不变，本人仍应看得到（且仍可复制，见 test_agent_duplicate_permission）。
"""
import uuid

from sqlalchemy import delete

from app.core.orm import AsyncSessionLocal
from app.models.agent import AIAgent
from app.services.ai.agent_manager import AgentManagerService


def _agent(*, name: str, created_by: str, is_system: bool) -> AIAgent:
    return AIAgent(
        id=str(uuid.uuid4()),
        name=name,
        display_name=name,
        created_by=created_by,
        is_system=is_system,
        is_enabled=True,
        engine_type="LOCAL",
    )


async def test_non_admin_only_sees_owned_agents():
    suffix = uuid.uuid4().hex[:8]
    alice_agent = _agent(name=f"alice-private-{suffix}", created_by="alice", is_system=False)
    bob_agent = _agent(name=f"bob-private-{suffix}", created_by="bob", is_system=False)
    # 自己创建后被管理员提升为系统级：created_by 仍是 bob
    bob_promoted = _agent(name=f"bob-promoted-{suffix}", created_by="bob", is_system=True)
    admin_system = _agent(name=f"admin-system-{suffix}", created_by="admin", is_system=True)

    async with AsyncSessionLocal() as session:
        session.add_all([alice_agent, bob_agent, bob_promoted, admin_system])
        await session.commit()

    try:
        async with AsyncSessionLocal() as session:
            bob_agents = await AgentManagerService.list_agents(
                session, user={"user_id": 1001, "user_name": "bob", "role": "user"}
            )
            bob_ids = {agent.id for agent in bob_agents}
            assert bob_agent.id in bob_ids
            assert bob_promoted.id in bob_ids
            # 别人创建的（含管理员的系统智能体）一律不出现
            assert alice_agent.id not in bob_ids
            assert admin_system.id not in bob_ids
            assert all(agent.created_by == "bob" for agent in bob_agents)

        async with AsyncSessionLocal() as session:
            admin_agents = await AgentManagerService.list_agents(
                session, user={"user_id": 1, "user_name": "admin", "role": "admin"}
            )
            admin_ids = {agent.id for agent in admin_agents}
            assert {alice_agent.id, bob_agent.id, bob_promoted.id, admin_system.id} <= admin_ids
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(AIAgent).where(
                    AIAgent.id.in_(
                        [alice_agent.id, bob_agent.id, bob_promoted.id, admin_system.id]
                    )
                )
            )
            await session.commit()
