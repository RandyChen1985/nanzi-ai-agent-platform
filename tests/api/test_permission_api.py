import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.auth_service import AuthService

@pytest.fixture
async def admin_token(db_session):
    # Ensure admin exists
    await AuthService.generate_api_key("test_api_admin", role="admin", db=db_session)
    # Re-fetch to get key (in real test, we might mock or return from generate)
    # Simplification: Generate temp key directly
    # Ideally reuse fixture logic, but for now:
    from app.models.user import User
    from sqlalchemy import select
    user = (await db_session.execute(select(User).where(User.user_name == "test_api_admin"))).scalar_one()
    
    # We need the PLAIN key, which generate_api_key returns but we didn't capture properly in fixture.
    # Let's use a helper or modify fixture. 
    # For now, let's create a fresh user and key in test.
    return None

@pytest.mark.asyncio
async def test_permission_api_lifecycle(db_session):
    import uuid
    suffix = str(uuid.uuid4())[:8]
    admin_name = f"api_admin_{suffix}"
    user_name = f"api_user_{suffix}"

    # 1. Create Admin
    admin_key = await AuthService.generate_api_key(admin_name, role="admin", db=db_session)
    
    # 2. Create Target User
    user_key = await AuthService.generate_api_key(user_name, role="user", db=db_session)
    from app.models.user import User
    from sqlalchemy import select
    target_user = (await db_session.execute(select(User).where(User.user_name == user_name))).scalar_one()
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 3. Get Permissions (Empty initially)
        resp = await client.get(
            f"/api/portal/management/users/{target_user.id}/permissions",
            headers={"X-API-Key": admin_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["permissions"]["agents"] == []

        # 4. Update Permissions
        update_payload = {
            "agents": ["agent-x"],
            "datasets": ["ds-y"],
            "apis": [],
            "metadata": [],
            "forbidden_tools": ["exec_command"],
            "forbidden_commands": ["rm", "shutdown"]
        }
        resp = await client.put(
            f"/api/portal/management/users/{target_user.id}/permissions",
            json=update_payload,
            headers={"X-API-Key": admin_key}
        )
        assert resp.status_code == 200

        # 5. Verify Update
        resp = await client.get(
            f"/api/portal/management/users/{target_user.id}/permissions",
            headers={"X-API-Key": admin_key}
        )
        data = resp.json()
        assert "agent-x" in data["permissions"]["agents"]
        assert "ds-y" in data["permissions"]["datasets"]
        assert "exec_command" in data["permissions"]["forbidden_tools"]
        assert "rm" in data["permissions"]["forbidden_commands"]

    # Cleanup
    await db_session.delete(target_user)
    # Admin cleanup omitted for brevity
