"""端到端：`send_mail` 的 mailkey 按 username 注入不同凭据（真实 DB，不走 HTTP）。

对应用户提出的场景：echo MCP 里加一个模拟发邮件的方法，参数 `username` / `mailkey`，
`mailkey` 声明为凭据参数，按 `username` 去匹配不同的 key。

这里验证的是**完整的注入链路**（除了最后的 HTTP 出站）：
`apply_mcp_arg_bindings` → `secret_resolver.resolve` → 绑定/规则/授权/策略 → 解密 → 填进 arguments。

用真实 DB 而不是 mock，因为要证明的东西恰恰是「查库、解密、规则匹配」这一串协作是否正确。
"""
import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import delete

from app.core.orm import AsyncSessionLocal
from app.models.mcp import McpServer
from app.models.secret import SecretBinding, SecretUsageLog, UserSecret
from app.services.secrets import center
from app.services.secrets.injection import apply_mcp_arg_bindings

pytestmark = pytest.mark.no_infrastructure

USER_ID = "e2e-locator-user"
SERVER_ID = "e2e-locator-echo-server"
SLOT = "arg:send_mail:mailkey"

# 三条凭据：key 是 A / B / CC，值是能被指纹区分开的明文
SECRETS = [
    ("A", "sk-alpha-111111"),
    ("B", "sk-bravo-222222"),
    ("CC", "sk-charlie-333333"),
]


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _tool_record():
    """够用的 tool_record：注入层只读这几个属性。"""
    return SimpleNamespace(
        tool_name="Echo:send_mail",
        server_id=SERVER_ID,
        credential_params='["mailkey"]',
        parameter_schema=None,
    )


async def _cleanup(session):
    await session.execute(delete(SecretBinding).where(SecretBinding.consumer_id == SERVER_ID))
    await session.execute(delete(SecretUsageLog).where(SecretUsageLog.user_id == USER_ID))
    # 连同「别人」的测试凭据一起清：last test 会建一条 owner=someone-else 的，
    # 不删的话下一轮跑会因为 secret_key 重复而直接失败
    await session.execute(
        delete(UserSecret).where(UserSecret.owner_id.in_([USER_ID, "someone-else"]))
    )
    await session.execute(delete(McpServer).where(McpServer.id == SERVER_ID))
    await session.commit()


@pytest.fixture
async def seeded():
    """建一个够真实的环境：服务（带 sse_url，否则 host 检查会直接拒）+ 三条凭据。"""
    async with AsyncSessionLocal() as session:
        await _cleanup(session)

        session.add(
            McpServer(
                id=SERVER_ID,
                server_name="E2E Locator Echo",
                sse_url="http://127.0.0.1:8790/mcp",
                scope="global",
                enabled_status=1,
            )
        )
        await session.commit()

        for key, value in SECRETS:
            await center.create_secret(
                session,
                owner_scope="user",
                owner_id=USER_ID,
                secret_key=key,
                name=f"{key} 的邮箱账号",
                value=value,
                # 备注是模型推断的唯一依据，这里刻意写成「C 的邮箱账号」这种能救回错名的形式
                remark=f"{key} 的邮箱账号",
                category="mail",
                allowed_scenarios=["mcp"],
            )

    yield

    async with AsyncSessionLocal() as session:
        await _cleanup(session)


async def _bind_rule(on_miss: str = "fail"):
    async with AsyncSessionLocal() as session:
        return await center.bind_rule(
            session,
            consumer_type="mcp_server",
            consumer_id=SERVER_ID,
            slot=SLOT,
            rule={
                "key_source": "arg:username",
                "transforms": ["trim"],
                "templates": ["{value}"],
                "target": "secret_key",
                # 刻意不传 match：默认值必须就能命中（secret_key 入库时被小写化）
                "on_miss": on_miss,
            },
            bound_by_user_id=None,  # 全局规则，等价于管理员配好的那一份
        )


async def _inject(arguments: dict, denials: dict | None = None) -> dict:
    return await apply_mcp_arg_bindings(
        _tool_record(),
        dict(arguments),
        user_info={"user_id": USER_ID},
        denials=denials if denials is not None else {},
    )


@pytest.mark.asyncio
async def test_different_username_injects_different_secret(seeded):
    """核心场景：同一个工具，username=A 用 A 的 key，username=B 用 B 的 key。"""
    await _bind_rule("fail")

    got_a = await _inject({"username": "A", "subject": "hi"})
    got_b = await _inject({"username": "B", "subject": "hi"})

    assert got_a["mailkey"] == "sk-alpha-111111"
    assert got_b["mailkey"] == "sk-bravo-222222"
    # 非凭据参数原样透传，不能被注入动到
    assert got_a["subject"] == "hi"
    assert got_b["subject"] == "hi"
    # 指纹能区分是哪条（这正是 send_mail 回显里给自动化断言用的）
    assert _fingerprint(got_a["mailkey"]) != _fingerprint(got_b["mailkey"])


@pytest.mark.asyncio
async def test_unmatched_username_fails_and_reports_slot(seeded):
    """key 写成 CC，username 传 C —— 规则匹配不上，`on_miss=fail` 必须报错。

    这条是「不能静默退回让模型自己填」的证明：denials 会有记录，调用方据此报错。
    """
    await _bind_rule("fail")

    denials: dict = {}
    got = await _inject({"username": "C"}, denials)

    assert "mailkey" not in got
    assert denials.get(SLOT) == "rule_miss"


@pytest.mark.asyncio
async def test_model_choice_rescues_the_mismatch(seeded):
    """用户的原用例：规则按 `C` 匹配不到，模型看到 `CC / C 的邮箱账号` 后回传 c3。"""
    await _bind_rule("model")

    # 候选按 secret_key 排序：A(c1) B(c2) CC(c3)
    got = await _inject({"username": "C", "secret_choice_mailkey": "c3"})

    assert got["mailkey"] == "sk-charlie-333333"


@pytest.mark.asyncio
async def test_model_choice_is_ignored_when_rule_hits(seeded):
    """规则命中时，模型乱填的编号必须被忽略——否则模型一句话就能改掉注入的凭据。"""
    await _bind_rule("model")

    got = await _inject({"username": "A", "secret_choice_mailkey": "c3"})

    assert got["mailkey"] == "sk-alpha-111111"


@pytest.mark.asyncio
async def test_bad_choice_number_is_treated_as_no_choice(seeded):
    """编号越界不能猜一个，只能当「模型没选」→ 未命中 → 失败。"""
    await _bind_rule("model")

    denials: dict = {}
    got = await _inject({"username": "C", "secret_choice_mailkey": "c99"}, denials)

    assert "mailkey" not in got
    assert denials.get(SLOT) == "rule_miss"


@pytest.mark.asyncio
async def test_other_users_secret_is_never_matched(seeded):
    """别人的私有凭据不在可见集合里：即便 secret_key 恰好对上，也不能用。"""
    async with AsyncSessionLocal() as session:
        await center.create_secret(
            session,
            owner_scope="user",
            owner_id="someone-else",
            secret_key="Z",  # 与下面要查的键相同
            name="别人的 Z",
            value="sk-zulu-999999",
            allowed_scenarios=["mcp"],
        )
    await _bind_rule("fail")

    denials: dict = {}
    got = await _inject({"username": "Z"}, denials)

    assert "mailkey" not in got
    assert denials.get(SLOT) == "rule_miss"


# ── echo MCP 的 send_mail：验收工具本身要可信 ──────────────────────────────
#
# 它是人肉验收和自动化共用的探针，所以「回显什么、不回显什么」必须钉住：
# 一旦有人图方便把明文塞进返回值，凭据就会跟着进对话与 trace。

@pytest.mark.asyncio
async def test_send_mail_masks_and_fingerprints_without_leaking_plaintext():
    import json

    from app.services.mcp.echo_server import send_mail

    out = await send_mail(None, "A", "sk-alpha-111111", "hi")
    assert out["username"] == "A"
    assert out["subject"] == "hi"
    assert out["mailkey_present"] is True
    assert out["mailkey_length"] == len("sk-alpha-111111")
    assert out["mailkey_fingerprint"] == _fingerprint("sk-alpha-111111")
    # 明文绝不能出现在返回值里
    assert "sk-alpha-111111" not in json.dumps(out, ensure_ascii=False)


@pytest.mark.asyncio
async def test_send_mail_reports_absent_key_explicitly():
    from app.services.mcp.echo_server import send_mail

    out = await send_mail(None, "A", None, "")
    assert out["mailkey_present"] is False
    assert out["mailkey_length"] == 0
    assert out["mailkey_masked"] is None
    assert out["mailkey_fingerprint"] is None


def test_send_mail_schema_declares_credential_param():
    """`mailkey` 必须在 schema 里——它同时是「声明由凭据提供」的对象。"""
    import json

    from app.services.mcp.echo_server import send_mail_tool_schema

    schema = json.loads(send_mail_tool_schema())
    assert set(schema["properties"]) == {"username", "mailkey", "subject"}
    assert schema["required"] == ["username"]


def test_send_mail_slot_matches_what_we_bind():
    """槽位必须和 `_bind_rule` 里写的那个一致，否则界面配了、注入却找不到。"""
    from app.services.secrets.injection import mcp_arg_slots

    pairs = mcp_arg_slots(_tool_record())
    assert pairs == [("mailkey", SLOT)]


@pytest.mark.asyncio
async def test_send_mail_is_registered_on_the_mcp_server():
    """函数存在 ≠ 服务暴露。

    `@echo_mcp.tool` 注册失败的话，函数照样能直接调用（上面几条都会过），但真实的
    `tools/call` 会 404——所以这条必须单独钉住。
    """
    from app.services.mcp.echo_server import echo_mcp

    tools = await echo_mcp.list_tools()
    names = {tool.name for tool in tools}
    assert "send_mail" in names
    # 验收要的是「同一个服务里既有 echo 又有 send_mail」，不是替换掉谁
    assert {"echo", "send_mail"} <= names


@pytest.mark.asyncio
async def test_send_mail_tool_schema_seen_by_mcp_client_keeps_mailkey():
    """MCP 客户端看到的 schema 里必须有 mailkey，平台才可能把它声明成凭据参数。"""
    from app.services.mcp.echo_server import echo_mcp

    tools = {tool.name: tool for tool in await echo_mcp.list_tools()}
    schema = tools["send_mail"].input_schema
    assert "mailkey" in schema.get("properties", {})
