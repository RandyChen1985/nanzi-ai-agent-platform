"""绑定只取某个字段（``sys_secret_bindings.value_path``）的写入契约。

守的是三件事：

1. **结构化凭据必须落到具体字段**：不选字段就绑，注进去的是一整段 JSON，
   调用方必然收不对，而失败点离配置处很远；
2. 字段名写错时在**点保存的那一刻**就报，给出最接近的候选，不留到运行期；
3. 单值凭据没有字段可选，硬给字段路径是配置错误，不是「忽略就好」。

字段清单接口（``/{id}/fields``）另外守一条：敏感字段只回名字，不回值——
选择器不需要值，给了就等于在「凭据不可读」上开旁路。
"""

from __future__ import annotations

import json
import uuid

import pytest

PREFIX = "/api/portal/secrets"


@pytest.fixture
async def clean_secrets():
    """只清理本用例建的凭据，绝不清表：这里连的是开发库。"""
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


def _headers(api_key: str) -> dict:
    return {"X-API-Key": api_key}


def _smtp_value(password: str = "Sup3rSecret!2026") -> str:
    return json.dumps(
        {
            "host": "smtp.exmail.qq.com",
            "port": 465,
            "username": "alert-bot@nanzi.ai",
            "password": password,
        },
        ensure_ascii=False,
    )


async def _create_secret(client, api_key: str, **overrides) -> dict:
    payload = {
        "secret_key": f"mail.smtp.{uuid.uuid4().hex[:8]}",
        "name": "告警邮箱",
        "value": _smtp_value(),
        "secret_type": "credential",
        "allowed_scenarios": ["api_tool"],
        "allowed_hosts": [],
        "value_format": "json",
        "value_template": "smtp",
        "value_secret_fields": ["password"],
    }
    payload.update(overrides)
    resp = await client.post(PREFIX, json=payload, headers=_headers(api_key))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _bind(client, api_key: str, secret_id: str, **body):
    payload = {
        "secret_id": secret_id,
        "consumer_type": "api_tool",
        "consumer_id": f"tool-{uuid.uuid4().hex[:6]}",
        "slot": "header:Authorization",
        # 不设全局：这是普通用户给自己绑自己的凭据（全局绑定只有管理员能设）
    }
    payload.update(body)
    return await client.post(f"{PREFIX}/bindings", json=payload, headers=_headers(api_key))


class TestStructuredBindingNeedsField:
    async def test_whole_json_requires_explicit_confirmation(
        self, client, clean_secrets, valid_api_key
    ):
        """参数位点不选字段又不声明「整段」时拒绝：注进去是一段 JSON，调用方必错。"""
        secret = await _create_secret(client, valid_api_key)

        resp = await _bind(
            client,
            valid_api_key,
            secret["id"],
            slot="query:api_key",
        )

        assert resp.status_code == 400
        assert "字段" in resp.json()["detail"]

    async def test_header_slot_is_exempt_because_the_value_names_the_field(
        self, client, clean_secrets, valid_api_key
    ):
        """header 位点的值自己能写 ``${secret:标识.字段}``，字段由引用表达，
        绑定层不必再强制一遍——否则「同一条凭据给不同槽位取不同字段」会被堵死。
        """
        secret = await _create_secret(client, valid_api_key)

        resp = await _bind(client, valid_api_key, secret["id"])

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["value_path"] == ""

    async def test_allow_whole_keeps_old_behaviour(
        self, client, clean_secrets, valid_api_key
    ):
        secret = await _create_secret(client, valid_api_key)

        resp = await _bind(
            client, valid_api_key, secret["id"], slot="query:total", allow_whole=True
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["value_path"] == ""

    async def test_field_is_saved_on_binding(self, client, clean_secrets, valid_api_key):
        secret = await _create_secret(client, valid_api_key)

        resp = await _bind(client, valid_api_key, secret["id"], value_path="password")

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["value_path"] == "password"

        listed = await client.get(
            f"{PREFIX}/bindings",
            params={"secret_id": secret["id"]},
            headers=_headers(valid_api_key),
        )
        rows = listed.json()["data"]
        assert [row["value_path"] for row in rows] == ["password"]
        # 列表还要能说出「结构化 · 模板名」：只给标识的话，界面看不出取的是字段
        assert rows[0]["value_format"] == "json"
        assert rows[0]["value_template"] == "smtp"


class TestFieldNameValidation:
    async def test_typo_is_rejected_with_suggestion(
        self, client, clean_secrets, valid_api_key
    ):
        """字段名写错要当场报，并给出最接近的候选——别让人自己去翻 JSON。"""
        secret = await _create_secret(client, valid_api_key)

        resp = await _bind(client, valid_api_key, secret["id"], value_path="passwrod")

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "passwrod" in detail
        assert "password" in detail

    async def test_unknown_field_without_close_match_only_names_it(
        self, client, clean_secrets, valid_api_key
    ):
        secret = await _create_secret(client, valid_api_key)

        resp = await _bind(client, valid_api_key, secret["id"], value_path="totally_unrelated")

        assert resp.status_code == 400
        assert "totally_unrelated" in resp.json()["detail"]

    @pytest.mark.parametrize("bad", ["a[0]", "a..b", "1abc", "a b"])
    async def test_malformed_path_is_rejected(
        self, client, clean_secrets, valid_api_key, bad
    ):
        secret = await _create_secret(client, valid_api_key)

        resp = await _bind(client, valid_api_key, secret["id"], value_path=bad)

        assert resp.status_code == 400
        assert "字段路径" in resp.json()["detail"]

    async def test_single_value_secret_has_no_fields(
        self, client, clean_secrets, valid_api_key
    ):
        """单值凭据给字段路径是配置错误：它没有字段可取。"""
        secret = await _create_secret(
            client,
            valid_api_key,
            value="sk-live-abc123",
            value_format="text",
            value_template=None,
            value_secret_fields=[],
        )

        resp = await _bind(client, valid_api_key, secret["id"], value_path="password")

        assert resp.status_code == 400
        assert "单值" in resp.json()["detail"]

    async def test_single_value_secret_binds_without_path(
        self, client, clean_secrets, valid_api_key
    ):
        secret = await _create_secret(
            client,
            valid_api_key,
            value="sk-live-abc123",
            value_format="text",
            value_template=None,
            value_secret_fields=[],
        )

        resp = await _bind(client, valid_api_key, secret["id"])

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["value_path"] == ""


class TestFieldListing:
    async def test_lists_fields_without_leaking_secret_values(
        self, client, clean_secrets, valid_api_key
    ):
        """选择器只需要字段名；敏感字段的**值**一个字都不能出现在响应里。"""
        secret = await _create_secret(client, valid_api_key)

        resp = await client.get(
            f"{PREFIX}/{secret['id']}/fields", headers=_headers(valid_api_key)
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["format"] == "json"
        assert data["template"] == "smtp"
        names = [field["name"] for field in data["fields"]]
        assert names == ["host", "port", "username", "password"]

        by_name = {field["name"]: field for field in data["fields"]}
        assert by_name["password"]["sensitive"] is True
        assert by_name["password"]["preview"] == ""
        # 非敏感字段给一段预览，方便认出「这个是端口」
        assert by_name["host"]["preview"] == "smtp.exmail.qq.com"
        assert by_name["port"]["preview"] == "465"
        assert "Sup3rSecret!2026" not in resp.text

    async def test_single_value_secret_returns_no_fields(
        self, client, clean_secrets, valid_api_key
    ):
        secret = await _create_secret(
            client,
            valid_api_key,
            value="sk-live-abc123",
            value_format="text",
            value_template=None,
            value_secret_fields=[],
        )

        resp = await client.get(
            f"{PREFIX}/{secret['id']}/fields", headers=_headers(valid_api_key)
        )

        assert resp.status_code == 200
        assert resp.json()["data"]["fields"] == []
        assert resp.json()["data"]["format"] == "text"

    async def test_unknown_secret_is_404(self, client, clean_secrets, valid_api_key):
        """不存在与无权看同样回 404：不透露「有这么一条凭据」。"""
        resp = await client.get(
            f"{PREFIX}/does-not-exist/fields", headers=_headers(valid_api_key)
        )

        assert resp.status_code == 404


class TestFieldListingNeverLeaksValues:
    """字段清单只给名字和「不是秘密的预览」。

    键名认不出来不等于值安全：企业微信/钉钉的 webhook 地址把 key 写在 query 上，
    键名却叫 ``webhook_url``；``appsecret`` 又是一个拆不开的整词。只靠词根过滤，
    字段选择器会把整段 webhook 明文吐在界面上。
    """

    async def _fields(self, client, api_key: str, secret: dict) -> dict:
        resp = await client.get(
            f"{PREFIX}/{secret['id']}/fields", headers=_headers(api_key)
        )
        assert resp.status_code == 200, resp.text
        return {f["name"]: f for f in resp.json()["data"]["fields"]}

    async def test_webhook_url_is_flagged_and_has_no_preview(
        self, client, clean_secrets, valid_api_key
    ):
        secret = await _create_secret(
            client,
            valid_api_key,
            value=json.dumps(
                {
                    # key 写在 query 上：地址本身就是凭证
                    "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=693a91f6-secret",
                    "corp_id": "ww1234567890",
                },
                ensure_ascii=False,
            ),
            value_template="wecom",
            value_secret_fields=["webhook_url"],
        )

        fields = await self._fields(client, valid_api_key, secret)

        assert fields["webhook_url"]["sensitive"] is True
        assert fields["webhook_url"]["preview"] == ""
        # 普通字段仍要有预览，否则选择器里全是同名空行，认不出哪个是哪个
        assert fields["corp_id"]["preview"] == "ww1234567890"

    async def test_secretish_value_is_not_previewed_even_with_plain_name(
        self, client, clean_secrets, valid_api_key
    ):
        secret = await _create_secret(
            client,
            valid_api_key,
            value=json.dumps({"endpoint": "sk-live-abcdef0123456789"}),
            value_secret_fields=[],
        )

        fields = await self._fields(client, valid_api_key, secret)

        assert fields["endpoint"]["preview"] == ""

    async def test_appsecret_key_name_is_recognised(
        self, client, clean_secrets, valid_api_key
    ):
        """`appsecret` 拆不出词根（没有驼峰也没有分隔符），必须显式收进词表。"""
        secret = await _create_secret(
            client,
            valid_api_key,
            value=json.dumps({"appsecret": "abcdef0123456789", "appid": "wx123"}),
            value_template="custom",
            value_secret_fields=[],
        )

        fields = await self._fields(client, valid_api_key, secret)

        assert fields["appsecret"]["sensitive"] is True
        assert fields["appsecret"]["preview"] == ""
        assert fields["appid"]["preview"] == "wx123"


async def test_field_list_respects_the_cap_even_with_many_siblings(client, clean_secrets, valid_api_key):
    """字段清单的上限不能被「外层兄弟键」穿透。

    `walk` 原来只在递归子结构后检查长度：子结构塞满 200 条返回，外层
    `for key, value in node.items()` 仍会继续走完剩下的兄弟键并逐个 append。
    扁平大对象（几十个键）或「一个子对象 + 一堆兄弟键」的结构都会超。
    """
    from app.services.secrets.center import _MAX_FIELD_ITEMS

    # 一个先被展开的子对象 + 大量兄弟键：子结构塞满后，
    # 外层剩下的兄弟键不能再往下追加
    big = {"nested": {f"k{i}": i for i in range(300)}}
    big.update({f"s{i}": i for i in range(300)})
    created = await client.post(
        PREFIX,
        json={
            "secret_key": f"big.{uuid.uuid4().hex[:8]}",
            "name": "大对象",
            "value": json.dumps(big),
            "secret_type": "credential",
            "allowed_scenarios": ["api_tool"],
            "allowed_hosts": [],
            "value_format": "json",
            "value_template": "smtp",
        },
        headers={"X-API-Key": valid_api_key},
    )
    assert created.status_code == 200, created.text
    secret_id = created.json()["data"]["id"]

    resp = await client.get(
        f"{PREFIX}/{secret_id}/fields", headers={"X-API-Key": valid_api_key}
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["data"]["fields"]) <= _MAX_FIELD_ITEMS
