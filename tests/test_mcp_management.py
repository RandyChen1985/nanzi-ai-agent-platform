import pytest
import uuid
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock
from app.models.mcp import McpServer, McpToolCache
from app.models.agent import AIAgent, AIAgentVersion
from app.models.user import User
from app.core.context import AgentContext, agent_context
from app.services.ai.tools.registry import ToolRegistry
from sqlalchemy import select, delete, update


@pytest.mark.asyncio
async def test_mcp_server_name_cannot_duplicate_between_global_and_personal(
    client: AsyncClient,
    admin_api_key: str,
):
    headers = {"Authorization": f"Bearer {admin_api_key}"}
    # V174 之后个人 MCP 的写操作同样要求 element:mcp_management:create，而 conftest 的
    # test_user 没有绑定任何业务角色（真实注册用户会绑 default 角色），拿不到该功能点。
    # 本用例验证「名称跨 scope 唯一」，与操作者角色无关，故统一用管理员账号覆盖。
    personal_headers = headers
    server_name = f"Shared MCP {uuid.uuid4().hex[:8]}"

    with patch(
        "app.api.portal.endpoints.mcp.McpClientService.sync_tools",
        new_callable=AsyncMock,
    ):
        global_resp = await client.post(
            "/api/portal/mcp/servers",
            json={
                "server_name": server_name,
                "sse_url": f"https://global.example/{uuid.uuid4().hex}",
                "scope": "global",
            },
            headers=headers,
        )
        assert global_resp.status_code == 200
        global_server_id = global_resp.json()["id"]

        personal_resp = await client.post(
            "/api/portal/mcp/servers",
            json={
                "server_name": server_name,
                "sse_url": f"https://personal.example/{uuid.uuid4().hex}",
                "scope": "personal",
            },
            headers=personal_headers,
        )

    assert personal_resp.status_code == 400
    assert "服务显示名称" in personal_resp.json()["message"]
    await client.delete(f"/api/portal/mcp/servers/{global_server_id}", headers=headers)


@pytest.mark.asyncio
async def test_mcp_server_update_rejects_name_used_by_other_scope(
    client: AsyncClient,
    admin_api_key: str,
):
    admin_headers = {"Authorization": f"Bearer {admin_api_key}"}
    # 同上：个人 MCP 的创建/更新受 element:mcp_management:* 约束，测试账号无角色授权。
    personal_headers = admin_headers
    existing_name = f"Existing MCP {uuid.uuid4().hex[:8]}"

    with patch(
        "app.api.portal.endpoints.mcp.McpClientService.sync_tools",
        new_callable=AsyncMock,
    ):
        existing_resp = await client.post(
            "/api/portal/mcp/servers",
            json={
                "server_name": existing_name,
                "sse_url": f"https://global.example/{uuid.uuid4().hex}",
                "scope": "global",
            },
            headers=admin_headers,
        )
        assert existing_resp.status_code == 200
        existing_server_id = existing_resp.json()["id"]

        personal_resp = await client.post(
            "/api/portal/mcp/servers",
            json={
                "server_name": f"Personal MCP {uuid.uuid4().hex[:8]}",
                "sse_url": f"https://personal.example/{uuid.uuid4().hex}",
                "scope": "personal",
            },
            headers=personal_headers,
        )
        assert personal_resp.status_code == 200
        personal_server_id = personal_resp.json()["id"]

        update_resp = await client.put(
            f"/api/portal/mcp/servers/{personal_server_id}",
            json={
                "server_name": existing_name,
                "sse_url": f"https://personal.example/{uuid.uuid4().hex}",
                "scope": "personal",
            },
            headers=personal_headers,
        )

    assert update_resp.status_code == 400
    assert "服务显示名称" in update_resp.json()["message"]
    await client.delete(f"/api/portal/mcp/servers/{existing_server_id}", headers=admin_headers)
    await client.delete(f"/api/portal/mcp/servers/{personal_server_id}", headers=personal_headers)


@pytest.mark.asyncio
async def test_runtime_mcp_visibility_is_global_plus_current_user(
    db_session,
):
    user_result = await db_session.execute(
        select(User).where(User.user_name == "test_user")
    )
    current_user = user_result.scalar_one()
    suffix = uuid.uuid4().hex[:10]
    global_server_id = f"global-visible-{suffix}"
    personal_server_id = f"personal-visible-{suffix}"
    global_tool_name = f"global-visible-{suffix}:search"
    personal_tool_name = f"personal-visible-{suffix}:search"

    db_session.add_all(
        [
            McpServer(
                id=global_server_id,
                server_name=f"Global Visible {suffix}",
                sse_url=f"https://global.example/{suffix}",
                scope="global",
                enabled_status=1,
            ),
            McpServer(
                id=personal_server_id,
                server_name=f"Personal Visible {suffix}",
                sse_url=f"https://personal.example/{suffix}",
                scope="personal",
                user_id=current_user.id,
                enabled_status=1,
            ),
            McpToolCache(
                id=f"global-tool-{suffix}",
                server_id=global_server_id,
                tool_name=global_tool_name,
                tool_description="Global test tool",
                parameter_schema='{"type":"object"}',
                is_published=True,
            ),
            McpToolCache(
                id=f"personal-tool-{suffix}",
                server_id=personal_server_id,
                tool_name=personal_tool_name,
                tool_description="Personal test tool",
                parameter_schema='{"type":"object"}',
                is_published=True,
            ),
        ]
    )
    await db_session.commit()

    ToolRegistry._db_tool_cache.clear()
    ToolRegistry._db_tool_source_cache.clear()
    ToolRegistry._db_tool_ids_fetched_at.clear()
    current_token = agent_context.set(
        AgentContext(agent_id="visibility-test", agent_name="Main", user_id=current_user.id)
    )
    try:
        assert (await ToolRegistry.get_runtime_tool(global_tool_name)).source_type == "mcp"
        assert (await ToolRegistry.get_runtime_tool(personal_tool_name)).source_type == "mcp"

        other_token = agent_context.set(
            AgentContext(agent_id="visibility-test", agent_name="Main", user_id=999999999)
        )
        try:
            assert (await ToolRegistry.get_runtime_tool(global_tool_name)).source_type == "mcp"
            assert await ToolRegistry.get_runtime_tool(personal_tool_name) is None
        finally:
            agent_context.reset(other_token)
    finally:
        agent_context.reset(current_token)
        ToolRegistry._db_tool_cache.clear()
        ToolRegistry._db_tool_source_cache.clear()
        ToolRegistry._db_tool_ids_fetched_at.clear()
        await db_session.execute(
            delete(McpToolCache).where(
                McpToolCache.server_id.in_([global_server_id, personal_server_id])
            )
        )
        await db_session.execute(
            delete(McpServer).where(
                McpServer.id.in_([global_server_id, personal_server_id])
            )
        )
        await db_session.commit()


@pytest.mark.asyncio
async def test_mcp_usage_reports_distinct_agents_and_active_published_agents(
    client: AsyncClient,
    admin_api_key: str,
    db_session,
):
    suffix = uuid.uuid4().hex[:10]
    server_id = f"usage-server-{suffix}"
    tool_name = f"usage-server-{suffix}:search"
    agent_one_id = f"usage-agent-one-{suffix}"
    agent_two_id = f"usage-agent-two-{suffix}"

    db_session.add_all(
        [
            McpServer(
                id=server_id,
                server_name=f"Usage Server {suffix}",
                sse_url=f"https://usage.example/{suffix}",
                scope="global",
                enabled_status=1,
            ),
            McpToolCache(
                id=f"usage-tool-{suffix}",
                server_id=server_id,
                tool_name=tool_name,
                tool_description="Usage test tool",
                parameter_schema='{"type":"object"}',
                is_published=True,
            ),
            AIAgent(
                id=agent_one_id,
                name=f"usage-agent-one-{suffix}",
                display_name=f"Usage Agent One {suffix}",
                is_enabled=True,
            ),
            AIAgent(
                id=agent_two_id,
                name=f"usage-agent-two-{suffix}",
                display_name=f"Usage Agent Two {suffix}",
                is_enabled=True,
            ),
            AIAgentVersion(
                id=f"usage-v1-{suffix}",
                agent_id=agent_one_id,
                version_number=1,
                system_prompt="usage test",
                tools=[tool_name],
                status="DRAFT",
            ),
            AIAgentVersion(
                id=f"usage-v2-{suffix}",
                agent_id=agent_one_id,
                version_number=2,
                system_prompt="usage test",
                tools=[{"name": tool_name}],
                status="PUBLISHED",
            ),
            AIAgentVersion(
                id=f"usage-v3-{suffix}",
                agent_id=agent_two_id,
                version_number=1,
                system_prompt="usage test",
                tools=[tool_name],
                status="DRAFT",
            ),
        ]
    )
    await db_session.commit()

    try:
        response = await client.get(
            f"/api/portal/mcp/servers/{server_id}/usage",
            headers={"Authorization": f"Bearer {admin_api_key}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["bound_agent_count"] == 2
        assert data["active_agent_count"] == 1
        assert data["bound_version_count"] == 3
        assert {item["id"] for item in data["agents"]} == {agent_one_id, agent_two_id}
        assert next(item for item in data["agents"] if item["id"] == agent_one_id)["active"] is True
        assert next(item for item in data["agents"] if item["id"] == agent_two_id)["active"] is False
    finally:
        await db_session.execute(
            delete(AIAgentVersion).where(
                AIAgentVersion.agent_id.in_([agent_one_id, agent_two_id])
            )
        )
        await db_session.execute(delete(AIAgent).where(AIAgent.id.in_([agent_one_id, agent_two_id])))
        await db_session.execute(delete(McpToolCache).where(McpToolCache.server_id == server_id))
        await db_session.execute(delete(McpServer).where(McpServer.id == server_id))
        await db_session.commit()

@pytest.mark.asyncio
async def test_mcp_server_crud(client: AsyncClient, admin_api_key: str):
    # Use random name to avoid potential unique constraints if any
    unique_name = f"Test Server {uuid.uuid4().hex[:4]}"
    # 1. Create Server
    payload = {
        "server_name": unique_name,
        "remark": "用于联调的测试 MCP",
        "sse_url": f"http://localhost:8000/sse/{uuid.uuid4().hex[:4]}",
        "auth_headers": '{"Authorization": "Bearer test"}',
        "enabled_status": 1
    }
    headers = {"Authorization": f"Bearer {admin_api_key}"}
    
    resp = await client.post("/api/portal/mcp/servers", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    server_id = data["id"]
    assert data["server_name"] == unique_name
    assert data.get("remark") == "用于联调的测试 MCP"
    assert data["published_tool_count"] == 0

    # 2. List Servers
    resp = await client.get("/api/portal/mcp/servers", headers=headers)
    assert resp.status_code == 200
    servers = resp.json()
    assert len(servers) >= 1
    found = next((s for s in servers if s["id"] == server_id), None)
    assert found is not None
    assert found.get("remark") == "用于联调的测试 MCP"

    # 3. Update Server
    update_payload = {
        "server_name": f"Updated {unique_name}",
        "remark": "更新后的备注",
        "sse_url": payload["sse_url"] + "_v2",
        "auth_headers": '{}',
        "enabled_status": 0
    }
    resp = await client.put(f"/api/portal/mcp/servers/{server_id}", json=update_payload, headers=headers)
    assert resp.status_code == 200
    updated_data = resp.json()
    assert updated_data["server_name"] == f"Updated {unique_name}"
    assert updated_data.get("remark") == "更新后的备注"

    # 4. Delete Server
    resp = await client.delete(f"/api/portal/mcp/servers/{server_id}", headers=headers)
    assert resp.status_code == 200
    
    # Verify deletion
    resp = await client.get("/api/portal/mcp/servers", headers=headers)
    servers = resp.json()
    found = next((s for s in servers if s["id"] == server_id), None)
    assert found is None


@pytest.mark.asyncio
async def test_disabling_mcp_server_does_not_trigger_resync(
    client: AsyncClient,
    admin_api_key: str,
):
    headers = {"Authorization": f"Bearer {admin_api_key}"}
    server_name = f"Disable Server {uuid.uuid4().hex[:8]}"

    with patch(
        "app.api.portal.endpoints.mcp.McpClientService.sync_tools",
        new_callable=AsyncMock,
    ) as sync_tools:
        create_resp = await client.post(
            "/api/portal/mcp/servers",
            json={
                "server_name": server_name,
                "sse_url": f"https://disable.example/{uuid.uuid4().hex}",
                "scope": "global",
                "enabled_status": 1,
            },
            headers=headers,
        )
        assert create_resp.status_code == 200
        server_id = create_resp.json()["id"]
        sync_tools.reset_mock()

        update_resp = await client.put(
            f"/api/portal/mcp/servers/{server_id}",
            json={
                "server_name": server_name,
                "sse_url": create_resp.json()["sse_url"],
                "scope": "global",
                "enabled_status": 0,
            },
            headers=headers,
        )

    assert update_resp.status_code == 200
    assert update_resp.json()["enabled_status"] == 0
    sync_tools.assert_not_awaited()
    await client.delete(f"/api/portal/mcp/servers/{server_id}", headers=headers)

@pytest.mark.asyncio
async def test_mcp_tools_sync_and_publish(client: AsyncClient, admin_api_key: str, db_session):
    # Setup: Create a server
    headers = {"Authorization": f"Bearer {admin_api_key}"}
    create_resp = await client.post("/api/portal/mcp/servers", json={
        "server_name": f"Tool Server {uuid.uuid4().hex[:4]}",
        "sse_url": f"http://mock-mcp/sse/{uuid.uuid4().hex[:4]}"
    }, headers=headers)
    server_id = create_resp.json()["id"]

    # 1. Insert Mock Tools directly into DB with RANDOM IDs
    tool1_id = f"tool-{uuid.uuid4().hex[:8]}"
    tool2_id = f"tool-{uuid.uuid4().hex[:8]}"
    
    session = db_session
    session.add(McpToolCache(
        id=tool1_id,
        server_id=server_id,
        tool_name="mock_tool_1",
        tool_description="Mock Tool 1",
        parameter_schema='{"type": "object"}',
        is_published=False
    ))
    session.add(McpToolCache(
        id=tool2_id,
        server_id=server_id,
        tool_name="mock_tool_2",
        tool_description="Mock Tool 2",
        parameter_schema='{"type": "object"}',
        is_published=True
    ))
    await session.commit()

    # 2. List Tools
    resp = await client.get(f"/api/portal/mcp/servers/{server_id}/tools", headers=headers)
    assert resp.status_code == 200
    tools = resp.json()
    assert len(tools) == 2
    t1 = next(t for t in tools if t["id"] == tool1_id)
    t2 = next(t for t in tools if t["id"] == tool2_id)
    assert t1["is_published"] is False
    assert t2["is_published"] is True

    # 3. Publish Tool 1
    resp = await client.put(f"/api/portal/mcp/tools/{tool1_id}/publish?published=true", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["is_published"] is True

    # 4. Verify Publish Status
    resp = await client.get(f"/api/portal/mcp/servers/{server_id}/tools", headers=headers)
    t1_updated = next(t for t in resp.json() if t["id"] == tool1_id)
    assert t1_updated["is_published"] is True

    # 5. Check Server Published Count
    resp = await client.get("/api/portal/mcp/servers", headers=headers)
    server_info = next(s for s in resp.json() if s["id"] == server_id)
    assert server_info["published_tool_count"] == 2

    # Cleanup
    await client.delete(f"/api/portal/mcp/servers/{server_id}", headers=headers)

@pytest.mark.asyncio
async def test_mcp_tool_execute(client: AsyncClient, admin_api_key: str, db_session):
    headers = {"Authorization": f"Bearer {admin_api_key}"}
    
    # Setup Server and Tool
    create_resp = await client.post("/api/portal/mcp/servers", json={
        "server_name": f"Exec Server {uuid.uuid4().hex[:4]}",
        "sse_url": f"http://mock-exec/sse/{uuid.uuid4().hex[:4]}"
    }, headers=headers)
    server_id = create_resp.json()["id"]
    
    tool_id = f"exec-tool-{uuid.uuid4().hex[:8]}"
    session = db_session
    session.add(McpToolCache(
        id=tool_id,
        server_id=server_id,
        tool_name="echo_tool",
        tool_description="Echoes input",
        parameter_schema='{"type": "object", "properties": {"msg": {"type": "string"}}}',
        is_published=True
    ))
    await session.commit()

    # Mock McpToolFactory.create_tool and the runtime tool execution
    with patch("app.api.portal.endpoints.mcp.McpToolFactory.create_tool") as mock_factory:
        mock_tool_instance = AsyncMock()
        mock_tool_instance.ainvoke.return_value = "Hello World"
        mock_factory.return_value = mock_tool_instance

        # Execute
        payload = {"arguments": {"msg": "Hello"}}
        resp = await client.post(f"/api/portal/mcp/tools/{tool_id}/execute", json=payload, headers=headers)
        
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        assert resp.json()["result"] == "Hello World"
        
        # Verify arguments passed
        mock_tool_instance.ainvoke.assert_called_once_with({"msg": "Hello"})

    # Cleanup
    await client.delete(f"/api/portal/mcp/servers/{server_id}", headers=headers)


@pytest.mark.asyncio
async def test_mcp_tool_mutations_enforce_platform_permissions_and_runtime_state(
    client: AsyncClient,
    admin_api_key: str,
    valid_api_key: str,
    db_session,
):
    admin_headers = {"Authorization": f"Bearer {admin_api_key}"}
    user_headers = {"Authorization": f"Bearer {valid_api_key}"}

    with patch(
        "app.api.portal.endpoints.mcp.McpClientService.sync_tools",
        new_callable=AsyncMock,
    ):
        create_resp = await client.post(
            "/api/portal/mcp/servers",
            json={
                "server_name": f"Permission Server {uuid.uuid4().hex[:8]}",
                "sse_url": f"https://permission.example/{uuid.uuid4().hex}",
                "scope": "global",
                "enabled_status": 1,
            },
            headers=admin_headers,
        )
    assert create_resp.status_code == 200
    server_id = create_resp.json()["id"]
    tool_id = f"permission-tool-{uuid.uuid4().hex[:8]}"
    db_session.add(
        McpToolCache(
            id=tool_id,
            server_id=server_id,
            tool_name="permission-server:tool",
            tool_description="Permission test tool",
            parameter_schema='{"type":"object"}',
            is_published=True,
            is_available=True,
        )
    )
    await db_session.commit()

    try:
        with patch("app.api.portal.endpoints.mcp.McpToolFactory.create_tool") as factory:
            non_admin_execute = await client.post(
                f"/api/portal/mcp/tools/{tool_id}/execute",
                json={"arguments": {}},
                headers=user_headers,
            )
            assert non_admin_execute.status_code == 403
            factory.assert_not_called()

        non_admin_publish = await client.put(
            f"/api/portal/mcp/tools/{tool_id}/publish?published=false",
            headers=user_headers,
        )
        assert non_admin_publish.status_code == 403

        with patch(
            "app.api.portal.endpoints.mcp.McpClientService.sync_tools",
            new_callable=AsyncMock,
        ):
            disable_resp = await client.put(
                f"/api/portal/mcp/servers/{server_id}",
                json={
                    "server_name": create_resp.json()["server_name"],
                    "sse_url": create_resp.json()["sse_url"],
                    "scope": "global",
                    "enabled_status": 0,
                },
                headers=admin_headers,
            )
        assert disable_resp.status_code == 200

        disabled_execute = await client.post(
            f"/api/portal/mcp/tools/{tool_id}/execute",
            json={"arguments": {}},
            headers=admin_headers,
        )
        assert disabled_execute.status_code == 409

        await db_session.execute(
            update(McpServer)
            .where(McpServer.id == server_id)
            .values(enabled_status=1)
        )
        await db_session.execute(
            update(McpToolCache)
            .where(McpToolCache.id == tool_id)
            .values(is_published=False)
        )
        await db_session.commit()
        unpublished_execute = await client.post(
            f"/api/portal/mcp/tools/{tool_id}/execute",
            json={"arguments": {}},
            headers=admin_headers,
        )
        assert unpublished_execute.status_code == 409
    finally:
        await client.delete(f"/api/portal/mcp/servers/{server_id}", headers=admin_headers)


@pytest.mark.asyncio
async def test_mcp_server_rename_migrates_cached_tools_and_agent_versions(
    client: AsyncClient,
    admin_api_key: str,
    db_session,
):
    headers = {"Authorization": f"Bearer {admin_api_key}"}
    suffix = uuid.uuid4().hex[:8]
    old_name = f"Rename Server {suffix}"
    new_name = f"Renamed Server {suffix}"
    old_tool_name = f"{old_name}:search"
    new_tool_name = f"{new_name}:search"
    agent_id = f"rename-agent-{suffix}"
    version_id = f"rename-version-{suffix}"

    with patch(
        "app.api.portal.endpoints.mcp.McpClientService.sync_tools",
        new_callable=AsyncMock,
    ):
        create_resp = await client.post(
            "/api/portal/mcp/servers",
            json={
                "server_name": old_name,
                "sse_url": f"https://rename.example/{suffix}",
                "scope": "global",
                "enabled_status": 1,
            },
            headers=headers,
        )
    assert create_resp.status_code == 200
    server_id = create_resp.json()["id"]

    db_session.add_all(
        [
            McpToolCache(
                id=f"rename-tool-{suffix}",
                server_id=server_id,
                tool_name=old_tool_name,
                tool_description="Rename tool",
                parameter_schema='{"type":"object"}',
                is_published=True,
                is_available=True,
            ),
            AIAgent(
                id=agent_id,
                name=f"rename-agent-name-{suffix}",
                display_name=f"Rename Agent {suffix}",
                is_enabled=True,
            ),
            AIAgentVersion(
                id=version_id,
                agent_id=agent_id,
                version_number=1,
                system_prompt="rename test",
                tools=[old_tool_name, {"name": old_tool_name, "label": "Search"}],
                status="DRAFT",
            ),
        ]
    )
    await db_session.commit()

    try:
        with patch(
            "app.api.portal.endpoints.mcp.McpClientService.sync_tools",
            new_callable=AsyncMock,
        ) as sync_tools:
            rename_resp = await client.put(
                f"/api/portal/mcp/servers/{server_id}",
                json={
                    "server_name": new_name,
                    "sse_url": create_resp.json()["sse_url"],
                    "scope": "global",
                    "enabled_status": 1,
                },
                headers=headers,
            )
        assert rename_resp.status_code == 200
        assert sync_tools.await_count == 1
        sync_call = sync_tools.await_args
        assert sync_call.args == (server_id,)
        assert "api_key" not in (sync_call.kwargs.get("user_info") or {})

        tool = (
            await db_session.execute(
                select(McpToolCache).where(McpToolCache.server_id == server_id)
            )
        ).scalar_one()
        version = await db_session.get(AIAgentVersion, version_id)
        assert tool.tool_name == new_tool_name
        assert version.tools == [
            new_tool_name,
            {"name": new_tool_name, "label": "Search"},
        ]
    finally:
        await db_session.execute(delete(AIAgentVersion).where(AIAgentVersion.id == version_id))
        await db_session.execute(delete(AIAgent).where(AIAgent.id == agent_id))
        await client.delete(f"/api/portal/mcp/servers/{server_id}", headers=headers)


@pytest.mark.asyncio
async def test_mcp_servers_can_share_same_sse_url_with_different_names(
    client: AsyncClient,
    admin_api_key: str,
):
    headers = {"Authorization": f"Bearer {admin_api_key}"}
    shared_url = f"https://shared-gateway.example/mcp/{uuid.uuid4().hex}"
    name_1 = f"Server A {uuid.uuid4().hex[:6]}"
    name_2 = f"Server B {uuid.uuid4().hex[:6]}"

    with patch(
        "app.api.portal.endpoints.mcp.McpClientService.sync_tools",
        new_callable=AsyncMock,
    ):
        resp1 = await client.post(
            "/api/portal/mcp/servers",
            json={
                "server_name": name_1,
                "sse_url": shared_url,
                "scope": "global",
            },
            headers=headers,
        )
        assert resp1.status_code == 200
        server_id_1 = resp1.json()["id"]

        resp2 = await client.post(
            "/api/portal/mcp/servers",
            json={
                "server_name": name_2,
                "sse_url": shared_url,
                "scope": "global",
            },
            headers=headers,
        )
        assert resp2.status_code == 200
        server_id_2 = resp2.json()["id"]

    await client.delete(f"/api/portal/mcp/servers/{server_id_1}", headers=headers)
    await client.delete(f"/api/portal/mcp/servers/{server_id_2}", headers=headers)

