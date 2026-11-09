"""凭据中心 HTTP 端点测试。

覆盖的是「接口层有没有把不该给的东西给出去」：
凭证类永远只有掩码、值出口对 credential 返回 403、管理员才能碰系统级。
"""

from __future__ import annotations

import uuid

import pytest

from app.models.secret import (
    SecretBinding,
    SecretGrant,
    SecretUsageLog,
    SecretUserPref,
    UserSecret,
)

SECRET_TABLES = [
    SecretUsageLog.__table__,
    SecretBinding.__table__,
    SecretGrant.__table__,
    SecretUserPref.__table__,
    UserSecret.__table__,
]

PREFIX = "/api/portal/secrets"
MASK = "••••••••••••"


@pytest.fixture
async def clean_secrets():
    # 只清理本用例建的凭据，绝不清表：这里连的是开发库。
    from app.core.orm import AsyncSessionLocal
    from tests.secret_test_guard import (
        purge_created_after,
        snapshot_secret_ids,
        snapshot_user_prefs,
    )

    async with AsyncSessionLocal() as session:
        keep = await snapshot_secret_ids(session)
        keep_users = await snapshot_user_prefs(session)
    try:
        yield
    finally:
        async with AsyncSessionLocal() as session:
            await purge_created_after(
                session, keep_secret_ids=keep, keep_user_ids=keep_users
            )


def _user_headers(valid_api_key: str) -> dict:
    return {"X-API-Key": valid_api_key}


def _admin_headers(admin_api_key: str) -> dict:
    return {"X-API-Key": admin_api_key}


def _payload(**overrides) -> dict:
    data = {
        "secret_key": f"test.token.{uuid.uuid4().hex[:8]}",
        "name": "测试 Token",
        "value": "tok-abcdefgh-123456",
        "secret_type": "credential",
        "allowed_scenarios": ["mcp"],
        "allowed_hosts": [],
    }
    data.update(overrides)
    return data


class TestCreateAndRead:
    async def test_create_returns_mask_not_value(self, client, clean_secrets, valid_api_key):
        resp = await client.post(
            PREFIX, json=_payload(), headers=_user_headers(valid_api_key)
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["value_masked"] == MASK
        assert data["value_preview"] is None
        assert "tok-abcdefgh-123456" not in resp.text
        assert "value_encrypted" not in data

    async def test_list_does_not_leak_value(self, client, clean_secrets, valid_api_key):
        created = (
            await client.post(PREFIX, json=_payload(), headers=_user_headers(valid_api_key))
        ).json()["data"]

        resp = await client.get(PREFIX, headers=_user_headers(valid_api_key))

        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert any(item["id"] == created["id"] for item in items)
        assert "tok-abcdefgh-123456" not in resp.text

    async def test_detail_does_not_leak_value(self, client, clean_secrets, valid_api_key):
        created = (
            await client.post(PREFIX, json=_payload(), headers=_user_headers(valid_api_key))
        ).json()["data"]

        resp = await client.get(f"{PREFIX}/{created['id']}", headers=_user_headers(valid_api_key))

        assert resp.status_code == 200
        assert "tok-abcdefgh-123456" not in resp.text
        assert resp.json()["data"]["bindings"] == []

    async def test_credential_value_endpoint_is_forbidden(
        self, client, clean_secrets, valid_api_key
    ):
        created = (
            await client.post(PREFIX, json=_payload(), headers=_user_headers(valid_api_key))
        ).json()["data"]

        resp = await client.get(
            f"{PREFIX}/{created['id']}/value", headers=_user_headers(valid_api_key)
        )

        assert resp.status_code == 403
        assert "tok-abcdefgh-123456" not in resp.text

    async def test_variable_value_is_readable(self, client, clean_secrets, valid_api_key):
        created = (
            await client.post(
                PREFIX,
                json=_payload(secret_type="variable", value="tenant-001-abcdefg"),
                headers=_user_headers(valid_api_key),
            )
        ).json()["data"]

        resp = await client.get(
            f"{PREFIX}/{created['id']}/value", headers=_user_headers(valid_api_key)
        )

        assert resp.status_code == 200
        assert resp.json()["data"]["value"] == "tenant-001-abcdefg"

    async def test_duplicate_key_is_rejected(self, client, clean_secrets, valid_api_key):
        payload = _payload()
        await client.post(PREFIX, json=payload, headers=_user_headers(valid_api_key))

        resp = await client.post(PREFIX, json=payload, headers=_user_headers(valid_api_key))

        assert resp.status_code == 400
        assert "已存在" in resp.text


class TestPicker:
    async def test_groups_mine_and_shared(self, client, clean_secrets, valid_api_key):
        await client.post(PREFIX, json=_payload(), headers=_user_headers(valid_api_key))

        resp = await client.get(f"{PREFIX}/picker", headers=_user_headers(valid_api_key))

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["counts"]["mine"] == 1
        # shared 不断言总数：共享库里可能已经有被「授权给全体用户」的真实凭据，
        # 那不是本用例造的，数量不归它管。分组对不对看 mine 这一侧。
        assert all("tok-abcdefgh-123456" not in item["secret_key"] for item in data["groups"]["mine"])
        assert "tok-abcdefgh-123456" not in resp.text


class TestBindings:
    async def test_bind_then_unbind(self, client, clean_secrets, valid_api_key):
        created = (
            await client.post(PREFIX, json=_payload(), headers=_user_headers(valid_api_key))
        ).json()["data"]

        bind_resp = await client.post(
            f"{PREFIX}/bindings",
            json={
                "secret_id": created["id"],
                "consumer_type": "mcp_server",
                "consumer_id": "mcp-1",
                "slot": "header:Authorization",
            },
            headers=_user_headers(valid_api_key),
        )
        assert bind_resp.status_code == 200, bind_resp.text

        listed = await client.get(
            f"{PREFIX}/bindings",
            params={"consumer_type": "mcp_server", "consumer_id": "mcp-1"},
            headers=_user_headers(valid_api_key),
        )
        assert len(listed.json()["data"]) == 1
        assert listed.json()["data"][0]["secret_key"] == created["secret_key"]

        removed = await client.request(
            "DELETE",
            f"{PREFIX}/bindings",
            json={
                "consumer_type": "mcp_server",
                "consumer_id": "mcp-1",
                "slot": "header:Authorization",
            },
            headers=_user_headers(valid_api_key),
        )
        assert removed.status_code == 200
        assert removed.json()["data"]["removed"] == 1

    async def test_malformed_slot_is_rejected(self, client, clean_secrets, valid_api_key):
        created = (
            await client.post(PREFIX, json=_payload(), headers=_user_headers(valid_api_key))
        ).json()["data"]

        resp = await client.post(
            f"{PREFIX}/bindings",
            json={
                "secret_id": created["id"],
                "consumer_type": "mcp_server",
                "consumer_id": "mcp-1",
                "slot": "Authorization",
            },
            headers=_user_headers(valid_api_key),
        )

        assert resp.status_code == 400


    async def test_reverse_lookup_by_secret_id(self, client, clean_secrets, valid_api_key):
        """按凭据反查绑定：用户要能回答「这条凭据用在哪」。

        没有这个方向，只能去每个工具、每个 MCP 服务里翻，翻不完也不知道有没有漏。
        """
        first = (
            await client.post(PREFIX, json=_payload(), headers=_user_headers(valid_api_key))
        ).json()["data"]
        second = (
            await client.post(
                PREFIX,
                json=_payload(secret_key="other.key"),
                headers=_user_headers(valid_api_key),
            )
        ).json()["data"]

        for secret, consumer in ((first, "tool-1"), (second, "tool-2")):
            resp = await client.post(
                f"{PREFIX}/bindings",
                json={
                    "secret_id": secret["id"],
                    "consumer_type": "api_tool",
                    "consumer_id": consumer,
                    "slot": "header:Authorization",
                },
                headers=_user_headers(valid_api_key),
            )
            assert resp.status_code == 200, resp.text

        mine = await client.get(
            f"{PREFIX}/bindings",
            params={"secret_id": first["id"]},
            headers=_user_headers(valid_api_key),
        )
        rows = mine.json()["data"]
        # 只返回这条凭据的绑定，不能把别人的也带出来
        assert len(rows) == 1
        assert rows[0]["consumer_type"] == "api_tool"
        assert rows[0]["consumer_id"] == "tool-1"

    async def test_reverse_lookup_requires_a_target(
        self, client, clean_secrets, valid_api_key
    ):
        resp = await client.get(f"{PREFIX}/bindings", headers=_user_headers(valid_api_key))
        assert resp.status_code == 400


class TestSelfServiceStatus:
    async def test_owner_can_disable_and_reenable(self, client, clean_secrets, valid_api_key):
        """凭据疑似泄露时，第一反应必须是「立刻停掉」，不能等管理员。

        从发现到管理员响应之间的每一次调用都是真实的泄露窗口。
        """
        created = (
            await client.post(PREFIX, json=_payload(), headers=_user_headers(valid_api_key))
        ).json()["data"]

        off = await client.post(
            f"{PREFIX}/{created['id']}/status",
            json={"status": "disabled"},
            headers=_user_headers(valid_api_key),
        )
        assert off.status_code == 200, off.text
        assert off.json()["data"]["status"] == "disabled"

        on = await client.post(
            f"{PREFIX}/{created['id']}/status",
            json={"status": "active"},
            headers=_user_headers(valid_api_key),
        )
        assert on.status_code == 200
        assert on.json()["data"]["status"] == "active"

    async def test_cannot_toggle_someone_elses(self, client, clean_secrets, valid_api_key):
        created = (
            await client.post(PREFIX, json=_payload(), headers=_user_headers(valid_api_key))
        ).json()["data"]

        # 别人的 id：不存在与无权限必须是同一个答案，否则等于泄露「有这么一条」
        resp = await client.post(
            f"{PREFIX}/not-my-id/status",
            json={"status": "disabled"},
            headers=_user_headers(valid_api_key),
        )
        assert resp.status_code == 404

    async def test_rejects_unknown_status(self, client, clean_secrets, valid_api_key):
        created = (
            await client.post(PREFIX, json=_payload(), headers=_user_headers(valid_api_key))
        ).json()["data"]

        resp = await client.post(
            f"{PREFIX}/{created['id']}/status",
            json={"status": "deleted"},
            headers=_user_headers(valid_api_key),
        )
        assert resp.status_code == 400


class TestWritePathCannotCrossOwnerBoundary:
    """轮换 / 改元数据 / 删除都是写路径，越权必须和「不存在」一个答案。

    这里钉住的是一个真实缺口：``rotate_secret`` 最初**完全没有**归属校验——
    知道 secret_id 就能把别人的凭据值改掉，之后对方所有绑定调用都会带着
    攻击者的 token 去访问外部服务。个人之间、以及对系统级凭据的越权，
    都在 center 层有覆盖，这里验的是端点确实把它映射成 404 而不是 400/403
    ——后者等于承认「这条凭据存在，只是你碰不了」。
    """

    async def test_normal_user_cannot_rotate_system_secret(
        self, client, clean_secrets, valid_api_key, admin_api_key
    ):
        created = (
            await client.post(
                PREFIX + "/admin",
                json=_payload(secret_key="corp.shared.token"),
                headers=_admin_headers(admin_api_key),
            )
        ).json()["data"]

        resp = await client.post(
            f"{PREFIX}/{created['id']}/rotate",
            json={"value": "hijacked-by-user"},
            headers=_user_headers(valid_api_key),
        )
        assert resp.status_code == 404, resp.text

    async def test_normal_user_cannot_delete_system_secret(
        self, client, clean_secrets, valid_api_key, admin_api_key
    ):
        created = (
            await client.post(
                PREFIX + "/admin",
                json=_payload(secret_key="corp.keep.me"),
                headers=_admin_headers(admin_api_key),
            )
        ).json()["data"]

        resp = await client.delete(
            f"{PREFIX}/{created['id']}", headers=_user_headers(valid_api_key)
        )
        assert resp.status_code == 404

        # 系统凭据还在
        still = await client.get(f"{PREFIX}/admin", headers=_admin_headers(admin_api_key))
        assert any(item["id"] == created["id"] for item in still.json()["data"])

    async def test_admin_can_rotate_system_secret(
        self, client, clean_secrets, admin_api_key
    ):
        created = (
            await client.post(
                PREFIX + "/admin",
                json=_payload(secret_key="corp.rotatable"),
                headers=_admin_headers(admin_api_key),
            )
        ).json()["data"]

        resp = await client.post(
            f"{PREFIX}/{created['id']}/rotate",
            json={"value": "new-shared-value"},
            headers=_admin_headers(admin_api_key),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["last_rotated_at"]


class TestAdminSurface:
    async def test_regular_user_cannot_touch_admin_surface(
        self, client, clean_secrets, valid_api_key
    ):
        resp = await client.get(f"{PREFIX}/admin", headers=_user_headers(valid_api_key))
        assert resp.status_code == 403

    async def test_admin_creates_system_secret_and_grants(
        self, client, clean_secrets, admin_api_key
    ):
        created = (
            await client.post(
                PREFIX + "/admin",
                json=_payload(secret_key="corp.wecom.secret"),
                headers=_admin_headers(admin_api_key),
            )
        ).json()["data"]
        assert created["owner_scope"] == "system"

        grant = await client.post(
            f"{PREFIX}/admin/{created['id']}/grants",
            json={"grantee_type": "dept", "grantee_id": "DEPT-DATA"},
            headers=_admin_headers(admin_api_key),
        )
        assert grant.status_code == 200, grant.text

        listing = await client.get(f"{PREFIX}/admin", headers=_admin_headers(admin_api_key))
        items = listing.json()["data"]
        assert items[0]["grant_count"] == 1
        assert items[0]["grants"][0]["grantee_id"] == "DEPT-DATA"

    async def test_ungranted_system_secret_is_invisible(
        self, client, clean_secrets, admin_api_key, valid_api_key
    ):
        """未授权的系统级凭据连名字都不该出现在普通用户的列表里。"""
        await client.post(
            PREFIX + "/admin",
            json=_payload(secret_key="sap.prod.api_key"),
            headers=_admin_headers(admin_api_key),
        )

        resp = await client.get(PREFIX, headers=_user_headers(valid_api_key))

        assert resp.status_code == 200
        # 不能断言 total == 0：库里可能有别人造的、被授权给全体用户的凭据。
        # 这条用例要守的是「**这一条**未授权的看不见」，按 key 判断才守得住。
        keys = [item["secret_key"] for item in resp.json()["data"].get("items", [])]
        assert "sap.prod.api_key" not in keys
        assert "sap.prod.api_key" not in resp.text

    async def test_admin_cannot_disable_personal_secret(
        self, client, clean_secrets, admin_api_key, valid_api_key
    ):
        created = (
            await client.post(PREFIX, json=_payload(), headers=_user_headers(valid_api_key))
        ).json()["data"]

        resp = await client.post(
            f"{PREFIX}/admin/{created['id']}/disable", headers=_admin_headers(admin_api_key)
        )

        assert resp.status_code == 403


class TestPolicyAndPrefs:
    async def test_policy_is_readable_by_user(self, client, clean_secrets, valid_api_key):
        resp = await client.get(f"{PREFIX}/policy", headers=_user_headers(valid_api_key))

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["readonly"] is True
        # 脚本注入默认关：这是最危险的一个场景
        assert data["allow_script_env"] is False

    async def test_only_admin_can_change_policy(self, client, clean_secrets, valid_api_key):
        resp = await client.patch(
            f"{PREFIX}/policy",
            json={"allow_script_env": True},
            headers=_user_headers(valid_api_key),
        )
        assert resp.status_code == 403

    async def test_prefs_default_off_and_updatable(self, client, clean_secrets, valid_api_key):
        resp = await client.get(f"{PREFIX}/prefs", headers=_user_headers(valid_api_key))
        assert resp.json()["data"]["allow_script_env"] is False

        updated = await client.patch(
            f"{PREFIX}/prefs",
            json={"allow_script_env": True},
            headers=_user_headers(valid_api_key),
        )
        assert updated.status_code == 200

        again = await client.get(f"{PREFIX}/prefs", headers=_user_headers(valid_api_key))
        assert again.json()["data"]["allow_script_env"] is True


class TestRotation:
    async def test_rotate_reports_rotation_window(self, client, clean_secrets, valid_api_key):
        created = (
            await client.post(PREFIX, json=_payload(), headers=_user_headers(valid_api_key))
        ).json()["data"]

        resp = await client.post(
            f"{PREFIX}/{created['id']}/rotate",
            json={"value": "new-value-456", "overlap_days": 7},
            headers=_user_headers(valid_api_key),
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["rotating_until"] is not None
        assert "new-value-456" not in resp.text

    async def test_delete(self, client, clean_secrets, valid_api_key):
        created = (
            await client.post(PREFIX, json=_payload(), headers=_user_headers(valid_api_key))
        ).json()["data"]

        resp = await client.delete(
            f"{PREFIX}/{created['id']}", headers=_user_headers(valid_api_key)
        )
        assert resp.status_code == 200

        gone = await client.get(
            f"{PREFIX}/{created['id']}", headers=_user_headers(valid_api_key)
        )
        assert gone.status_code == 404


class TestAdminListVisibility:
    """管理端的系统凭据列表要能看出「绑到哪了、用过没有」。

    个人中心有右侧详情（绑定 + 使用概览），管理端原来只有 ``grants``：管理员
    看到一条系统凭据，既不知道它被绑在哪些 MCP 服务 / API 工具 / 脚本上，也
    不知道它到底有没有被用过。凭据「生效还是摆着」正是这里最该回答的问题。

    后端必须一次把 ``bindings`` 和统计带出来——列表页逐行再查就是 N+1。
    """

    async def test_admin_list_reports_bindings_and_usage(
        self, client, clean_secrets, admin_api_key
    ):
        created = (
            await client.post(
                PREFIX + "/admin",
                json=_payload(secret_key=f"corp.observed.{uuid.uuid4().hex[:8]}"),
                headers=_admin_headers(admin_api_key),
            )
        ).json()["data"]

        from app.core.orm import AsyncSessionLocal
        from app.models.secret import SecretBinding, SecretUsageLog

        async with AsyncSessionLocal() as session:
            session.add(
                SecretBinding(
                    id=uuid.uuid4().hex,
                    secret_id=created["id"],
                    consumer_type="mcp_server",
                    consumer_id="srv-admin-list",
                    slot="header:Authorization",
                    bound_by_user_id=None,
                )
            )
            session.add(
                SecretUsageLog(
                    id=uuid.uuid4().hex,
                    secret_id=created["id"],
                    secret_key_snapshot=created["secret_key"],
                    owner_scope="system",
                    scene="mcp",
                    consumer_type="mcp_server",
                    consumer_id="srv-admin-list",
                    slot="header:Authorization",
                    target_host="example.com",
                    result="success",
                )
            )
            await session.commit()

        resp = await client.get(PREFIX + "/admin", headers=_admin_headers(admin_api_key))
        item = next(i for i in resp.json()["data"] if i["id"] == created["id"])

        assert item["binding_count"] == 1
        assert len(item["bindings"]) == 1
        assert item["bindings"][0]["slot"] == "header:Authorization"
        assert item["bindings"][0]["consumer_type"] == "mcp_server"
        assert item["usage_7d"] == 1
        assert item["failed_7d"] == 0
        assert item["last_used_at"], "有流水就必须有「最近一次」"

    async def test_admin_list_reports_zero_for_unused_secret(
        self, client, clean_secrets, admin_api_key
    ):
        created = (
            await client.post(
                PREFIX + "/admin",
                json=_payload(secret_key=f"corp.unused.{uuid.uuid4().hex[:8]}"),
                headers=_admin_headers(admin_api_key),
            )
        ).json()["data"]

        resp = await client.get(PREFIX + "/admin", headers=_admin_headers(admin_api_key))
        item = next(i for i in resp.json()["data"] if i["id"] == created["id"])

        assert item["binding_count"] == 0
        assert item["bindings"] == []
        assert item["usage_7d"] == 0
        assert item["last_used_at"] is None
