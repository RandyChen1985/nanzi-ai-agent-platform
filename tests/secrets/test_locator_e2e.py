"""端到端：`send_mail` 的 mailkey 按 username 注入不同凭据（真实 DB，不走 HTTP）。

对应用户提出的场景：echo MCP 里加一个模拟发邮件的方法，参数 `username` / `mailkey`，
`mailkey` 声明为凭据参数，按 `username` 去匹配不同的 key。

这里验证的是**完整的注入链路**（除了最后的 HTTP 出站）：
`apply_mcp_arg_bindings` → `secret_resolver.resolve` → 绑定/规则/授权/策略 → 解密 → 填进 arguments。

用真实 DB 而不是 mock，因为要证明的东西恰恰是「查库、解密、规则匹配」这一串协作是否正确。
"""
import hashlib
from pathlib import Path
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
        row = await center.bind_rule(
            session,
            consumer_type="mcp_server",
            consumer_id=SERVER_ID,
            slot=SLOT,
            rule={
                "key_source": "arg:to",
                "transforms": ["trim"],
                "templates": ["{value}"],
                "target": "secret_key",
                # 刻意不传 match：默认值必须就能命中（secret_key 入库时被小写化）
                "on_miss": on_miss,
            },
            bound_by_user_id=None,  # 全局规则，等价于管理员配好的那一份
        )
        # 建没建成本身就该断言：以前这里 `return` 一个可能为 None 的结果，
        # 后面所有「规则没命中」的失败都会伪装成「注入没生效」，排查方向全错。
        assert row is not None, "规则绑定没建成"
        return row


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

    got_a = await _inject({"to": "A", "subject": "hi"})
    got_b = await _inject({"to": "B", "subject": "hi"})

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
    got = await _inject({"to": "C"}, denials)

    assert "mailkey" not in got
    assert denials.get(SLOT) == "rule_miss"


@pytest.mark.asyncio
async def test_model_choice_rescues_the_mismatch(seeded):
    """用户的原用例：规则按 `C` 匹配不到，模型看到 `CC / C 的邮箱账号` 后回传 c3。"""
    await _bind_rule("model")

    # 候选按 secret_key 排序：A(c1) B(c2) CC(c3)
    got = await _inject({"to": "C", "secret_choice_mailkey": "c3"})

    assert got["mailkey"] == "sk-charlie-333333"


@pytest.mark.asyncio
async def test_model_choice_is_ignored_when_rule_hits(seeded):
    """规则命中时，模型乱填的编号必须被忽略——否则模型一句话就能改掉注入的凭据。"""
    await _bind_rule("model")

    got = await _inject({"to": "A", "secret_choice_mailkey": "c3"})

    assert got["mailkey"] == "sk-alpha-111111"


@pytest.mark.asyncio
async def test_bad_choice_number_is_treated_as_no_choice(seeded):
    """编号越界不能猜一个，只能当「模型没选」→ 未命中 → 失败。"""
    await _bind_rule("model")

    denials: dict = {}
    got = await _inject({"to": "C", "secret_choice_mailkey": "c99"}, denials)

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
    got = await _inject({"to": "Z"}, denials)

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

    # 签名：send_mail(ctx, to, subject, host, port, ..., mailkey)
    out = await send_mail(None, "A", "hi", None, None, None, None, None, None, "sk-alpha-111111")
    assert out["to"] == "A"
    assert out["subject"] == "hi"
    assert out["mailkey"]["present"] is True
    assert out["mailkey"]["length"] == len("sk-alpha-111111")
    assert out["mailkey"]["fingerprint"] == _fingerprint("sk-alpha-111111")
    # 明文绝不能出现在返回值里
    assert "sk-alpha-111111" not in json.dumps(out, ensure_ascii=False)


@pytest.mark.asyncio
async def test_send_mail_reports_absent_key_explicitly():
    from app.services.mcp.echo_server import send_mail

    out = await send_mail(None, "A")
    assert out["mailkey"]["present"] is False
    # 没收到就不该有任何可推断的痕迹（掩码/指纹/长度都不给）
    assert set(out["mailkey"]) == {"present"}


def test_send_mail_schema_declares_credential_param():
    """`mailkey` 必须在 schema 里——它同时是「声明由凭据提供」的对象。"""
    import json

    from app.services.mcp.echo_server import send_mail_tool_schema

    schema = json.loads(send_mail_tool_schema())
    # 名字与「邮箱 SMTP 发送」模板字段对齐：映射面板的「按名字自动匹配」靠同名命中
    assert {"host", "port", "security", "username", "password", "from_name"} <= set(
        schema["properties"]
    )
    assert {"to", "subject", "mailkey"} <= set(schema["properties"])
    assert schema["required"] == ["to"]


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


@pytest.mark.asyncio
async def test_send_mail_reports_every_smtp_field():
    """SMTP 字段要能一眼看出「映射对了没有」。

    这条工具存在的意义就是验收：一条结构化凭据铺到 host/port/security/username/
    password/from_name 上，回执里要能逐个核对。非敏感的给明文（host 映射错了一看
    便知），敏感的只给掩码指纹（password 泄露进 trace 就白做了）。
    """
    import json

    from app.services.mcp.echo_server import send_mail

    out = await send_mail(
        None, "ops@nanzi.ai", "日报", "smtp.exmail.qq.com", 465, "ssl",
        "alert-bot@nanzi.ai", "Whq7mT2026smtp", "南孜平台告警", None,
    )
    smtp = out["smtp"]
    assert smtp["host"]["value"] == "smtp.exmail.qq.com"
    assert smtp["port"]["value"] == 465
    assert smtp["security"]["value"] == "ssl"
    assert smtp["username"]["value"] == "alert-bot@nanzi.ai"
    assert smtp["from_name"]["value"] == "南孜平台告警"
    # 密码只给掩码与指纹
    assert smtp["password"]["present"] is True
    assert smtp["password"]["fingerprint"] == _fingerprint("Whq7mT2026smtp")
    # 只声明不传 = 明确「没收到」，不能和「收到空串」混为一谈
    assert out["mailkey"]["present"] is False
    assert "Whq7mT2026smtp" not in json.dumps(out, ensure_ascii=False)


@pytest.mark.asyncio
async def test_send_mail_default_mode_stays_pure_mock():
    """mode 缺省必须是纯模拟：键集不变、不带 real，避免既有验收用例误判。

    ``mode`` 是后加的能力，既有注入验收都依赖「不传 mode 就等于今天的行为」；
    一旦默认值变成 verify/send，这些用例会真的去连 smtp.exmail.qq.com。
    """
    from app.services.mcp.echo_server import send_mail

    out = await send_mail(
        None, "a@b.com", "hi", "smtp.qq.com", 465, "ssl",
        "ops@b.com", "secret", "运维", "sk-alpha-111111",
    )

    assert set(out) == {"ok", "to", "subject", "smtp", "mailkey", "message"}
    assert "real" not in out
    assert out["message"] == "已模拟给 a@b.com 发送邮件"
    assert out["smtp"]["password"]["fingerprint"] == _fingerprint("secret")
    assert "secret" not in str(out["smtp"]["password"])


class TestRuleMissMessageShowsTheValue:
    """规则未命中时，错误信息要说清「拿什么去找的、找的是什么」。

    用户拿到「要找 arg:username」这种句子是没法排查的：它可能意味着「拿入参
    username 的值去找了但没命中」，也可能意味着「username 这个入参压根没有值」，
    两者的修法完全不同（换规则来源 vs 改绑定）。
    """

    @staticmethod
    def _spec(key_source: str):
        from app.services.secrets.locator import parse_rule

        return parse_rule({
            "key_source": key_source,
            "transforms": ["trim"],
            "templates": ["{value}"],
            "target": "secret_key",
            "match": "exact_ci",
            "on_miss": "fail",
        })

    def test_missing_argument_says_it_has_no_value(self):
        from app.services.secrets.resolver import SecretResolver

        detail = SecretResolver._miss_detail(
            self._spec("arg:username"), [], {"to": "a@b.com"}, None
        )
        assert "username" in detail
        assert "没有值" in detail
        # 这句是给「把凭据参数当规则来源」的人看的：他们最常踩这个坑
        assert "不会出现在入参里" in detail
        # 没查可见集合就不能报候选数，否则用户以为库里真的没有凭据
        assert "可见候选" not in detail

    def test_value_case_shows_argument_value_and_target(self):
        from app.services.secrets.resolver import SecretResolver

        detail = SecretResolver._miss_detail(
            self._spec("arg:to"), ["cexlong@cexlong.com"], {"to": "cexlong@cexlong.com"}, [1, 2]
        )
        assert "to='cexlong@cexlong.com'" in detail       # 传进来的值
        assert "要找 cexlong@cexlong.com" in detail        # 变形后实际去比的 key
        assert "可见候选 2 条" in detail

    def test_blank_value_is_visible_as_quoted_blanks(self):
        from app.services.secrets.resolver import SecretResolver

        detail = SecretResolver._miss_detail(self._spec("arg:to"), [], {"to": "   "}, None)
        assert "to='   '" in detail
        assert "变形后为空" in detail

    def test_sensitive_argument_value_is_masked(self):
        from app.services.secrets.resolver import SecretResolver

        detail = SecretResolver._miss_detail(
            self._spec("arg:password"), ["hunter2"], {"password": "hunter2"}, [1]
        )
        # 入参值和由它变形出来的「要找」都会进会话正文与日志，两处都不能是明文
        assert "hunter2" not in detail
        assert "***" in detail

    def test_identity_key_source_keeps_old_phrasing(self):
        from app.services.secrets.resolver import SecretResolver

        detail = SecretResolver._miss_detail(self._spec("user.email"), [], {}, [])
        assert "要找 user.email" in detail


class TestModelChoiceMode:
    """「让模型挑」是一种独立配法，不是「规则未命中后的兜底」。

    它没有「拿什么去比」这一步，所以规则里 `key_source` 为空——这在别的配法下
    是坏配置（算不出候选、必然未命中），只在 `on_miss=model` 时成立。
    """

    def test_empty_key_source_is_allowed_when_model_chooses(self):
        from app.services.secrets.locator import build_candidates, parse_rule

        spec = parse_rule({"key_source": "", "on_miss": "model", "value_template": "smtp"})
        assert spec.key_source == ""
        assert spec.on_miss == "model"
        # 不算 key，但也不能崩：resolver 靠「候选为空」走到 on_miss 分支
        assert build_candidates(spec, args={"to": "a@b.com"}) == []

    def test_empty_key_source_is_rejected_without_model_choice(self):
        from app.services.secrets.locator import RuleError, parse_rule

        with pytest.raises(RuleError) as err:
            parse_rule({"key_source": "", "on_miss": "fail"})
        # 报错要指出该怎么办，否则用户只知道少了个字段
        assert "on_miss" in str(err.value)

    def test_summary_says_model_chooses(self):
        from app.services.secrets.locator import parse_rule, rule_summary

        spec = parse_rule({"key_source": "", "on_miss": "model", "value_template": "smtp"})
        summary = rule_summary(spec)
        assert "模型" in summary
        # 不能渲染成「（取）」——那是来源为空时的占位残渣
        assert "取）" not in summary


class TestSlotCapabilityRejectsImpossibleConfigs:
    """有些配法在某个位点上不可能生效，必须保存时就拒。

    存进库的代价是静默的：界面显示配好了，运行时永远不命中。依据在注入层——
    `apply_script_env_bindings` 不传 rule_args，两个 Header 级函数不传
    preferred_secret_id。
    """

    @staticmethod
    async def _bind(slot: str, consumer_type: str, rule: dict):
        from app.services.secrets import center

        await center.bind_rule(
            None,
            consumer_type=consumer_type,
            consumer_id="t1",
            slot=slot,
            rule=rule,
            bound_by_user_id="u1",
            dry_run=True,
        )

    @pytest.mark.asyncio
    async def test_header_slot_rejects_model_choice(self):
        from app.services.secrets.center import SecretValidationError

        with pytest.raises(SecretValidationError) as err:
            await self._bind(
                "header:Authorization", "mcp_server",
                {"key_source": "user.email", "on_miss": "model"},
            )
        assert "Header" in str(err.value)

    @pytest.mark.asyncio
    async def test_header_slot_still_allows_fail_and_fallback(self):
        for on_miss in ("fail", "fallback_fixed"):
            await self._bind(
                "header:Authorization", "mcp_server",
                {"key_source": "user.email", "on_miss": on_miss},
            )

    @pytest.mark.asyncio
    async def test_script_env_rejects_rule(self):
        from app.services.secrets.center import SecretValidationError

        with pytest.raises(SecretValidationError) as err:
            await self._bind("env:API_TOKEN", "script_tool", {"key_source": "user.email"})
        assert "环境变量" in str(err.value)


class TestVisibleCacheIsPerCallOnly:
    """可见集合缓存必须只在一次调用内有效，且不缓存明文。

    「一条凭据供多个参数」时，每个槽位都会重新查一遍可见集合（带权限与场景
    过滤）。它只取决于 (user_id, dept_code)，与槽位无关，所以按这个键缓存。
    风险在于作用域：一旦跨调用复用，改完凭据（或撤销授权）之后旧结果还会生效。
    这里直接断言「不传就不缓存」——缓存的载体是调用方传进来的 dict，没有它
    就每次现查，这是作用域正确的结构保证。
    """

    def test_resolve_accepts_visible_cache_and_defaults_to_none(self):
        import inspect

        from app.services.secrets.resolver import SecretResolver

        params = inspect.signature(SecretResolver.resolve).parameters
        assert "visible_cache" in params
        assert params["visible_cache"].default is None

    def test_visible_cache_keyed_by_identity_not_slot(self):
        src = (Path(__file__).resolve().parents[2] / "app/services/secrets/resolver.py").read_text(encoding="utf-8")
        # 键必须只由身份构成：把 slot 掺进去就退化成「每个槽位一份缓存」，
        # 多值组等于没优化
        assert 'key = (user_id, dept_code or "")' in src
        assert "visible_cache[key] = list(got)" in src

    def test_injection_entries_share_one_cache_per_call(self):
        src = (Path(__file__).resolve().parents[2] / "app/services/secrets/injection.py").read_text(encoding="utf-8")
        # 每个注入入口各声明一份（单次调用作用域），并且都传给 resolve
        assert src.count("visible_cache: dict[tuple[str, str], list] = {}") == 5
        assert src.count("visible_cache=visible_cache") == 5
