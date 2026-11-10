"""子代理提问透传：安全边界与来源标注。

子代理读过网页 / 上传文档 / 知识库片段后，它的提问内容属于「不可信输入」。
若不设边界，提示词注入可以把攻击者的话包装成提问卡显示在主会话里，
还带着子智能体的权威感；选项里的 ``action`` 更能诱导用户点击触发页面动作。
"""

import json
from pathlib import Path

import pytest

from app.core.context import (
    AgentContext,
    execution_options_context,
    set_agent_context,
)
from app.services.ai.tools.user_question_tools import ask_user_question

pytestmark = pytest.mark.no_infrastructure


def _args(**overrides):
    payload = {
        "question": "要对比哪几家竞品？",
        "options": [
            {"id": "a", "label": "A 公司"},
            {"id": "b", "label": "B 公司"},
        ],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def delegated_sub_agent():
    """模拟子代理执行环境：委派标记 + 子代理自身上下文。"""
    options_token = execution_options_context.set({"delegated_sub_agent": True})
    set_agent_context(
        AgentContext(agent_id="sub-123", agent_name="chat-bi", delegation_depth=1)
    )
    yield
    set_agent_context(None)
    execution_options_context.reset(options_token)


async def test_delegated_ask_rejects_option_action(delegated_sub_agent):
    """选项不允许带 action：子代理不能借用页面动作诱导用户点击。"""
    result = json.loads(
        await ask_user_question.ainvoke(
            _args(
                options=[
                    {"id": "a", "label": "立即验证", "action": "open_dataset_picker"},
                    {"id": "b", "label": "稍后"},
                ]
            )
        )
    )

    assert result["status"] == "error"
    assert "action" in result["error"]


async def test_delegated_ask_rejects_overlong_question(delegated_sub_agent):
    """子代理提问的问题文本上限比父更严（200 字），防止夹带长段指令。"""
    result = json.loads(
        await ask_user_question.ainvoke(_args(question="异常" * 120))
    )

    assert result["status"] == "error"
    assert "200" in result["error"]


async def test_delegated_ask_rejects_too_many_options(delegated_sub_agent):
    """选项数 2~6：太多选项等于把决策负担甩回给用户，也可能是注入的载体。"""
    options = [{"id": f"o{i}", "label": f"选项 {i}"} for i in range(7)]

    result = json.loads(await ask_user_question.ainvoke(_args(options=options)))

    assert result["status"] == "error"
    assert "6" in result["error"]


async def test_delegated_ask_rejects_overlong_option_label(delegated_sub_agent):
    result = json.loads(
        await ask_user_question.ainvoke(
            _args(
                options=[
                    {"id": "a", "label": "公司" * 40},
                    {"id": "b", "label": "B 公司"},
                ]
            )
        )
    )

    assert result["status"] == "error"
    assert "60" in result["error"]


async def test_main_agent_ask_keeps_original_limits():
    """父自己的提问不受子代理那套严格上限影响，行为保持原样。"""
    options_token = execution_options_context.set({})
    try:
        result = json.loads(
            await ask_user_question.ainvoke(
                _args(
                    question="异常" * 120,
                    options=[
                        {"id": "a", "label": "立即验证", "action": "open_dataset_picker"},
                        {"id": "b", "label": "稍后"},
                    ],
                )
            )
        )
    finally:
        execution_options_context.reset(options_token)

    assert result["status"] == "awaiting_user"


# ---------------------------------------------------------------------------
# 委派层：卡片透传 + 以父会话登记 pending + needs_user_input 收尾
# ---------------------------------------------------------------------------


def _question_chunk():
    return {
        "type": "user_question",
        "question_id": "uq_test0001",
        "question": "要对比哪几家竞品？",
        "options": [{"id": "a", "label": "A 公司"}, {"id": "b", "label": "B 公司"}],
        "is_multi_select": True,
        "allow_custom_input": True,
        "status": "pending",
    }


async def test_delegated_user_question_is_registered_to_parent_session():
    """关键一步：pending 必须登记在**父会话**下，否则用户回答的回执查不到。"""
    import asyncio
    from contextlib import contextmanager
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.core.context import set_agent_context
    from app.services.ai.tools.agent_delegate_tool import sub_agent_call
    from tests.ai.tools.test_delegation_session_resume import _delegation_env

    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        trace_buffer=[],
        user_id=7,
        conversation_id="conv-parent",
    )
    main_ctx.event_queue = asyncio.Queue()
    set_agent_context(main_ctx)

    async def mock_execute(history):
        yield _question_chunk()

    mock_executor = MagicMock()
    mock_executor.execute = mock_execute

    persist = AsyncMock()
    try:
        with _delegation_env(mock_executor), patch(
            "app.services.ai.user_question.persist_user_question_event", persist
        ):
            text = await sub_agent_call.func(agent_name="chat-bi", query="查竞品数据")
    finally:
        set_agent_context(None)

    persist.assert_awaited_once()
    kwargs = persist.await_args.kwargs
    assert kwargs["conversation_id"] == "conv-parent"
    assert kwargs["user_id"] == 7

    # 队列里还混着生命周期日志，只取提问事件
    forwarded_events = []
    while not main_ctx.event_queue.empty():
        forwarded_events.append(main_ctx.event_queue.get_nowait())
    question_events = [
        event for event in forwarded_events if event.get("type") == "user_question"
    ]
    assert len(question_events) == 1
    forwarded = question_events[0]
    assert forwarded["question_id"] == "uq_test0001"
    # 卡片要标清来源，否则用户以为父智能体在问
    assert forwarded["source_agent_display_name"] == "数据查询助手"
    assert "等待用户回答" in text
    assert "resume" in text


async def test_delegated_user_question_not_confused_with_empty_result():
    """子代理提问不是「没产出」：不能给出 EMPTY 文案让父重复委派。"""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.core.context import set_agent_context
    from app.services.ai.tools.agent_delegate_tool import (
        EMPTY_DELEGATION_RESULT_MESSAGE,
        sub_agent_call,
    )
    from tests.ai.tools.test_delegation_session_resume import _delegation_env

    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        trace_buffer=[],
        user_id=7,
        conversation_id="conv-parent",
    )
    main_ctx.event_queue = asyncio.Queue()
    set_agent_context(main_ctx)

    async def mock_execute(history):
        yield _question_chunk()

    mock_executor = MagicMock()
    mock_executor.execute = mock_execute

    try:
        with _delegation_env(mock_executor), patch(
            "app.services.ai.user_question.persist_user_question_event", AsyncMock()
        ):
            text = await sub_agent_call.func(agent_name="chat-bi", query="查竞品数据")
    finally:
        set_agent_context(None)

    assert text != EMPTY_DELEGATION_RESULT_MESSAGE
    assert "未产生可交付正文" not in text


async def test_only_one_delegated_question_card_per_turn():
    """batch 并行时 store 只有一个 active 槽：同一时刻必须只放行一张卡。

    否则后登记的会把先登记的标成 ``superseded``，用户只看到最后一张，
    前面几张被静默吃掉——那是最难排查的一类故障。
    """
    from unittest.mock import AsyncMock, patch

    from app.services.ai.tools.agent_delegate_tool import (
        _persist_delegated_user_question,
    )

    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        user_id=7,
        conversation_id="conv-parent",
    )
    chunk = _question_chunk()
    persist = AsyncMock()

    with patch("app.services.ai.user_question.persist_user_question_event", persist):
        first = await _persist_delegated_user_question(
            main_ctx, chunk, "助手A", run_id="run-a"
        )
        second = await _persist_delegated_user_question(
            main_ctx, chunk, "助手B", run_id="run-b"
        )

    assert first is None
    assert second is not None
    assert "已有" in second
    assert persist.await_count == 1


async def test_delegated_question_requires_conversation_binding():
    """缺会话/用户时宁可显式报错，也不发一张用户答了也回不来的死卡。"""
    from unittest.mock import AsyncMock, patch

    from app.services.ai.tools.agent_delegate_tool import (
        _persist_delegated_user_question,
    )

    main_ctx = AgentContext(
        agent_id="main", agent_name="MainAgent", delegation_depth=0
    )
    persist = AsyncMock()

    with patch("app.services.ai.user_question.persist_user_question_event", persist):
        error = await _persist_delegated_user_question(
            main_ctx, _question_chunk(), "助手A", run_id="run-a"
        )

    assert error is not None
    assert "主会话" in error
    persist.assert_not_awaited()


def test_user_question_sse_carries_source_agent():
    """卡片来源要能传到前端，否则用户以为是父智能体在问。"""
    from app.services.ai.user_question import build_user_question_sse

    tool_output = json.dumps(
        {
            "status": "awaiting_user",
            "interaction_type": "question",
            "question_id": "uq_src0001",
            "question": "要对比哪几家竞品？",
            "options": [{"id": "a", "label": "A 公司"}, {"id": "b", "label": "B 公司"}],
            "source_agent_name": "chat-bi",
            "source_agent_display_name": "数据查询助手",
        },
        ensure_ascii=False,
    )

    event = build_user_question_sse(
        tool_name="ask_user_question", tool_output=tool_output
    )

    assert event is not None
    assert event["source_agent_name"] == "chat-bi"
    assert event["source_agent_display_name"] == "数据查询助手"


def test_user_question_sse_omits_empty_source_agent():
    """父自己提问时没有来源，保持字段为空而不是伪造一个。"""
    from app.services.ai.user_question import build_user_question_sse

    tool_output = json.dumps(
        {
            "status": "awaiting_user",
            "interaction_type": "question",
            "question_id": "uq_src0002",
            "question": "要看哪个时间段？",
            "options": [{"id": "a", "label": "本周"}, {"id": "b", "label": "本月"}],
        },
        ensure_ascii=False,
    )

    event = build_user_question_sse(
        tool_name="ask_user_question", tool_output=tool_output
    )

    assert event is not None
    assert event["source_agent_display_name"] == ""


# ---------------------------------------------------------------------------
# 落库快照：实时看到的卡与刷新后回放的卡必须是同一张
#
# 实时卡片挂在消息对象上（不落库），历史回放只读 process_timeline 的快照。
# 快照少留一个字段，用户刷新一次就会看到「实时有徽标、历史没徽标」两种样子。
# ---------------------------------------------------------------------------


def _snapshot_chunk(**overrides):
    chunk = {
        "type": "user_question",
        "tool_call_id": "call_snap1",
        "question_id": "uq_snap0001",
        "question": "要对比哪几家竞品？",
        "options": [{"id": "a", "label": "A 公司"}, {"id": "b", "label": "B 公司"}],
        "is_multi_select": False,
        "allow_custom_input": True,
    }
    chunk.update(overrides)
    return chunk


def _persisted_card(chunk):
    from app.services.ai.runtime.agentscope.process_timeline_snapshot import (
        apply_stream_chunk,
    )

    state = []
    apply_stream_chunk(state, chunk)
    return state[-1]["user_question"]


def test_persisted_card_keeps_source_agent():
    """快照必须留下来源，否则回放时「来自：xxx」徽标凭空消失。"""
    card = _persisted_card(
        _snapshot_chunk(
            source_agent_name="chat-bi",
            source_agent_display_name="数据查询助手",
        )
    )

    assert card["source_agent_name"] == "chat-bi"
    assert card["source_agent_display_name"] == "数据查询助手"


def test_persisted_card_omits_empty_source_agent():
    """父代理自己提问时两个键都不落，免得快照里堆一串空字符串。"""
    card = _persisted_card(_snapshot_chunk(source_agent_name="", source_agent_display_name=""))

    assert "source_agent_name" not in card
    assert "source_agent_display_name" not in card


def test_persisted_card_keeps_option_flags():
    """回放时卡片可能仍在等待回答：置灰提示与点击动作要一起还原。"""
    card = _persisted_card(
        _snapshot_chunk(
            options=[
                {"id": "a", "label": "A 公司", "disabled": True},
                {"id": "b", "label": "B 公司", "action": "open_dataset_picker"},
            ]
        )
    )

    assert card["options"][0]["disabled"] is True
    assert card["options"][1]["action"] == "open_dataset_picker"


async def test_pending_record_keeps_source_agent():
    """pending 记录与快照留同一份事实，以后要从这里补建卡片不必回头追字段。"""
    from unittest.mock import AsyncMock, patch

    from app.services.ai.user_question import persist_user_question_event

    store = AsyncMock()
    with patch(
        "app.services.ai.user_question_store.UserQuestionStore.from_runtime",
        AsyncMock(return_value=store),
    ):
        await persist_user_question_event(
            event=_snapshot_chunk(
                source_agent_name="chat-bi",
                source_agent_display_name="数据查询助手",
            ),
            user_id=7,
            conversation_id="conv-snap-1",
        )

    payload = store.create_pending.await_args.kwargs["payload"]
    assert payload["source_agent_name"] == "chat-bi"
    assert payload["source_agent_display_name"] == "数据查询助手"


# ---------------------------------------------------------------------------
# 提示词契约：模型得知道 resume 与委派内提问这两件事存在
# ---------------------------------------------------------------------------


def test_delegation_capability_table_documents_resume():
    """父智能体要能从能力表知道「追问同一位子代理用 resume」。"""
    from app.services.ai.agent_prompts import AgentServicePrompts

    text = " ".join(row for _, row in AgentServicePrompts._PLATFORM_KNOWLEDGE_LOOKUP_ROWS)

    assert "resume=true" in text


def test_ask_user_question_description_allows_delegated_asking():
    """子代理侧也要知道：委派中提问是被允许的，卡片会直达用户。"""
    from app.services.ai.tools.user_question_tools import ask_user_question

    description = ask_user_question.description

    assert "委派" in description


def test_batch_status_recognizes_waiting_for_user():
    """batch 汇总必须把「等待用户回答」和「完成」分开。

    否则 3 个做完 + 1 个在等用户，会被汇总成 completed_count=4 / failed_count=0，
    父智能体看到「全部完成」就会直接汇总回复，把那张还没人回答的提问卡当成
    已经办好了——用户答完才发现结果里根本没有那一路的结论。
    """
    from app.services.ai.subagent_protocol import (
        NEEDS_USER_INPUT_SUB_AGENT_RESULT_MESSAGE,
    )
    from app.services.ai.tools.agent_delegate_tool import _batch_result_status

    assert (
        _batch_result_status(NEEDS_USER_INPUT_SUB_AGENT_RESULT_MESSAGE)
        == "needs_user_input"
    )


def test_batch_status_still_classifies_normal_and_failed():
    from app.services.ai.tools.agent_delegate_tool import (
        EMPTY_DELEGATION_RESULT_MESSAGE,
        _batch_result_status,
    )

    assert _batch_result_status("子智能体返回的查询结果") == "completed"
    assert _batch_result_status("错误：子智能体执行超时") == "failed"
    assert _batch_result_status(EMPTY_DELEGATION_RESULT_MESSAGE) == "failed"


def test_batch_description_documents_no_resume():
    """批量入口不支持续聊，父必须知道回答后要用单次调用继续。"""
    from app.services.ai.tools.agent_delegate_tool import sub_agent_batch_call

    description = sub_agent_batch_call.description

    assert "sub_agent_call(resume=True)" in description
    assert "不支持续聊" in description


async def test_needs_user_input_result_names_the_agent_to_resume():
    """父必须能从工具结果里读出「该 resume 哪个子代理」。

    文案若是通用的一句「子智能体正在等待用户回答」，父在批量或多子代理场景下
    根本分不清是谁在等——一旦 resume 错对象，那个子代理会拿「用户回答了 X」
    当新任务从零重跑，给出的结论与真正提问的那个毫无关系。
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.core.context import set_agent_context
    from app.services.ai.tools.agent_delegate_tool import sub_agent_call
    from tests.ai.tools.test_delegation_session_resume import _delegation_env

    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        trace_buffer=[],
        user_id=7,
        conversation_id="conv-name-agent",
    )
    main_ctx.event_queue = asyncio.Queue()
    set_agent_context(main_ctx)

    async def mock_execute(history):
        yield _question_chunk()

    mock_executor = MagicMock()
    mock_executor.execute = mock_execute

    try:
        with _delegation_env(mock_executor), patch(
            "app.services.ai.user_question.persist_user_question_event", AsyncMock()
        ):
            text = await sub_agent_call.func(agent_name="chat-bi", query="查竞品数据")
    finally:
        set_agent_context(None)

    assert "等待用户回答" in text
    assert "chat-bi" in text or "chat_bi" in text, (
        f"工具结果必须点明是哪个子代理在等回答，实际是：{text}"
    )
    assert "resume=True" in text or "resume=true" in text


async def test_forwarded_card_carries_display_name_and_no_internal_instruction_leak():
    """转发给前端的卡片要带中文显示名，时间线日志不能出现「给模型看的指令」。"""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.core.context import set_agent_context
    from app.services.ai.tools.agent_delegate_tool import sub_agent_call
    from tests.ai.tools.test_delegation_session_resume import _delegation_env

    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        trace_buffer=[],
        user_id=7,
        conversation_id="conv-badge",
    )
    main_ctx.event_queue = asyncio.Queue()
    set_agent_context(main_ctx)

    async def mock_execute(history):
        yield _question_chunk()

    mock_executor = MagicMock()
    mock_executor.execute = mock_execute

    try:
        with _delegation_env(mock_executor), patch(
            "app.services.ai.user_question.persist_user_question_event", AsyncMock()
        ):
            await sub_agent_call.func(agent_name="chat-bi", query="查竞品数据")
    finally:
        set_agent_context(None)

    events = []
    while not main_ctx.event_queue.empty():
        events.append(main_ctx.event_queue.get_nowait())

    cards = [item for item in events if isinstance(item, dict) and item.get("type") == "user_question"]
    assert cards, "应当把提问卡转发到主聊天流"
    assert cards[0].get("source_agent_display_name"), (
        "卡片来源必须带显示名，否则前端只能显示英文 slug"
    )

    # 时间线日志是给用户看的；「本轮请勿重复提问」这类指令只该出现在给模型的工具结果里
    leaked = [
        item
        for item in events
        if isinstance(item, dict) and "本轮请勿重复提问" in str(item.get("details") or "")
    ]
    assert not leaked, f"时间线日志泄漏了内部指令：{leaked}"


def test_unattended_delivery_marker_reads_scheduled_and_subscription_flags():
    from app.services.ai.tool_nudge_policy import unattended_delivery_marker

    assert unattended_delivery_marker({"is_scheduled_task": True}) is True
    assert unattended_delivery_marker({"is_subscription_task": "true"}) is True
    assert unattended_delivery_marker({"quick_suggestions_forbidden": 1}) is True
    assert unattended_delivery_marker({"user_id": 7}) is False
    assert unattended_delivery_marker(None) is False


@pytest.mark.asyncio
async def test_scheduled_task_delegation_cannot_ask_user():
    """定时任务里的子代理不许弹提问卡：整条链路都没有在线用户。

    定时任务的 execution_options 原本不携带无人值守标记，子代理 options 又只有
    delegated_sub_agent=True，于是「无人值守就不许提问」的守卫被委派绕过——卡片被塞进
    一个没有消费者的队列，任务以「等待用户回答」收尾，15 分钟后过期，结果永久缺一块。
    """
    from app.core.context import set_execution_options_context

    set_execution_options_context({"delegated_sub_agent": True, "unattended": True})
    try:
        output = json.loads(
            await ask_user_question.ainvoke(
                _args(question="请问要查哪一天的?")
            )
        )
    finally:
        set_execution_options_context({})

    assert output["status"] == "error"
    assert "无人值守" in output["error"]


@pytest.mark.asyncio
async def test_interactive_delegation_can_still_ask_user():
    """交互式会话里的委派仍要放行（这是本次功能的入口）。"""
    from app.core.context import set_execution_options_context

    set_execution_options_context({"delegated_sub_agent": True})
    try:
        output = json.loads(
            await ask_user_question.ainvoke(
                _args(question="请问要查哪一天的?")
            )
        )
    finally:
        set_execution_options_context({})

    assert output["status"] == "awaiting_user"


def test_unattended_marker_is_forwarded_to_sub_agent_options():
    """父链路的无人值守标记要跟着委派走下去。"""
    source = (
        Path(__file__).resolve().parents[3]
        / "app/services/ai/tools/agent_delegate_tool.py"
    ).read_text(encoding="utf-8")

    assert 'sub_execution_options["unattended"] = True' in source


@pytest.mark.asyncio
async def test_partial_output_before_question_is_not_dropped():
    """子代理「先写半份结论、再问一句」时，那半份正文不能丢。

    丢了父既没法按 batch hint 的要求「先汇报已完成部分」，也不知道子代理做到哪一步。
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.ai.subagent_protocol import (
        NEEDS_USER_INPUT_SUB_AGENT_RESULT_MESSAGE,
    )
    from app.services.ai.tools.agent_delegate_tool import sub_agent_call
    from tests.ai.tools.test_delegation_session_resume import _delegation_env

    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-main",
        trace_buffer=[],
        user_id=7,
        conversation_id="conv-partial",
    )
    main_ctx.event_queue = asyncio.Queue()
    set_agent_context(main_ctx)

    async def mock_execute(history):
        yield {"type": "content", "content": "华东区域销售额 120 万，同比 +8%。"}
        yield _question_chunk()

    executor = MagicMock()
    executor.execute = mock_execute

    try:
        with _delegation_env(executor), patch(
            "app.services.ai.user_question.persist_user_question_event", AsyncMock()
        ):
            tool_text = await sub_agent_call.func(agent_name="chat-bi", query="查竞品数据")
    finally:
        set_agent_context(None)

    assert tool_text.startswith(NEEDS_USER_INPUT_SUB_AGENT_RESULT_MESSAGE)
    assert "华东区域销售额 120 万" in tool_text, "提问前已产出的正文被丢弃了"


def test_tool_description_states_delegation_option_bounds():
    """工具描述要写清委派场景的选项上限。

    schema 与平台提示词都允许 2-12 个选项，而委派校验只允许 6 个。子代理按 2-12
    规划、给了 8 个，会在校验处被打回；它很可能就此认为「不能提问」而放弃，
    用户再也收不到卡。
    """
    from app.services.ai.tools.user_question_tools import AskUserQuestionTool

    description = AskUserQuestionTool.description
    assert "2-4 个选项" in description
    assert "最多 6 个" in description
