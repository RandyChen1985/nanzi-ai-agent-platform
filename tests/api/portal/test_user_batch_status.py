"""批量启用 / 禁用用户接口的行为与安全契约测试。

覆盖：
- 批量禁用 / 启用生效并能回读
- 批量禁用时自动跳过当前登录账号（skipped_self）
- 不存在的用户计入 not_found
- 参数校验（空列表 / 非法 status / 超过上限）
- 权限校验（无 element:user:edit 拒绝）
- 禁用后该用户无法再通过 API Key 认证（登不进来）
"""
import time
import uuid

import pytest
from httpx import AsyncClient


BATCH_URL = "/api/portal/management/users/batch-status"


async def _create_user(client: AsyncClient, admin_api_key: str, prefix: str) -> dict:
    """创建一个测试用户，返回包含 api_key 的用户信息。"""
    unique_name = f"{prefix}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    resp = await client.post(
        "/api/portal/management/users",
        headers={"X-API-Key": admin_api_key},
        json={"user_name": unique_name, "role": "user"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _me(client: AsyncClient, api_key: str):
    return await client.get("/api/portal/auth/me", headers={"X-API-Key": api_key})


async def _status_map(client: AsyncClient, admin_api_key: str) -> dict:
    listing = await client.get(
        "/api/portal/management/users?page=1&size=1000",
        headers={"X-API-Key": admin_api_key},
    )
    assert listing.status_code == 200, listing.text
    return {u["id"]: u["status"] for u in listing.json()["items"]}


@pytest.mark.asyncio
async def test_batch_disable_users(client: AsyncClient, admin_api_key: str):
    """批量禁用多个用户，返回明细且状态落库。"""
    user_a = await _create_user(client, admin_api_key, "batch_dis_a")
    user_b = await _create_user(client, admin_api_key, "batch_dis_b")

    resp = await client.patch(
        BATCH_URL,
        headers={"X-API-Key": admin_api_key},
        json={"user_ids": [user_a["id"], user_b["id"]], "status": 0},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["updated"] == 2
    assert data["skipped_self"] == 0
    assert data["not_found"] == 0
    assert set(data["updated_ids"]) == {user_a["id"], user_b["id"]}

    status_map = await _status_map(client, admin_api_key)
    assert status_map[user_a["id"]] == 0
    assert status_map[user_b["id"]] == 0


@pytest.mark.asyncio
async def test_batch_enable_restores_access(client: AsyncClient, admin_api_key: str):
    """批量启用可恢复被禁用用户的登录能力。"""
    user = await _create_user(client, admin_api_key, "batch_enable")

    disabled = await client.patch(
        BATCH_URL,
        headers={"X-API-Key": admin_api_key},
        json={"user_ids": [user["id"]], "status": 0},
    )
    assert disabled.status_code == 200
    assert disabled.json()["updated"] == 1

    blocked = await _me(client, user["api_key"])
    assert blocked.status_code in (401, 403), blocked.text

    enabled = await client.patch(
        BATCH_URL,
        headers={"X-API-Key": admin_api_key},
        json={"user_ids": [user["id"]], "status": 1},
    )
    assert enabled.status_code == 200
    assert enabled.json()["updated"] == 1

    restored = await _me(client, user["api_key"])
    assert restored.status_code == 200, restored.text

    status_map = await _status_map(client, admin_api_key)
    assert status_map[user["id"]] == 1


@pytest.mark.asyncio
async def test_batch_disable_skips_self(client: AsyncClient, admin_api_key: str):
    """批量禁用命中自己时自动跳过，其余目标照常执行。"""
    me_resp = await client.get(
        "/api/portal/auth/me", headers={"X-API-Key": admin_api_key}
    )
    admin_id = int(me_resp.json()["data"]["user_id"])
    other = await _create_user(client, admin_api_key, "batch_self")

    resp = await client.patch(
        BATCH_URL,
        headers={"X-API-Key": admin_api_key},
        json={"user_ids": [admin_id, other["id"]], "status": 0},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["skipped_self"] == 1
    assert data["updated"] == 1
    assert data["updated_ids"] == [other["id"]]

    # 自己仍处于启用状态，会话依旧可用（若被禁用，/auth/me 会直接失败）
    assert (await _me(client, admin_api_key)).status_code == 200


@pytest.mark.asyncio
async def test_batch_disable_only_self_returns_no_update(
    client: AsyncClient, admin_api_key: str
):
    """只选中自己时不会误更新任何账号，仍如实回报 skipped_self。"""
    me_resp = await client.get(
        "/api/portal/auth/me", headers={"X-API-Key": admin_api_key}
    )
    admin_id = int(me_resp.json()["data"]["user_id"])

    resp = await client.patch(
        BATCH_URL,
        headers={"X-API-Key": admin_api_key},
        json={"user_ids": [admin_id], "status": 0},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "updated": 0,
        "skipped_self": 1,
        "not_found": 0,
        "updated_ids": [],
    }


@pytest.mark.asyncio
async def test_batch_status_counts_unknown_users(client: AsyncClient, admin_api_key: str):
    """不存在的用户 ID 计入 not_found，不影响其他目标。"""
    user = await _create_user(client, admin_api_key, "batch_missing")

    resp = await client.patch(
        BATCH_URL,
        headers={"X-API-Key": admin_api_key},
        json={"user_ids": [user["id"], 999999999], "status": 0},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["updated"] == 1
    assert data["not_found"] == 1
    assert data["updated_ids"] == [user["id"]]


@pytest.mark.asyncio
async def test_batch_status_deduplicates_ids(client: AsyncClient, admin_api_key: str):
    """重复 ID 只按一个用户统计，避免计数虚高。"""
    user = await _create_user(client, admin_api_key, "batch_dup")

    resp = await client.patch(
        BATCH_URL,
        headers={"X-API-Key": admin_api_key},
        json={"user_ids": [user["id"], user["id"], user["id"]], "status": 0},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == 1


@pytest.mark.asyncio
async def test_batch_status_rejects_empty_ids(client: AsyncClient, admin_api_key: str):
    resp = await client.patch(
        BATCH_URL,
        headers={"X-API-Key": admin_api_key},
        json={"user_ids": [], "status": 0},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_batch_status_rejects_invalid_status(client: AsyncClient, admin_api_key: str):
    resp = await client.patch(
        BATCH_URL,
        headers={"X-API-Key": admin_api_key},
        json={"user_ids": [1], "status": 2},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_batch_status_rejects_oversized_batch(client: AsyncClient, admin_api_key: str):
    resp = await client.patch(
        BATCH_URL,
        headers={"X-API-Key": admin_api_key},
        json={"user_ids": list(range(1, 202)), "status": 0},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_batch_status_requires_permission(client: AsyncClient, valid_api_key: str):
    """普通用户没有 element:user:edit，应被拒绝。"""
    resp = await client.patch(
        BATCH_URL,
        headers={"X-API-Key": valid_api_key},
        json={"user_ids": [1], "status": 0},
    )
    assert resp.status_code == 403
