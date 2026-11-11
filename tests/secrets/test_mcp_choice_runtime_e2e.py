"""Registry 级端到端：模型看到的参数表 → 模型填的编号 → 出站参数。

前面两个文件各自验了一半：

* ``test_mcp_choice_schema.py`` 验「模型收到的 schema 里有编号字段」，但不真调用；
* ``test_locator_e2e.py`` 验「注入层会采纳编号」，但从 `apply_mcp_arg_bindings` 直接进，
  绕过了 registry 与 schema。

真实链路里这两段是**同一个入口**：``ToolRegistry.get_runtime_tool()`` 返回的
``RuntimeToolSpec``，其中的 ``parameters_schema`` 就是发给模型的工具定义，
``spec.callable`` 就是模型填完参数后平台执行的入口。P3 那次事故恰恰是「链路两端
各自都对、接在一起的却是两份不同的 schema」——只有把两端接起来跑一次才能发现。

这里用 monkeypatch 把最外层的远端调用换掉（不连真实 MCP 服务），其余全部走真实
代码：registry 查库 → create_tool → schema 生成 → 身份注入 → 规则匹配 → 解密 → 出站参数。
"""
import json
import uuid

import pytest
from sqlalchemy import delete

from app.core.orm import AsyncSessionLocal
from app.models.mcp import McpServer, McpToolCache
from app.models.secret import SecretBinding, SecretUsageLog, UserSecret
from app.services.secrets import center

pytestmark = pytest.mark.no_infrastructure

USER_ID = "registry-choice-user"
SERVER_ID = "registry-choice-server"
TOOL_NAME = "E2EChoiceServer:send_mail"
SLOT = "arg:send_mail:mailkey"

VALUE_A = "sk-reg-alpha-111"
VALUE_B = "sk-reg-bravo-222"
VALUE_CC = "sk-reg-charlie-333"

PARAMETER_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "username": {"type": "string", "title": "Username"},
            "mailkey": {"type": "string", "title": "Mailkey"},
            "subject": {"type": "string", "default": ""},
        },
        "required": ["username", "mailkey"],
        "title": "send_mailArguments",
    },
    ensure_ascii=False,
)

RULE = {
    "key_source": "arg:username",
    "transforms": ["trim"],
    "templates": ["{value}"],
    "target": "secret_key",
    # 关键：规则未命中时把决定权交给模型（编号通道）
    "on_miss": "model",
}


async def _cleanup(session):
    await session.execute(delete(SecretBinding).where(SecretBinding.consumer_id == SERVER_ID))
    await session.execute(delete(SecretUsageLog).where(SecretUsageLog.user_id == USER_ID))
    await session.execute(delete(UserSecret).where(UserSecret.owner_id == USER_ID))
    await session.execute(delete(McpToolCache).where(McpToolCache.server_id == SERVER_ID))
    await session.execute(delete(McpServer).where(McpServer.id == SERVER_ID))
    await session.commit()


@pytest.fixture
async def seeded():
    """真实 DB：一个全局 MCP 服务 + 一条工具（mailkey 走规则）+ 三条凭据。"""
    async with AsyncSessionLocal() as session:
        await _cleanup(session)

        session.add(
            McpServer(
                id=SERVER_ID,
                server_name="E2E Choice Server",
                # host 检查需要真实地址；白名单在凭据上放成 *
                sse_url="http://127.0.0.1:8790/mcp",
                scope="global",
                enabled_status=1,
            )
        )
        session.add(
            McpToolCache(
                id=str(uuid.uuid4()),
                server_id=SERVER_ID,
                tool_name=TOOL_NAME,
                tool_description="发邮件（验收用）",
                parameter_schema=PARAMETER_SCHEMA,
                credential_params=json.dumps(["mailkey"]),
                is_published=True,
                is_available=True,
            )
        )
        await session.commit()

        for key, value in [("A", VALUE_A), ("B", VALUE_B), ("CC", VALUE_CC)]:
            await center.create_secret(
                session,
                owner_scope="user",
                owner_id=USER_ID,
                secret_key=key,
                name=f"{key} 的邮箱账号",
                value=value,
                # 备注是模型推断的主要依据
                remark=f"{key} 的邮箱账号",
                category="mail",
                allowed_scenarios=["mcp"],
                allowed_hosts=["*"],
            )

        await center.bind_rule(
            session,
            consumer_type="mcp_server",
            consumer_id=SERVER_ID,
            slot=SLOT,
            rule=RULE,
            bound_by_user_id=USER_ID,
        )

    yield

    async with AsyncSessionLocal() as session:
        await _cleanup(session)


@pytest.fixture
def fake_runtime(monkeypatch):
    """把「模型的调用」和「远端调用」两端替换掉，中间全部走真实代码。"""
    captured: dict = {}

    monkeypatch.setattr(
        "app.services.ai.tools.mcp_factory.current_mcp_agent_identity",
        lambda: ({"user_id": USER_ID}, {}),
    )

    async def _fake_stream(**kwargs):
        captured["arguments"] = dict(kwargs.get("arguments") or {})
        yield "remote-ok"

    monkeypatch.setattr(
        "app.services.ai.tools.mcp_factory.stream_remote_tool_with_progress",
        _fake_stream,
    )
    return captured


async def _call_via_spec(spec, **kwargs):
    """像 AgentScope 那样调：``spec.callable(**参数)``，再把异步生成器读到底。"""
    from app.services.ai.tools.tool_compat import resolve_tool_invocation_result

    return await resolve_tool_invocation_result(await spec.callable(**kwargs))


@pytest.mark.asyncio
async def test_model_sees_choice_field_and_its_choice_reaches_the_wire(seeded, fake_runtime):
    """一条用例跑完整链路：schema 里有口子 → 模型填 c3 → 出站带的是 CC 的值。"""
    from app.services.ai.tools.registry import ToolRegistry

    spec = await ToolRegistry.get_runtime_tool(TOOL_NAME)

    # ① 模型拿到的工具定义里必须有编号字段，且凭据参数本身不可见
    properties = spec.parameters_schema["properties"]
    assert "secret_choice_mailkey" in properties
    assert "mailkey" not in properties
    assert "mailkey" not in spec.parameters_schema.get("required", [])

    # ② 模型按 schema 填参（username=C 匹配不上 cc，只能靠编号兜底）
    await _call_via_spec(spec, username="C", subject="hi", secret_choice_mailkey="c3")

    # ③ 出站参数：值来自 CC；编号字段绝不能发给远端
    sent = fake_runtime["arguments"]
    assert sent["mailkey"] == VALUE_CC
    assert "secret_choice_mailkey" not in sent
    assert sent["username"] == "C"


@pytest.mark.asyncio
async def test_rule_hit_ignores_the_choice_on_the_real_path(seeded, fake_runtime):
    """规则命中时编号被忽略：确定性优先。"""
    from app.services.ai.tools.registry import ToolRegistry

    spec = await ToolRegistry.get_runtime_tool(TOOL_NAME)
    await _call_via_spec(spec, username="A", secret_choice_mailkey="c2")

    sent = fake_runtime["arguments"]
    assert sent["mailkey"] == VALUE_A
    assert "secret_choice_mailkey" not in sent


@pytest.mark.asyncio
async def test_choice_field_is_optional_in_the_published_schema(seeded):
    """编号字段必须可选：规则命中时模型本来就该留空。"""
    from app.services.ai.tools.registry import ToolRegistry

    spec = await ToolRegistry.get_runtime_tool(TOOL_NAME)
    field = spec.parameters_schema["properties"]["secret_choice_mailkey"]
    assert field["type"] == "string"
    assert "list_my_credentials" in field["description"]
    assert "secret_choice_mailkey" not in spec.parameters_schema.get("required", [])
