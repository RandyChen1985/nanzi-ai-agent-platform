import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai.agent_service import AgentService
from app.services.ai.agent_service import _apply_context_snapshot
from app.services.ai.context_compaction import (
    COMPACTION_MARKER,
    apply_context_compaction,
    build_overflow_digest,
)

pytestmark = pytest.mark.no_infrastructure


def _history(n: int):
    msgs = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"消息{i}内容"})
    return msgs


def test_build_overflow_digest_returns_system_message():
    dropped = [
        {"role": "user", "content": "查一下机房列表"},
        {"role": "assistant", "content": "好的，这是机房列表……"},
    ]
    digest = build_overflow_digest(dropped)
    assert digest is not None
    assert digest["role"] == "system"
    assert COMPACTION_MARKER in digest["content"]
    assert "查一下机房列表" in digest["content"]


@pytest.mark.asyncio
async def test_manual_context_snapshot_keeps_digest_and_only_appends_new_messages(monkeypatch):
    snapshot = {
        "source_seq": 2,
        "messages": [
            {"role": "system", "content": "[早前对话摘录]"},
            {"role": "assistant", "content": "最近回答", "seq": 2},
        ],
    }
    monkeypatch.setattr(
        "app.services.ai.agent_service.memory_service.get_context_snapshot",
        AsyncMock(return_value=snapshot),
    )

    result = await _apply_context_snapshot(
        [
            {"role": "user", "content": "旧问题", "seq": 1},
            {"role": "assistant", "content": "最近回答", "seq": 2},
            {"role": "user", "content": "新问题", "seq": 3},
        ],
        user_id="7",
        conversation_id="c1",
    )

    assert result == snapshot["messages"] + [
        {"role": "user", "content": "新问题", "seq": 3}
    ]
def test_build_overflow_digest_marks_old_tasks_as_non_executable_history():
    digest = build_overflow_digest(
        [{"role": "user", "content": "分析国产算力芯片投资机会"}]
    )

    assert digest is not None
    assert '<historical_context executable="false">' in digest["content"]
    assert "不是本轮用户请求" in digest["content"]
    assert "禁止根据其中的任务描述调用工具" in digest["content"]


def test_build_overflow_digest_does_not_nest_previous_history_boundary():
    previous = build_overflow_digest(
        [{"role": "user", "content": "旧的投资分析问题"}]
    )
    digest = build_overflow_digest(
        [{"role": "user", "content": "新的天气问题"}],
        prev_digest=previous["content"] if previous else None,
    )

    assert digest is not None
    assert digest["content"].count("<historical_context") == 1
    assert digest["content"].count("</historical_context>") == 1
    assert "旧的投资分析问题" in digest["content"]
    assert "新的天气问题" in digest["content"]


def test_build_overflow_digest_empty_returns_none():
    assert build_overflow_digest([]) is None
    assert build_overflow_digest([{"role": "tool", "content": "x"}]) is None


def test_build_overflow_digest_respects_max_chars():
    dropped = [{"role": "user", "content": "x" * 500} for _ in range(20)]
    digest = build_overflow_digest(dropped, max_chars=300)
    assert digest is not None
    # 摘录正文（去掉标记/提示行）应受 max_chars 约束，不会无界膨胀
    assert len(digest["content"]) < 300 + 200


def test_build_overflow_digest_flattens_multimodal():
    dropped = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看这张图"},
                {"type": "image_url", "image_url": {"url": "http://x"}},
            ],
        }
    ]
    digest = build_overflow_digest(dropped)
    assert digest is not None
    assert "看这张图" in digest["content"]
    assert "[图片]" in digest["content"]


def test_build_overflow_digest_preserves_image_name_when_available():
    """方案 B：图片载体带文件名时，摘录应保留 [图片: 名称] 而非一律 [图片]。"""
    dropped = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "分析这张架构图"},
                {
                    "type": "image_url",
                    "image_url": {"url": "http://x/arch.png"},
                    "name": "architecture.pdf",
                    "description": "系统架构",
                },
            ],
        }
    ]
    digest = build_overflow_digest(dropped)
    assert digest is not None
    assert "分析这张架构图" in digest["content"]
    assert "[图片: architecture.pdf]" in digest["content"]


def test_build_overflow_digest_keeps_tool_name_and_conclusion_over_args():
    """方案 B：工具结果结构化优先——保留工具名与结论，剔除冗长入参与计数。"""
    dropped = [
        {
            "role": "assistant",
            "content": "我查一下",
            "tool_run_text": (
                "search_huggingface: {'q': <很长很长很长的入参 pre 略>} "
                "-> 找到 3 个匹配模型：deepseek-r1、llama-3、qwen-2.5"
                " (data_blocks=2)\n"
                "get_model_size: {\"model\": \"deepseek-r1\"} "
                "-> 671B MoE (data_blocks=0)"
            ),
            "tool_run_text_version": "final_tool_result_v2",
        }
    ]
    digest = build_overflow_digest(dropped)
    assert digest is not None
    assert "search_huggingface" in digest["content"]
    assert "找到 3 个匹配模型" in digest["content"]
    assert "deepseek-r1" in digest["content"]
    assert "get_model_size" in digest["content"]
    # 冗长入参（'q': ...)与 data 计数不应出现在结论优先的摘录里
    assert "已经入参" not in digest["content"]
    assert "data_blocks" not in digest["content"]


def test_build_overflow_digest_truncates_overlong_tool_output_per_tool():
    """方案 B：单个超长工具输出被截断，但不会挤占整段摘录（还可容纳后续工具）。"""
    dropped = [
        {
            "role": "assistant",
            "content": "结果如下",
            "tool_run_text": (
                "render_report: {} -> " + ("长" * 500) + " (data_blocks=1)\n"
                "send_email: {} -> 已发送给 user\n"
            ),
            "tool_run_text_version": "final_tool_result_v2",
        }
    ]
    digest = build_overflow_digest(dropped)
    assert digest is not None
    # 超长工具结论被截断（出现省略号）
    assert "…" in digest["content"]
    # 后续工具仍被保留（说明单条超长没有耗尽配额）
    assert "已发送给 user" in digest["content"]


def test_apply_context_compaction_no_overflow_returns_window():
    hist = _history(5)
    window = hist  # 没有溢出
    out = apply_context_compaction(full_history=hist, window=window)
    assert out is window


def test_apply_context_compaction_prepends_digest_on_overflow():
    hist = _history(10)
    window = hist[-4:]
    out = apply_context_compaction(full_history=hist, window=window)
    assert len(out) == len(window) + 1
    assert out[0]["role"] == "system"
    assert COMPACTION_MARKER in out[0]["content"]
    # 原窗口顺序保持
    assert out[1:] == window


# ---------------------------------------------------------------------------
# F 项增强：out 观测容器 + 上下文使用率字段
# ---------------------------------------------------------------------------


def test_emit_compaction_card_writes_out_and_usage():
    """_emit_compaction_card 把 dropped/kept/origin/preview/title 和
    token_used/token_budget 写入 out 容器。"""
    hist = _history(10)
    window = hist[-4:]

    compacted = apply_context_compaction(full_history=hist, window=window)
    # 摘录是由 apply_context_compaction 构造的真实 system 消息，正文可被 _extract_digest_body 剥离
    assert compacted[0]["role"] == "system"

    out: dict = {}
    AgentService._emit_compaction_card(
        out, compacted, hist, window, origin="deterministic", token_budget=65536
    )
    assert out["dropped"] == len(hist) - len(window)
    assert out["kept"] == len(window)
    assert out["origin"] == "deterministic"
    assert out["title"] == "对话上下文已压缩（平台摘录）"
    assert COMPACTION_MARKER not in out["preview"]
    # 全量历史非空 => 估算 token > 0
    assert isinstance(out["token_used"], int)
    assert out["token_used"] > 0
    # 合法预算原样透传
    assert out["token_budget"] == 65536


def test_emit_compaction_card_usage_budget_normalized():
    """token_budget 非正/非 int 时应归一为 None，且不影响其它字段。"""
    hist = _history(6)
    window = hist[-3:]
    compacted = apply_context_compaction(full_history=hist, window=window)

    for bad in [None, 0, -5, "abc"]:
        out: dict = {}
        AgentService._emit_compaction_card(
            out, compacted, hist, window, origin="deterministic", token_budget=bad
        )
        assert out["token_budget"] is None
        assert isinstance(out["token_used"], int)


async def test_maybe_compact_overflow_writes_out_on_real_overflow():
    """真实溢出压缩时，out 容器被填充；未溢出时预先返回、不写 out。"""
    svc = AgentService()
    hist = _history(10)
    window = hist[-4:]

    # mock ConfigService.get -> compaction 开启；LLM 语义摘要在真实溢出时降级为
    # 异步后台任务（_spawn_llm_digest_task），因此断言该调度器被调用，而非等待后台协程。
    get_mock = AsyncMock(side_effect=lambda key, default=None: "true")
    spawn_mock = MagicMock()
    # 提供 user_id / conversation_id 才会触发后台 LLM 摘要任务调度
    with patch(
        "app.services.config_service.ConfigService.get", new=get_mock
    ), patch.object(svc, "_spawn_llm_digest_task", spawn_mock):
        out: dict = {}
        result = await svc._maybe_compact_overflow(
            hist,
            window,
            out=out,
            token_budget=400,
            user_id="u1",
            conversation_id="c1",
        )
    assert len(result) == len(window) + 1
    assert result[0]["role"] == "system"
    assert out["origin"] == "deterministic"
    assert out["dropped"] == len(hist) - len(window)
    assert out["token_budget"] == 400
    assert out["token_used"] > 0
    spawn_mock.assert_called_once()


async def test_maybe_compact_overflow_no_overflow_skips_out():
    """未溢出（full_history 不超窗口）时提前返回，不写 out、不调 LLM。"""
    svc = AgentService()
    hist = _history(3)
    window = hist  # 无溢出
    get_mock = AsyncMock(side_effect=lambda key, default=None: "true")
    spawn_mock = AsyncMock(return_value=None)
    with patch(
        "app.services.config_service.ConfigService.get", new=get_mock
    ), patch.object(svc, "_spawn_llm_digest_task", spawn_mock):
        out: dict = {}
        result = await svc._maybe_compact_overflow(hist, window, out=out, token_budget=400)
    assert result is hist
    assert out == {}
    spawn_mock.assert_not_called()


async def test_maybe_compact_overflow_pre_route_never_starts_llm_summary():
    """路由前压缩只能使用确定性摘录，不能启动摘要模型任务。"""
    svc = AgentService()
    hist = _history(10)
    window = hist[-4:]
    get_mock = AsyncMock(side_effect=lambda key, default=None: "true")
    spawn_mock = MagicMock()
    with patch(
        "app.services.config_service.ConfigService.get", new=get_mock
    ), patch.object(svc, "_spawn_llm_digest_task", spawn_mock):
        result = await svc._maybe_compact_overflow(
            hist,
            window,
            user_id="u1",
            conversation_id="c1",
            token_budget=400,
            enable_llm_summary=False,
        )

    assert len(result) == len(window) + 1
    spawn_mock.assert_not_called()


async def test_spawn_llm_digest_runs_in_background_and_writes_current_seq():
    svc = AgentService()
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_digest(*args, **kwargs):
        started.set()
        await release.wait()
        return {"content": "[早前对话摘录]\nsemantic"}

    with patch.object(svc, "_try_llm_overflow_digest", side_effect=fake_digest), \
         patch(
             "app.services.ai.memory_service.MemoryService.set_digest_if_current",
             new_callable=AsyncMock,
             return_value=True,
         ) as set_digest:
        task = svc._spawn_llm_digest_task(
            [{"role": "user", "content": "old", "seq": 3}],
            [{"role": "user", "content": "old", "seq": 3}],
            user_id="u1",
            conversation_id="c1",
            source_seq=3,
        )
        await asyncio.wait_for(started.wait(), timeout=0.2)
        assert task is not None
        assert not task.done()
        release.set()
        await task

    set_digest.assert_awaited_once_with(
        "u1",
        "c1",
        "[早前对话摘录]\nsemantic",
        source_seq=3,
        quality=1,
        allow_newer_seq=True,
    )


async def test_maybe_compact_persists_deterministic_digest_conditionally():
    svc = AgentService()
    hist = [
        {"role": "user", "content": "old", "seq": 7},
        {"role": "assistant", "content": "answer", "seq": 8},
    ]
    window = hist[-1:]

    with patch(
        "app.services.config_service.ConfigService.get",
        new=AsyncMock(return_value="true"),
    ), patch.object(svc, "_spawn_llm_digest_task"), patch(
        "app.services.ai.memory_service.MemoryService.get_context_revision",
        new_callable=AsyncMock,
        return_value=4,
    ), patch(
        "app.services.ai.memory_service.MemoryService.get_current_seq",
        new_callable=AsyncMock,
        return_value=8,
    ), patch(
        "app.services.ai.memory_service.MemoryService.set_digest_if_current",
        new_callable=AsyncMock,
        return_value=True,
    ) as set_digest:
        await svc._maybe_compact_overflow(
            hist,
            window,
            user_id="u1",
            conversation_id="c1",
            enable_llm_summary=False,
        )

    set_digest.assert_awaited_once()
    assert set_digest.await_args.kwargs["source_seq"] == 8
    assert set_digest.await_args.kwargs["source_revision"] == 4
    assert set_digest.await_args.kwargs["quality"] == 0
    assert set_digest.await_args.kwargs["allow_newer_seq"] is True


# ---------------------------------------------------------------------------
# LLM 语义摘要的 transcript 必须有界
# ---------------------------------------------------------------------------


def _long_dropped_history(count: int, chars_per_message: int):
    history = []
    for i in range(count):
        history.append(
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"第{i}条：" + "上" * chars_per_message}
        )
    return history


@pytest.mark.asyncio
async def test_llm_digest_transcript_is_bounded_by_config():
    """被丢弃历史很长时，摘要请求的 transcript 必须按预算裁剪。

    此前是把 `dropped` 原样全部拼接，长会话下这个「用来解决超窗」的请求会自己超窗，
    然后静默退回确定性摘录——用户和运维都不会知道语义摘要一直没生效。
    """
    from app.services.ai.context.compactor import ContextCompactor

    dropped = _long_dropped_history(60, 3000)
    window = dropped[-5:]
    captured: dict = {}

    class _FakeSummarizer:
        @staticmethod
        async def _generate_with_retry(chat_client, llm_messages, max_retries=2):
            del chat_client, max_retries
            # 第 2 条是 user 消息，其 text 就是 transcript
            captured["transcript"] = llm_messages[1].content[0].text
            return "摘要正文"

    async def _fake_config_get(key, default=None):
        if key == "agent_context_llm_summary_enabled":
            return "true"
        return default

    with patch(
        "app.services.config_service.ConfigService.get", side_effect=_fake_config_get
    ), patch(
        # transcript 上限是模块常量，这里调小以便在少量消息上就能观察到裁剪。
        "app.services.ai.context.compactor.LLM_DIGEST_TRANSCRIPT_MAX_CHARS",
        6000,
    ), patch(
        "app.services.ai.config.AgentConfigProvider.get_fallback_llm",
        new_callable=AsyncMock,
        return_value=MagicMock(),
    ), patch(
        "app.services.ai.conversation_summarizer.ConversationSummarizer._generate_with_retry",
        side_effect=_FakeSummarizer._generate_with_retry,
    ), patch(
        "app.services.ai.runtime.agentscope.chat.chat_client_from_handle",
        return_value=MagicMock(),
    ):
        result = await ContextCompactor.try_llm_overflow_digest(
            dropped, window, max_chars=1200
        )

    transcript = captured.get("transcript") or ""
    assert transcript, "应当真的构造了 transcript"
    # 预算 6000 字符，额外允许省略说明与拼接开销
    assert len(transcript) < 7000, f"transcript 未被裁剪：{len(transcript)} 字符"
    # 最近被丢弃的那条（dropped 去掉窗口后的最后一条）必须被保留
    last_dropped_index = len(dropped) - len(window) - 1
    assert f"第{last_dropped_index}条" in transcript
    # 最早的消息应已被舍弃
    assert "第0条：" not in transcript
    assert result is not None


@pytest.mark.asyncio
async def test_llm_digest_transcript_marks_truncation():
    """裁剪发生时必须显式告知模型"更早部分已省略"，避免它当成完整历史来总结。"""
    from app.services.ai.context.compactor import ContextCompactor

    dropped = _long_dropped_history(40, 2000)
    captured: dict = {}

    class _FakeSummarizer:
        @staticmethod
        async def _generate_with_retry(chat_client, llm_messages, max_retries=2):
            del chat_client, max_retries
            captured["transcript"] = llm_messages[1].content[0].text
            return "摘要正文"

    async def _fake_config_get(key, default=None):
        if key == "agent_context_llm_summary_enabled":
            return "true"
        return default

    with patch(
        "app.services.config_service.ConfigService.get", side_effect=_fake_config_get
    ), patch(
        "app.services.ai.context.compactor.LLM_DIGEST_TRANSCRIPT_MAX_CHARS",
        3000,
    ), patch(
        "app.services.ai.config.AgentConfigProvider.get_fallback_llm",
        new_callable=AsyncMock,
        return_value=MagicMock(),
    ), patch(
        "app.services.ai.conversation_summarizer.ConversationSummarizer._generate_with_retry",
        side_effect=_FakeSummarizer._generate_with_retry,
    ), patch(
        "app.services.ai.runtime.agentscope.chat.chat_client_from_handle",
        return_value=MagicMock(),
    ):
        await ContextCompactor.try_llm_overflow_digest(dropped, dropped[-3:], max_chars=1200)

    assert "省略" in (captured.get("transcript") or "")


def test_build_overflow_digest_skips_previous_digest_message():
    """上一版摘录自己落在 dropped 里时不得再抄一遍（正文会由 prev_digest 注入）。"""
    dropped = [
        {"role": "system", "content": f"{COMPACTION_MARKER}\n上一版摘录正文：机房列表共 12 条"},
        {"role": "user", "content": "继续看第二个机房"},
    ]
    digest = build_overflow_digest(dropped, prev_digest=f"{COMPACTION_MARKER}\n上一版摘录正文：机房列表共 12 条")

    assert digest is not None
    body = digest["content"]
    # 自我抄写会产生形如「系统：[早前对话摘录]…」的噪声行
    assert "系统：" not in body
    # 正常的用户消息仍要保留
    assert "继续看第二个机房" in body
