"""API 工具参数的「模型挑一条」通道：真的能用吗。

MCP 工具参数早就有 ``secret_choice_<参数名>`` 编号回传通道，API 工具参数
（``x-nanzi-system-params`` 里 ``source: rule``）以前没有——配了 ``on_miss=model``
也永远救不回来，界面看着选项生效、调用必然失败。

这个文件盯住三件事：

1. 编号字段真的出现在模型可见的 schema 里（否则模型想挑也没处填）；
2. 编号指向的凭据值真的进了 HTTP 报文，**而编号字段本身绝不进**；
3. 规则命中时编号被忽略——确定性优先，AI 只兜底。

全程进程内（``/api/test/echo`` + ``ASGITransport``），不起 uvicorn、不占端口。
"""
import hashlib
import json
import logging
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import delete

from app.core.orm import AsyncSessionLocal
from app.models.secret import SecretBinding, SecretUsageLog, UserSecret
from app.models.tool import SysApiTool
from app.services.ai.tools.generic_api import GenericApiToolFactory
from app.services.secrets import center

USER_ID = "choice-e2e-user"
TOOL_ID = "choice-e2e-api-tool"
ECHO_URL = "http://testserver/api/test/echo"

VALUE_A = "sk-choice-aaa-111"
VALUE_B = "sk-choice-bbb-222"
VALUE_CC = "sk-choice-ccc-333"

#: 排序后的编号（`catalog_sort_key` = secret_key → id，secret_key 入库会小写化）：
#: ``a`` → c1、``b`` → c2、``cc`` → c3。规则按 ``arg:username`` 取键，
#: ``username=C`` 匹配不上 ``cc``——这正是要靠模型兜底的原始场景。
CHOICE_OF_CC = "c3"
CHOICE_OF_B = "c2"

#: 规则未命中时把决定权交给模型（编号通道）——这是本次修复的核心场景。
RULE = {
    "key_source": "arg:username",
    "transforms": ["trim"],
    "templates": ["{value}"],
    "target": "secret_key",
    "on_miss": "model",
}

SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "username": {"type": "string", "description": "收件人"},
        },
        "required": ["username"],
        # mailkey 不走 properties：声明为 rule 来源后它对模型不可见，由平台注入
        "x-nanzi-system-params": [
            {"name": "mailkey", "source": "rule", "type": "string"},
        ],
    },
    ensure_ascii=False,
)


def _fp(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


async def _cleanup(session):
    await session.execute(delete(SecretBinding).where(SecretBinding.consumer_id == TOOL_ID))
    await session.execute(delete(SecretUsageLog).where(SecretUsageLog.user_id == USER_ID))
    await session.execute(delete(UserSecret).where(UserSecret.owner_id == USER_ID))
    await session.execute(delete(SysApiTool).where(SysApiTool.id == TOOL_ID))
    await session.commit()


@pytest.fixture
async def choice_env():
    """一条工具（body 的 mailkey 走「按规则匹配 + 挑不到让模型挑」）+ 三条凭据。"""
    async with AsyncSessionLocal() as session:
        await _cleanup(session)

        session.add(
            SysApiTool(
                id=TOOL_ID,
                name="choice-echo",
                description="验收用：请求打到内置回显端点",
                method="POST",
                url_template=ECHO_URL,
                headers=None,
                parameter_schema=SCHEMA,
                is_active=True,
            )
        )
        await session.commit()

        for key, value, remark in [
            ("A", VALUE_A, "A 的邮箱账号"),
            ("B", VALUE_B, "B 的邮箱账号"),
            ("CC", VALUE_CC, "C 的邮箱账号（入参写成 C 时要靠模型认出来）"),
        ]:
            await center.create_secret(
                session,
                owner_scope="user",
                owner_id=USER_ID,
                secret_key=key,
                name=f"{key} 凭据",
                value=value,
                remark=remark,
                category="mail",
                allowed_scenarios=["api_tool"],
                allowed_hosts=["*"],
            )

        await center.bind_rule(
            session,
            consumer_type="api_tool",
            consumer_id=TOOL_ID,
            slot="body:mailkey",
            rule=RULE,
            bound_by_user_id=USER_ID,
        )

    yield

    async with AsyncSessionLocal() as session:
        await _cleanup(session)


@pytest.fixture
def in_process_http(monkeypatch):
    """把 generic_api 的 httpx 换成走 ASGI 的客户端：进程内发请求，不占端口。"""
    from app.main import app as main_app

    transport = httpx.ASGITransport(app=main_app)
    real_httpx = httpx

    class _InProcessClient(real_httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.pop("trust_env", None)
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(
        "app.services.ai.tools.generic_api.httpx",
        SimpleNamespace(AsyncClient=_InProcessClient, URL=real_httpx.URL),
    )


async def _load_tool():
    async with AsyncSessionLocal() as session:
        return await session.get(SysApiTool, TOOL_ID)


@pytest.mark.asyncio
async def test_schema_exposes_secret_choice_field(choice_env):
    """模型必须看得到编号字段，否则「让模型挑一条」选项等于没配。"""
    config = await _load_tool()
    tool = GenericApiToolFactory.create_tool(config)
    properties = tool.args_schema.model_json_schema()["properties"]

    assert "secret_choice_mailkey" in properties
    # 凭据参数本身仍然不可见（值只能由平台注入）
    assert "mailkey" not in properties
    # 普通参数照常存在
    assert "username" in properties
    # 编号字段必须是可选的：规则命中时模型本来就该留空
    assert "secret_choice_mailkey" not in tool.args_schema.model_json_schema().get("required", [])


@pytest.mark.asyncio
async def test_model_choice_reaches_the_wire(choice_env, in_process_http):
    """规则未命中 + 模型给了 c3 → 报文里带的是 CC 的值，且编号字段不泄漏。"""
    config = await _load_tool()
    captured: dict = {}
    response, _elapsed, _url = await GenericApiToolFactory._perform_request(
        config,
        {"username": "C", "secret_choice_mailkey": CHOICE_OF_CC},
        user_info={"user_id": USER_ID},
        capture=captured,
    )

    assert response.status_code == 200
    seen = response.json()
    assert seen["body_fingerprint"]["mailkey"] == _fp(VALUE_CC)
    # 编号是平台内部约定，远端不该看到它
    assert "secret_choice_mailkey" not in seen["body"]
    assert "secret_choice_mailkey" not in captured.get("body", {})
    # 明文永远不出现在回显里
    assert VALUE_CC not in json.dumps(seen, ensure_ascii=False)


@pytest.mark.asyncio
async def test_rule_hit_ignores_model_choice(choice_env, in_process_http, caplog):
    """规则命中时编号被忽略：确定性优先，AI 只兜底。

    注意这条**不能**只看「值对不对」——模型挑的是 b，规则挑的是 a，两者不同，
    正好能区分是谁生效的。同时日志里要留下「忽略模型选择」，否则模型推错了
    永远发现不了。
    """
    caplog.set_level(logging.INFO, logger="app.services.secrets.resolver")
    config = await _load_tool()
    response, _e, _u = await GenericApiToolFactory._perform_request(
        config,
        {"username": "A", "secret_choice_mailkey": CHOICE_OF_B},
        user_info={"user_id": USER_ID},
    )

    body = response.json()["body_fingerprint"]
    assert body["mailkey"] == _fp(VALUE_A)
    assert "忽略模型选择" in caplog.text


@pytest.mark.asyncio
async def test_out_of_range_choice_is_ignored_not_guessed(choice_env, in_process_http, caplog):
    """编号越界（模型记错了清单长度）当「没选」处理，绝不猜一条。"""
    caplog.set_level(logging.INFO, logger="app.services.secrets.injection")
    config = await _load_tool()

    with pytest.raises(Exception) as exc:
        await GenericApiToolFactory._perform_request(
            config,
            {"username": "C", "secret_choice_mailkey": "c99"},
            user_info={"user_id": USER_ID},
        )
    # 报错要点出缺的是哪个参数，而不是一个泛泛的 http 错误
    assert "mailkey" in str(exc.value)
    assert "编号越界" in caplog.text


@pytest.mark.asyncio
async def test_without_choice_it_still_fails(choice_env, in_process_http):
    """没给编号就是没解决：行为和 on_miss=fail 一样，绝不静默注入别的凭据。"""
    config = await _load_tool()
    with pytest.raises(Exception) as exc:
        await GenericApiToolFactory._perform_request(
            config, {"username": "C"}, user_info={"user_id": USER_ID}
        )
    assert "mailkey" in str(exc.value)

@pytest.mark.asyncio
async def test_choice_field_never_leaks_into_query(choice_env, in_process_http):
    """GET 的凭据参数落在 query：编号同样不能被发出去。

    pop 在 method 无关的位置，但 query 与 body 是两条编码路径。少了这条用例，
    有人把剔除挪进 body 分支时不会有任何测试变红。
    """
    async with AsyncSessionLocal() as session:
        tool = await session.get(SysApiTool, TOOL_ID)
        tool.method = "GET"
        await session.execute(
            delete(SecretBinding).where(SecretBinding.consumer_id == TOOL_ID)
        )
        await center.bind_rule(
            session,
            consumer_type="api_tool",
            consumer_id=TOOL_ID,
            slot="query:mailkey",
            rule=RULE,
            bound_by_user_id=USER_ID,
        )
        await session.commit()

    config = await _load_tool()
    response, _e, _u = await GenericApiToolFactory._perform_request(
        config,
        {"username": "C", "secret_choice_mailkey": CHOICE_OF_CC},
        user_info={"user_id": USER_ID},
    )

    seen = response.json()
    assert seen["query_fingerprint"]["mailkey"] == _fp(VALUE_CC)
    assert "secret_choice_mailkey" not in seen["query"]
    assert "secret_choice_mailkey" not in (seen["body"] or {})
