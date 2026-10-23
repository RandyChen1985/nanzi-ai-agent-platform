"""失效引用的数据库判定与清理：用真实 ORM 会话验证 SQL 口径。

只造自己的临时数据并在 finally 清理，不触碰既有智能体/MCP 配置。
"""
import uuid

from sqlalchemy import delete, update

from app.core.orm import AsyncSessionLocal
from app.models.agent import AIAgent, AIAgentVersion
from app.models.mcp import McpServer, McpToolCache
from app.services.mcp.stale_tool_cleanup import prune_agent_versions_by_names
from app.services.mcp.tool_ref_health import find_stale_tool_refs


async def test_tool_health_classification_and_prune_end_to_end():
    suffix = uuid.uuid4().hex[:10]
    server_id = str(uuid.uuid4())
    server_name = f"mcp-public-ghost-{suffix}"
    tool_name = f"{server_name}:query"
    agent_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    user = {"user_id": 999999, "user_name": f"ghost-tester-{suffix}", "role": "admin"}

    async with AsyncSessionLocal() as session:
        session.add(
            McpServer(
                id=server_id,
                server_name=server_name,
                sse_url="https://example.invalid/sse",
                enabled_status=1,
                scope="global",
            )
        )
        session.add(
            McpToolCache(
                id=str(uuid.uuid4()),
                server_id=server_id,
                tool_name=tool_name,
                is_published=True,
                is_available=True,
            )
        )
        session.add(
            AIAgent(
                id=agent_id,
                name=f"ghost-refs-{suffix}",
                display_name="幽灵引用测试",
                created_by=user["user_name"],
                is_system=False,
            )
        )
        session.add(
            AIAgentVersion(
                id=version_id,
                agent_id=agent_id,
                version_number=1,
                model_name="deepseek-chat",
                system_prompt="test",
                tools=["get_dataset_schema", tool_name],
                status="DRAFT",
            )
        )
        await session.commit()

    try:
        async with AsyncSessionLocal() as session:
            # 1) 静态工具 + 已发布可用的 MCP 工具：都不该被判为失效
            assert await find_stale_tool_refs(
                session, ["get_dataset_schema", tool_name], user=user
            ) == []

            # 2) 工具下线：只报这一项，并保留完整名与服务名供前端展示
            await session.execute(
                update(McpToolCache)
                .where(McpToolCache.server_id == server_id)
                .values(is_published=False)
            )
            await session.commit()
            refs = await find_stale_tool_refs(
                session, ["get_dataset_schema", tool_name], user=user
            )
            assert [item["reason"] for item in refs] == ["tool_unpublished"]
            assert refs[0]["name"] == tool_name
            assert refs[0]["server_name"] == server_name
            assert refs[0]["display_name"] == "query"
            assert refs[0]["reason_label"] == "工具已下线"

            # 3) 服务禁用优先级高于工具下线
            await session.execute(
                update(McpServer).where(McpServer.id == server_id).values(enabled_status=0)
            )
            await session.commit()
            refs = await find_stale_tool_refs(session, [tool_name], user=user)
            assert [item["reason"] for item in refs] == ["server_disabled"]

            # 4) 远端已删除：缓存行保留但 is_available=False
            await session.execute(
                update(McpServer).where(McpServer.id == server_id).values(enabled_status=1)
            )
            await session.execute(
                update(McpToolCache)
                .where(McpToolCache.server_id == server_id)
                .values(is_published=True, is_available=False)
            )
            await session.commit()
            refs = await find_stale_tool_refs(session, [tool_name], user=user)
            assert [item["reason"] for item in refs] == ["tool_removed_remote"]

            # 5) prune：字符串引用被移除，静态工具保留；重复执行幂等
            assert await prune_agent_versions_by_names(session, {tool_name}) == 1
            await session.commit()
            version = await session.get(AIAgentVersion, version_id)
            await session.refresh(version)
            assert version.tools == ["get_dataset_schema"]
            assert await prune_agent_versions_by_names(session, {tool_name}) == 0

            # 6) 服务被删（模拟 delete_mcp_server 的落库结果）：报 server_deleted
            await session.execute(delete(McpToolCache).where(McpToolCache.server_id == server_id))
            await session.execute(delete(McpServer).where(McpServer.id == server_id))
            await session.commit()
            refs = await find_stale_tool_refs(session, [tool_name], user=user)
            assert [item["reason"] for item in refs] == ["server_deleted"]
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(McpToolCache).where(McpToolCache.server_id == server_id))
            await session.execute(delete(McpServer).where(McpServer.id == server_id))
            await session.execute(delete(AIAgentVersion).where(AIAgentVersion.id == version_id))
            await session.execute(delete(AIAgent).where(AIAgent.id == agent_id))
            await session.commit()


async def test_personal_server_of_another_user_is_not_classified_as_visible():
    suffix = uuid.uuid4().hex[:10]
    server_id = str(uuid.uuid4())
    server_name = f"mcp-private-other-{suffix}"
    tool_name = f"{server_name}:secret"
    owner = {"user_id": 111111, "user_name": f"owner-{suffix}", "role": "user"}
    outsider = {"user_id": 222222, "user_name": f"outsider-{suffix}", "role": "user"}

    async with AsyncSessionLocal() as session:
        session.add(
            McpServer(
                id=server_id,
                server_name=server_name,
                sse_url="https://example.invalid/sse",
                enabled_status=1,
                scope="personal",
                user_id=owner["user_id"],
            )
        )
        session.add(
            McpToolCache(
                id=str(uuid.uuid4()),
                server_id=server_id,
                tool_name=tool_name,
                is_published=True,
                is_available=True,
            )
        )
        await session.commit()

    try:
        async with AsyncSessionLocal() as session:
            # 属主自己：可用，不报失效
            assert await find_stale_tool_refs(session, [tool_name], user=owner) == []
            # 别人引用它（例如分享撤回后的历史配置）：不可见，但不该泄露启用/发布状态
            refs = await find_stale_tool_refs(session, [tool_name], user=outsider)
            assert [item["reason"] for item in refs] == ["server_forbidden"]
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(McpToolCache).where(McpToolCache.server_id == server_id))
            await session.execute(delete(McpServer).where(McpServer.id == server_id))
            await session.commit()


async def test_tool_health_endpoint_returns_stale_refs_and_rejects_other_users(
    client, admin_api_key, valid_api_key
):
    """HTTP 层：管理员可读到失效清单，非 owner 一律 403。"""
    suffix = uuid.uuid4().hex[:10]
    agent_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    ghost_tool = f"mcp-public-ghost-{suffix}:query"

    async with AsyncSessionLocal() as session:
        session.add(
            AIAgent(
                id=agent_id,
                name=f"ghost-health-{suffix}",
                display_name="健康检查测试",
                # 故意不写成 TestUser 的名字：管理员走 is_admin 放行，普通用户必须被拦
                created_by="someone-else",
                is_system=False,
            )
        )
        session.add(
            AIAgentVersion(
                id=version_id,
                agent_id=agent_id,
                version_number=1,
                model_name="deepseek-chat",
                system_prompt="test",
                tools=["get_dataset_schema", ghost_tool],
                status="DRAFT",
            )
        )
        await session.commit()

    try:
        admin_headers = {"Authorization": f"Bearer {admin_api_key}"}
        resp = await client.get(
            f"/api/portal/agents/{agent_id}/versions/{version_id}/tool-health",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["agent_id"] == agent_id
        assert payload["version_id"] == version_id
        assert payload["stale_count"] == 1
        assert payload["stale_tools"][0]["name"] == ghost_tool
        assert payload["stale_tools"][0]["reason"] == "server_deleted"
        assert payload["stale_tools"][0]["reason_label"] == "MCP 服务已删除"

        # 版本不属于该智能体 → 404，而不是返回别的版本配置
        resp = await client.get(
            f"/api/portal/agents/{agent_id}/versions/{uuid.uuid4()}/tool-health",
            headers=admin_headers,
        )
        assert resp.status_code == 404

        # 非 owner 的普通用户：403
        user_headers = {"Authorization": f"Bearer {valid_api_key}"}
        resp = await client.get(
            f"/api/portal/agents/{agent_id}/versions/{version_id}/tool-health",
            headers=user_headers,
        )
        assert resp.status_code == 403
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(AIAgentVersion).where(AIAgentVersion.id == version_id))
            await session.execute(delete(AIAgent).where(AIAgent.id == agent_id))
            await session.commit()


async def test_delete_mcp_server_prunes_agent_version_refs(client, admin_api_key):
    """删除服务 = 删缓存 + 删服务 + 清理版本引用，一次事务内完成。"""
    suffix = uuid.uuid4().hex[:10]
    server_id = str(uuid.uuid4())
    server_name = f"mcp-public-prune-{suffix}"
    tool_name = f"{server_name}:query"
    agent_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())

    async with AsyncSessionLocal() as session:
        session.add(
            McpServer(
                id=server_id,
                server_name=server_name,
                sse_url="https://example.invalid/sse",
                enabled_status=1,
                scope="global",
            )
        )
        session.add(
            McpToolCache(
                id=str(uuid.uuid4()),
                server_id=server_id,
                tool_name=tool_name,
                is_published=True,
                is_available=True,
            )
        )
        session.add(
            AIAgent(
                id=agent_id,
                name=f"ghost-prune-{suffix}",
                display_name="删除清理测试",
                created_by="someone-else",
                is_system=False,
            )
        )
        session.add(
            AIAgentVersion(
                id=version_id,
                agent_id=agent_id,
                version_number=1,
                model_name="deepseek-chat",
                system_prompt="test",
                tools=[tool_name, "get_dataset_schema"],
                status="PUBLISHED",
            )
        )
        await session.commit()

    headers = {"Authorization": f"Bearer {admin_api_key}"}
    try:
        resp = await client.delete(f"/api/portal/mcp/servers/{server_id}", headers=headers)
        assert resp.status_code == 200
        payload = resp.json()
        assert tool_name in payload["removed_tool_names"]
        assert payload["affected_agent_versions"] >= 1

        # 已发布版本也在清理范围内：运行时用的就是它，不清就会继续出现「工具不存在」
        async with AsyncSessionLocal() as session:
            version = await session.get(AIAgentVersion, version_id)
            assert version.tools == ["get_dataset_schema"]

        health = await client.get(
            f"/api/portal/agents/{agent_id}/versions/{version_id}/tool-health",
            headers=headers,
        )
        assert health.status_code == 200
        assert health.json()["stale_count"] == 0
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(McpToolCache).where(McpToolCache.server_id == server_id))
            await session.execute(delete(McpServer).where(McpServer.id == server_id))
            await session.execute(delete(AIAgentVersion).where(AIAgentVersion.id == version_id))
            await session.execute(delete(AIAgent).where(AIAgent.id == agent_id))
            await session.commit()


async def test_tool_health_post_checks_unsaved_tools(client, admin_api_key, valid_api_key):
    """新建/克隆版本没有版本 id：用请求体里的 tools 做同样的只读判定。"""
    suffix = uuid.uuid4().hex[:10]
    agent_id = str(uuid.uuid4())
    ghost_tool = f"mcp-public-ghost-{suffix}:query"

    async with AsyncSessionLocal() as session:
        session.add(
            AIAgent(
                id=agent_id,
                name=f"ghost-draft-{suffix}",
                display_name="草稿工具诊断",
                created_by="someone-else",
                is_system=False,
            )
        )
        await session.commit()

    admin_headers = {"Authorization": f"Bearer {admin_api_key}"}
    try:
        resp = await client.post(
            f"/api/portal/agents/{agent_id}/tool-health",
            json={"tools": ["get_dataset_schema", ghost_tool]},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["version_id"] is None
        assert payload["stale_count"] == 1
        assert payload["stale_tools"][0]["name"] == ghost_tool
        assert payload["stale_tools"][0]["reason"] == "server_deleted"

        # 空的待保存配置：正常返回 0，不报错
        resp = await client.post(
            f"/api/portal/agents/{agent_id}/tool-health",
            json={"tools": []},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["stale_count"] == 0

        # 非 owner 的普通用户：403
        resp = await client.post(
            f"/api/portal/agents/{agent_id}/tool-health",
            json={"tools": [ghost_tool]},
            headers={"Authorization": f"Bearer {valid_api_key}"},
        )
        assert resp.status_code == 403
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(AIAgent).where(AIAgent.id == agent_id))
            await session.commit()
