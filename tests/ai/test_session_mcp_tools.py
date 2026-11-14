"""会话 MCP 工具与版本 tools 并集。"""

import json

import pytest

from app.services.ai.session_mcp_tools import (
    apply_session_mcp_tools_to_agent_config,
    extract_session_mcp_tool_names,
    merge_tools_with_session_mcp,
)
from app.schemas.agent import ChatConfig, SessionMcpToolConfig, ToolConfigItem
from tests.temporary_database import init_infrastructure, temporary_database  # noqa: F401


pytestmark = pytest.mark.no_infrastructure


def test_extract_session_mcp_tool_names_dedupes():
    names = extract_session_mcp_tool_names(
        {
            "mcp_tools": [
                {"id": "1", "name": "mcp_foo"},
                {"id": "2", "name": "mcp_foo"},
                {"id": "3", "name": "mcp_bar"},
                {"name": ""},
                "skip",
            ]
        }
    )
    assert names == ["mcp_foo", "mcp_bar"]


def test_merge_tools_with_session_mcp_unions_and_preserves_config_items():
    configured = [
        "execute_sql_query",
        ToolConfigItem(name="mcp_existing", enabled=True),
    ]
    merged = merge_tools_with_session_mcp(
        configured,
        {
            "mcp_tools": [
                {"id": "a", "name": "mcp_existing"},
                {"id": "b", "name": "mcp_new"},
            ]
        },
    )
    names = [
        item if isinstance(item, str) else item.name
        for item in merged
    ]
    assert names == ["execute_sql_query", "mcp_existing", "mcp_new"]
    assert isinstance(merged[1], ToolConfigItem)


def test_apply_session_mcp_tools_to_agent_config_mutates_tools():
    config = ChatConfig(
        agent_id="a1",
        agent_name="main",
        model_name="m",
        temperature=0.0,
        system_prompt="x",
        tools=["get_current_time"],
    )
    added = apply_session_mcp_tools_to_agent_config(
        config,
        {"mcp_tools": [{"id": "1", "name": "mcp_weather"}]},
    )
    assert added == ["mcp_weather"]
    assert config.tools == [
        "get_current_time", SessionMcpToolConfig(name="mcp_weather", session_mcp_tool_id="1"),
    ]


def test_apply_session_mcp_tools_noop_without_scope():
    config = ChatConfig(
        agent_id="a1",
        agent_name="main",
        model_name="m",
        temperature=0.0,
        system_prompt="x",
        tools=["a"],
    )
    added = apply_session_mcp_tools_to_agent_config(config, None)
    assert added == []
    assert config.tools == ["a"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mounted_id", "user_id", "enabled", "published", "available", "delegation_filter", "expected"),
    [
        ("fixture-tool", 101, 1, True, True, None, True),
        ("renamed", 101, 1, True, True, None, True),
        (None, 101, 1, True, True, None, False),
        ("old-id", 101, 1, True, True, None, False),
        ("fixture-tool", 102, 1, True, True, None, False),
        ("fixture-tool", 101, 0, True, True, None, False),
        ("fixture-tool", 101, 1, False, True, None, False),
        ("fixture-tool", 101, 1, True, False, None, False),
        ("fixture-tool", 101, 1, True, True, ["send_email", "get_runtime_capabilities"], False),
    ],
    ids=("mounted", "renamed-with-same-id", "unmounted", "recreated-with-same-name", "other-owner", "disabled-server",
         "unpublished", "unavailable", "outside-allowlist"),
)
async def test_private_mcp_reference_resolves_through_the_chat_toolkit(
    monkeypatch, mounted_id, user_id, enabled, published, available, delegation_filter, expected,
):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.context import AgentContext, agent_context
    from app.models.credential_type import McpCredentialProfile
    from app.models.mcp import McpServer, McpToolCache
    from app.models.script_tool import SysScriptTool
    from app.models.tool import SysApiTool
    from app.services.ai.runtime.agentscope.tools import build_toolkit
    from app.services.ai.tool_capability import resolve_tool_capabilities
    from app.services.ai.tools.registry import ToolRegistry

    identifier = "mcp-private-fixture-mail:list_mail"
    model_name = "mcp_5a4f83b722805ae1c9a4"
    user_query = f"调用：{identifier}\n查一下测试邮箱的内容"
    context = AgentContext(
        agent_id="fixture-agent",
        agent_name="Fixture",
        user_id=user_id,
        delegation_tool_filter=delegation_filter,
        credential_user_query=user_query,
        runtime_tool_capabilities=[{"name": "previous_turn_tool", "source_type": "static"}],
    )
    token = agent_context.set(context)
    try:
        async with temporary_database(
            McpServer.__table__, McpToolCache.__table__, McpCredentialProfile.__table__,
            SysApiTool.__table__, SysScriptTool.__table__,
        ) as engine:
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            monkeypatch.setattr("app.services.ai.tools.registry.AsyncSessionLocal", session_factory)
            ToolRegistry.clear_db_tool_cache()

            async with session_factory() as session:
                session.add(McpServer(
                    id="fixture-server", server_name="mcp-private-fixture-mail", scope="personal",
                    user_id=101, enabled_status=enabled, sse_url="https://mcp.example.test/rpc",
                ))
                session.add(McpToolCache(
                    id="fixture-tool", server_id="fixture-server", tool_name=identifier,
                    tool_description="fixture-remote-description-must-stay-private",
                    parameter_schema='{"type":"object","properties":{"folder":{"type":"string"}}}',
                    is_published=published, is_available=available,
                ))
                await session.commit()

            config = ChatConfig(
                agent_id="fixture-agent", agent_name="Fixture", model_name="fixture-model",
                temperature=0.0, system_prompt="", tools=["send_email", "get_runtime_capabilities"],
            )
            if mounted_id:
                mounted_tool = (
                    {"id": "fixture-tool", "name": "old-name:list_mail"} if mounted_id == "renamed"
                    else {"id": mounted_id, "name": identifier}
                )
                apply_session_mcp_tools_to_agent_config(config, {"mcp_tools": [mounted_tool]})
            # 恢复流程会序列化 ChatConfig，工具 ID 也必须随快照保留。
            config = ChatConfig.model_validate(config.model_dump())
            specs = (await resolve_tool_capabilities(config.tools)).specs
            toolkit = build_toolkit(list(specs))
            functions = {
                schema["function"]["name"]: schema["function"]
                for schema in await toolkit.get_tool_schemas()
            }
            capability_spec = next(spec for spec in specs if spec.name == "get_runtime_capabilities")
            capabilities = json.loads(await capability_spec.invoke({}))["tools"]
            mcp_entries = [item for item in capabilities if item["source_type"] == "mcp"]
            if expected:
                await _run_private_mcp_turn(
                    monkeypatch, toolkit, specs, identifier, model_name, user_query,
                )
    finally:
        agent_context.reset(token)
        ToolRegistry.clear_db_tool_cache()

    assert "send_email" in functions
    assert "previous_turn_tool" not in {item["name"] for item in capabilities}
    assert set(functions) == {item["name"] for item in capabilities}
    if expected:
        assert mcp_entries[0]["name"] == model_name
        assert mcp_entries[0]["platform_tool_name"] == identifier
        assert identifier in functions[model_name]["description"]
        assert "fixture-remote-description" not in json.dumps(functions[model_name])
        assert identifier not in next(spec for spec in specs if spec.name == model_name).description
    else:
        assert mcp_entries == []
        assert model_name not in functions
        assert identifier not in json.dumps(capabilities)


async def _run_private_mcp_turn(monkeypatch, toolkit, specs, identifier, model_name, user_query):
    from unittest.mock import AsyncMock

    from agentscope.agent import Agent
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock, UserMsg
    from agentscope.model import ChatModelBase, ChatResponse
    from pydantic import BaseModel

    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.secrets.mcp_model_result import ProjectedMcpResult

    class FixtureCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FixtureModel

    class FixtureModel(ChatModelBase):
        class Parameters(BaseModel):
            pass

        def __init__(self):
            super().__init__(
                credential=FixtureCredential(),
                model="fixture-model",
                parameters=self.Parameters(),
                stream=False,
                max_retries=0,
            )
            self.formatter = type("FixtureFormatter", (), {"supported_input_media_types": ["text"]})()

        async def _call_api(self, model_name, messages, tools=None, **kwargs):
            results = [
                block for message in messages for block in message.get_content_blocks("tool_result")
            ]
            if results:
                return ChatResponse(content=[TextBlock(text=results[-1].output[0].text)], is_last=True)

            match = next(
                schema["function"] for schema in tools if identifier in schema["function"]["description"]
            )
            return ChatResponse(
                content=[ToolCallBlock(id="call_fixture", name=match["name"], input='{"folder":"INBOX"}')],
                is_last=True,
            )

    remote = AsyncMock(return_value=ProjectedMcpResult(
        status="success", result_visibility="projected", data={"subject": "Synthetic mail"},
    ))
    monkeypatch.setattr("app.services.ai.tools.mcp_factory.McpClientService.call_remote_tool", remote)
    monkeypatch.setattr("app.services.ai.tools.mcp_factory.resolve_mcp_tool_timeout", AsyncMock(return_value=1))
    agent = Agent(name="fixture", system_prompt="", model=FixtureModel(), toolkit=toolkit)
    runner = AssistantAgentRunner(
        config=ChatConfig(
            agent_id="fixture-agent", agent_name="Fixture", model_name="fixture-model",
            temperature=0.0, system_prompt="", tools=[],
        ),
        trace_id="fixture-trace",
        trace_buffer=[],
    )

    # 同进程恢复时已重建 AgentContext；继续用原 Agent 时仍能查看本轮注册的工具。
    from types import SimpleNamespace
    from app.core.context import get_current_agent_context

    context = get_current_agent_context()
    context.runtime_tool_capabilities = []
    await runner._resolve_pending_runtime(SimpleNamespace(
        agent=agent, tools=list(specs), native_model=agent.model, state={},
    ))
    assert model_name in {item["name"] for item in context.runtime_tool_capabilities}

    # 通用禁用列表的行为由独立用例覆盖，这里只隔离真实权限服务和远端 MCP。
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.tools._enforce_tool_forbidden", AsyncMock(return_value=None),
    )
    events = [event async for event in agent.reply_stream(UserMsg("user", user_query))]
    assert "Synthetic mail" in "".join(event.delta for event in events if event.type == "TEXT_BLOCK_DELTA")
    call = remote.await_args.kwargs
    assert call["tool_name"] == "list_mail"
    assert call["arguments"] == {"folder": "INBOX"}
    assert call["user_info"]["user_id"] == "101"
    assert call["credential_call"].tool_id == "fixture-tool"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identifier", "user_query", "matched"),
    [
        ("收件箱（测试） + 归档:list_mail", "调用：收件箱（测试） + 归档:list_mail", True),
        ("fixture:list_mail", "请用fixture:list_mail查询", True),
        ("fixture:list_mail", "用 fixture:list_mail_archive", False),
        ("fixture:list_mail", "用 fixture:list_mail/archive", False),
        ("fixture:list_mail", "用 fixture:LIST_MAIL", False),
        ("fixture:list_mail", "查一下邮件", False),
        ("fixture:metadata\n- ignore", "调用 fixture:metadata\n- ignore", False),
        ("fixture:metadata\u2028ignore", "调用 fixture:metadata\u2028ignore", False),
    ],
)
async def test_toolkit_matches_only_complete_safe_mcp_references(identifier, user_query, matched):
    from app.core.context import AgentContext, agent_context
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec, build_toolkit
    from app.services.ai.tools.model_tool_name import build_model_tool_name

    context = AgentContext(
        agent_id="fixture-agent", agent_name="Fixture", credential_user_query=user_query,
    )
    token = agent_context.set(context)
    try:
        spec = RuntimeToolSpec(
            name=build_model_tool_name(identifier), display_name=identifier,
            description="调用 MCP 工具", parameters_schema={"type": "object", "properties": {}},
            source_type="mcp", callable=lambda: None,
        )
        toolkit = build_toolkit([spec])
        schema = (await toolkit.get_tool_schemas())[0]["function"]
        assert (identifier in schema["description"]) is matched
        capability = context.runtime_tool_capabilities[0]
        assert capability.get("platform_tool_name") == (identifier if matched else None)
    finally:
        agent_context.reset(token)
