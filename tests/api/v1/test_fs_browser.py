import pytest
import os
from httpx import AsyncClient, ASGITransport
from app.main import app

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
        assert isinstance(data["data"]["items"], list)

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
