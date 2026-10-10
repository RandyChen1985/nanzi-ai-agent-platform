import pytest
import asyncio
import json
import app.services.ai.tools.agent_delegate_tool as delegation_tool
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.ai.tools.agent_delegate_tool import (
    sub_agent_call,
    clean_sub_agent_output,
    _extract_delegation_text,
    finalize_delegation_result,
    finalize_delegation_output,
    resolve_delegation_permission_options,
    filter_delegable_system_agents,
    resolve_runnable_delegable_system_agents,
    delegable_agent_name_aliases,
    EMPTY_DELEGATION_RESULT_MESSAGE,
DELEGATION_INTERRUPT_MESSAGES,
)
from app.core.context import AgentContext, set_agent_context
from app.schemas.agent import ChatConfig
from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner

pytestmark = pytest.mark.no_infrastructure

_DISPATCH_PATCH = "app.services.ai.tools.agent_delegate_tool._dispatch_sub_agent_executor"

ID_MAIN_KB = "11111111111111111111111111111111"
ID_SUB_KB = "22222222222222222222222222222222"
ID_FRONTEND_KB = "33333333333333333333333333333333"


def test_resolve_delegation_depth_never_exceeds_platform_limit():
    resolver = getattr(delegation_tool, "resolve_delegation_depth", lambda *_: (None, "missing"))
    assert resolver(0, None) == (1, None)
    assert resolver(0, 1) == (1, None)
    assert resolver(0, 2) == (1, None)
    assert resolver(1, 2)[1] == "depth_exceeded"


def test_resolve_delegation_tool_filter_only_narrows_configured_tools():
    resolver = getattr(delegation_tool, "resolve_delegation_tool_filter", lambda *_: (None, "missing"))
    filtered, error = resolver(
        ["search_knowledge_base", "read_file"],
        ["search_knowledge_base"],
    )

    assert filtered == ["search_knowledge_base"]
    assert error is None


def test_resolve_delegation_tool_filter_rejects_unknown_tools():
    resolver = getattr(delegation_tool, "resolve_delegation_tool_filter", lambda *_: (None, "missing"))
    filtered, error = resolver(
        ["search_knowledge_base"],
        ["Bash"],
    )

    assert filtered is None
    assert error == "unknown_tool"


def test_apply_delegation_tool_filter_controls_visible_and_runnable_specs():
    from app.services.ai.runtime.agentscope.tools import apply_delegation_tool_filter

    tools = [
        SimpleNamespace(name="search_knowledge_base"),
        SimpleNamespace(name="read_file"),
    ]

    filtered = apply_delegation_tool_filter(tools, ["search_knowledge_base"])

    assert filtered == [tools[0]]


def test_apply_delegation_tool_filter_empty_allowlist_hides_all_specs():
    from app.services.ai.runtime.agentscope.tools import apply_delegation_tool_filter

    tools = [SimpleNamespace(name="search_knowledge_base")]

    assert apply_delegation_tool_filter(tools, []) == []


MCP_PLATFORM_TOOL_NAME = "mcp-public-admin-ivsom-mcp-server:mcp_query_abnormal_list"
MCP_MODEL_TOOL_NAME = "mcp_mcp-public-admin-ivsom-mcp-server_mcp_query_abnor_c243c57fdb"


def test_resolve_delegation_tool_filter_normalizes_mcp_platform_names():
    """MCP 工具在委派链路里也要落到模型侧调用名，否则子智能体工具会被静默过滤。"""
    resolver = getattr(delegation_tool, "resolve_delegation_tool_filter", lambda *_: (None, "missing"))

    filtered, error = resolver([MCP_PLATFORM_TOOL_NAME], [MCP_PLATFORM_TOOL_NAME])

    assert error is None
    assert filtered == [MCP_MODEL_TOOL_NAME]

    filtered_by_alias, alias_error = resolver([MCP_PLATFORM_TOOL_NAME], [MCP_MODEL_TOOL_NAME])

    assert alias_error is None
    assert filtered_by_alias == [MCP_MODEL_TOOL_NAME]


def test_delegation_tool_filter_matches_registered_mcp_spec_name():
    from app.services.ai.runtime.agentscope.tools import apply_delegation_tool_filter

    tool = SimpleNamespace(name=MCP_MODEL_TOOL_NAME)

    assert apply_delegation_tool_filter([tool], [MCP_MODEL_TOOL_NAME]) == [tool]
    assert apply_delegation_tool_filter([tool], [MCP_PLATFORM_TOOL_NAME]) == []


def test_agent_context_keeps_agent_toolcall_timeout_snapshot():
    context = AgentContext(
        agent_id="main-agent-id",
        agent_name="MainAgent",
        agent_max_toolcall_timeout_seconds=300.0,
    )

    assert context.agent_max_toolcall_timeout_seconds == 300.0


@pytest.mark.asyncio
async def test_resolve_delegation_timeout_uses_global_agent_toolcall_config(monkeypatch):
    monkeypatch.setattr(
        "app.services.ai.tools.agent_delegate_tool.get_current_agent_context",
        lambda: None,
    )
    loader = AsyncMock(return_value=300.0)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.tool_timeout.load_agent_max_toolcall_timeout",
        loader,
    )

    assert await delegation_tool._resolve_delegation_timeout_seconds() == 300.0
    loader.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_delegation_timeout_prefers_current_run_snapshot(monkeypatch):
    set_agent_context(
        AgentContext(
            agent_id="main-agent-id",
            agent_name="MainAgent",
            agent_max_toolcall_timeout_seconds=300.0,
        )
    )
    loader = AsyncMock(side_effect=AssertionError("should use run snapshot"))
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.tool_timeout.load_agent_max_toolcall_timeout",
        loader,
    )

    try:
        assert await delegation_tool._resolve_delegation_timeout_seconds() == 300.0
    finally:
        set_agent_context(None)


@contextmanager
def _mock_delegation_runtime_config():
    with patch(
        "app.services.ai.tools.agent_delegate_tool._resolve_delegation_timeout_seconds",
        AsyncMock(return_value=60.0),
    ), patch(
        "app.services.ai.tools.agent_delegate_tool._resolve_delegation_result_max_chars",
        AsyncMock(return_value=8000),
    ), patch(
        # 就绪性判定（evaluate_agent_readiness）会去查发布版本与主类型所需资源，
        # 单测不铺这套数据，这里直接放行，聚焦委派逻辑本身。
        "app.services.ai.tools.agent_delegate_tool.resolve_runnable_delegable_system_agents",
        AsyncMock(side_effect=lambda session, agents, **kwargs: list(agents)),
    ):
        yield


@contextmanager
def _mock_system_agents_session(agents):
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = agents
    mock_execute_result = MagicMock()
    mock_execute_result.scalars.return_value = mock_scalars
    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_execute_result
    mock_session_context = MagicMock()
    mock_session_context.__aenter__.return_value = mock_session
    with patch(
        "app.services.ai.tools.agent_delegate_tool.AsyncSessionLocal",
        return_value=mock_session_context,
    ):
        yield


def _make_system_agent(*, agent_id, name, display_name="测试助手"):
    mock_agent = MagicMock()
    mock_agent.id = agent_id
    mock_agent.name = name
    mock_agent.display_name = display_name
    mock_agent.is_enabled = True
    mock_agent.is_system = True
    mock_agent.agent_type = "CHATBI" if name == "chat-bi" else "GENERAL"
    mock_agent.capabilities = ["data_query"] if name == "chat-bi" else ["general_chat"]
    mock_agent.engine_config = {"dataset_ids": [ID_SUB_KB]} if name == "chat-bi" else {}
    mock_agent.sort_order = 0
    return mock_agent


def test_clean_sub_agent_output():
    raw_text = "Here is the data: \n<sql_plan>\nSELECT * FROM test;\n</sql_plan>\nDone."
    cleaned = clean_sub_agent_output(raw_text)
    assert "SELECT * FROM test" not in cleaned
    assert "Here is the data:" in cleaned
    assert "Done." in cleaned


def test_extract_delegation_text():
    assert _extract_delegation_text({"content": "hello"}) == "hello"
    assert _extract_delegation_text({"type": "error", "content": "fail"}) == "fail"
    assert _extract_delegation_text({"text": "alt"}) == "alt"
    assert _extract_delegation_text({"type": "log", "title": "step"}) == ""


@pytest.mark.asyncio
async def test_consume_sub_agent_stream_keeps_only_final_answer():
    """子代理流只把最终正文计入委派结果：旁白/commit 丢弃，promote 计入。"""
    from app.services.ai.tools.agent_delegate_tool import _consume_sub_agent_stream

    async def sub_stream():
        yield {"type": "log", "title": "执行中", "status": "success"}
        yield {"type": "process_narration", "content": "我先查一下企业信息"}
        yield {"type": "process_narration_commit", "content": "我先查一下企业信息"}
        yield {"type": "process_narration", "content": "让我再看看财务数据"}
        yield {"type": "process_narration_commit", "content": "让我再看看财务数据"}
        yield {"type": "process_narration_promote", "content": "# 最终报告\n正文"}

    eq = asyncio.Queue()
    ctx = AgentContext(agent_id="main", agent_name="MainAgent", event_queue=eq)
    full_output, interrupt = await _consume_sub_agent_stream(
        sub_stream(),
        main_ctx=ctx,
        sub_display_name="专家",
    )

    assert full_output == "# 最终报告\n正文"
    assert interrupt is None
    # 旁白/commit 不进结果也不转发；log 仍转发并加前缀
    log_chunks = []
    while not eq.empty():
        log_chunks.append(await eq.get())
        eq.task_done()
    assert [c["type"] for c in log_chunks] == ["log"]
    assert log_chunks[0]["title"] == "[专家] 执行中"


@pytest.mark.asyncio
async def test_consume_sub_agent_stream_propagates_stream_error_as_interrupt():
    from app.services.ai.tools.agent_delegate_tool import _consume_sub_agent_stream

    async def sub_stream():
        yield {
            "type": "error",
            "status": "error",
            "content": "⚠️ [流式安全拦截] 检测到重复输出。",
        }
        yield {"type": "answer_delta", "content": "不应继续消费的正文"}

    ctx = AgentContext(agent_id="main", agent_name="MainAgent", event_queue=asyncio.Queue())
    full_output, interrupt = await _consume_sub_agent_stream(
        sub_stream(),
        main_ctx=ctx,
        sub_display_name="专家",
    )

    assert interrupt == "error"
    assert "流式安全拦截" in full_output
    assert "不应继续消费的正文" not in full_output


@pytest.mark.asyncio
async def test_consume_sub_agent_stream_marks_forwarded_logs_with_subagent_metadata():
    from app.services.ai.tools.agent_delegate_tool import _consume_sub_agent_stream

    async def sub_stream():
        yield {"type": "log", "title": "检索知识库", "status": "pending"}

    eq = asyncio.Queue()
    ctx = AgentContext(agent_id="main", agent_name="MainAgent", event_queue=eq)
    await _consume_sub_agent_stream(
        sub_stream(),
        main_ctx=ctx,
        sub_display_name="知识库助手",
        subagent_metadata={
            "display_name": "知识库助手",
            "run_id": "subrun_test",
            "child_trace_id": "sub_child",
        },
    )

    forwarded = await eq.get()
    assert forwarded["subagent"] == {
        "display_name": "知识库助手",
        "run_id": "subrun_test",
        "child_trace_id": "sub_child",
    }


@pytest.mark.asyncio
async def test_consume_sub_agent_stream_retraction_replaces_accumulated_text():
    """retraction 用新正文整体替换已积累内容，而不是追加。"""
    from app.services.ai.tools.agent_delegate_tool import _consume_sub_agent_stream

    async def sub_stream():
        yield {"content": "旧正文"}
        yield {"type": "retraction", "content": "新正文", "final": True}
        yield {"type": "answer_delta", "content": " 补充"}

    ctx = AgentContext(agent_id="main", agent_name="MainAgent")
    full_output, interrupt = await _consume_sub_agent_stream(
        sub_stream(),
        main_ctx=ctx,
        sub_display_name="专家",
    )

    assert full_output == "新正文 补充"
    assert interrupt is None


@pytest.mark.asyncio
async def test_consume_sub_agent_stream_legacy_promote_still_counts_as_answer():
    """旧版 promote 类型（其他 executor 兼容）仍计入委派正文。"""
    from app.services.ai.tools.agent_delegate_tool import _consume_sub_agent_stream

    async def sub_stream():
        yield {"type": "process_narration", "content": "旁白"}
        yield {"type": "process_narration_promote", "content": "最终正文"}

    ctx = AgentContext(agent_id="main", agent_name="MainAgent")
    full_output, interrupt = await _consume_sub_agent_stream(
        sub_stream(),
        main_ctx=ctx,
        sub_display_name="专家",
    )

    assert full_output == "最终正文"
    assert interrupt is None


def test_finalize_delegation_output_empty():
    assert finalize_delegation_output("") == EMPTY_DELEGATION_RESULT_MESSAGE
    assert finalize_delegation_output("   ") == EMPTY_DELEGATION_RESULT_MESSAGE


def test_finalize_delegation_output_truncation():
    long_text = "x" * 5000
    result = finalize_delegation_output(long_text, max_chars=100)
    assert len(result) < 200
    assert "截断" in result


def test_finalize_delegation_result_keeps_typed_status_and_metadata():
    result = finalize_delegation_result(
        "查询结果",
        target_agent_id="agent-1",
        target_agent_name="数据助手",
    )

    assert result.status.value == "completed"
    assert result.to_tool_text() == "查询结果"
    assert result.to_metadata() == {
        "status": "completed",
        "target_agent_id": "agent-1",
        "target_agent_name": "数据助手",
        "error_code": None,
        "interrupt_type": None,
        "truncated": False,
        "capability": None,
        "evidence_count": 0,
        "artifact_count": 0,
        "content_chars": 4,
        "run_id": None,
        "parent_trace_id": None,
        "parent_conversation_id": None,
        "child_trace_id": None,
        "child_session_id": None,
        "stop_reason": "completed",
        "structured": False,
    }


def test_sub_agent_request_metadata_has_no_credentials_or_raw_query():
    from app.services.ai.subagent_protocol import SubAgentRequest

    request = SubAgentRequest(
        target_agent_name="data-agent",
        query="查询订单金额",
        caller_agent_id="main-agent",
        caller_agent_name="主助手",
        delegation_depth=0,
    )

    metadata = request.to_metadata()

    assert metadata["target_agent_name"] == "data-agent"
    assert metadata["query_chars"] == len("查询订单金额")
    assert "查询订单金额" not in str(metadata)


def test_resolve_delegation_permission_options_preserves_main_approval_boundary():
    assert resolve_delegation_permission_options({"approval_mode": "ask"}) == {
        "approval_mode": "ask",
    }
    assert resolve_delegation_permission_options(None) == {"approval_mode": "ask"}
    assert resolve_delegation_permission_options({"approval_mode": "allow"}) == {
        "approval_mode": "allow",
    }


def test_capability_candidates_include_all_agents_sorted_by_sort_order():
    candidates = AssistantAgentRunner._build_sub_agent_candidates_by_capability(
        [
            {
                "id": "b",
                "name": "later",
                "sort_order": 1,
                "capabilities": ["data_query"],
            },
            {
                "id": "a",
                "name": "preferred",
                "sort_order": 5,
                "capabilities": ["data_query"],
            },
            {
                "id": "c",
                "name": "finance",
                "sort_order": 3,
                "capabilities": ["data_query"],
            },
        ]
    )

    assert candidates["data_query"] == ["preferred", "finance", "later"]


@pytest.mark.asyncio
async def test_filter_delegable_system_agents_hides_self_and_unauthorized_agents():
    main_agent = _make_system_agent(agent_id="main-agent-id", name="assistant", display_name="主助手")
    allowed_agent = _make_system_agent(agent_id="allowed-agent-id", name="chat-bi", display_name="数据助手")
    denied_agent = _make_system_agent(agent_id="denied-agent-id", name="knowledge-base", display_name="知识库助手")
    disabled_agent = _make_system_agent(agent_id="disabled-agent-id", name="disabled", display_name="禁用助手")
    disabled_agent.is_enabled = False

    mock_session = AsyncMock()

    async def mock_check_permission(user_id, resource_type, resource_id):
        return resource_id == "allowed-agent-id"

    with patch(
        "app.services.permission_service.PermissionService.check_permission",
        AsyncMock(side_effect=mock_check_permission),
    ):
        delegable = await filter_delegable_system_agents(
            mock_session,
            [main_agent, allowed_agent, denied_agent, disabled_agent],
            user_id=100,
            is_admin=False,
            current_agent_id="main-agent-id",
        )

    assert delegable == [allowed_agent]
    assert delegable_agent_name_aliases(delegable) >= {"chat-bi", "chat_bi", "数据助手"}


@pytest.mark.asyncio
async def test_runnable_delegable_agents_exclude_unloadable_candidate():
    ready_agent = _make_system_agent(
        agent_id="ready-agent-id",
        name="chat-bi",
        display_name="数据助手",
    )
    ready_agent.agent_type = "CHATBI"
    ready_agent.capabilities = ["data_query"]
    ready_agent.engine_config = {"dataset_ids": ["dataset-1"]}
    unavailable_agent = _make_system_agent(
        agent_id="unavailable-agent-id",
        name="knowledge-base",
        display_name="知识库助手",
    )
    mock_session = AsyncMock()
    ready_config = SimpleNamespace(
        tools=["execute_sql_query"],
        engine_config={"dataset_ids": ["dataset-1"]},
        capabilities=["data_query"],
    )

    async def mock_get_config(_session, *, agent_id=None, agent_name=None):
        return ready_config if agent_id == "ready-agent-id" else None

    with patch(
        "app.services.ai.agent_manager.AgentManagerService.get_active_agent_config",
        AsyncMock(side_effect=mock_get_config),
    ):
        runnable = await resolve_runnable_delegable_system_agents(
            mock_session,
            [ready_agent, unavailable_agent],
            user_id=None,
            is_admin=True,
            current_agent_id="main-id",
        )

    assert runnable == [ready_agent]


@pytest.mark.asyncio
async def test_sub_agent_call_depth_check():
    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=1,
    )
    set_agent_context(main_ctx)
    try:
        res = await sub_agent_call.func(agent_name="data-agent", query="test")
        assert "拒绝执行以防死循环" in res
    finally:
        set_agent_context(None)


def test_sub_agent_call_tool_schema_exposes_optional_protocol_controls():
    fields = sub_agent_call.args_schema.model_fields

    assert {"agent_name", "query", "max_depth", "tool_filter", "output_schema"} <= set(fields)


def test_sub_agent_batch_call_exposes_bounded_batch_protocol():
    batch_tool = getattr(delegation_tool, "sub_agent_batch_call", None)
    assert batch_tool is not None
    assert "calls" in batch_tool.args_schema.model_fields


@pytest.mark.asyncio
async def test_sub_agent_batch_call_runs_items_concurrently_and_keeps_input_order(monkeypatch):
    batch_tool = getattr(delegation_tool, "sub_agent_batch_call", None)
    assert batch_tool is not None

    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_buffer=[],
    )
    set_agent_context(main_ctx)
    active = 0
    max_active = 0

    async def fake_sub_agent_call(**kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.03 if kwargs["agent_name"] == "slow" else 0.01)
        active -= 1
        return f"result:{kwargs['agent_name']}"

    monkeypatch.setattr(delegation_tool.sub_agent_call, "func", fake_sub_agent_call)

    try:
        raw = await batch_tool.ainvoke(
            {
                "calls": [
                    {"agent_name": "slow", "query": "任务一"},
                    {"agent_name": "fast", "query": "任务二"},
                ]
            }
        )
    finally:
        set_agent_context(None)

    payload = json.loads(raw)
    assert payload["status"] == "completed"
    assert max_active == 2
    assert [item["agent_name"] for item in payload["results"]] == ["slow", "fast"]
    assert [item["content"] for item in payload["results"]] == [
        "result:slow",
        "result:fast",
    ]


@pytest.mark.asyncio
async def test_sub_agent_batch_call_rejects_more_than_four_items():
    batch_tool = getattr(delegation_tool, "sub_agent_batch_call", None)
    assert batch_tool is not None
    raw = await batch_tool.ainvoke(
        {
            "calls": [
                {"agent_name": f"agent-{index}", "query": "任务"}
                for index in range(5)
            ]
        }
    )
    payload = json.loads(raw)
    assert payload["status"] == "error"
    assert "最多 4 个" in payload["error"]


@pytest.mark.asyncio
async def test_sub_agent_call_normal_execution_and_log_forwarding():
    eq = asyncio.Queue()
    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        event_queue=eq,
        trace_buffer=[],
    )
    set_agent_context(main_ctx)

    async def mock_execute(history):
        yield {"type": "log", "title": "Executing SQL query", "status": "success"}
        yield {"content": "Data: 100 orders. <sql_plan>PLAN</sql_plan>"}

    mock_executor = MagicMock()
    mock_executor.execute = mock_execute

    sub_config = ChatConfig(
        agent_id="sub-123",
        agent_name="chat-bi",
        agent_display_name="数据查询助手",
        system_prompt="sub",
        tools=["execute_sql_query"],
        capabilities=["data_query"],
        engine_config={"dataset_ids": [ID_SUB_KB]},
        model_name="test",
        temperature=0.0,
    )

    mock_agent = _make_system_agent(agent_id="sub-123", name="chat-bi", display_name="数据查询助手")
    mock_get_config = AsyncMock(return_value=sub_config)
    mock_dispatch = AsyncMock(return_value=mock_executor)

    with _mock_delegation_runtime_config(), \
         _mock_system_agents_session([mock_agent]), \
         patch("app.services.ai.agent_manager.AgentManagerService.get_active_agent_config", mock_get_config), \
         patch(_DISPATCH_PATCH, mock_dispatch), \
         patch("app.services.permission_service.PermissionService.check_permission", AsyncMock(return_value=True)):

        res = await sub_agent_call.func(agent_name="chat-bi", query="查询数据")

        assert "Data: 100 orders." in res
        assert "<sql_plan>" not in res

        log_chunks = []
        while not eq.empty():
            log_chunks.append(await eq.get())
            eq.task_done()

        assert len(log_chunks) == 3
        forwarded = next(chunk for chunk in log_chunks if chunk["title"] == "[数据查询助手] Executing SQL query")
        assert forwarded["type"] == "log"
        assert forwarded["subagent"]["display_name"] == "数据查询助手"
        lifecycle = [
            chunk for chunk in log_chunks
            if str(chunk.get("id", "")).startswith("subagent_")
        ]
        assert [chunk["status"] for chunk in lifecycle] == ["pending", "success"]
        assert lifecycle[-1]["subagent"]["stop_reason"] == "completed"

    set_agent_context(None)


@pytest.mark.asyncio
async def test_sub_agent_call_applies_tool_filter_and_validates_structured_output():
    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        trace_buffer=[],
    )
    set_agent_context(main_ctx)

    async def mock_execute(history):
        from app.core.context import get_current_agent_context

        current_ctx = get_current_agent_context()
        assert current_ctx.delegation_tool_filter == ["execute_sql_query"]
        yield {"structured": {"answer": "100"}, "content": "查询结果：100"}

    mock_executor = MagicMock()
    mock_executor.execute = mock_execute
    sub_config = ChatConfig(
        agent_id="sub-123",
        agent_name="chat-bi",
        agent_display_name="数据查询助手",
        system_prompt="sub",
        tools=["execute_sql_query"],
        capabilities=["data_query"],
        engine_config={"dataset_ids": [ID_SUB_KB]},
        model_name="test",
        temperature=0.0,
    )
    mock_agent = _make_system_agent(agent_id="sub-123", name="chat-bi", display_name="数据查询助手")

    with _mock_delegation_runtime_config(), \
         _mock_system_agents_session([mock_agent]), \
         patch("app.services.ai.agent_manager.AgentManagerService.get_active_agent_config", AsyncMock(return_value=sub_config)), \
         patch(_DISPATCH_PATCH, AsyncMock(return_value=mock_executor)), \
         patch("app.services.permission_service.PermissionService.check_permission", AsyncMock(return_value=True)):
        result = await sub_agent_call.func(
            agent_name="chat-bi",
            query="查询数据",
            tool_filter=["execute_sql_query"],
            output_schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        )

    assert result == "查询结果：100"
    set_agent_context(None)


@pytest.mark.asyncio
async def test_sub_agent_call_rejects_invalid_structured_output():
    main_ctx = AgentContext(agent_id="main", agent_name="MainAgent", delegation_depth=0)
    set_agent_context(main_ctx)

    async def mock_execute(history):
        yield {"structured": {"answer": 100}, "content": "查询结果：100"}

    mock_executor = MagicMock()
    mock_executor.execute = mock_execute
    sub_config = ChatConfig(
        agent_id="sub-123",
        agent_name="chat-bi",
        agent_display_name="数据查询助手",
        system_prompt="sub",
        tools=["execute_sql_query"],
        capabilities=["data_query"],
        engine_config={"dataset_ids": [ID_SUB_KB]},
        model_name="test",
        temperature=0.0,
    )
    mock_agent = _make_system_agent(agent_id="sub-123", name="chat-bi", display_name="数据查询助手")

    with _mock_delegation_runtime_config(), \
         _mock_system_agents_session([mock_agent]), \
         patch("app.services.ai.agent_manager.AgentManagerService.get_active_agent_config", AsyncMock(return_value=sub_config)), \
         patch(_DISPATCH_PATCH, AsyncMock(return_value=mock_executor)), \
         patch("app.services.permission_service.PermissionService.check_permission", AsyncMock(return_value=True)):
        result = await sub_agent_call.func(
            agent_name="chat-bi",
            query="查询数据",
            output_schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        )

    assert "结构化输出不符合约定" in result
    assert "property 'answer' must be a string" in result
    set_agent_context(None)


@pytest.mark.asyncio
async def test_sub_agent_call_rejects_invalid_schema_before_dispatch():
    main_ctx = AgentContext(agent_id="main", agent_name="MainAgent", delegation_depth=0)
    set_agent_context(main_ctx)
    mock_dispatch = AsyncMock()

    with patch(_DISPATCH_PATCH, mock_dispatch):
        result = await sub_agent_call.func(
            agent_name="chat-bi",
            query="查询数据",
            output_schema={"type": "array"},
        )

    assert "output_schema 无效" in result
    assert "schema root must be an object" in result
    mock_dispatch.assert_not_called()
    set_agent_context(None)


@pytest.mark.asyncio
async def test_sub_agent_call_self_delegation():
    main_ctx = AgentContext(
        agent_id="main-agent-id",
        agent_name="MainAgent",
        delegation_depth=0,
    )
    set_agent_context(main_ctx)

    mock_agent = _make_system_agent(agent_id="main-agent-id", name="chat-bi", display_name="数据查询助手")

    sub_config = ChatConfig(
        agent_id="main-agent-id",
        agent_name="chat-bi",
        agent_display_name="数据查询助手",
        system_prompt="sub",
        tools=["execute_sql_query"],
        capabilities=["data_query"],
        engine_config={"dataset_ids": [ID_SUB_KB]},
        model_name="test",
        temperature=0.0,
    )
    mock_get_config = AsyncMock(return_value=sub_config)

    with _mock_delegation_runtime_config(), \
         _mock_system_agents_session([mock_agent]), \
         patch("app.services.ai.agent_manager.AgentManagerService.get_active_agent_config", mock_get_config):

        res = await sub_agent_call.func(agent_name="chat-bi", query="test")
        assert "主智能体无法委派调用自身" in res

    set_agent_context(None)


@pytest.mark.asyncio
async def test_sub_agent_call_context_inheritance_and_user_info():
    from app.services.ai.grounding.ledger import EvidenceLedger

    shared_ledger = EvidenceLedger(user_id="100", conversation_id="conv-delegation")
    main_ctx = AgentContext(
        agent_id="main-agent-id",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        conversation_id="conversation-parent",
        dataset_ids=[ID_MAIN_KB],
        knowledge_dataset_ids=[ID_FRONTEND_KB],
        user_id=100,
        is_admin=False,
        api_key="sk-main-key",
        permission_options={"approval_mode": "ask"},
        grounding_evidence_ledger=shared_ledger,
        user_dimensions={
            "user_name": "test_user",
            "real_name": "Test User",
            "dept_code": "DEPT01",
            "org_path": "/ROOT/DEPT01",
            "extra_data": {"role_level": 3},
        },
    )
    set_agent_context(main_ctx)

    mock_agent = _make_system_agent(agent_id="sub-agent-id", name="chat-bi", display_name="数据查询助手")

    sub_config = ChatConfig(
        agent_id="sub-agent-id",
        agent_name="chat-bi",
        agent_display_name="数据查询助手",
        system_prompt="sub",
        tools=["execute_sql_query"],
        capabilities=["data_query"],
        model_name="test",
        temperature=0.0,
        engine_config={"dataset_ids": [ID_SUB_KB]},
    )
    mock_get_config = AsyncMock(return_value=sub_config)

    mock_executor = MagicMock()

    async def mock_execute(history):
        from app.core.context import get_current_agent_context

        current_ctx = get_current_agent_context()
        assert history == [{"role": "user", "content": "查询数据"}]
        assert current_ctx is not None
        assert current_ctx.conversation_id.startswith("child_session_")
        assert current_ctx.conversation_id != "conversation-parent"
        assert current_ctx.parent_conversation_id == "conversation-parent"
        assert current_ctx.child_session_id == current_ctx.conversation_id
        assert set(current_ctx.dataset_ids) == {ID_MAIN_KB, ID_SUB_KB}
        assert current_ctx.knowledge_dataset_ids == [ID_FRONTEND_KB]
        assert current_ctx.agent_dataset_ids == [ID_SUB_KB]
        assert set(current_ctx.engine_config.get("dataset_ids")) == {ID_MAIN_KB, ID_SUB_KB}
        assert current_ctx.delegation_depth == 1
        assert current_ctx.trace_id.startswith("sub_")
        assert current_ctx.parent_trace_id == "trace-main"
        assert current_ctx.delegation_run_id.startswith("subrun_")
        assert current_ctx.grounding_evidence_ledger is shared_ledger
        yield {"content": "Data output"}

    mock_executor.execute = mock_execute
    mock_dispatch = AsyncMock(return_value=mock_executor)

    with _mock_delegation_runtime_config(), \
         _mock_system_agents_session([mock_agent]), \
         patch("app.services.ai.agent_manager.AgentManagerService.get_active_agent_config", mock_get_config), \
         patch(_DISPATCH_PATCH, mock_dispatch) as patched_dispatch, \
         patch("app.services.permission_service.PermissionService.check_permission", AsyncMock(return_value=True)):

        res = await sub_agent_call.func(agent_name="chat-bi", query="查询数据")

        assert "Data output" in res
        patched_dispatch.assert_called_once()
        kwargs = patched_dispatch.call_args.kwargs
        assert kwargs["conversation_id"].startswith("child_session_")
        assert kwargs["conversation_id"] != "conversation-parent"
        assert kwargs["user_info"] == {
            "user_id": 100,
            "role": "user",
            "api_key": "sk-main-key",
            "user_name": "test_user",
            "real_name": "Test User",
            "dept_code": "DEPT01",
            "org_path": "/ROOT/DEPT01",
            "extra_data": {"role_level": 3},
        }
        assert kwargs["permission_options"] == {"approval_mode": "ask"}

    set_agent_context(None)


@pytest.mark.asyncio
async def test_sub_agent_call_empty_output_returns_guidance():
    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
    )
    set_agent_context(main_ctx)

    async def mock_execute(history):
        yield {"type": "log", "title": "only logs", "status": "success"}

    mock_executor = MagicMock()
    mock_executor.execute = mock_execute

    sub_config = ChatConfig(
        agent_id="sub-123",
        agent_name="chat-bi",
        agent_display_name="数据查询助手",
        system_prompt="sub",
        tools=["execute_sql_query"],
        capabilities=["data_query"],
        engine_config={"dataset_ids": [ID_SUB_KB]},
        model_name="test",
        temperature=0.0,
    )
    mock_agent = _make_system_agent(agent_id="sub-123", name="chat-bi", display_name="数据查询助手")

    with _mock_delegation_runtime_config(), \
         _mock_system_agents_session([mock_agent]), \
         patch("app.services.ai.agent_manager.AgentManagerService.get_active_agent_config", AsyncMock(return_value=sub_config)), \
         patch(_DISPATCH_PATCH, AsyncMock(return_value=mock_executor)), \
         patch("app.services.permission_service.PermissionService.check_permission", AsyncMock(return_value=True)):

        res = await sub_agent_call.func(agent_name="chat-bi", query="查询数据")
        assert res == EMPTY_DELEGATION_RESULT_MESSAGE

    set_agent_context(None)


@pytest.mark.asyncio
async def test_sub_agent_call_blocks_duplicate_same_agent_and_query():
    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
    )
    set_agent_context(main_ctx)

    async def mock_execute(history):
        yield {"content": "Data output"}

    mock_executor = MagicMock()
    mock_executor.execute = mock_execute

    sub_config = ChatConfig(
        agent_id="sub-123",
        agent_name="chat-bi",
        agent_display_name="数据查询助手",
        system_prompt="sub",
        tools=["execute_sql_query"],
        capabilities=["data_query"],
        engine_config={"dataset_ids": [ID_SUB_KB]},
        model_name="test",
        temperature=0.0,
    )
    mock_agent = _make_system_agent(agent_id="sub-123", name="chat-bi", display_name="数据查询助手")
    mock_dispatch = AsyncMock(return_value=mock_executor)

    with _mock_delegation_runtime_config(), \
         _mock_system_agents_session([mock_agent]), \
         patch("app.services.ai.agent_manager.AgentManagerService.get_active_agent_config", AsyncMock(return_value=sub_config)), \
         patch(_DISPATCH_PATCH, mock_dispatch), \
         patch("app.services.permission_service.PermissionService.check_permission", AsyncMock(return_value=True)):

        first = await sub_agent_call.func(agent_name="chat-bi", query="查询数据")
        second = await sub_agent_call.func(agent_name="chat_bi", query="  查询数据  ")

    assert "Data output" in first
    assert "使用相同问题执行过一次" in second
    assert mock_dispatch.call_count == 1

    set_agent_context(None)


@pytest.mark.asyncio
async def test_sub_agent_call_caps_same_agent_attempts_per_turn():
    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
    )
    set_agent_context(main_ctx)

    async def mock_execute(history):
        yield {"content": f"Data output for {history[-1]['content']}"}

    mock_executor = MagicMock()
    mock_executor.execute = mock_execute

    sub_config = ChatConfig(
        agent_id="sub-123",
        agent_name="chat-bi",
        agent_display_name="数据查询助手",
        system_prompt="sub",
        tools=["execute_sql_query"],
        capabilities=["data_query"],
        engine_config={"dataset_ids": [ID_SUB_KB]},
        model_name="test",
        temperature=0.0,
    )
    mock_agent = _make_system_agent(agent_id="sub-123", name="chat-bi", display_name="数据查询助手")
    mock_dispatch = AsyncMock(return_value=mock_executor)

    with _mock_delegation_runtime_config(), \
         _mock_system_agents_session([mock_agent]), \
         patch("app.services.ai.agent_manager.AgentManagerService.get_active_agent_config", AsyncMock(return_value=sub_config)), \
         patch(_DISPATCH_PATCH, mock_dispatch), \
         patch("app.services.permission_service.PermissionService.check_permission", AsyncMock(return_value=True)):

        first = await sub_agent_call.func(agent_name="chat-bi", query="查询数据 A")
        second = await sub_agent_call.func(agent_name="chat-bi", query="查询数据 B")
        third = await sub_agent_call.func(agent_name="chat-bi", query="查询数据 C")

    assert "Data output for 查询数据 A" in first
    assert "Data output for 查询数据 B" in second
    assert "已多次委派" in third
    assert mock_dispatch.call_count == 2

    set_agent_context(None)


@pytest.mark.asyncio
async def test_sub_agent_call_permission_interrupt_returns_error():
    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
    )
    set_agent_context(main_ctx)

    async def mock_execute(history):
        yield {"type": "permission_required", "permission_request_id": "req-1"}

    mock_executor = MagicMock()
    mock_executor.execute = mock_execute

    sub_config = ChatConfig(
        agent_id="sub-123",
        agent_name="chat-bi",
        agent_display_name="数据查询助手",
        system_prompt="sub",
        tools=["execute_sql_query"],
        capabilities=["data_query"],
        engine_config={"dataset_ids": [ID_SUB_KB]},
        model_name="test",
        temperature=0.0,
    )
    mock_agent = _make_system_agent(agent_id="sub-123", name="chat-bi", display_name="数据查询助手")

    with _mock_delegation_runtime_config(), \
         _mock_system_agents_session([mock_agent]), \
         patch("app.services.ai.agent_manager.AgentManagerService.get_active_agent_config", AsyncMock(return_value=sub_config)), \
         patch(_DISPATCH_PATCH, AsyncMock(return_value=mock_executor)), \
         patch("app.services.permission_service.PermissionService.check_permission", AsyncMock(return_value=True)):

        res = await sub_agent_call.func(agent_name="chat-bi", query="查询数据")
        assert res == DELEGATION_INTERRUPT_MESSAGES["permission_required"]

    set_agent_context(None)


@pytest.mark.asyncio
async def test_sub_agent_call_timeout_generator_closed():
    main_ctx = AgentContext(
        agent_id="main-agent-id",
        agent_name="MainAgent",
        delegation_depth=0,
    )
    set_agent_context(main_ctx)

    mock_agent = _make_system_agent(agent_id="sub-agent-id", name="chat-bi", display_name="数据查询助手")

    sub_config = ChatConfig(
        agent_id="sub-agent-id",
        agent_name="chat-bi",
        agent_display_name="数据查询助手",
        system_prompt="sub",
        tools=["execute_sql_query"],
        capabilities=["data_query"],
        engine_config={"dataset_ids": [ID_SUB_KB]},
        model_name="test",
        temperature=0.0,
    )
    mock_get_config = AsyncMock(return_value=sub_config)

    aclose_called = False

    async def my_stream():
        nonlocal aclose_called
        try:
            yield {"content": "Data chunk"}
            await asyncio.sleep(10.0)
        finally:
            aclose_called = True

    mock_executor = MagicMock()
    mock_executor.execute = MagicMock(return_value=my_stream())
    mock_dispatch = AsyncMock(return_value=mock_executor)

    async def mock_wait_for(fut, timeout=None):
        task = asyncio.create_task(fut)
        await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        raise asyncio.TimeoutError()

    with _mock_delegation_runtime_config(), \
         _mock_system_agents_session([mock_agent]), \
         patch("app.services.ai.agent_manager.AgentManagerService.get_active_agent_config", mock_get_config), \
         patch(_DISPATCH_PATCH, mock_dispatch), \
         patch("asyncio.wait_for", mock_wait_for):

        res = await sub_agent_call.func(agent_name="chat-bi", query="查询数据")
        assert "响应超时" in res

    assert aclose_called is True

    set_agent_context(None)


async def test_consume_sub_agent_stream_forwards_citation_events():
    """子智能体的 citation 必须穿透到主聊天流。

    回归场景：主助手委派给知识库专家后，回答正文里的 [ID:n] 点不开、消息底部
    引用列表整块消失，只弹「引用详情加载中，请稍候」。原因是 citation 既不是正文
    （``_extract_delegation_text`` 只认 content/text/message，取不到东西），也不在
    log / browser_session 的转发分支里，于是被静默丢弃；而单专家对话不经过委派，
    所以那条路径一切正常。
    """
    from app.services.ai.tools.agent_delegate_tool import _consume_sub_agent_stream

    citation_chunk = {
        "type": "citation",
        "data": [
            {
                "id": "1",
                "chunk_id": "ck-1",
                "doc_name": "手册.pdf",
                "content": "片段正文",
            }
        ],
    }

    async def sub_stream():
        yield {"type": "log", "title": "检索知识库", "status": "success"}
        yield citation_chunk
        yield {"type": "process_narration_promote", "content": "答案"}

    eq = asyncio.Queue()
    ctx = AgentContext(agent_id="main", agent_name="MainAgent", event_queue=eq)
    full_output, interrupt = await _consume_sub_agent_stream(
        sub_stream(),
        main_ctx=ctx,
        sub_display_name="知识库助手",
    )

    assert full_output == "答案", "citation 不得计入委派正文"
    assert interrupt is None

    forwarded = []
    while not eq.empty():
        forwarded.append(await eq.get())
        eq.task_done()

    citations = [c for c in forwarded if c.get("type") == "citation"]
    assert len(citations) == 1, (
        "子智能体的 citation 被丢掉了：主助手回答里的 [ID:n] 将永远无法展开"
    )
    assert citations[0] == citation_chunk, "citation 必须原样透传，前端按数组形状去重合并"
