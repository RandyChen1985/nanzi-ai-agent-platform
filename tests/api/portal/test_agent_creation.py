import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_create_duplicate_agent(client: AsyncClient, admin_api_key: str):
    """Test that creating an agent with a duplicate name fails with 400"""
    import time
    unique_name = f"test_agent_{int(time.time())}"
    
    # 1. Create first agent
    response = await client.post(
        "/api/portal/agents/",
        headers={"X-API-Key": admin_api_key},
        json={
            "name": unique_name,
            "display_name": "Test Agent",
            "description": "Test Description",
            "capabilities": ["test"],
            "is_system": False
        }
    )
    assert response.status_code == 200
    
    # 2. Try to create duplicate
    response_dup = await client.post(
        "/api/portal/agents/",
        headers={"X-API-Key": admin_api_key},
        json={
            "name": unique_name, # Same name
            "display_name": "Duplicate Agent",
            "description": "Should fail",
            "capabilities": [],
            "is_system": False
        }
    )
    
    assert response_dup.status_code == 400
    data = response_dup.json()
    assert "message" in data
    # 文案已改为中文并指明字段：撞名发生在「物理标识符」上，英文原文对用户不可操作
    assert "物理标识符" in data["message"]
    assert unique_name in data["message"]
