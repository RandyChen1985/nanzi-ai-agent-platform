"""智能体级「检索范围确认」配置的落库契约。

这条链路踩过一次：选项画在版本抽屉里，而版本保存只提交 versionForm，
从不写 agent 级 ``engine_config``，于是「选了仅用绑定库 → 重开还是跟随全局」。
前端靠即时 PATCH 保存（见 ``test_knowledge_scope_gate_config_contract.py``），
这里锁住后端侧的两件事：

1. ``PUT /agents/{id}`` 会原样保存 ``engine_config.scope_gate``；
2. 保存后 ``GET`` 回显同一个值，且 ``engine_config`` 里其他键不被覆盖掉。
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models.agent import AIAgent
from app.services.auth_service import AuthService


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _make_agent(session, *, owner: str, engine_config=None) -> AIAgent:
    agent = AIAgent(
        id=str(uuid.uuid4()),
        name=f"scope-gate-{_uid()}",
        display_name="范围配置接口测试",
        created_by=owner,
        is_system=False,
        is_enabled=True,
        engine_type="LOCAL",
        engine_config=engine_config,
    )
    session.add(agent)
    await session.flush()
    await session.commit()
    return agent


@pytest.mark.asyncio
async def test_update_agent_persists_scope_gate(db_session):
    """选了「仅用绑定库」必须真的写进 engine_config，否则配置页与运行时判定两空。"""
    owner = f"scope_owner_{_uid()}"
    key = await AuthService.generate_api_key(owner, role="admin", db=db_session)
    agent = await _make_agent(
        db_session,
        owner=owner,
        engine_config={"dataset_ids": ["a" * 32], "default_markdown_theme": "zen"},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-API-Key": key}
        resp = await client.put(
            f"/api/portal/agents/{agent.id}",
            json={
                "name": agent.name,
                "display_name": agent.display_name,
                "description": agent.description or "",
                "is_enabled": True,
                "engine_type": "LOCAL",
                "engine_config": {
                    "dataset_ids": ["a" * 32],
                    "default_markdown_theme": "zen",
                    "scope_gate": "bound_only",
                },
            },
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["engine_config"]["scope_gate"] == "bound_only"

    # 直接读库确认不是只在响应里好看。
    # 必须按列查：接口跑在独立 session 上，本 session 的 identity map 里仍是
    # 创建时的对象，取整对象会读到旧 engine_config（第一次写这条测试就被它误导过）。
    stored_config = (
        await db_session.execute(
            select(AIAgent.engine_config).where(AIAgent.id == agent.id)
        )
    ).scalar_one()
    assert (stored_config or {}).get("scope_gate") == "bound_only"
    # 其他键不能被整体替换冲掉
    assert (stored_config or {}).get("default_markdown_theme") == "zen"
    assert (stored_config or {}).get("dataset_ids") == ["a" * 32]


@pytest.mark.asyncio
async def test_update_agent_clears_scope_gate_when_omitted_from_config(db_session):
    """退回「跟随全局」时前端会删键，后端必须真的删掉，而不是留着旧值。"""
    owner = f"scope_owner_{_uid()}"
    key = await AuthService.generate_api_key(owner, role="admin", db=db_session)
    agent = await _make_agent(
        db_session,
        owner=owner,
        engine_config={"dataset_ids": ["a" * 32], "scope_gate": "bound_only"},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put(
            f"/api/portal/agents/{agent.id}",
            json={
                "name": agent.name,
                "display_name": agent.display_name,
                "description": agent.description or "",
                "is_enabled": True,
                "engine_type": "LOCAL",
                "engine_config": {"dataset_ids": ["a" * 32]},
            },
            headers={"X-API-Key": key},
        )
    assert resp.status_code == 200, resp.text
    assert "scope_gate" not in (resp.json().get("engine_config") or {})

    stored_config = (
        await db_session.execute(
            select(AIAgent.engine_config).where(AIAgent.id == agent.id)
        )
    ).scalar_one()
    assert "scope_gate" not in (stored_config or {})
