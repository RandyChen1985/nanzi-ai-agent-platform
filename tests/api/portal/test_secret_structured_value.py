"""结构化凭据（``value_format=json``）的写入与展示契约。

守的是四件事：

1. 结构化只改「录入与展示」，存储契约不变——凭证类仍然是掩码、仍然读不回；
2. 非法的「结构化值」在写入时就拒掉，而不是存进去等注入时炸；
3. 摘要只含非敏感字段，敏感值一个字符都不能出现在响应里；
4. 单值凭据（存量与大多数 token）行为完全不变。
"""

from __future__ import annotations

import json
import uuid

import pytest

PREFIX = "/api/portal/secrets"
MASK = "••••••••••••"


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


def _smtp_value(address: str = "alert-bot@nanzi.ai", password: str = "Sup3rSecret!2026") -> str:
    return json.dumps(
        {
            "host": "smtp.exmail.qq.com",
            "port": 465,
            "security": "ssl",
            "username": address,
            "password": password,
        },
        ensure_ascii=False,
    )


def _payload(**overrides) -> dict:
    data = {
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
    data.update(overrides)
    return data


class TestStructuredCreate:
    async def test_returns_format_template_and_summary(self, client, clean_secrets, valid_api_key):
        resp = await client.post(PREFIX, json=_payload(), headers=_headers(valid_api_key))

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["value_format"] == "json"
        assert data["value_template"] == "smtp"
        # 摘要只拼非敏感字段：host / port / username
        assert data["value_summary"] == "smtp.exmail.qq.com · 465 · alert-bot@nanzi.ai"
        # 凭证类照旧：掩码 + 不可读
        assert data["value_masked"] == MASK
        assert data["readable"] is False
        assert data["value_preview"] is None

    async def test_password_never_appears_in_response(self, client, clean_secrets, valid_api_key):
        resp = await client.post(PREFIX, json=_payload(), headers=_headers(valid_api_key))

        assert resp.status_code == 200
        assert "Sup3rSecret!2026" not in resp.text
        assert "Sup3rSecret" not in json.dumps(resp.json(), ensure_ascii=False)

    async def test_invalid_json_is_rejected_at_write_time(self, client, clean_secrets, valid_api_key):
        resp = await client.post(
            PREFIX,
            json=_payload(value='{"host": "smtp", '),
            headers=_headers(valid_api_key),
        )

        assert resp.status_code == 400
        assert "JSON" in resp.json()["detail"]

    async def test_array_top_level_is_rejected(self, client, clean_secrets, valid_api_key):
        resp = await client.post(
            PREFIX, json=_payload(value='["a", "b"]'), headers=_headers(valid_api_key)
        )

        assert resp.status_code == 400
        assert "对象" in resp.json()["detail"]

    async def test_unknown_format_is_rejected(self, client, clean_secrets, valid_api_key):
        resp = await client.post(
            PREFIX, json=_payload(value_format="yaml"), headers=_headers(valid_api_key)
        )

        assert resp.status_code == 400

    async def test_variable_structured_value_uses_summary_not_prefix(
        self, client, clean_secrets, valid_api_key
    ):
        resp = await client.post(
            PREFIX,
            json=_payload(
                secret_type="variable",
                value=json.dumps({"tenant": "nanzi", "endpoint": "https://api.nanzi.ai"}),
                value_template=None,
                value_secret_fields=[],
            ),
            headers=_headers(valid_api_key),
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["value_masked"] == "nanzi · https://api.nanzi.ai"
        # 结构化变量不再给前 8 个字符
        assert data["value_preview"] is None


class TestStructuredList:
    async def test_list_carries_format_and_template(self, client, clean_secrets, valid_api_key):
        created = (
            await client.post(PREFIX, json=_payload(), headers=_headers(valid_api_key))
        ).json()["data"]

        resp = await client.get(PREFIX, headers=_headers(valid_api_key))

        assert resp.status_code == 200
        item = next(i for i in resp.json()["data"]["items"] if i["id"] == created["id"])
        # 列表靠这三个字段区分「单值」与「结构化 · SMTP」
        assert item["value_format"] == "json"
        assert item["value_template"] == "smtp"
        assert item["value_summary"]
        assert "Sup3rSecret" not in resp.text

    async def test_plain_value_still_reports_text_format(self, client, clean_secrets, valid_api_key):
        resp = await client.post(
            PREFIX,
            json=_payload(
                value="ghp_abcdefghijklmnop",
                value_format="text",
                value_template=None,
                value_secret_fields=[],
            ),
            headers=_headers(valid_api_key),
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["value_format"] == "text"
        assert data["value_template"] is None
        assert data["value_summary"] is None
        assert data["value_masked"] == MASK

    async def test_template_is_ignored_for_plain_format(self, client, clean_secrets, valid_api_key):
        # 前端漏传 value_format 但带了模板时，不应把模板写进单值记录
        resp = await client.post(
            PREFIX,
            json=_payload(
                value="plain-token-1234",
                value_format="text",
                value_template="smtp",
                value_secret_fields=[],
            ),
            headers=_headers(valid_api_key),
        )

        assert resp.status_code == 200
        assert resp.json()["data"]["value_template"] is None


class TestStructuredRotate:
    async def test_rotate_recomputes_summary(self, client, clean_secrets, valid_api_key):
        created = (
            await client.post(PREFIX, json=_payload(), headers=_headers(valid_api_key))
        ).json()["data"]

        resp = await client.post(
            f"{PREFIX}/{created['id']}/rotate",
            json={
                "value": json.dumps(
                    {
                        "host": "smtp.163.com",
                        "port": 587,
                        "username": "notify@nanzi.ai",
                        "password": "NewSecret#2026",
                    }
                ),
                "value_format": "json",
                "value_template": "smtp",
                "value_secret_fields": ["password"],
            },
            headers=_headers(valid_api_key),
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["value_summary"] == "smtp.163.com · 587 · notify@nanzi.ai"
        assert "NewSecret" not in resp.text

    async def test_rotate_keeps_format_when_not_passed(self, client, clean_secrets, valid_api_key):
        created = (
            await client.post(PREFIX, json=_payload(), headers=_headers(valid_api_key))
        ).json()["data"]

        resp = await client.post(
            f"{PREFIX}/{created['id']}/rotate",
            json={
                "value": json.dumps({"host": "smtp.example.com", "password": "x"}),
                "value_secret_fields": ["password"],
            },
            headers=_headers(valid_api_key),
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["value_format"] == "json"
        assert data["value_template"] == "smtp"
        assert data["value_summary"] == "smtp.example.com"

    async def test_rotate_to_invalid_json_is_rejected(self, client, clean_secrets, valid_api_key):
        created = (
            await client.post(PREFIX, json=_payload(), headers=_headers(valid_api_key))
        ).json()["data"]

        resp = await client.post(
            f"{PREFIX}/{created['id']}/rotate",
            json={"value": "{broken", "value_format": "json", "value_secret_fields": []},
            headers=_headers(valid_api_key),
        )

        assert resp.status_code == 400


class TestSummaryFallbackEndToEnd:
    """「自定义 JSON」和「轮换没带声明」是两条最容易漏掉声明的路径。

    它们都靠后端兜底：前端没有模板可依，`value_secret_fields` 会是空的，
    如果后端不自己认敏感键名，密码就会出现在 `value_summary` 这个明文列里。
    """

    async def test_custom_json_hides_password_without_declaration(
        self, client, clean_secrets, valid_api_key
    ):
        resp = await client.post(
            PREFIX,
            json=_payload(
                value=json.dumps({"host": "db.internal", "password": "MyPass123"}),
                value_template=None,
                value_secret_fields=[],
            ),
            headers=_headers(valid_api_key),
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["value_summary"] == "db.internal"
        assert "MyPass123" not in resp.text

    async def test_rotate_without_secret_fields_still_hides_password(
        self, client, clean_secrets, valid_api_key
    ):
        created = (
            await client.post(PREFIX, json=_payload(), headers=_headers(valid_api_key))
        ).json()["data"]

        resp = await client.post(
            f"{PREFIX}/{created['id']}/rotate",
            json={
                # 刻意不带 value_secret_fields：调用方换了实现、或者按旧结构传值
                "value": json.dumps({"host": "smtp.new.com", "password": "RotatedPass1"}),
                "value_format": "json",
            },
            headers=_headers(valid_api_key),
        )

        assert resp.status_code == 200, resp.text
        assert "RotatedPass1" not in resp.text
        assert resp.json()["data"]["value_summary"] == "smtp.new.com"
