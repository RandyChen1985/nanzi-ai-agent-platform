import pytest
import os
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.utils.fs_paths import get_data_base_dir

@pytest.mark.asyncio
async def test_list_root_directory(db_session, valid_api_key):
    """
    测试获取文件系统默认根目录列表
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/chat/fs/list",
            headers={"X-API-Key": valid_api_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 200
        assert data["message"] == "success"
        assert "current_path" in data["data"]
        assert "is_root" in data["data"]
        assert data["data"]["is_root"] is True
        assert data["data"]["scope"] == "user_scoped"
        assert isinstance(data["data"]["items"], list)
        item_paths = {item["path"] for item in data["data"]["items"]}
        base = get_data_base_dir()
        assert os.path.join(base, "uploads") in item_paths or any("uploads" in p for p in item_paths)

@pytest.mark.asyncio
async def test_regular_user_cannot_list_other_workspace(db_session, valid_api_key):
    base = get_data_base_dir()
    other_dir = os.path.join(base, "agent_workspaces", "other_user__999", "conv-a")
    os.makedirs(other_dir, exist_ok=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/chat/fs/list",
            params={"path": other_dir},
            headers={"X-API-Key": valid_api_key},
        )
        assert resp.status_code == 403
        assert "越权" in resp.json()["message"]

@pytest.mark.asyncio
async def test_regular_user_cannot_preview_other_workspace_file(db_session, valid_api_key):
    base = get_data_base_dir()
    other_file = os.path.join(base, "agent_workspaces", "other_user__999", "secret.txt")
    os.makedirs(os.path.dirname(other_file), exist_ok=True)
    with open(other_file, "w", encoding="utf-8") as handle:
        handle.write("secret")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/chat/fs/preview",
            params={"path": other_file},
            headers={"X-API-Key": valid_api_key},
        )
        assert resp.status_code == 403

@pytest.mark.asyncio
async def test_list_traversal_interception(db_session, valid_api_key):
    """
    测试防路径穿越的安全防御拦截逻辑，应当返回 403
    """
    forbidden_paths = [
        "/app/data/../../../etc/passwd",
        "data/../../../etc/shadow",
        "../../",
        "data/.."
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for fpath in forbidden_paths:
            resp = await client.get(
                "/api/v1/chat/fs/list",
                params={"path": fpath},
                headers={"X-API-Key": valid_api_key}
            )
            assert resp.status_code == 403
            assert "安全越权拦截" in resp.json()["message"]

@pytest.mark.asyncio
async def test_list_nonexistent_directory(db_session, valid_api_key):
    """
    测试获取不存在的子目录，应当返回 404
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/chat/fs/list",
            params={"path": os.path.abspath("data/nonexistent_folder_abc")},
            headers={"X-API-Key": valid_api_key}
        )
        assert resp.status_code == 404
        assert "不存在" in resp.json()["message"]
