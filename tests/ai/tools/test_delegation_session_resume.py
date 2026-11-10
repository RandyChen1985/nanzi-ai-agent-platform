"""委派续聊（resume）与提问透传引入的新状态语义。

提问透传让子代理可以在委派中向用户提问，此时它这一轮是「正常停下等回答」，
不是「没产出」。这两件事在协议层必须区分开，否则父会拿到 EMPTY 文案、
以为子代理什么都没干，从而重复委派或替用户编答案。
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.context import AgentContext, set_agent_context
from app.schemas.agent import ChatConfig
from app.services.ai.subagent_protocol import (
    EMPTY_SUB_AGENT_RESULT_MESSAGE,
    SubAgentResult,
    SubAgentResultStatus,
    SubAgentStopReason,
)
from app.services.ai.tools.agent_delegate_tool import (
    EMPTY_DELEGATION_RESULT_MESSAGE,
    finalize_delegation_result,
    sub_agent_call,
)

pytestmark = pytest.mark.no_infrastructure

_DISPATCH_PATCH = "app.services.ai.tools.agent_delegate_tool._dispatch_sub_agent_executor"
ID_SUB_KB = "22222222222222222222222222222222"


@contextmanager
def _mock_delegation_runtime_config():
    with patch(
        "app.services.ai.tools.agent_delegate_tool._resolve_delegation_timeout_seconds",
        AsyncMock(return_value=60.0),
    ), patch(
        "app.services.ai.tools.agent_delegate_tool._resolve_delegation_result_max_chars",
        AsyncMock(return_value=8000),
    ), patch(
        "app.services.ai.tools.agent_delegate_tool._filter_ready_delegable_agents",
        AsyncMock(side_effect=lambda session, agents: list(agents)),
    ), patch(
        "app.services.ai.tools.agent_delegate_tool.can_delegate_to_agent",
        AsyncMock(return_value=True),
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


def _make_system_agent(*, agent_id="sub-123", name="chat-bi", display_name="数据查询助手"):
    mock_agent = MagicMock()
    mock_agent.id = agent_id
    mock_agent.name = name
    mock_agent.display_name = display_name
    mock_agent.is_enabled = True
    mock_agent.is_system = True
    mock_agent.agent_type = "CHATBI"
    mock_agent.capabilities = ["data_query"]
    mock_agent.engine_config = {"dataset_ids": [ID_SUB_KB]}
    mock_agent.sort_order = 0
    return mock_agent


def _sub_config():
    return ChatConfig(
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


@contextmanager
def _delegation_env(executor):
    with _mock_delegation_runtime_config(), _mock_system_agents_session(
        [_make_system_agent()]
    ), patch(
        "app.services.ai.agent_manager.AgentManagerService.get_active_agent_config",
        AsyncMock(return_value=_sub_config()),
    ), patch(
        _DISPATCH_PATCH, AsyncMock(return_value=executor)
    ), patch(
        "app.services.permission_service.PermissionService.check_permission",
        AsyncMock(return_value=True),
    ):
        yield


def test_needs_user_input_status_has_matching_stop_reason():
    assert SubAgentResultStatus.NEEDS_USER_INPUT.value == "needs_user_input"
    assert SubAgentStopReason.NEEDS_USER_INPUT.value == "needs_user_input"

    result = SubAgentResult(status=SubAgentResultStatus.NEEDS_USER_INPUT)

    assert result.stop_reason is SubAgentStopReason.NEEDS_USER_INPUT


def test_needs_user_input_result_text_points_to_waiting_for_user():
    result = SubAgentResult(status=SubAgentResultStatus.NEEDS_USER_INPUT, content="")

    text = result.to_tool_text()

    assert text != EMPTY_SUB_AGENT_RESULT_MESSAGE
    assert "等待用户回答" in text
    assert "resume" in text


def test_finalize_delegation_result_preserves_needs_user_input_without_content():
    result = finalize_delegation_result(
        "",
        target_agent_name="chat-bi",
        status=SubAgentResultStatus.NEEDS_USER_INPUT,
    )

    assert result.status is SubAgentResultStatus.NEEDS_USER_INPUT
    assert result.content != EMPTY_DELEGATION_RESULT_MESSAGE
    assert "等待用户回答" in result.content


@pytest.mark.asyncio
async def test_resume_reuses_prior_sub_session_history():
    """续聊的核心行为：第二轮子会话历史要包含第一轮的 query 与终态正文。"""
    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        trace_buffer=[],
    )
    set_agent_context(main_ctx)

    seen_histories: list[list[dict]] = []
    answers = iter(["第一轮答案：华东区 Q3 3,842 万", "第二轮答案：已补竞品 A、B 对比"])

    async def mock_execute(history):
        seen_histories.append([dict(item) for item in history])
        yield {"content": next(answers)}

    mock_executor = MagicMock()
    mock_executor.execute = mock_execute

    try:
        with _delegation_env(mock_executor):
            await sub_agent_call.func(agent_name="chat-bi", query="统计 Q3 华东区销售额")
            await sub_agent_call.func(
                agent_name="chat-bi",
                query="竞品选定 A、B 公司，继续",
                resume=True,
            )
    finally:
        set_agent_context(None)

    assert len(seen_histories) == 2
    assert seen_histories[0] == [{"role": "user", "content": "统计 Q3 华东区销售额"}]
    assert seen_histories[1] == [
        {"role": "user", "content": "统计 Q3 华东区销售额"},
        {"role": "assistant", "content": "第一轮答案：华东区 Q3 3,842 万"},
        {"role": "user", "content": "竞品选定 A、B 公司，继续"},
    ]


@pytest.mark.asyncio
async def test_second_call_without_resume_starts_fresh_session():
    """不续聊时必须保持原有语义：子会话从零开始，绝不继承上一轮。"""
    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        trace_buffer=[],
    )
    set_agent_context(main_ctx)

    seen_histories: list[list[dict]] = []
    answers = iter(["第一轮答案", "第二轮答案"])

    async def mock_execute(history):
        seen_histories.append([dict(item) for item in history])
        yield {"content": next(answers)}

    mock_executor = MagicMock()
    mock_executor.execute = mock_execute

    try:
        with _delegation_env(mock_executor):
            await sub_agent_call.func(agent_name="chat-bi", query="统计 Q3 华东区销售额")
            await sub_agent_call.func(agent_name="chat-bi", query="那华南区呢")
    finally:
        set_agent_context(None)

    assert seen_histories[1] == [{"role": "user", "content": "那华南区呢"}]


@pytest.mark.asyncio
async def test_resume_without_cache_degrades_to_fresh_session():
    """没缓存时 resume 不能报错、也不能拿半截历史：降级为全新会话。"""
    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        trace_buffer=[],
    )
    set_agent_context(main_ctx)

    seen_histories: list[list[dict]] = []

    async def mock_execute(history):
        seen_histories.append([dict(item) for item in history])
        yield {"content": "答案"}

    mock_executor = MagicMock()
    mock_executor.execute = mock_execute

    try:
        with _delegation_env(mock_executor):
            await sub_agent_call.func(
                agent_name="chat-bi",
                query="首次就直接续聊的问题",
                resume=True,
            )
    finally:
        set_agent_context(None)

    assert seen_histories == [[{"role": "user", "content": "首次就直接续聊的问题"}]]


@pytest.mark.asyncio
async def test_resume_budget_is_independent_from_requery_budget():
    """重查额度用尽后，续聊仍应可用——两者不是同一份额度。"""
    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        trace_buffer=[],
    )
    set_agent_context(main_ctx)

    seen_histories: list[list[dict]] = []
    answers = iter(["答 A", "答 B", "答 C"])

    async def mock_execute(history):
        seen_histories.append([dict(item) for item in history])
        yield {"content": next(answers)}

    mock_executor = MagicMock()
    mock_executor.execute = mock_execute

    try:
        with _delegation_env(mock_executor):
            await sub_agent_call.func(agent_name="chat-bi", query="问题 A")
            await sub_agent_call.func(agent_name="chat-bi", query="问题 B")
            resumed = await sub_agent_call.func(
                agent_name="chat-bi",
                query="补充：只看华东区",
                resume=True,
            )
    finally:
        set_agent_context(None)

    assert "已达上限" not in resumed
    assert len(seen_histories) == 3
    assert seen_histories[2][-1] == {"role": "user", "content": "补充：只看华东区"}


@pytest.mark.asyncio
async def test_resume_rejected_after_resume_budget_exhausted():
    """续聊自己有上限：默认 2 次，第 3 次拒绝并提示改用不带 resume 的新委派。"""
    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        trace_buffer=[],
    )
    set_agent_context(main_ctx)

    async def mock_execute(history):
        yield {"content": "答案"}

    mock_executor = MagicMock()
    mock_executor.execute = mock_execute

    try:
        with _delegation_env(mock_executor):
            await sub_agent_call.func(agent_name="chat-bi", query="问题 A")
            await sub_agent_call.func(agent_name="chat-bi", query="追问 1", resume=True)
            await sub_agent_call.func(agent_name="chat-bi", query="追问 2", resume=True)
            blocked = await sub_agent_call.func(
                agent_name="chat-bi",
                query="追问 3",
                resume=True,
            )
    finally:
        set_agent_context(None)

    assert "已达上限" in blocked
    assert "resume" in blocked


@pytest.mark.asyncio
async def test_resume_marks_subagent_trace_metadata():
    """续聊要在 trace 元数据上留痕：前端靠 ``resume_index`` 显示「续聊 #N」。

    只在协议层加枚举不够——用户看不到状态，只能看到时间线上两次长得一模一样的
    委派。序号还必须是「第几次续聊」而不是布尔，否则无法表达第三次、第四次。
    """
    import asyncio

    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        trace_buffer=[],
        conversation_id="conv-1",
        user_id=1,
    )
    main_ctx.event_queue = asyncio.Queue()
    set_agent_context(main_ctx)

    async def mock_execute(history):
        yield {"type": "content", "content": "查询结果"}

    executor = MagicMock()
    executor.execute = mock_execute

    try:
        with _delegation_env(executor):
            await sub_agent_call.func(agent_name="chat-bi", query="第一次查竞品")
            await sub_agent_call.func(
                agent_name="chat-bi", query="再补一个季度", resume=True
            )
    finally:
        set_agent_context(None)

    metas = []
    while not main_ctx.event_queue.empty():
        event = main_ctx.event_queue.get_nowait()
        if isinstance(event, dict) and isinstance(event.get("subagent"), dict):
            metas.append(event["subagent"])

    resumed = [meta for meta in metas if meta.get("resume") is True]
    fresh = [meta for meta in metas if meta.get("resume") is False]

    assert resumed, "续聊必须留下 resume=True 的 trace 元数据"
    assert {meta["resume_index"] for meta in resumed} == {1}
    assert fresh, "首次委派应留下 resume=False 的元数据"
    assert {meta["resume_index"] for meta in fresh} == {0}


@pytest.mark.asyncio
async def test_resume_across_turns_uses_shared_store():
    """用户答完提问卡是**新的一轮请求**：缓存必须活在 ctx 之外。

    否则父照提示词调 ``resume=True``，每次都因 ctx 重建而拿不到历史、降级成
    全新会话——用户答完问题还得把条件重讲一遍。这条用两个独立 ctx 模拟真实的
    「提问一轮 → 回答一轮」。
    """
    import asyncio

    from app.services.ai.delegation_session_store import DelegationSessionStore
    from tests.ai.tools.test_delegation_session_store import _FakeRedis

    redis = _FakeRedis()
    captured: list[list[dict]] = []

    async def mock_execute(history):
        captured.append([dict(item) for item in history])
        yield {"type": "content", "content": "查询结果"}

    executor = MagicMock()
    executor.execute = mock_execute

    def _fresh_ctx():
        ctx = AgentContext(
            agent_id="main",
            agent_name="MainAgent",
            delegation_depth=0,
            trace_id="trace-main",
            trace_buffer=[],
            user_id=7,
            conversation_id="conv-cross-turn",
        )
        ctx.event_queue = asyncio.Queue()
        return ctx

    def _run(coro_factory):
        set_agent_context(_fresh_ctx())
        try:
            with _delegation_env(executor):
                return coro_factory()
        finally:
            set_agent_context(None)

    with patch(
        "app.services.ai.delegation_session_store.DelegationSessionStore.from_runtime",
        AsyncMock(return_value=DelegationSessionStore(redis)),
    ):
        set_agent_context(_fresh_ctx())
        try:
            with _delegation_env(executor):
                await sub_agent_call.func(
                    agent_name="chat-bi", query="查上海到北京的火车票"
                )
        finally:
            set_agent_context(None)

        # 第二轮：全新的 ctx（用户答完后的新请求）
        set_agent_context(_fresh_ctx())
        try:
            with _delegation_env(executor):
                await sub_agent_call.func(
                    agent_name="chat-bi", query="日期是今天", resume=True
                )
        finally:
            set_agent_context(None)

    assert len(captured) == 2
    second_history = captured[1]
    assert any(
        "查上海到北京的火车票" in str(message.get("content"))
        for message in second_history
    ), "续聊必须从共享缓存里取回上一轮历史，而不是重新开一个空会话"


@pytest.mark.asyncio
async def test_delegation_question_is_cached_as_assistant_turn():
    """子代理问过什么要进缓存。

    只存「用户原任务」的话，续聊时子代理看到的是「任务 + 用户答了日期」，
    却没有「我问过日期」这条 assistant 轮——它很可能把同一个问题再问一遍，
    用户就会觉得「我刚答完你怎么又问」。
    """
    import asyncio

    from unittest.mock import AsyncMock, patch

    from app.services.ai.delegation_session_store import DelegationSessionStore
    from tests.ai.tools.test_delegation_session_store import _FakeRedis

    redis = _FakeRedis()
    asked = "请问您计划哪一天从上海出发去北京？"

    async def mock_execute(history):
        yield {
            "type": "user_question",
            "question_id": "uq_ask0001",
            "question": asked,
            "options": [{"id": "today", "label": "今天"}],
            "status": "pending",
        }

    executor = MagicMock()
    executor.execute = mock_execute

    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        trace_buffer=[],
        user_id=7,
        conversation_id="conv-ask-cache",
    )
    main_ctx.event_queue = asyncio.Queue()
    set_agent_context(main_ctx)

    try:
        with _delegation_env(executor), patch(
            "app.services.ai.user_question.persist_user_question_event", AsyncMock()
        ), patch(
            "app.services.ai.delegation_session_store.DelegationSessionStore.from_runtime",
            AsyncMock(return_value=DelegationSessionStore(redis)),
        ):
            await sub_agent_call.func(agent_name="chat-bi", query="帮我查火车票")
    finally:
        set_agent_context(None)

    cached = await DelegationSessionStore(redis).load(
        user_id=7, conversation_id="conv-ask-cache", agent_key="chat_bi"
    )

    assert cached, "提问这一轮也要留下会话缓存"
    assert any(message.get("role") == "assistant" and asked in str(message.get("content")) for message in cached), (
        "缓存里必须有「子代理问过什么」这一条 assistant 轮"
    )


@pytest.mark.asyncio
async def test_resume_without_cache_does_not_consume_resume_budget():
    """缓存过期后降级为全新会话：不能白扣续聊额度，也不能在 trace 里谎称续聊。

    降级后如果还带着 ``resume=True`` 往下走，会同时坏三件事：白扣一次续聊额度
    （默认只有 2 次）、绕过「相同问题不许重复委派」的签名检查、以及在前端时间线
    上写一个假的「续聊 #1」，排查时会被彻底带偏。
    """
    import asyncio

    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        trace_buffer=[],
        user_id=7,
        conversation_id="conv-empty-cache",
    )
    main_ctx.event_queue = asyncio.Queue()
    set_agent_context(main_ctx)

    async def mock_execute(history):
        yield {"type": "content", "content": "查询结果"}

    executor = MagicMock()
    executor.execute = mock_execute

    try:
        with _delegation_env(executor):
            text = await sub_agent_call.func(agent_name="chat-bi", query="没有缓存的续聊", resume=True)
    finally:
        set_agent_context(None)

    assert "查询结果" in text
    # 降级后不该占续聊额度
    assert main_ctx.delegation_resume_call_counts.get("chat_bi", 0) == 0

    metas = []
    while not main_ctx.event_queue.empty():
        event = main_ctx.event_queue.get_nowait()
        if isinstance(event, dict) and isinstance(event.get("subagent"), dict):
            metas.append(event["subagent"])

    assert metas, "应当仍然产生委派 trace"
    assert all(meta.get("resume") is False for meta in metas), (
        "降级为全新会话后，trace 不能再标记为续聊"
    )


@pytest.mark.asyncio
async def test_resume_never_sends_two_consecutive_user_turns():
    """缓存末条是 user 时不能拼出连续两条 user。

    提问轮如果没记下子代理问了什么，缓存里就只有一条 user。续聊时再追加一条 user，
    严格校验角色交替的网关会直接 400。这里断言合并成一条。
    """
    import asyncio

    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        trace_buffer=[],
        user_id=7,
        conversation_id="conv-alternate",
    )
    main_ctx.event_queue = asyncio.Queue()
    # 模拟「只存了问、没存答」的缓存
    main_ctx.delegation_session_histories["chat_bi"] = [
        {"role": "user", "content": "查上海到北京的火车票"}
    ]
    set_agent_context(main_ctx)

    captured: list[list[dict]] = []

    async def mock_execute(history):
        captured.append([dict(item) for item in history])
        yield {"type": "content", "content": "查询结果"}

    executor = MagicMock()
    executor.execute = mock_execute

    try:
        with _delegation_env(executor):
            await sub_agent_call.func(
                agent_name="chat-bi", query="日期是今天", resume=True
            )
    finally:
        set_agent_context(None)

    history = captured[0]
    roles = [message["role"] for message in history]
    assert roles == ["user"], f"应当合并成一条 user，实际是 {roles}"
    assert "查上海到北京的火车票" in history[0]["content"]
    assert "日期是今天" in history[0]["content"]


def test_needs_user_input_tool_text_names_target_agent():
    """正文为空时的兜底文案也要点名子代理，否则父无从判断 resume 谁。"""
    result = SubAgentResult(
        status=SubAgentResultStatus.NEEDS_USER_INPUT,
        target_agent_name="chat-bi",
    )

    text = result.to_tool_text()

    assert "等待用户回答" in text
    assert "chat-bi" in text


@pytest.mark.asyncio
async def test_full_ask_then_answer_then_resume_keeps_coherent_history():
    """端到端：委派 → 子代理提问 → 用户回答 → resume，历史必须自洽。

    第 2 轮子代理应当看到完整的三段：原始任务、它自己问过的问题、用户回答后的
    新指令；顺序与角色都要对，否则它要么重复提问，要么把补充条件当新任务。
    """
    import asyncio

    from unittest.mock import AsyncMock, patch

    from app.services.ai.delegation_session_store import DelegationSessionStore
    from tests.ai.tools.test_delegation_session_store import _FakeRedis

    redis = _FakeRedis()
    asked = "请问您计划哪一天从上海出发去北京？"
    captured: list[list[dict]] = []

    async def executor_for_round(history, ask: bool):
        captured.append([dict(item) for item in history])
        if ask:
            yield {
                "type": "user_question",
                "question_id": "uq_e2e0001",
                "question": asked,
                "options": [{"id": "today", "label": "今天"}],
                "status": "pending",
            }
        else:
            yield {"type": "content", "content": "G2 次列车 08:00 出发，余票充足。"}

    def _fresh_ctx():
        ctx = AgentContext(
            agent_id="main",
            agent_name="MainAgent",
            delegation_depth=0,
            trace_id="trace-main",
            trace_buffer=[],
            user_id=7,
            conversation_id="conv-e2e",
        )
        ctx.event_queue = asyncio.Queue()
        return ctx

    def _executor(ask):
        executor = MagicMock()

        async def _execute(history):
            async for chunk in executor_for_round(history, ask):
                yield chunk

        executor.execute = _execute
        return executor

    store_patch = patch(
        "app.services.ai.delegation_session_store.DelegationSessionStore.from_runtime",
        AsyncMock(return_value=DelegationSessionStore(redis)),
    )
    with store_patch, patch(
        "app.services.ai.user_question.persist_user_question_event", AsyncMock()
    ):
        # 第 1 轮：委派，子代理提问后停下
        set_agent_context(_fresh_ctx())
        try:
            with _delegation_env(_executor(ask=True)):
                first = await sub_agent_call.func(agent_name="chat-bi", query="查上海到北京的火车票")
        finally:
            set_agent_context(None)

        # 第 2 轮：用户已回答，父 resume（全新 ctx，缓存只可能来自共享 store）
        set_agent_context(_fresh_ctx())
        try:
            with _delegation_env(_executor(ask=False)):
                second = await sub_agent_call.func(
                    agent_name="chat-bi",
                    query="用户已确认出发日期为今天（2026-11-10），请给出车次。",
                    resume=True,
                )
        finally:
            set_agent_context(None)

    assert "等待用户回答" in first
    assert "G2 次列车" in second

    round2_history = captured[1]
    roles = [item["role"] for item in round2_history]
    assert roles == ["user", "assistant", "user"], f"角色序列不对：{roles}"

    contents = [str(item["content"]) for item in round2_history]
    assert "查上海到北京的火车票" in contents[0], "第 1 段应是原始任务"
    assert asked in contents[1], "第 2 段应是子代理自己问过的问题"
    assert "2026-11-10" in contents[2], "第 3 段应是用户回答后的新指令"


def _make_probe_executor(captured, label):
    executor = MagicMock()

    async def _execute(history):
        captured.append((label, [dict(item) for item in history]))
        yield {"type": "content", "content": f"答案-{label}"}

    executor.execute = _execute
    return executor


@pytest.mark.asyncio
async def test_fresh_delegation_does_not_inherit_previous_session():
    """resume=False 是全新会话，不能把之前那轮的对话写进缓存。

    否则同轮内「q1 委派 → q2 换问题重查（工具描述要求 resume=false）→ q3 续聊」时，
    q3 会把 q1、q2 两份互不相干的会话拼在一起当上下文，子代理把无关任务的旧结论
    当成「自己上一轮说过的」，沿用错误口径。
    """
    import asyncio

    from unittest.mock import AsyncMock, patch

    from app.services.ai.delegation_session_store import DelegationSessionStore

    captured: list[tuple[str, list[dict]]] = []
    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        trace_buffer=[],
        user_id=7,
        conversation_id="conv-fresh",
    )
    main_ctx.event_queue = asyncio.Queue()
    set_agent_context(main_ctx)

    try:
        with patch(
            "app.services.ai.delegation_session_store.DelegationSessionStore.from_runtime",
            AsyncMock(return_value=DelegationSessionStore(None)),
        ):
            for label, kwargs in [("q1", {}), ("q2", {}), ("q3", {"resume": True})]:
                with _delegation_env(_make_probe_executor(captured, label)):
                    await sub_agent_call.func(
                        agent_name="chat-bi", query=f"查询{label}", **kwargs
                    )
    finally:
        set_agent_context(None)

    round3 = [item for label, hist in captured if label == "q3" for item in hist]
    contents = " ".join(str(item.get("content")) for item in round3)

    assert "查询q2" in contents, "续聊应接上 q2 那次会话"
    assert "查询q1" not in contents, "q1 是另一次独立会话，不能被拼进 q3 的上下文"


@pytest.mark.asyncio
async def test_cached_history_never_has_two_consecutive_user_turns():
    """缓存自身也不能出现连续两条 user。

    提问轮 if 没拿到「它问了什么」时会写一条纯 user（answer=None）。下一次 resume
    若直接 append user，缓存里就会出现 [user, user, assistant] —— 之后任何一轮把它
    喂给严格校验角色交替的网关都会 400。
    """
    import asyncio

    from unittest.mock import AsyncMock, patch

    from app.services.ai.delegation_session_store import DelegationSessionStore

    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        trace_buffer=[],
        user_id=7,
        conversation_id="conv-noconcat",
    )
    main_ctx.event_queue = asyncio.Queue()
    # 纯 user 记录的缓存（提问轮没记下问题）
    main_ctx.delegation_session_histories["chat_bi"] = [
        {"role": "user", "content": "帮我查火车票"}
    ]
    set_agent_context(main_ctx)

    captured: list[tuple[str, list[dict]]] = []
    try:
        with patch(
            "app.services.ai.delegation_session_store.DelegationSessionStore.from_runtime",
            AsyncMock(return_value=DelegationSessionStore(None)),
        ):
            with _delegation_env(_make_probe_executor(captured, "r1")):
                await sub_agent_call.func(
                    agent_name="chat-bi", query="日期是今天", resume=True
                )
    finally:
        set_agent_context(None)

    cached = main_ctx.delegation_session_histories["chat_bi"]
    roles = [item["role"] for item in cached]
    assert not any(
        roles[i] == roles[i + 1] == "user" for i in range(len(roles) - 1)
    ), f"缓存里出现了连续两条 user：{roles}"


@pytest.mark.asyncio
async def test_failed_resume_does_not_burn_quota():
    """续聊失败（超时/异常）要把额度退回，否则两次抖动就再也追问不了。

    额度必须在调用模型前扣（否则超额拦不住），失败时不退的话，用户第三次追问会直接
    收到「已达上限」，而一次都没真正续聊成功。
    """
    import asyncio

    from app.services.ai.delegation_session_store import DelegationSessionStore

    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        trace_buffer=[],
        user_id=7,
        conversation_id="conv-quota",
    )
    main_ctx.event_queue = asyncio.Queue()
    set_agent_context(main_ctx)

    failing = MagicMock()

    async def _boom(history):
        raise RuntimeError("gateway timeout")
        yield  # pragma: no cover

    failing.execute = _boom

    try:
        with patch(
            "app.services.ai.delegation_session_store.DelegationSessionStore.from_runtime",
            AsyncMock(return_value=DelegationSessionStore(None)),
        ):
            for _ in range(2):
                with _delegation_env(failing):
                    await sub_agent_call.func(
                        agent_name="chat-bi", query="再补充一个条件", resume=True
                    )

            assert main_ctx.delegation_resume_call_counts.get("chat_bi", 0) == 0, (
                "失败的续聊不该占用额度"
            )

            captured: list[list[dict]] = []

            def _ok(label):
                ex = MagicMock()

                async def _execute(history):
                    captured.append([dict(item) for item in history])
                    yield {"type": "content", "content": "答案"}

                ex.execute = _execute
                return ex

            with _delegation_env(_ok("third")):
                result = await sub_agent_call.func(
                    agent_name="chat-bi", query="再补充一个条件", resume=True
                )
    finally:
        set_agent_context(None)

    assert "已达上限" not in str(result), "两次失败不该把额度耗光"


@pytest.mark.asyncio
async def test_concurrent_writes_persist_the_merged_history():
    """并发写同一个子代理的缓存时，落库的必须是最新合并结果。

    batch 里同一子代理出现两次（或模型并行发两次委派）时共享同一份内存列表；
    各自序列化本地快照先后落地，先完成那一路会把后一路覆盖掉——下一轮 resume
    静默丢掉内容，且没有任何日志。
    """
    import asyncio

    from app.services.ai.delegation_session_store import DelegationSessionStore
    from app.services.ai.tools.agent_delegate_tool import _record_sub_session_history

    ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        trace_buffer=[],
        user_id=7,
        conversation_id="conv-concurrent",
    )

    saved: list[list[dict]] = []

    class _CapturingStore(DelegationSessionStore):
        async def save(self, **kwargs):
            saved.append(list(kwargs.get("history") or []))

    async def _write(query, answer, resume):
        await _record_sub_session_history(
            ctx, agent_key="chat_bi", query=query, answer=answer, resume=resume
        )

    with patch(
        "app.services.ai.delegation_session_store.DelegationSessionStore.from_runtime",
        AsyncMock(return_value=_CapturingStore(None)),
    ):
        await asyncio.gather(
            _write("查 A", "答案 A", True),
            _write("查 B", "答案 B", True),
        )

    assert saved, "应当至少落库一次"
    assert len(saved[-1]) == len(ctx.delegation_session_histories["chat_bi"]), (
        "最后落库的内容应当是合并后的完整历史，而不是某一路的本地快照"
    )
