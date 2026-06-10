import pytest
import uuid
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock
from app.models.mcp import McpServer, McpToolCache
from sqlalchemy import select, delete

@pytest.mark.asyncio
async def test_mcp_server_crud(client: AsyncClient, admin_api_key: str):
    # Use random name to avoid potential unique constraints if any
    unique_name = f"Test Server {uuid.uuid4().hex[:4]}"
    # 1. Create Server
    payload = {
        "server_name": unique_name,
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
    assert data["published_tool_count"] == 0

    # 2. List Servers
    resp = await client.get("/api/portal/mcp/servers", headers=headers)
    assert resp.status_code == 200
    servers = resp.json()
    assert len(servers) >= 1
    found = next((s for s in servers if s["id"] == server_id), None)
    assert found is not None

    # 3. Update Server
    update_payload = {
        "server_name": f"Updated {unique_name}",
        "sse_url": payload["sse_url"] + "_v2",
        "auth_headers": '{}',
        "enabled_status": 0
    }
    resp = await client.put(f"/api/portal/mcp/servers/{server_id}", json=update_payload, headers=headers)
    assert resp.status_code == 200
    updated_data = resp.json()
    assert updated_data["server_name"] == f"Updated {unique_name}"

    # 4. Delete Server
    resp = await client.delete(f"/api/portal/mcp/servers/{server_id}", headers=headers)
    assert resp.status_code == 200
    
    # Verify deletion
    resp = await client.get("/api/portal/mcp/servers", headers=headers)
    servers = resp.json()
    found = next((s for s in servers if s["id"] == server_id), None)
    assert found is None

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
