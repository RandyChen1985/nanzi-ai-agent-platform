"""API 工具的凭据注入，真的进到 HTTP 报文里了吗。

用内置的 `/api/test/echo`（见 `app/services/mcp/http_echo.py`）+ `ASGITransport`，
**全程进程内**：不起 uvicorn、不占端口、不依赖网络。但走的是真实 ASGI 栈——
请求头、query、body 的编码与传输都是真的，只有传输层换成了内存。

为什么不满足于 `apply_api_tool_bindings` 的返回值断言：注入逻辑本身已经由
`test_locator_e2e.py` 覆盖，而「值进没进 HTTP 报文」是另一件事（比如 header 值
含非 ASCII、body 被复用、参数落位错了位置，都只有发一次才暴露）。
"""
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import delete

from app.core.orm import AsyncSessionLocal
from app.models.secret import SecretBinding, SecretUsageLog, UserSecret
from app.models.tool import SysApiTool
from app.services.ai.tools.generic_api import GenericApiToolFactory
from app.services.secrets import center

USER_ID = "http-e2e-user"
TOOL_ID = "http-e2e-api-tool"
ECHO_URL = "http://testserver/api/test/echo"

HEADER_VALUE = "sk-header-111111"
BODY_VALUE_A = "sk-alpha-111111"
BODY_VALUE_B = "sk-bravo-222222"

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
async def api_env():
    """一条工具（header 走固定凭据、body 走规则）+ 三条凭据。"""
    async with AsyncSessionLocal() as session:
        await _cleanup(session)

        session.add(
            SysApiTool(
                id=TOOL_ID,
                name="e2e-http-echo",
                description="验收用：请求打到内置回显端点",
                method="POST",
                url_template=ECHO_URL,
                # 空值 = 等凭据注入；有手填值的头注入层不会碰
                headers=json.dumps({"X-Api-Key": ""}),
                parameter_schema=SCHEMA,
                is_active=True,
            )
        )
        await session.commit()

        for key, value, remark in [
            ("hdr", HEADER_VALUE, "固定的 header 凭据"),
            ("A", BODY_VALUE_A, "A 的邮箱账号"),
            ("B", BODY_VALUE_B, "B 的邮箱账号"),
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
                # 回显端点走 testserver，不属于内网后缀；白名单留空会被判 denied_by_host
                allowed_hosts=["*"],
            )

        header_secret = next(
            s for s in (
                await center.list_visible_secrets(session, user_id=USER_ID)
            ) if s.secret_key == "hdr"
        )
        await center.bind_secret(
            session,
            secret_id=header_secret.id,
            consumer_type="api_tool",
            consumer_id=TOOL_ID,
            slot="header:X-Api-Key",
            bound_by_user_id=USER_ID,
            dept_code="",
        )
        await center.bind_rule(
            session,
            consumer_type="api_tool",
            consumer_id=TOOL_ID,
            slot="body:mailkey",
            rule={
                "key_source": "arg:username",
                "transforms": ["trim"],
                "templates": ["{value}"],
                "target": "secret_key",
                "on_miss": "fail",
            },
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
async def test_header_credential_reaches_the_wire(api_env, in_process_http):
    """固定凭据注入的 header，服务端收到的就是解密后的明文。"""
    config = await _load_tool()
    captured: dict = {}
    response, _elapsed, _url = await GenericApiToolFactory._perform_request(
        config,
        {"username": "A"},
        user_info={"user_id": USER_ID},
        capture=captured,
    )
    assert response.status_code == 200
    seen = response.json()

    # 服务端看到的是明文 → 指纹对得上；回显里没有明文
    assert seen["headers_fingerprint"]["x-api-key"] == _fp(HEADER_VALUE)
    assert HEADER_VALUE not in json.dumps(seen, ensure_ascii=False)
    # capture 拿到的是「将要发出的请求」，两者必须一致。
    # HTTP 头名大小写不敏感，按小写比——否则断言会被实现的 key 拼写绑死。
    captured_headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert captured_headers["x-api-key"] == HEADER_VALUE


@pytest.mark.asyncio
async def test_rule_credential_follows_username_on_the_wire(api_env, in_process_http):
    """按规则注入的 body 参数：username 不同，服务端收到的 mailkey 就不同。"""
    config = await _load_tool()

    resp_a, _e, _u = await GenericApiToolFactory._perform_request(
        config, {"username": "A"}, user_info={"user_id": USER_ID}
    )
    resp_b, _e2, _u2 = await GenericApiToolFactory._perform_request(
        config, {"username": "B"}, user_info={"user_id": USER_ID}
    )

    body_a = resp_a.json()["body_fingerprint"]
    body_b = resp_b.json()["body_fingerprint"]

    assert body_a["mailkey"] == _fp(BODY_VALUE_A)
    assert body_b["mailkey"] == _fp(BODY_VALUE_B)
    assert body_a["mailkey"] != body_b["mailkey"]
    # 普通参数照常透传（值被掩码，但键必须在）
    assert "username" in resp_a.json()["body"]


@pytest.mark.asyncio
async def test_unmatched_username_raises_before_sending(api_env, in_process_http):
    """规则未命中时必须在发请求前报错，而不是带着空值去换一个 401。"""
    config = await _load_tool()
    with pytest.raises(Exception) as exc:
        await GenericApiToolFactory._perform_request(
            config, {"username": "C"}, user_info={"user_id": USER_ID}
        )
    # 报错要说清是哪个参数，而不是一个泛泛的 http 错误
    assert "mailkey" in str(exc.value)
