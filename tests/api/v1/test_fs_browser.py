import pytest
import os
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.utils.fs_access import get_user_private_workspace_root
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
    base = get_data_base_dir()
    uploads = os.path.join(base, "uploads")
    os.makedirs(uploads, exist_ok=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/chat/fs/list",
            params={"path": os.path.join(uploads, "nonexistent_folder_abc")},
            headers={"X-API-Key": valid_api_key}
        )
        assert resp.status_code == 404
        assert "不存在" in resp.json()["message"]

@pytest.mark.asyncio
async def test_write_own_workspace_text_file(db_session, valid_api_key):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        me_resp = await client.get(
            "/api/portal/auth/me",
            headers={"X-API-Key": valid_api_key},
        )
        assert me_resp.status_code == 200
        workspace_root_path = get_user_private_workspace_root(me_resp.json()["data"])
        assert workspace_root_path
        os.makedirs(workspace_root_path, exist_ok=True)

        list_resp = await client.get(
            "/api/v1/chat/fs/list",
            headers={"X-API-Key": valid_api_key},
        )
        assert list_resp.status_code == 200
        items = list_resp.json()["data"]["items"]
        workspace_root = next(
            (item["path"] for item in items if item.get("is_user_workspace")),
            None,
        )
        assert workspace_root, "expected user workspace root in virtual list"

        target = os.path.join(workspace_root, "conv-write-test", "editable.txt")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("before")

        write_resp = await client.put(
            "/api/v1/chat/fs/write",
            json={"path": target, "content": "after edit"},
            headers={"X-API-Key": valid_api_key},
        )
        assert write_resp.status_code == 200
        assert write_resp.json()["data"]["path"] == os.path.normpath(target)
        with open(target, encoding="utf-8") as handle:
            assert handle.read() == "after edit"


@pytest.mark.asyncio
async def test_regular_user_cannot_write_public_uploads(db_session, valid_api_key):
    base = get_data_base_dir()
    target = os.path.join(base, "uploads", "should-not-write.txt")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("public")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/chat/fs/write",
            json={"path": target, "content": "hack"},
            headers={"X-API-Key": valid_api_key},
        )
        assert resp.status_code == 403
        with open(target, encoding="utf-8") as handle:
            assert handle.read() == "public"


@pytest.mark.asyncio
async def test_regular_user_cannot_write_other_workspace_file(db_session, valid_api_key):
    base = get_data_base_dir()
    target = os.path.join(base, "agent_workspaces", "other_user__999", "secret.txt")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("secret")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/chat/fs/write",
            json={"path": target, "content": "hack"},
            headers={"X-API-Key": valid_api_key},
        )
        assert resp.status_code == 403
        with open(target, encoding="utf-8") as handle:
            assert handle.read() == "secret"
