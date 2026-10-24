"""复制智能体的权限边界。

系统智能体是管理员的资产：复制会把提示词、工具/MCP 绑定、模型参数整份带走，副本
``is_system=False`` 且归复制人所有、直接 PUBLISHED 可立即对话。界面已经看不到别人
创建的系统智能体，但 id 会在对话/Embed 链接里出现，所以接口必须自己挡。

边界按 ``created_by`` 划：自己创建后被提升为系统级的仍可复制（是他的东西），
拦的是「别人创建的系统智能体」。
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import delete

from app.core.orm import AsyncSessionLocal
from app.models.agent import AIAgent, AIAgentVersion
from app.services.ai.agent_manager import AgentManagerService


BOB = {"user_id": 1001, "user_name": "bob", "role": "user"}


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


def _request(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, display_name=name)


async def _cleanup(*agent_ids: str) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AIAgentVersion).where(AIAgentVersion.agent_id.in_(list(agent_ids))))
        await session.execute(delete(AIAgent).where(AIAgent.id.in_(list(agent_ids))))
        await session.commit()


async def test_non_admin_cannot_duplicate_foreign_system_agent():
    suffix = uuid.uuid4().hex[:8]
    admin_system = _agent(name=f"admin-system-dup-{suffix}", created_by="admin", is_system=True)
    async with AsyncSessionLocal() as session:
        session.add(admin_system)
        await session.commit()

    created_ids: list[str] = []
    try:
        async with AsyncSessionLocal() as session:
            with pytest.raises(HTTPException) as exc:
                await AgentManagerService.duplicate_agent(
                    session, admin_system.id, _request(f"bob-copy-{suffix}"), user=BOB
                )
            assert exc.value.status_code == 403
            assert "不能复制" in str(exc.value.detail)

            # 拒绝时不能留下半成品
            leftover = await AgentManagerService.list_agents(session, user=BOB)
            assert all(agent.name != f"bob-copy-{suffix}" for agent in leftover)
    finally:
        await _cleanup(admin_system.id, *created_ids)


async def test_non_admin_can_duplicate_own_promoted_system_agent():
    suffix = uuid.uuid4().hex[:8]
    bob_promoted = _agent(name=f"bob-promoted-dup-{suffix}", created_by="bob", is_system=True)
    async with AsyncSessionLocal() as session:
        session.add(bob_promoted)
        await session.commit()

    created_ids: list[str] = []
    try:
        async with AsyncSessionLocal() as session:
            copy_agent = await AgentManagerService.duplicate_agent(
                session, bob_promoted.id, _request(f"bob-own-copy-{suffix}"), user=BOB
            )
            assert copy_agent is not None
            created_ids.append(copy_agent.id)
            # 副本归复制人、不再是系统级
            assert copy_agent.is_system is False
            assert copy_agent.created_by == "bob"
    finally:
        await _cleanup(bob_promoted.id, *created_ids)
