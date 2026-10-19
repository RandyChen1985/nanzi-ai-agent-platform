"""工作区文件「通用下载」端点契约。

背景：工作区浏览器的下载按钮原先复用 ``/api/v1/chat/fs/preview``，
而预览按扩展名白名单（图片/文本/PDF/Office）放行，导致 .zip 等二进制文件
在列表里看得见、点下载却得到 400「不支持预览该类型的文件」。

本文件锁定两条语义的分离：
  - ``/fs/preview``：仍然是白名单语义，zip 继续被拒（画布不能乱渲染二进制）；
  - ``/fs/download``：归属校验与 preview 完全一致，但不限制 MIME，
    以 attachment + application/octet-stream 原样返回字节。
"""

import os
import zipfile
from io import BytesIO

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.utils.fs_access import get_user_private_workspace_root
from app.utils.fs_paths import get_data_base_dir


async def _user_workspace_dir(client: AsyncClient, api_key: str) -> str:
    """经列表 API 取当前用户私有工作区路径，避免在测试里猜测 user_key 组成。"""
    me_resp = await client.get("/api/portal/auth/me", headers={"X-API-Key": api_key})
    assert me_resp.status_code == 200, me_resp.text
    workspace_root = get_user_private_workspace_root(me_resp.json()["data"])
    assert workspace_root
    os.makedirs(workspace_root, exist_ok=True)
    return workspace_root


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_download_zip_returns_original_bytes_as_attachment(db_session, valid_api_key):
    """zip 属于预览白名单之外的二进制：下载端点必须原样返回字节并强制 attachment。"""
    payload = _zip_bytes({"readme.txt": b"hello", "data.csv": b"a,b\n1,2\n"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        workspace_root = await _user_workspace_dir(client, valid_api_key)
        target_dir = os.path.join(workspace_root, "conv-download-test")
        os.makedirs(target_dir, exist_ok=True)
        target = os.path.join(target_dir, "交付包.zip")
        with open(target, "wb") as handle:
            handle.write(payload)

        resp = await client.get(
            "/api/v1/chat/fs/download",
            params={"path": target},
            headers={"X-API-Key": valid_api_key},
        )

    assert resp.status_code == 200, resp.text
    assert resp.content == payload
    assert resp.headers["content-type"].startswith("application/octet-stream")
    disposition = resp.headers.get("content-disposition", "")
    assert disposition.startswith("attachment")
    # 中文文件名必须走 RFC 5987 的 filename*，否则浏览器会拿到乱码或丢名字
    assert "filename*=utf-8''" in disposition.lower()


@pytest.mark.asyncio
async def test_download_does_not_limit_preview_whitelist_formats(db_session, valid_api_key):
    """非 Office/图片/文本的任意扩展名都应可下载：.7z 与无扩展名文件各验一次。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        workspace_root = await _user_workspace_dir(client, valid_api_key)
        for name in ("bundle.7z", "no-extension-file"):
            target = os.path.join(workspace_root, "conv-download-test", name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(b"\x00binary\xff")

            resp = await client.get(
                "/api/v1/chat/fs/download",
                params={"path": target},
                headers={"X-API-Key": valid_api_key},
            )

            assert resp.status_code == 200, f"{name}: {resp.text}"
            assert resp.content == b"\x00binary\xff"


@pytest.mark.asyncio
async def test_download_rejects_other_users_workspace_file(db_session, valid_api_key):
    """下载必须复用预览的归属校验，不能成为绕过越权拦截的新通道。"""
    base = get_data_base_dir()
    other_file = os.path.join(base, "agent_workspaces", "other_user__999", "private.zip")
    os.makedirs(os.path.dirname(other_file), exist_ok=True)
    with open(other_file, "wb") as handle:
        handle.write(_zip_bytes({"secret.txt": b"secret"}))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/chat/fs/download",
            params={"path": other_file},
            headers={"X-API-Key": valid_api_key},
        )

    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_download_missing_file_returns_404(db_session, valid_api_key):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        workspace_root = await _user_workspace_dir(client, valid_api_key)
        resp = await client.get(
            "/api/v1/chat/fs/download",
            params={"path": os.path.join(workspace_root, "ghost-missing.zip")},
            headers={"X-API-Key": valid_api_key},
        )

    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_download_rejects_directory_path(db_session, valid_api_key):
    """目录不是可下载实体：不能被 FileResponse 当作文件流式返回。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        workspace_root = await _user_workspace_dir(client, valid_api_key)
        directory = os.path.join(workspace_root, "conv-download-dir")
        os.makedirs(directory, exist_ok=True)

        resp = await client.get(
            "/api/v1/chat/fs/download",
            params={"path": directory},
            headers={"X-API-Key": valid_api_key},
        )

    assert resp.status_code in (400, 404), resp.text


@pytest.mark.asyncio
async def test_preview_still_rejects_zip(db_session, valid_api_key):
    """预览语义保持不变：zip 不能进画布，否则等于把二进制塞给前端渲染。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        workspace_root = await _user_workspace_dir(client, valid_api_key)
        target = os.path.join(workspace_root, "conv-download-test", "preview-guard.zip")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(_zip_bytes({"a.txt": b"a"}))

        resp = await client.get(
            "/api/v1/chat/fs/preview",
            params={"path": target},
            headers={"X-API-Key": valid_api_key},
        )

    assert resp.status_code == 400, resp.text
    assert "不支持预览" in resp.text
