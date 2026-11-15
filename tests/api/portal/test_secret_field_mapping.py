"""规则绑定的取用字段 + 一次保存整张映射表。

两块都属于「一条凭据供多个参数」：

1. **规则绑定也要能取字段**：规则挑的是「哪一条凭据」，命中结构化凭据时如果不
   说明取哪个字段，注进去的是一整份 JSON。规则还能声明模板约束——命中模板不符的
   凭据按未命中处理，不会拿另一种结构去取字段。
2. **批量保存要么整张生效，要么一条不动**：映射面板一次提交十几个参数，
   半成功是最难收拾的结果——界面说保存成功，实际只有一部分参数拿到了凭据。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

PREFIX = "/api/portal/secrets"


@pytest.fixture
async def clean_secrets():
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


async def _batch(client, api_key: str, items: list[dict], **body):
    payload = {
        "consumer_type": "api_tool",
        "consumer_id": f"tool-{uuid.uuid4().hex[:6]}",
        **body,
        "items": items,
    }
    return await client.post(f"{PREFIX}/bindings/batch", json=payload, headers=_headers(api_key))


async def _create_secret(client, api_key: str, **overrides) -> dict:
    payload = {
        "secret_key": f"mail.map.{uuid.uuid4().hex[:8]}",
        "name": "告警邮箱",
        "value": json.dumps(
            {
                "host": "smtp.exmail.qq.com",
                "port": 465,
                "username": "alert-bot@nanzi.ai",
                "password": "Sup3rSecret!2026",
            },
            ensure_ascii=False,
        ),
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


async def _bind_rule(client, api_key: str, **body):
    payload = {
        "consumer_type": "api_tool",
        "consumer_id": f"tool-{uuid.uuid4().hex[:6]}",
        "slot": "query:mailkey",
        "strategy": "rule",
        "rule": {"key_source": "user.user_id", "target": "name", "match": "exact"},
    }
    payload.update(body)
    return await client.post(f"{PREFIX}/bindings", json=payload, headers=_headers(api_key))


class TestRuleBindingTakesAField:
    """规则绑定不指向某一条凭据，但「取哪个字段」仍然是它能声明的事实。"""

    async def test_rule_keeps_value_path(self, client, clean_secrets, valid_api_key):
        resp = await _bind_rule(client, valid_api_key, value_path="password")

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["value_path"] == "password"

    async def test_rule_persists_value_path_and_shows_it_in_listing(
        self, client, clean_secrets, valid_api_key
    ):
        consumer_id = f"tool-{uuid.uuid4().hex[:6]}"
        await _bind_rule(
            client, valid_api_key, consumer_id=consumer_id, value_path="password"
        )

        listed = await client.get(
            f"{PREFIX}/bindings",
            params={"consumer_type": "api_tool", "consumer_id": consumer_id},
            headers=_headers(valid_api_key),
        )
        rows = listed.json()["data"]
        rule_row = next(r for r in rows if r["strategy"] == "rule")
        assert rule_row["value_path"] == "password"
        # 摘要里也要有：列表光看「按部门挑凭据」看不出注进去的是什么
        assert "password" in (rule_row["rule_summary"] or "")

    async def test_rule_rejects_malformed_path(self, client, clean_secrets, valid_api_key):
        """规则不能校验「字段是否存在」（凭据是运行时挑的），但格式必须当场拦。"""
        resp = await _bind_rule(client, valid_api_key, value_path="a[0]")

        assert resp.status_code == 400
        assert "字段" in resp.json()["detail"]

    async def test_rule_accepts_template_constraint(
        self, client, clean_secrets, valid_api_key
    ):
        rule = {
            "key_source": "user.user_id",
            "target": "name",
            "match": "exact",
            "value_template": "smtp",
        }
        consumer_id = f"tool-{uuid.uuid4().hex[:6]}"
        resp = await _bind_rule(
            client,
            valid_api_key,
            consumer_id=consumer_id,
            rule=rule,
            value_path="password",
        )

        assert resp.status_code == 200, resp.text
        listed = await client.get(
            f"{PREFIX}/bindings",
            params={"consumer_type": "api_tool", "consumer_id": consumer_id},
            headers=_headers(valid_api_key),
        )
        rows = listed.json()["data"]
        # 模板约束是规则的一部分，回填时必须原样回来，否则再打开编辑器它就丢了
        assert rows[0]["rule"]["value_template"] == "smtp"

    async def test_rule_rejects_bad_template_id(self, client, clean_secrets, valid_api_key):
        rule = {
            "key_source": "user.user_id",
            "target": "name",
            "value_template": "SMTP!",
        }
        resp = await _bind_rule(client, valid_api_key, rule=rule)

        assert resp.status_code == 400
        assert "value_template" in resp.json()["detail"]

    async def test_rule_still_rejects_secret_id_inside_rule(
        self, client, clean_secrets, valid_api_key
    ):
        """规则里永远不能出现具体凭据——否则改规则就成了「读别人凭据」的手段。"""
        rule = {"key_source": "user.user_id", "secret_id": "s-anything"}
        resp = await _bind_rule(client, valid_api_key, rule=rule)

        assert resp.status_code == 400


class TestBatchBindings:
    """一张映射表一次保存。"""

    async def _batch(self, client, api_key: str, items: list[dict], **body):
        payload = {
            "consumer_type": "api_tool",
            "consumer_id": f"tool-{uuid.uuid4().hex[:6]}",
            "items": items,
        }
        payload.update(body)
        return await client.post(
            f"{PREFIX}/bindings/batch", json=payload, headers=_headers(api_key)
        )

    async def test_saves_every_row_against_one_secret(
        self, client, clean_secrets, valid_api_key
    ):
        """三个参数都取同一条凭据的不同字段——这正是「一条凭据供多个参数」。"""
        secret = await _create_secret(client, valid_api_key)
        consumer_id = f"tool-{uuid.uuid4().hex[:6]}"

        resp = await self._batch(
            client,
            valid_api_key,
            [
                {
                    "slot": "query:username",
                    "secret_id": secret["id"],
                    "value_path": "username",
                },
                {"slot": "query:mailkey", "secret_id": secret["id"], "value_path": "password"},
                {"slot": "query:smtphost", "secret_id": secret["id"], "value_path": "host"},
            ],
            consumer_id=consumer_id,
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["saved"] == 3

        listed = await client.get(
            f"{PREFIX}/bindings",
            params={"consumer_type": "api_tool", "consumer_id": consumer_id},
            headers=_headers(valid_api_key),
        )
        rows = listed.json()["data"]
        by_name = {
            r["slot"].split(":")[-1]: (r["secret_id"], r["value_path"]) for r in rows
        }
        assert by_name["username"] == (secret["id"], "username")
        assert by_name["mailkey"] == (secret["id"], "password")
        assert by_name["smtphost"] == (secret["id"], "host")

    async def test_one_bad_row_saves_nothing(self, client, clean_secrets, valid_api_key):
        """有一条不过就整体拒绝：半成功会让界面和库里对不上。"""
        secret = await _create_secret(client, valid_api_key)
        consumer_id = f"tool-{uuid.uuid4().hex[:6]}"

        resp = await self._batch(
            client,
            valid_api_key,
            [
                {
                    "slot": "query:username",
                    "secret_id": secret["id"],
                    "value_path": "username",
                },
                # 这条凭据里没有 nosuchfield
                {
                    "slot": "query:mailkey",
                    "secret_id": secret["id"],
                    "value_path": "nosuchfield",
                },
            ],
            consumer_id=consumer_id,
        )

        assert resp.status_code == 400
        assert "未保存任何改动" in resp.json()["detail"]

        listed = await client.get(
            f"{PREFIX}/bindings",
            params={"consumer_type": "api_tool", "consumer_id": consumer_id},
            headers=_headers(valid_api_key),
        )
        assert listed.json()["data"] == []

    async def test_empty_secret_id_unbinds_that_row(
        self, client, clean_secrets, valid_api_key
    ):
        """映射表里被清空的那行 = 这个参数不再由该凭据提供。"""
        secret = await _create_secret(client, valid_api_key)
        consumer_id = f"tool-{uuid.uuid4().hex[:6]}"
        await self._batch(
            client,
            valid_api_key,
            [
                {
                    "slot": "query:username",
                    "secret_id": secret["id"],
                    "value_path": "username",
                },
                {"slot": "query:mailkey", "secret_id": secret["id"], "value_path": "password"},
            ],
            consumer_id=consumer_id,
        )

        resp = await self._batch(
            client,
            valid_api_key,
            [
                {
                    "slot": "query:username",
                    "secret_id": secret["id"],
                    "value_path": "username",
                },
                {"slot": "query:mailkey", "secret_id": ""},
            ],
            consumer_id=consumer_id,
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["removed"] == 1

        listed = await client.get(
            f"{PREFIX}/bindings",
            params={"consumer_type": "api_tool", "consumer_id": consumer_id},
            headers=_headers(valid_api_key),
        )
        slots = [r["slot"] for r in listed.json()["data"]]
        assert all(not s.endswith(":mailkey") for s in slots)

    async def test_name_without_known_tool_is_rejected_clearly(
        self, client, clean_secrets, valid_api_key
    ):
        """只给参数名时槽位要靠工具配置推（GET 走 query、POST 走 body）。

        工具还没保存过就推不出来——这时要明说「工具不存在」，
        而不是记成一条槽位为空的绑定。
        """
        secret = await _create_secret(client, valid_api_key)

        resp = await self._batch(
            client,
            valid_api_key,
            [{"name": "username", "secret_id": secret["id"], "value_path": "username"}],
        )

        assert resp.status_code == 400
        assert "工具不存在" in resp.json()["detail"]

    async def test_mcp_rows_take_explicit_slots(self, client, clean_secrets, valid_api_key):
        """MCP 参数的槽位前端能算（``arg:短工具名:参数名``），给了就直接用。"""
        secret = await _create_secret(
            client, valid_api_key, allowed_scenarios=["mcp"]
        )
        consumer_id = f"srv-{uuid.uuid4().hex[:6]}"

        resp = await self._batch(
            client,
            valid_api_key,
            [
                {
                    "slot": "arg:send_mail:mailkey",
                    "secret_id": secret["id"],
                    "value_path": "password",
                }
            ],
            consumer_type="mcp_server",
            consumer_id=consumer_id,
        )

        assert resp.status_code == 200, resp.text

        listed = await client.get(
            f"{PREFIX}/bindings",
            params={"consumer_type": "mcp_server", "consumer_id": consumer_id},
            headers=_headers(valid_api_key),
        )
        rows = listed.json()["data"]
        assert len(rows) == 1
        assert rows[0]["slot"] == "arg:send_mail:mailkey"
        assert rows[0]["value_path"] == "password"


class TestBatchRuleRows:
    """多值凭据的「按规则」来源：一次提交，每个参数各自取不同字段。

    规则和单值是同一条规则——只是每个参数多了「取哪个字段」。所以整组写 N 条
    规则绑定（规则 JSON 相同、value_path 不同），运行时各槽位独立求值但必然
    命中同一条凭据（规则确定性 + 同一次调用身份不变）。
    """

    async def _batch(self, client, api_key: str, items: list[dict], **body):
        payload = {
            "consumer_type": "api_tool",
            "consumer_id": f"tool-{uuid.uuid4().hex[:6]}",
            "items": items,
        }
        payload.update(body)
        return await client.post(
            f"{PREFIX}/bindings/batch", json=payload, headers=_headers(api_key)
        )

    async def test_each_param_gets_the_same_rule_with_its_own_field(
        self, client, clean_secrets, valid_api_key
    ):
        rule = {
            "key_source": "user.dept_code",
            "target": "name",
            "match": "exact",
            "value_template": "smtp",
        }
        consumer_id = f"tool-{uuid.uuid4().hex[:6]}"

        resp = await self._batch(
            client,
            valid_api_key,
            [
                {
                    "slot": "query:username",
                    "strategy": "rule",
                    "rule": rule,
                    "value_path": "username",
                },
                {
                    "slot": "query:mailkey",
                    "strategy": "rule",
                    "rule": rule,
                    "value_path": "password",
                },
            ],
            consumer_id=consumer_id,
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["saved"] == 2

        listed = await client.get(
            f"{PREFIX}/bindings",
            params={"consumer_type": "api_tool", "consumer_id": consumer_id},
            headers=_headers(valid_api_key),
        )
        rows = listed.json()["data"]
        by_slot = {r["slot"].split(":")[-1]: r for r in rows}
        assert all(r["strategy"] == "rule" for r in rows)
        assert by_slot["username"]["value_path"] == "username"
        assert by_slot["mailkey"]["value_path"] == "password"
        # 规则本体相同，模板约束也带过去了
        assert by_slot["username"]["rule"]["value_template"] == "smtp"
        assert by_slot["mailkey"]["rule"]["value_template"] == "smtp"

    async def test_bad_rule_saves_nothing(self, client, clean_secrets, valid_api_key):
        consumer_id = f"tool-{uuid.uuid4().hex[:6]}"

        resp = await self._batch(
            client,
            valid_api_key,
            [
                {
                    "slot": "query:ok",
                    "strategy": "rule",
                    "rule": {"key_source": "user.user_id"},
                    "value_path": "username",
                },
                {
                    "slot": "query:bad",
                    "strategy": "rule",
                    # 模板标识大小写不合规
                    "rule": {"key_source": "user.user_id", "value_template": "SMTP"},
                },
            ],
            consumer_id=consumer_id,
        )

        assert resp.status_code == 400
        assert "未保存任何改动" in resp.json()["detail"]

        listed = await client.get(
            f"{PREFIX}/bindings",
            params={"consumer_type": "api_tool", "consumer_id": consumer_id},
            headers=_headers(valid_api_key),
        )
        assert listed.json()["data"] == []

    async def test_rule_row_without_rule_is_rejected(
        self, client, clean_secrets, valid_api_key
    ):
        resp = await self._batch(
            client,
            valid_api_key,
            [{"slot": "query:x", "strategy": "rule", "rule": None}],
        )

        assert resp.status_code == 400
        assert "规则" in resp.json()["detail"]

    async def test_none_clears_both_fixed_and_rule(
        self, client, clean_secrets, valid_api_key
    ):
        """一个参数回到「模型填写」时，固定绑定和规则绑定都得清。

        只清固定那条，规则还会继续生效——用户看到的现象是「说好改回模型填写了，
        结果还在注凭据」，而且界面上两处状态对不上。
        """
        secret = await _create_secret(client, valid_api_key)
        consumer_id = f"tool-{uuid.uuid4().hex[:6]}"
        slot = "query:mailkey"

        # 先同时挂上固定绑定和规则绑定（两者本来就可以并存）
        await self._batch(
            client,
            valid_api_key,
            [{"slot": slot, "secret_id": secret["id"], "value_path": "password"}],
            consumer_id=consumer_id,
        )
        await self._batch(
            client,
            valid_api_key,
            [
                {
                    "slot": slot,
                    "strategy": "rule",
                    "rule": {"key_source": "user.user_id"},
                    "value_path": "password",
                }
            ],
            consumer_id=consumer_id,
        )
        before = await client.get(
            f"{PREFIX}/bindings",
            params={"consumer_type": "api_tool", "consumer_id": consumer_id},
            headers=_headers(valid_api_key),
        )
        assert len(before.json()["data"]) == 2

        resp = await self._batch(
            client,
            valid_api_key,
            [{"slot": slot, "strategy": "none"}],
            consumer_id=consumer_id,
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["removed"] == 2

        after = await client.get(
            f"{PREFIX}/bindings",
            params={"consumer_type": "api_tool", "consumer_id": consumer_id},
            headers=_headers(valid_api_key),
        )
        assert after.json()["data"] == []

    async def test_duplicate_slot_in_one_batch_is_rejected(
        self, client, clean_secrets, valid_api_key
    ):
        """两行落到同一个槽位时后一条会静默覆盖前一条。

        `date` 和 `query:date` 解析出来是同一个槽位——用户以为配了两个参数，
        实际只有一个生效。这类覆盖在返回里看不出来，只能提前拦住。
        """
        secret = await _create_secret(client, valid_api_key)
        consumer_id = f"tool-{uuid.uuid4().hex[:6]}"

        resp = await self._batch(
            client,
            valid_api_key,
            [
                {"slot": "query:date", "secret_id": secret["id"], "value_path": "password"},
                {"slot": "query:date", "secret_id": secret["id"], "value_path": "username"},
            ],
            consumer_id=consumer_id,
        )

        assert resp.status_code == 400
        assert "同一个槽位" in resp.json()["detail"]

        listed = await client.get(
            f"{PREFIX}/bindings",
            params={"consumer_type": "api_tool", "consumer_id": consumer_id},
            headers=_headers(valid_api_key),
        )
        assert listed.json()["data"] == []

    async def test_rollback_leaves_no_audit_trail(
        self, client, clean_secrets, valid_api_key
    ):
        """整批回滚时不能留下「已绑定」的审计记录。

        审计走独立 session 和独立事务（见 center._audit 的说明），所以在批量写库
        的中间写审计，一旦后面的行失败整批回滚，审计里就会留下没发生过的操作。
        漏记只是少一条线索，错记会误导排查。
        """
        secret = await _create_secret(client, valid_api_key)
        consumer_id = f"tool-{uuid.uuid4().hex[:6]}"

        resp = await self._batch(
            client,
            valid_api_key,
            [
                {
                    "slot": "query:ok",
                    "secret_id": secret["id"],
                    "value_path": "password",
                },
                # 同一槽位出现两次 → 整批被拒
                {"slot": "query:ok", "secret_id": secret["id"], "value_path": "username"},
            ],
            consumer_id=consumer_id,
        )
        assert resp.status_code == 400

        logs = await client.get(
            f"{PREFIX}/audit",
            params={"secret_id": secret["id"]},
            headers=_headers(valid_api_key),
        )
        assert logs.status_code == 200, logs.text
        leaked = [
            row
            for row in logs.json()["data"]
            if consumer_id in str(row.get("detail") or "")
        ]
        assert leaked == [], f"回滚的批次不该留下审计：{leaked}"

    async def test_successful_batch_still_writes_audit(
        self, client, clean_secrets, valid_api_key
    ):
        """审计延后到提交之后，但成功路径必须照常记。

        延后写审计最容易犯的错是「延着延着就漏了」——回滚时干净，成功时也干净。
        """
        secret = await _create_secret(client, valid_api_key)
        consumer_id = f"tool-{uuid.uuid4().hex[:6]}"

        resp = await self._batch(
            client,
            valid_api_key,
            [
                {"slot": "query:a", "secret_id": secret["id"], "value_path": "password"},
                {"slot": "query:b", "secret_id": secret["id"], "value_path": "username"},
            ],
            consumer_id=consumer_id,
        )
        assert resp.status_code == 200, resp.text

        logs = await client.get(
            f"{PREFIX}/audit",
            params={"secret_id": secret["id"]},
            headers=_headers(valid_api_key),
        )
        assert logs.status_code == 200, logs.text
        mine = [
            row for row in logs.json()["data"] if consumer_id in str(row.get("detail") or "")
        ]
        assert len(mine) == 2, mine
        assert all(row["action"] == "bind" for row in mine)
        assert {row["detail"] and "query:a" in row["detail"] for row in mine} != {False}


class TestNestedValuePath:
    """嵌套字段与数组下标。

    执行期 `pick_field_typed` 一直是逐段走 dict / list 的，能取 `auth.token` 和
    `list.0.id`。缺的只是配置期：路径正则不收数字段、绑定前只校验顶层键、
    `/fields` 只列顶层。结果是「运行时能跑，界面配不出来」。
    """

    async def test_nested_path_can_be_bound(self, client, clean_secrets, valid_api_key):
        secret = await _create_secret(
            client,
            valid_api_key,
            value=json.dumps(
                {"auth": {"token": "T-1", "user": "bot"}, "host": "h"}, ensure_ascii=False
            ),
            value_secret_fields=["auth.token"],
        )
        consumer_id = f"tool-{uuid.uuid4().hex[:6]}"
        resp = await _batch(
            client,
            valid_api_key,
            [{"slot": "query:t", "secret_id": secret["id"], "value_path": "auth.token"}],
            consumer_id=consumer_id,
        )
        assert resp.status_code == 200, resp.text

        rows = (
            await client.get(
                f"{PREFIX}/bindings",
                params={"consumer_type": "api_tool", "consumer_id": consumer_id},
                headers=_headers(valid_api_key),
            )
        ).json()["data"]
        assert [row["value_path"] for row in rows] == ["auth.token"]

    async def test_array_index_can_be_bound(self, client, clean_secrets, valid_api_key):
        secret = await _create_secret(
            client,
            valid_api_key,
            value=json.dumps({"hosts": ["a.example", "b.example"]}, ensure_ascii=False),
        )
        consumer_id = f"tool-{uuid.uuid4().hex[:6]}"
        resp = await _batch(
            client,
            valid_api_key,
            [{"slot": "query:h", "secret_id": secret["id"], "value_path": "hosts.1"}],
            consumer_id=consumer_id,
        )
        assert resp.status_code == 200, resp.text

    async def test_missing_middle_segment_is_reported(self, client, clean_secrets, valid_api_key):
        """断在中间那一段要说清是哪一段，不能只说顶层字段不存在。

        `auth` 存在、`auth.tokn` 拼错时，只查顶层会放行——错误要等调用方报 401 才出现。
        """
        secret = await _create_secret(
            client,
            valid_api_key,
            value=json.dumps({"auth": {"token": "T-1"}}, ensure_ascii=False),
        )
        consumer_id = f"tool-{uuid.uuid4().hex[:6]}"
        resp = await _batch(
            client,
            valid_api_key,
            [{"slot": "query:t", "secret_id": secret["id"], "value_path": "auth.tokn"}],
            consumer_id=consumer_id,
        )
        assert resp.status_code == 400, resp.text
        assert "auth.tokn" in resp.text

    async def test_non_numeric_segment_on_array_is_rejected(
        self, client, clean_secrets, valid_api_key
    ):
        secret = await _create_secret(
            client,
            valid_api_key,
            value=json.dumps({"hosts": ["a.example"]}, ensure_ascii=False),
        )
        consumer_id = f"tool-{uuid.uuid4().hex[:6]}"
        resp = await _batch(
            client,
            valid_api_key,
            [{"slot": "query:h", "secret_id": secret["id"], "value_path": "hosts.first"}],
            consumer_id=consumer_id,
        )
        assert resp.status_code == 400, resp.text
        assert "下标" in resp.text or "没有字段" in resp.text

    async def test_fields_list_includes_nested_paths(self, client, clean_secrets, valid_api_key):
        """`/fields` 要能列出嵌套路径，否则界面上根本选不出来。"""
        secret = await _create_secret(
            client,
            valid_api_key,
            value=json.dumps(
                {"auth": {"token": "T-1"}, "hosts": ["a.example"]}, ensure_ascii=False
            ),
        )
        resp = await client.get(
            f"{PREFIX}/{secret['id']}/fields", headers=_headers(valid_api_key)
        )
        assert resp.status_code == 200, resp.text
        names = [field["name"] for field in resp.json()["data"]["fields"]]
        assert "auth" in names, names
        assert "auth.token" in names, names
        assert "hosts" in names, names
        assert "hosts.0" in names, names


class TestPersonalCredentialOnPlatformTool:
    """管理员给平台级工具配**自己的**个人凭据。

    个人凭据不可能给所有人用，所以「全局绑定」这个请求落地时要降级成本人绑定。
    之前这里直接报「只有系统级凭据可以设为全局绑定」——而前端参数入口是硬编码
    `is_global: true` 的，于是**任何个人凭据都绑不上**，连管理员自己都不行。
    管理员先用自己的账号跑通、再换成平台账号，是最常见不过的路径。
    """

    async def test_personal_secret_is_downgraded_not_rejected(
        self, client, clean_secrets, valid_api_key
    ):
        # 建一条个人级凭据（owner_scope=user）
        payload = {
            "secret_key": f"mine.{uuid.uuid4().hex[:8]}",
            "name": "我自己的邮箱",
            "value": json.dumps({"host": "smtp.mine.com"}, ensure_ascii=False),
            "secret_type": "credential",
            "allowed_scenarios": ["api_tool"],
            "allowed_hosts": [],
            "value_format": "json",
            "value_template": "smtp",
        }
        created = await client.post(PREFIX, json=payload, headers=_headers(valid_api_key))
        assert created.status_code == 200, created.text
        secret = created.json()["data"]

        consumer_id = f"tool-{uuid.uuid4().hex[:6]}"
        resp = await _batch(
            client,
            valid_api_key,
            [{"slot": "query:h", "secret_id": secret["id"], "value_path": "host"}],
            consumer_id=consumer_id,
            # 前端参数入口就是硬编码 true，这里如实模拟
            is_global=True,
        )
        assert resp.status_code == 200, resp.text

        rows = (
            await client.get(
                f"{PREFIX}/bindings",
                params={"consumer_type": "api_tool", "consumer_id": consumer_id},
                headers=_headers(valid_api_key),
            )
        ).json()["data"]
        assert len(rows) == 1
        # 关键：落到「本人绑定」，不是全局
        assert rows[0]["is_global"] is False
        assert rows[0]["bound_by_me"] is True

    async def test_non_admin_cannot_make_a_global_binding(
        self, client, clean_secrets, valid_api_key, admin_api_key
    ):
        """权限没有放松，只是换了判断位置。

        原来在接口层对整批一刀切（`is_global` → 必须管理员），放宽「个人凭据」时
        很容易顺手把这条也放开——那样普通用户就能把一条系统级凭据绑成全局，
        改掉所有人看到的配置。
        """
        # 管理员建一条**系统级**凭据
        payload = {
            "secret_key": f"sys.{uuid.uuid4().hex[:8]}",
            "name": "平台邮箱",
            "value": json.dumps({"host": "smtp.corp.com"}, ensure_ascii=False),
            "secret_type": "credential",
            "allowed_scenarios": ["api_tool"],
            "allowed_hosts": [],
            "value_format": "json",
            "value_template": "smtp",
            "owner_scope": "system",
        }
        created = await client.post(
            f"{PREFIX}/admin", json=payload, headers=_headers(admin_api_key)
        )
        assert created.status_code == 200, created.text
        secret = created.json()["data"]
        assert secret["owner_scope"] == "system", secret

        resp = await _batch(
            client,
            valid_api_key,  # 普通用户
            [{"slot": "query:h", "secret_id": secret["id"], "value_path": "host"}],
            consumer_id=f"tool-{uuid.uuid4().hex[:6]}",
            is_global=True,
        )
        assert resp.status_code == 400, resp.text
        assert "管理员" in resp.text

    async def test_non_admin_cannot_write_a_global_rule(
        self, client, clean_secrets, valid_api_key
    ):
        """规则不指向凭据，但全局规则照样影响所有人——权限必须同样管住。

        这条曾经漏过：`create_binding` 的 rule 分支没传 `allow_global`，
        `bind_rule` 的默认值是 True，于是任何普通用户传 `is_global` +
        `strategy="rule"` 就能改掉全平台的取数规则。
        """
        # 直接测服务层：HTTP 那条会先校验「工具是否存在」，
        # 传个编造的 consumer_id 时根本走不到权限判断，测不到这里要测的东西。
        from app.core.orm import AsyncSessionLocal
        from app.services.secrets import center
        from app.services.secrets.center import SecretPermissionError

        async with AsyncSessionLocal() as session:
            with pytest.raises(SecretPermissionError, match="管理员"):
                await center.bind_rule(
                    session,
                    consumer_type="api_tool",
                    consumer_id="tool-anything",
                    slot="query:key",
                    rule={"key_source": "arg:to", "target": "secret_key"},
                    bound_by_user_id=None,  # 全局
                    allow_global=False,  # 非管理员
                )
            await session.rollback()

    async def test_batch_rule_global_is_rejected_cleanly(
        self, client, clean_secrets, valid_api_key
    ):
        """批量路径要报 400 而不是 500。

        校验轮漏传 `allow_global` 时它会放行，错误要等到写入轮才冒出来，
        而写入轮没有 try/except——用户看到的是 500，且不知道是哪一条错了。
        """
        resp = await _batch(
            client,
            valid_api_key,
            [
                {
                    "slot": "query:key",
                    "strategy": "rule",
                    "rule": {"key_source": "arg:to", "target": "secret_key"},
                }
            ],
            consumer_id=f"tool-{uuid.uuid4().hex[:6]}",
            is_global=True,
        )
        assert resp.status_code == 400, resp.text
        assert "管理员" in resp.text


class TestBatchSyncsCredentialParams:
    """MCP 工具的「由凭据提供」清单由 batch 同批写，不再有第二次 PUT。

    生产事故的根因就在这里：绑定和清单原来是两个请求，第一次成功、第二次失败
    （当时是 `port` 的类型校验 400）就留下「绑了却不注入」——界面显示已映射，
    运行时模型照旧自己填密码，服务端只回一句 credentials_rejected。
    """

    async def _mcp_tool(self, owner_id: int, schema: dict, *, scope: str = "personal"):
        from app.core.orm import AsyncSessionLocal
        from app.models.mcp import McpServer, McpToolCache

        server_id = f"srv-{uuid.uuid4().hex[:8]}"
        tool_id = f"tool-{uuid.uuid4().hex[:8]}"
        async with AsyncSessionLocal() as session:
            session.add(
                McpServer(
                    id=server_id,
                    server_name=f"mcp-private-{uuid.uuid4().hex[:6]}",
                    sse_url="https://mail.example.com/mcp",
                    scope=scope,
                    user_id=owner_id,
                    enabled_status=1,
                )
            )
            session.add(
                McpToolCache(
                    id=tool_id,
                    server_id=server_id,
                    tool_name="list-mail",
                    parameter_schema=json.dumps(schema, ensure_ascii=False),
                    is_published=False,
                )
            )
            await session.commit()
        return server_id, tool_id

    async def _owner_id(self, client, api_key: str) -> int:
        """测试账号的 user_id。走平台自己的鉴权链路，别在测试里复制 hash 规则。"""
        from app.core.orm import AsyncSessionLocal
        from app.services.auth_service import AuthService

        async with AsyncSessionLocal() as session:
            info = await AuthService.verify_api_key(api_key, session)
        assert info and info.get("user_id"), "测试账号不存在"
        return int(info["user_id"])

    async def _stored_params(self, tool_id: str):
        from app.core.orm import AsyncSessionLocal
        from app.models.mcp import McpToolCache

        async with AsyncSessionLocal() as session:
            tool = await session.get(McpToolCache, tool_id)
            raw = tool.credential_params
        return json.loads(raw) if raw else []

    async def test_batch_writes_the_list_in_the_same_request(
        self, client, clean_secrets, valid_api_key
    ):
        secret = await _create_secret(client, valid_api_key)
        owner = await self._owner_id(client, valid_api_key)
        server_id, tool_id = await self._mcp_tool(
            owner,
            {"properties": {"username": {"type": "string"}, "password": {"type": "string"}}},
        )

        resp = await client.post(
            f"{PREFIX}/bindings/batch",
            json={
                "consumer_type": "mcp_server",
                "consumer_id": server_id,
                "mcp_tool_id": tool_id,
                "items": [
                    {
                        "slot": "arg:list-mail:username",
                        "name": "username",
                        "secret_id": secret["id"],
                        "value_path": "username",
                    },
                    {
                        "slot": "arg:list-mail:password",
                        "name": "password",
                        "secret_id": secret["id"],
                        "value_path": "password",
                    },
                ],
            },
            headers=_headers(valid_api_key),
        )

        assert resp.status_code == 200, resp.text
        # 一次请求同时落了两份数据：绑定 + 清单
        assert resp.json()["data"]["credential_params"] == ["username", "password"]
        assert await self._stored_params(tool_id) == ["username", "password"]

    async def test_row_back_to_model_removes_it_from_the_list(
        self, client, clean_secrets, valid_api_key
    ):
        """某行改成「模型填写」后，参数必须从清单里消失，否则模型永远看不到它。"""
        secret = await _create_secret(client, valid_api_key)
        owner = await self._owner_id(client, valid_api_key)
        server_id, tool_id = await self._mcp_tool(
            owner,
            {"properties": {"username": {"type": "string"}, "password": {"type": "string"}}},
        )

        def _body(username_strategy: str, username_secret: str):
            user_row: dict = {
                "slot": "arg:list-mail:username",
                "name": "username",
                "strategy": username_strategy,
            }
            if username_secret:
                user_row.update({"secret_id": username_secret, "value_path": "username"})
            return {
                "consumer_type": "mcp_server",
                "consumer_id": server_id,
                "mcp_tool_id": tool_id,
                "items": [
                    user_row,
                    {
                        "slot": "arg:list-mail:password",
                        "name": "password",
                        "secret_id": secret["id"],
                        "value_path": "password",
                    },
                ],
            }

        first = await client.post(
            f"{PREFIX}/bindings/batch",
            json=_body("fixed", secret["id"]),
            headers=_headers(valid_api_key),
        )
        assert first.status_code == 200, first.text
        assert await self._stored_params(tool_id) == ["username", "password"]

        second = await client.post(
            f"{PREFIX}/bindings/batch",
            json=_body("none", ""),
            headers=_headers(valid_api_key),
        )
        assert second.status_code == 200, second.text
        assert await self._stored_params(tool_id) == ["password"]

    async def test_unknown_param_name_is_rejected_and_nothing_is_saved(
        self, client, clean_secrets, valid_api_key
    ):
        """清单直接决定哪些参数从模型可见 schema 里消失，名字拼错就不能放进去。

        校验放在同一个事务里：不合法就整批回滚，绑定也不会留下——
        「绑定存了、清单没跟上」正是这次要消灭的状态。
        """
        secret = await _create_secret(client, valid_api_key)
        owner = await self._owner_id(client, valid_api_key)
        server_id, tool_id = await self._mcp_tool(
            owner, {"properties": {"password": {"type": "string"}}}
        )

        resp = await client.post(
            f"{PREFIX}/bindings/batch",
            json={
                "consumer_type": "mcp_server",
                "consumer_id": server_id,
                "mcp_tool_id": tool_id,
                "items": [
                    {
                        "slot": "arg:list-mail:passwrod",
                        "name": "passwrod",
                        "secret_id": secret["id"],
                        "value_path": "password",
                    },
                ],
            },
            headers=_headers(valid_api_key),
        )

        assert resp.status_code == 400, resp.text
        assert "不在工具" in resp.text
        assert await self._stored_params(tool_id) == []
        listed = await client.get(
            f"{PREFIX}/bindings",
            params={"consumer_type": "mcp_server", "consumer_id": server_id},
            headers=_headers(valid_api_key),
        )
        assert listed.json()["data"] == []

    async def test_object_param_cannot_be_declared_as_credential(
        self, client, clean_secrets, valid_api_key
    ):
        """对象/数组参数仍由模型填：凭据里那段 JSON 结构不一定对得上对方的嵌套 schema。"""
        secret = await _create_secret(client, valid_api_key)
        owner = await self._owner_id(client, valid_api_key)
        server_id, tool_id = await self._mcp_tool(
            owner, {"properties": {"filter": {"type": "object"}}}
        )

        resp = await client.post(
            f"{PREFIX}/bindings/batch",
            json={
                "consumer_type": "mcp_server",
                "consumer_id": server_id,
                "mcp_tool_id": tool_id,
                "items": [
                    {
                        "slot": "arg:list-mail:filter",
                        "name": "filter",
                        "secret_id": secret["id"],
                        "value_path": "username",
                    },
                ],
            },
            headers=_headers(valid_api_key),
        )

        assert resp.status_code == 400, resp.text
        assert "凭据只能提供字符串、数字、布尔" in resp.text
        assert await self._stored_params(tool_id) == []

    async def test_tool_from_another_server_is_rejected(
        self, client, clean_secrets, valid_api_key
    ):
        """mcp_tool_id 必须属于 consumer_id 指向的那个服务。

        不校验的话，两个都归同一个人的服务之间可以错配：绑定写进 A、清单写进 B，
        两边都不是用户想要的状态，而且都「保存成功」。
        """
        secret = await _create_secret(client, valid_api_key)
        owner = await self._owner_id(client, valid_api_key)
        _, tool_id = await self._mcp_tool(
            owner, {"properties": {"password": {"type": "string"}}}
        )
        other_server_id, _ = await self._mcp_tool(
            owner, {"properties": {"password": {"type": "string"}}}
        )

        resp = await client.post(
            f"{PREFIX}/bindings/batch",
            json={
                "consumer_type": "mcp_server",
                "consumer_id": other_server_id,
                "mcp_tool_id": tool_id,
                "items": [
                    {
                        "slot": "arg:list-mail:password",
                        "name": "password",
                        "secret_id": secret["id"],
                        "value_path": "password",
                    },
                ],
            },
            headers=_headers(valid_api_key),
        )

        assert resp.status_code == 400, resp.text
        assert "不是同一个 MCP 服务" in resp.text
        assert await self._stored_params(tool_id) == []


class TestBatchUnbindCoversDowngradedBindings:
    """全局保存要连带清掉「本人的降级绑定」。

    管理员在平台级工具上绑自己的个人凭据时，`bind_secret` 会把绑定降级成
    `bound_by_user_id=本人`（个人凭据不可能给所有人用）。界面把它显示成已绑定，
    但保存时若只按 is_global 删全局层，那条绑定删不掉——「改回模型填写」之后它
    还留着，下次打开弹窗又显示由凭据提供，而清单已经被移除：反向的半保存。
    """

    async def _mcp_tool(self, owner_id: int):
        from app.core.orm import AsyncSessionLocal
        from app.models.mcp import McpServer, McpToolCache

        server_id = f"srv-{uuid.uuid4().hex[:8]}"
        tool_id = f"tool-{uuid.uuid4().hex[:8]}"
        async with AsyncSessionLocal() as session:
            session.add(
                McpServer(
                    id=server_id,
                    server_name=f"mcp-platform-{uuid.uuid4().hex[:6]}",
                    sse_url="https://mail.example.com/mcp",
                    scope="global",
                    user_id=None,
                    enabled_status=1,
                )
            )
            session.add(
                McpToolCache(
                    id=tool_id,
                    server_id=server_id,
                    tool_name="list-mail",
                    parameter_schema=json.dumps(
                        {"properties": {"password": {"type": "string"}}},
                        ensure_ascii=False,
                    ),
                    is_published=False,
                )
            )
            await session.commit()
        return server_id, tool_id

    async def _rows(self, consumer_id: str, slot: str):
        from sqlalchemy import select

        from app.core.orm import AsyncSessionLocal
        from app.models.secret import SecretBinding

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SecretBinding).where(
                    SecretBinding.consumer_type == "mcp_server",
                    SecretBinding.consumer_id == consumer_id,
                    SecretBinding.slot == slot,
                )
            )
            return list(result.scalars().all())

    async def test_switching_back_to_model_also_clears_my_own_downgraded_binding(
        self, client, clean_secrets, admin_api_key
    ):
        from app.core.orm import AsyncSessionLocal
        from app.services.auth_service import AuthService

        # 管理员绑**自己的**个人凭据：个人凭据不可能给所有人用，bind_secret 会把它
        # 降级成本人绑定——正是这条降级绑定在全局保存时删不掉
        secret = await _create_secret(client, admin_api_key)
        async with AsyncSessionLocal() as session:
            info = await AuthService.verify_api_key(admin_api_key, session)
        owner = int(info["user_id"])

        server_id, tool_id = await self._mcp_tool(owner)
        slot = "arg:list-mail:password"

        # 1) 平台级服务 + 自己的个人凭据 + 全局保存 → 库里落成降级绑定
        body = {
            "consumer_type": "mcp_server",
            "consumer_id": server_id,
            "is_global": True,
            "mcp_tool_id": tool_id,
            "items": [
                {
                    "slot": slot,
                    "name": "password",
                    "secret_id": secret["id"],
                    "value_path": "password",
                }
            ],
        }
        resp = await client.post(
            f"{PREFIX}/bindings/batch", json=body, headers=_headers(admin_api_key)
        )
        assert resp.status_code == 200, resp.text
        rows = await self._rows(server_id, slot)
        assert len(rows) == 1
        # 关键前提：个人凭据的全局绑定被降级成本人绑定
        assert str(rows[0].bound_by_user_id or "") == str(owner)

        # 2) 改回「模型填写」→ 那条降级绑定必须一起被清掉
        body["items"] = [{"slot": slot, "name": "password", "strategy": "none"}]
        resp = await client.post(
            f"{PREFIX}/bindings/batch", json=body, headers=_headers(admin_api_key)
        )
        assert resp.status_code == 200, resp.text

        assert await self._rows(server_id, slot) == []
        assert resp.json()["data"]["credential_params"] == []

    async def test_saving_untouched_params_writes_no_unbind_audit(
        self, client, clean_secrets, admin_api_key
    ):
        """整表保存时，本来就是「模型填写」的参数不该留下解绑记录。

        前端为了同步清单会把工具的全部参数都提交上来（没绑的填 none）。无条件记
        一条 unbind 的话，一个 15 参数、只绑了 1 个密码的工具每保存一次就多 14 条
        从没发生过的解绑记录，审计表很快就没法看了。
        """
        from sqlalchemy import select

        from app.core.orm import AsyncSessionLocal
        from app.models.secret import SecretAuditLog
        from app.services.auth_service import AuthService

        secret = await _create_secret(client, admin_api_key)
        async with AsyncSessionLocal() as session:
            info = await AuthService.verify_api_key(admin_api_key, session)
        owner = int(info["user_id"])

        server_id, tool_id = await self._mcp_tool(owner)
        assert secret  # 造凭据这一步本身不该失败

        async with AsyncSessionLocal() as session:
            before = len(
                (
                    await session.execute(
                        select(SecretAuditLog.id).where(
                            SecretAuditLog.action == "unbind"
                        )
                    )
                )
                .scalars()
                .all()
            )

        # 完全没有绑定的两个参数，提交成 none
        resp = await client.post(
            f"{PREFIX}/bindings/batch",
            json={
                "consumer_type": "mcp_server",
                "consumer_id": server_id,
                "is_global": True,
                "mcp_tool_id": tool_id,
                "items": [
                    {"slot": "arg:list-mail:password", "name": "password", "strategy": "none"},
                    {"slot": "arg:list-mail:extra", "name": "extra", "strategy": "none"},
                ],
            },
            headers=_headers(admin_api_key),
        )
        assert resp.status_code == 200, resp.text

        async with AsyncSessionLocal() as session:
            after = len(
                (
                    await session.execute(
                        select(SecretAuditLog.id).where(
                            SecretAuditLog.action == "unbind"
                        )
                    )
                )
                .scalars()
                .all()
            )

        # 什么都没删掉，就不该有解绑记录
        assert after == before

    async def test_real_unbind_is_still_audited(self, client, clean_secrets, admin_api_key):
        """真删到东西时审计不能丢——上面那条跳过不能变成「再也不记」。"""
        from sqlalchemy import select

        from app.core.orm import AsyncSessionLocal
        from app.models.secret import SecretAuditLog
        from app.services.auth_service import AuthService

        secret = await _create_secret(client, admin_api_key)
        async with AsyncSessionLocal() as session:
            info = await AuthService.verify_api_key(admin_api_key, session)
        owner = int(info["user_id"])

        server_id, tool_id = await self._mcp_tool(owner)
        slot = "arg:list-mail:password"
        body = {
            "consumer_type": "mcp_server",
            "consumer_id": server_id,
            "is_global": True,
            "mcp_tool_id": tool_id,
            "items": [
                {
                    "slot": slot,
                    "name": "password",
                    "secret_id": secret["id"],
                    "value_path": "password",
                }
            ],
        }
        assert (
            await client.post(
                f"{PREFIX}/bindings/batch", json=body, headers=_headers(admin_api_key)
            )
        ).status_code == 200

        body["items"] = [{"slot": slot, "name": "password", "strategy": "none"}]
        assert (
            await client.post(
                f"{PREFIX}/bindings/batch", json=body, headers=_headers(admin_api_key)
            )
        ).status_code == 200

        async with AsyncSessionLocal() as session:
            rows = (
                (
                    await session.execute(
                        select(SecretAuditLog).where(
                            SecretAuditLog.action == "unbind",
                            # 审计表没有 consumer 列，槽位只落在 detail JSON 里
                            SecretAuditLog.detail.like("%list-mail:password%"),
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert rows, "真删掉了绑定却没有解绑审计"


class TestNonAdminCannotTouchTheGlobalLayer:
    """普通用户不能借 `is_global=true` 拆掉全平台的绑定。

    单条 `DELETE /bindings` 有 `只有管理员可以解除全局绑定` 的保护，批量接口原先漏了：
    `bound_by = None if payload.is_global else user_id` 直接照单全收，于是普通用户在
    **自己有权访问的平台级服务**上发一句 `is_global=True` + `strategy="none"`，就能删掉
    全平台共用的绑定和规则——而这些配置是所有人共用的。
    """

    def test_global_layer_requires_admin(self):
        src = (
            Path(__file__).parents[3] / "app/api/portal/endpoints/secrets.py"
        ).read_text(encoding="utf-8")
        # 解绑/清规则的归属必须按身份算：非管理员永远只动本人层
        assert "unbind_bound_by: str | None = None if (payload.is_global and is_admin) else user_id" in src
        # 两处解绑（none 分支、fixed 清旧规则）都要用这个归属，不能退回 bound_by
        assert src.count("bound_by=unbind_bound_by") == 2
        # 写入侧仍按凭据作用域判（个人凭据要能降级），所以 bound_by 的写法保留
        assert "bound_by: str | None = None if payload.is_global else user_id" in src

    async def test_downgraded_to_personal_layer_instead_of_global(
        self, client, clean_secrets, valid_api_key
    ):
        """普通用户即使传 is_global=True，绑定也只落在本人层。"""
        from sqlalchemy import select

        from app.core.orm import AsyncSessionLocal
        from app.models.secret import SecretBinding
        from app.services.auth_service import AuthService

        secret = await _create_secret(client, valid_api_key)
        async with AsyncSessionLocal() as session:
            info = await AuthService.verify_api_key(valid_api_key, session)
        owner = int(info["user_id"])

        server_id = f"srv-{uuid.uuid4().hex[:8]}"
        async with AsyncSessionLocal() as session:
            from app.models.mcp import McpServer

            session.add(
                McpServer(
                    id=server_id,
                    server_name=f"mcp-mine-{uuid.uuid4().hex[:6]}",
                    sse_url="https://mail.example.com/mcp",
                    scope="personal",
                    user_id=owner,
                    enabled_status=1,
                )
            )
            await session.commit()

        resp = await client.post(
            f"{PREFIX}/bindings/batch",
            json={
                "consumer_type": "mcp_server",
                "consumer_id": server_id,
                "is_global": True,  # 非管理员提这个要求
                "items": [
                    {
                        "slot": "arg:list-mail:password",
                        "name": "password",
                        "secret_id": secret["id"],
                        "value_path": "password",
                    }
                ],
            },
            headers=_headers(valid_api_key),
        )
        assert resp.status_code == 200, resp.text

        async with AsyncSessionLocal() as session:
            rows = (
                (
                    await session.execute(
                        select(SecretBinding).where(
                            SecretBinding.consumer_id == server_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert rows, "绑定没落库"
        # 落成本人层，不是全局层（空串/NULL 代表全局）
        assert str(rows[0].bound_by_user_id or "") == str(owner)


class TestGlobalLayerIsProtectedThroughTheRealEndpoint:
    """走真实接口验证越权被挡住，而不只是断言源码里有那行判断。

    review 指出前面几条是源码文本断言：把 `bound_by=unbind_bound_by` 改回
    `bound_by=bound_by`、或者把那行判断删掉，文本断言会红，但如果有人换一种写法
    实现同样的漏洞，文本断言照样绿。这里用真实请求打一遍。
    """

    async def test_non_admin_none_cannot_delete_a_global_binding(
        self, client, clean_secrets, valid_api_key, admin_api_key
    ):
        from sqlalchemy import select

        from app.core.orm import AsyncSessionLocal
        from app.models.mcp import McpServer
        from app.models.secret import SecretBinding
        from app.services.auth_service import AuthService

        # 管理员建一个平台级服务 + 一条全局绑定
        async with AsyncSessionLocal() as session:
            admin_info = await AuthService.verify_api_key(admin_api_key, session)
        admin_id = int(admin_info["user_id"])
        server_id = f"srv-{uuid.uuid4().hex[:8]}"

        # 系统级凭据走管理员入口（`/admin`），普通入口会把 owner_scope 落成个人
        system_secret = await client.post(
            f"{PREFIX}/admin",
            json={
                "secret_key": f"shared.mail.{uuid.uuid4().hex[:6]}",
                "name": "全平台共用的邮箱凭据",
                "value": json.dumps({"password": "pw-shared"}),
                "secret_type": "credential",
                "allowed_scenarios": ["mcp"],
                "allowed_hosts": [],
                "value_format": "json",
                "value_template": "smtp",
                "owner_scope": "system",
            },
            headers=_headers(admin_api_key),
        )
        assert system_secret.status_code == 200, system_secret.text
        secret_id = system_secret.json()["data"]["id"]

        async with AsyncSessionLocal() as session:
            session.add(
                McpServer(
                    id=server_id,
                    server_name=f"mcp-shared-{uuid.uuid4().hex[:6]}",
                    sse_url="https://mail.example.com/mcp",
                    scope="global",
                    user_id=None,
                    enabled_status=1,
                )
            )
            await session.commit()

        slot = "arg:list-mail:password"
        created = await client.post(
            f"{PREFIX}/bindings/batch",
            json={
                "consumer_type": "mcp_server",
                "consumer_id": server_id,
                "is_global": True,
                "items": [
                    {
                        "slot": slot,
                        "name": "password",
                        "secret_id": secret_id,
                        "value_path": "password",
                    }
                ],
            },
            headers=_headers(admin_api_key),
        )
        assert created.status_code == 200, created.text

        async def _global_rows():
            async with AsyncSessionLocal() as session:
                rows = (
                    (
                        await session.execute(
                            select(SecretBinding).where(
                                SecretBinding.consumer_id == server_id,
                                SecretBinding.slot == slot,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            return [r for r in rows if not (r.bound_by_user_id or "")]

        assert await _global_rows(), "前置条件：全局绑定没建成"

        # 普通用户谎称 is_global=True，想把全局层清掉
        attack = await client.post(
            f"{PREFIX}/bindings/batch",
            json={
                "consumer_type": "mcp_server",
                "consumer_id": server_id,
                "is_global": True,
                "items": [{"slot": slot, "name": "password", "strategy": "none"}],
            },
            headers=_headers(valid_api_key),
        )
        assert attack.status_code == 200, attack.text

        # 全局绑定必须还在
        assert await _global_rows(), "普通用户把全平台的绑定删掉了"


class TestLocatePreviewAgreesWithRuntime:
    """试算走**真实接口**，确认 target=field 和场景过滤都真的生效。

    之前这两点只有源码文本断言：把 `field_value=` 删掉、或者把 `scene=` 删掉，文本
    断言照样绿，但配置者点「试算」看到的答案会跟真实调用分家。
    """

    async def _preview(self, client, api_key, *, rule, sample_args, consumer_type):
        return await client.post(
            f"{PREFIX}/locate-preview",
            json={
                "consumer_type": consumer_type,
                "rule": rule,
                "sample_args": sample_args,
            },
            headers=_headers(api_key),
        )

    async def test_field_rule_hits(self, client, clean_secrets, valid_api_key):
        await _create_secret(
            client,
            valid_api_key,
            secret_key=f"mail.prev.{uuid.uuid4().hex[:6]}",
            value=json.dumps({"username": "alice@corp.com", "password": "pw"}),
            allowed_scenarios=["mcp"],
        )

        resp = await self._preview(
            client,
            valid_api_key,
            consumer_type="mcp_server",
            rule={
                "key_source": "arg:username",
                "target": "field",
                "target_field": "username",
                "match": "exact_ci",
            },
            sample_args={"username": "alice@corp.com"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["ok"] is True, data["miss_reason"]

    async def test_scene_mismatch_does_not_hit(self, client, clean_secrets, valid_api_key):
        """只授权给 api_tool 的凭据，在 mcp_server 上试算必须不命中。

        不筛场景的话，试算会说「命中」，真实调用却因场景不符被过滤——又一次
        「试算说行、跑起来不行」。
        """
        await _create_secret(
            client,
            valid_api_key,
            secret_key=f"mail.prev.{uuid.uuid4().hex[:6]}",
            value=json.dumps({"username": "bob@corp.com", "password": "pw"}),
            allowed_scenarios=["api_tool"],
        )

        as_mcp = await self._preview(
            client,
            valid_api_key,
            consumer_type="mcp_server",
            rule={
                "key_source": "arg:username",
                "target": "field",
                "target_field": "username",
                "match": "exact_ci",
            },
            sample_args={"username": "bob@corp.com"},
        )
        assert as_mcp.status_code == 200, as_mcp.text
        assert as_mcp.json()["data"]["ok"] is False, "场景不符却报了命中"

        # 换成 api_tool 就应当命中：同一个字段值，差别只在场景
        as_api = await self._preview(
            client,
            valid_api_key,
            consumer_type="api_tool",
            rule={
                "key_source": "arg:username",
                "target": "field",
                "target_field": "username",
                "match": "exact_ci",
            },
            sample_args={"username": "bob@corp.com"},
        )
        assert as_api.status_code == 200, as_api.text
        assert as_api.json()["data"]["ok"] is True, "场景放行却没有命中"
