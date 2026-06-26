import pytest
import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.ai.tools.agent_delegate_tool import (
    sub_agent_call,
    clean_sub_agent_output,
    _extract_delegation_text,
    finalize_delegation_output,
    resolve_delegation_permission_options,
    filter_delegable_system_agents,
    delegable_agent_name_aliases,
    EMPTY_DELEGATION_RESULT_MESSAGE,
    DELEGATION_INTERRUPT_MESSAGES,
)
from app.core.context import AgentContext, set_agent_context
from app.schemas.agent import ChatConfig

pytestmark = pytest.mark.no_infrastructure

_DISPATCH_PATCH = "app.services.ai.tools.agent_delegate_tool._dispatch_sub_agent_executor"

ID_MAIN_KB = "11111111111111111111111111111111"
ID_SUB_KB = "22222222222222222222222222222222"
ID_FRONTEND_KB = "33333333333333333333333333333333"


@contextmanager
def _mock_delegation_runtime_config():
    with patch(
        "app.services.ai.tools.agent_delegate_tool._resolve_delegation_timeout_seconds",
        AsyncMock(return_value=60.0),
    ), patch(
        "app.services.ai.tools.agent_delegate_tool._resolve_delegation_result_max_chars",
        AsyncMock(return_value=8000),
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


def test_finalize_delegation_output_empty():
    assert finalize_delegation_output("") == EMPTY_DELEGATION_RESULT_MESSAGE
    assert finalize_delegation_output("   ") == EMPTY_DELEGATION_RESULT_MESSAGE


def test_finalize_delegation_output_truncation():
    long_text = "x" * 5000
    result = finalize_delegation_output(long_text, max_chars=100)
    assert len(result) < 200
    assert "截断" in result


def test_resolve_delegation_permission_options_preserves_main_approval_boundary():
    assert resolve_delegation_permission_options({"approval_mode": "ask"}) == {
        "approval_mode": "ask",
    }
    assert resolve_delegation_permission_options(None) == {"approval_mode": "ask"}
    assert resolve_delegation_permission_options({"approval_mode": "allow"}) == {
        "approval_mode": "allow",
    }


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
        tools=[],
        capabilities=[],
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

        assert len(log_chunks) == 1
        assert log_chunks[0]["type"] == "log"
        assert log_chunks[0]["title"] == "[数据查询助手] Executing SQL query"

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
        tools=[],
        capabilities=[],
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
    main_ctx = AgentContext(
        agent_id="main-agent-id",
        agent_name="MainAgent",
        delegation_depth=0,
        dataset_ids=[ID_MAIN_KB],
        knowledge_dataset_ids=[ID_FRONTEND_KB],
        user_id=100,
        is_admin=False,
        api_key="sk-main-key",
        permission_options={"approval_mode": "ask"},
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
        tools=[],
        capabilities=[],
        model_name="test",
        temperature=0.0,
        engine_config={"dataset_ids": [ID_SUB_KB]},
    )
    mock_get_config = AsyncMock(return_value=sub_config)

    mock_executor = MagicMock()

    async def mock_execute(history):
        from app.core.context import get_current_agent_context

        current_ctx = get_current_agent_context()
        assert current_ctx is not None
        assert set(current_ctx.dataset_ids) == {ID_MAIN_KB, ID_SUB_KB}
        assert current_ctx.knowledge_dataset_ids == [ID_FRONTEND_KB]
        assert set(current_ctx.engine_config.get("dataset_ids")) == {ID_MAIN_KB, ID_SUB_KB}
        assert current_ctx.delegation_depth == 1
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
        tools=[],
        capabilities=[],
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
        tools=[],
        capabilities=[],
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
        tools=[],
        capabilities=[],
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
        tools=[],
        capabilities=[],
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
        tools=[],
        capabilities=[],
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
