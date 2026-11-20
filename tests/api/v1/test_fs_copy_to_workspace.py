"""公共只读文件复制到本人工作区（/fs/copy-to-workspace）。

这组用例锁的是「公共文件能不能分享」的修复路径：公共目录不在分享支持的范围内，
所以给了一条「复制一份到我的工作区，再对副本分享」的通道。通道本身的安全边界
（只能从本人可读处取、只能落到本人工作区、不覆盖已有文件）必须一起锁住。

**清理约束**：不能在真实 `data/docs` 下直接建测试文件——那是用户可见的公共目录，
上一次就这么把 copy-*.md 留在了里面。本模块用 `public_docs_dir` 在 docs 下开一个
唯一命名的子目录，并在测试结束时整个删掉；复制到工作区的副本由 `created_paths`
统一回收。用不了 `tmp_path`：`assert_path_allowed` 只允许公共根内的路径，
临时目录会被判越权。
"""

import os
import shutil
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.utils.fs_paths import get_data_base_dir


@pytest.fixture
def public_docs_dir():
    """公共 docs 下的一次性目录，用例结束后整个移除。"""
    path = os.path.join(get_data_base_dir(), "docs", f"__copy_test_{uuid.uuid4().hex[:10]}")
    os.makedirs(path, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def created_paths():
    """收集复制出来的副本路径，用例结束后删掉，不在测试账户工作区留垃圾。"""
    paths: list[str] = []
    try:
        yield paths
    finally:
        for item in paths:
            try:
                os.remove(item)
            except OSError:
                pass


@pytest.mark.asyncio
async def test_copy_public_file_lands_in_private_workspace(db_session, valid_api_key, public_docs_dir, created_paths):
    """公共文件复制成功，副本在本人工作区里，且内容一致。"""
    source = os.path.join(public_docs_dir, "manual.md")
    with open(source, "w", encoding="utf-8") as fh:
        fh.write("# 平台手册\n\n复制测试内容\n")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/chat/fs/copy-to-workspace",
            headers={"X-API-Key": valid_api_key},
            json={"source_path": source},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    created_paths.append(data["path"])

    # 副本必须落在 agent_workspaces/<本人> 之内——这正是分享所要求的路径前提
    assert os.path.isfile(data["path"])
    assert "/agent_workspaces/" in data["path"].replace(os.sep, "/")
    assert data["size"] == os.path.getsize(source)
    with open(data["path"], encoding="utf-8") as fh:
        assert fh.read() == "# 平台手册\n\n复制测试内容\n"
    # 源文件必须原样保留：复制不是移动
    assert os.path.isfile(source)


@pytest.mark.asyncio
async def test_copy_does_not_overwrite_existing_file(db_session, valid_api_key, public_docs_dir, created_paths):
    """重名时分配新名字，绝不覆盖已有文件。"""
    source = os.path.join(public_docs_dir, "dup.md")
    with open(source, "w", encoding="utf-8") as fh:
        fh.write("公共内容\n")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/chat/fs/copy-to-workspace",
            headers={"X-API-Key": valid_api_key},
            json={"source_path": source},
        )
        second = await client.post(
            "/api/v1/chat/fs/copy-to-workspace",
            headers={"X-API-Key": valid_api_key},
            json={"source_path": source},
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    created_paths.extend([first.json()["data"]["path"], second.json()["data"]["path"]])
    assert first.json()["data"]["path"] != second.json()["data"]["path"]
    for resp in (first, second):
        with open(resp.json()["data"]["path"], encoding="utf-8") as fh:
            assert fh.read() == "公共内容\n"


@pytest.mark.asyncio
async def test_copy_rejects_unknown_source(db_session, valid_api_key, public_docs_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/chat/fs/copy-to-workspace",
            headers={"X-API-Key": valid_api_key},
            json={"source_path": os.path.join(public_docs_dir, "no-such-file-xyz.md")},
        )
    assert resp.status_code == 404
    assert "不存在" in resp.text


@pytest.mark.asyncio
async def test_copy_rejects_directory_source(db_session, valid_api_key, public_docs_dir):
    """只复制文件：目录要递归+预算，不在「复制后分享」的场景里。"""
    src_dir = os.path.join(public_docs_dir, "a-directory")
    os.makedirs(src_dir, exist_ok=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/chat/fs/copy-to-workspace",
            headers={"X-API-Key": valid_api_key},
            json={"source_path": src_dir},
        )
    assert resp.status_code == 400
    assert "目录" in resp.text


@pytest.mark.asyncio
async def test_copy_rejects_target_outside_private_workspace(db_session, valid_api_key, public_docs_dir):
    """目标必须是本人工作区：否则「公共目录只读」就形同虚设。"""
    source = os.path.join(public_docs_dir, "target-guard.md")
    with open(source, "w", encoding="utf-8") as fh:
        fh.write("x\n")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/chat/fs/copy-to-workspace",
            headers={"X-API-Key": valid_api_key},
            json={"source_path": source, "target_dir": public_docs_dir},
        )
    assert resp.status_code in (400, 403), resp.text
    # 越权目标不得留下任何副本
    assert os.path.isfile(source)


@pytest.mark.asyncio
async def test_copy_rejects_other_users_workspace_as_source(db_session, valid_api_key):
    """不能把别人的工作区文件「复制」进自己这里——那是横向读取的入口。"""
    base = get_data_base_dir()
    other_dir = os.path.join(base, "agent_workspaces", "other_user__999")
    os.makedirs(other_dir, exist_ok=True)
    other_file = os.path.join(other_dir, "secret.txt")
    with open(other_file, "w", encoding="utf-8") as fh:
        fh.write("别人的东西\n")

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/chat/fs/copy-to-workspace",
                headers={"X-API-Key": valid_api_key},
                json={"source_path": other_file},
            )
        assert resp.status_code in (400, 403), resp.text
    finally:
        try:
            os.remove(other_file)
        except OSError:
            pass
