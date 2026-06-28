import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.auth_service import AuthService
from app.models.agent import AIAgent
from sqlalchemy import select
import uuid

# Helper to create unique data
def unique_id():
    return str(uuid.uuid4())[:8]

@pytest.mark.asyncio
async def test_agent_crud_lifecycle(db_session):
    """
    Test the full lifecycle of an Agent: Create -> Read -> Update -> Delete
    """
    # 1. Setup Admin User (Required for management)
    admin_key = await AuthService.generate_api_key(f"test_admin_{unique_id()}", role="admin", db=db_session)
    
    agent_name = f"test-agent-{unique_id()}"
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        
        # --- CREATE ---
        create_payload = {
            "name": agent_name,
            "display_name": "Test Agent",
            "description": "Created by automated test",
            "is_system": False,
            "is_enabled": True,
            "engine_type": "LOCAL",
            "engine_config": {"dataset_ids": []}
        }
        
        resp = await client.post(
            "/api/portal/agents/",
            json=create_payload,
            headers={"X-API-Key": admin_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == agent_name
        agent_id = data["id"]
        
        # --- READ (List) ---
        resp = await client.get("/api/portal/agents/", headers={"X-API-Key": admin_key})
        assert resp.status_code == 200
        agents = resp.json()
        assert any(a["id"] == agent_id for a in agents)
        
        # --- UPDATE ---
        # Note: PUT requires full payload if schema is AIAgentBase (name is required)
        update_payload = {
            "name": agent_name,
            "display_name": "Updated Test Agent",
            "is_enabled": False,
            "engine_type": "LOCAL" # Engine type might default but name is certainly required
        }
        resp = await client.put(
            f"/api/portal/agents/{agent_id}",
            json=update_payload,
            headers={"X-API-Key": admin_key}
        )
        assert resp.status_code == 200
        updated_data = resp.json()
        assert updated_data["display_name"] == "Updated Test Agent"
        assert updated_data["is_enabled"] is False
        
        # --- DELETE ---
        resp = await client.delete(
            f"/api/portal/agents/{agent_id}",
            headers={"X-API-Key": admin_key}
        )
        assert resp.status_code == 200
        
        # Verify Deletion
        resp = await client.get("/api/portal/agents/", headers={"X-API-Key": admin_key})
        agents = resp.json()
        assert not any(a["id"] == agent_id for a in agents)

@pytest.mark.asyncio
async def test_agent_permission_control(db_session):
    """
    Verify that non-admin users cannot create or delete system agents (if logic exists),
    or at least verify basic access control.
    """
    user_key = await AuthService.generate_api_key(f"test_user_{unique_id()}", role="user", db=db_session)
    
    agent_name = f"sys-agent-{unique_id()}"
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        
        # 1. User tries to create SYSTEM agent (Should be forbidden or coerced)
        payload = {
            "name": agent_name,
            "display_name": "System Agent Attempt",
            "is_system": True 
        }
        
        resp = await client.post(
            "/api/portal/agents/",
            json=payload,
            headers={"X-API-Key": user_key}
        )
        
        # If user can create, is_system should be forced to False
        if resp.status_code == 200:
            data = resp.json()
            assert data["is_system"] is False, "Normal user should not create system agents"
            
            # Cleanup
            await client.delete(f"/api/portal/agents/{data['id']}", headers={"X-API-Key": user_key})

@pytest.mark.asyncio
async def test_duplicate_agent_name(db_session):
    """
    Verify unique constraint on agent name.
    """
    admin_key = await AuthService.generate_api_key(f"test_admin_d_{unique_id()}", role="admin", db=db_session)
    name = f"dup-agent-{unique_id()}"
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"name": name, "display_name": "Original"}
        
        # 1. Create First
        resp = await client.post("/api/portal/agents/", json=payload, headers={"X-API-Key": admin_key})
        assert resp.status_code == 200
        id1 = resp.json()["id"]
        
        # 2. Create Duplicate
        resp = await client.post("/api/portal/agents/", json=payload, headers={"X-API-Key": admin_key})
        # Should be 400 or 409 or 500(IntegrityError handled?)
        # Our global exception handler maps integrity error to 500 or 400 depending on implementation.
        # But commonly we expect failure.
        assert resp.status_code != 200
        
        # Cleanup
        await client.delete(f"/api/portal/agents/{id1}", headers={"X-API-Key": admin_key})

@pytest.mark.asyncio
async def test_agent_reorder(db_session):
    """Verify batch reorder updates sort_order (desc: higher value appears first)."""
    admin_key = await AuthService.generate_api_key(f"test_admin_r_{unique_id()}", role="admin", db=db_session)
    user_key = await AuthService.generate_api_key(f"test_user_r_{unique_id()}", role="user", db=db_session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created_ids = []
        for idx, name_suffix in enumerate(["a", "b", "c"]):
            resp = await client.post(
                "/api/portal/agents/",
                json={
                    "name": f"reorder-agent-{name_suffix}-{unique_id()}",
                    "display_name": f"Agent {name_suffix.upper()}",
                    "sort_order": (3 - idx) * 10,
                },
                headers={"X-API-Key": admin_key},
            )
            assert resp.status_code == 200
            created_ids.append(resp.json()["id"])

        # Reverse order: last agent should become first
        reorder_payload = {
            "items": [
                {"id": created_ids[2], "sort_order": 30},
                {"id": created_ids[0], "sort_order": 20},
                {"id": created_ids[1], "sort_order": 10},
            ]
        }
        resp = await client.post(
            "/api/portal/agents/reorder",
            json=reorder_payload,
            headers={"X-API-Key": admin_key},
        )
        assert resp.status_code == 200

        resp = await client.get("/api/portal/agents/", headers={"X-API-Key": admin_key})
        assert resp.status_code == 200
        ordered = [a["id"] for a in resp.json() if a["id"] in created_ids]
        assert ordered[0] == created_ids[2]

        # Non-admin cannot reorder
        resp = await client.post(
            "/api/portal/agents/reorder",
            json=reorder_payload,
            headers={"X-API-Key": user_key},
        )
        assert resp.status_code == 403

        for agent_id in created_ids:
            await client.delete(f"/api/portal/agents/{agent_id}", headers={"X-API-Key": admin_key})
