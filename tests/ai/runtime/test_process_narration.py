from app.services.ai.runtime.agentscope.event_stream import new_native_stream_state
from app.services.ai.runtime.agentscope.process_narration import (
    accumulate_visible_answer,
    agent_text_is_process_narration,
    extract_agent_answer_after_process_narration,
    on_model_call_end,
    on_model_call_start,
    on_text_delta,
    on_tool_call_start,
    on_tool_result_end,
)


def test_accumulate_visible_answer_persists_model_fallback_notice():
    warning = accumulate_visible_answer(
        "",
        {
            "type": "model_fallback",
            "content": "> ⚠️ fallback warning",
        },
    )
    assert warning == "> ⚠️ fallback warning"


def test_retraction_keeps_persisted_model_fallback_notice():
    warning = "> ⚠️ 主模型 `deepseek-v4-pro` 调用失败，本次回答由 fallback 模型 `gemma-4-31b` 生成。\n\n"
    full = accumulate_visible_answer("", {"type": "model_fallback", "content": warning})

    assert accumulate_visible_answer(
        full,
        {"type": "retraction", "content": "fallback answer"},
    ) == f"{warning.strip()}\n\nfallback answer"


def _types(events):
    return [event.get("type") for event in events]


def test_text_before_tool_stays_narration_and_never_enters_the_body():
    state = new_native_stream_state()
    events = []
    events.extend(on_model_call_start(state))
    events.extend(on_text_delta(state, "I'll search first."))
    events.extend(on_tool_call_start(state))

    assert _types(events) == ["process_narration", "process_narration_commit"]
    assert state["process_narration"] == "I'll search first."
    assert state["full_content"] == ""
    assert state["pending_reply_text"] == ""


def test_text_without_tools_promotes_to_answer_only_when_the_turn_ends():
    state = new_native_stream_state()
    events = []
    events.extend(on_model_call_start(state))
    events.extend(on_text_delta(state, "晋景新能综合分析报告"))
    events.extend(on_model_call_end(state))

    assert events == [
        {"type": "process_narration", "content": "晋景新能综合分析报告"},
        {"type": "process_narration_promote", "content": "晋景新能综合分析报告"},
    ]
    assert state["full_content"] == "晋景新能综合分析报告"
    assert state["process_narration"] == ""
    assert state["pending_reply_text"] == ""


def test_candidate_text_enters_body_immediately_without_promote():
    state = new_native_stream_state(candidate_answer_enabled=True)

    events = on_text_delta(state, "这是直接回答。")
    completion_events = on_model_call_end(state)

    assert events == [
        {
            "type": "answer_delta",
            "content": "这是直接回答。",
            "phase": "candidate",
        }
    ]
    assert completion_events == []
    assert state["full_content"] == "这是直接回答。"
    assert state["process_narration"] == ""
    assert state["pending_reply_text"] == ""


def test_candidate_text_is_retracted_then_committed_as_narration_before_tool():
    state = new_native_stream_state(candidate_answer_enabled=True)
    events = on_text_delta(state, "我先直接给出一个草稿。")
    events.extend(on_tool_call_start(state, tool_name="search_knowledge_base"))

    assert events == [
        {
            "type": "answer_delta",
            "content": "我先直接给出一个草稿。",
            "phase": "candidate",
        },
        {"type": "retraction", "content": "", "final": False},
        {"type": "process_narration", "content": "我先直接给出一个草稿。"},
        {"type": "process_narration_commit", "content": "我先直接给出一个草稿。"},
    ]
    assert state["full_content"] == ""
    assert state["process_narration"] == "我先直接给出一个草稿。"
    assert state["pending_reply_text"] == ""


def test_candidate_text_survives_bookkeeping_tool_without_retraction():
    state = new_native_stream_state(candidate_answer_enabled=True)
    events = on_text_delta(state, "这是无需检索的回答。")
    events.extend(on_tool_call_start(state, tool_name="todo_write"))
    events.extend(on_model_call_end(state))

    assert events == [
        {
            "type": "answer_delta",
            "content": "这是无需检索的回答。",
            "phase": "candidate",
        }
    ]
    assert state["full_content"] == "这是无需检索的回答。"
    assert state["process_narration"] == ""


def test_tool_then_final_answer_promotes_once_after_the_last_model_turn():
    state = new_native_stream_state()
    events = []
    events.extend(on_model_call_start(state))
    events.extend(on_text_delta(state, "Let me crawl a few more pages."))
    events.extend(on_tool_call_start(state))
    on_tool_result_end(state)
    events.extend(on_model_call_end(state))
    events.extend(on_model_call_start(state))
    events.extend(on_text_delta(state, "# 报告"))
    events.extend(on_text_delta(state, "\n正文"))
    events.extend(on_model_call_end(state))

    assert state["process_narration"] == "Let me crawl a few more pages."
    assert state["full_content"] == "# 报告\n正文"
    types = _types(events)
    assert types.count("process_narration") == 3
    assert types.count("process_narration_commit") == 1
    assert types.count("process_narration_promote") == 1
    assert types.count("answer_delta") == 0
    assert types.count("retraction") == 0


def test_post_tool_text_before_next_tool_stays_process_narration():
    state = new_native_stream_state()
    on_tool_call_start(state)
    on_tool_result_end(state)
    events = []
    events.extend(on_text_delta(state, "让我再搜一次。"))
    events.extend(on_tool_call_start(state))

    assert state["full_content"] == ""
    assert state["process_narration"] == "让我再搜一次。"
    assert events == [
        {"type": "process_narration", "content": "让我再搜一次。"},
        {"type": "process_narration_commit", "content": "让我再搜一次。"},
    ]


def test_agent_text_matching_narration_is_not_treated_as_body():
    state = new_native_stream_state()
    on_text_delta(state, "I'll help you search")
    on_tool_call_start(state)

    assert agent_text_is_process_narration(state, "I'll help you search") is True
    assert agent_text_is_process_narration(state, "# 晋景新能综合分析报告") is False


def test_agent_text_with_narration_prefix_only_reconciles_the_unseen_answer():
    state = new_native_stream_state()
    on_text_delta(state, "我先查询一下。")
    on_tool_call_start(state)

    assert extract_agent_answer_after_process_narration(
        state,
        "我先查询一下。\n\n# 查询结果\n正文",
    ) == "# 查询结果\n正文"


def test_post_tool_short_text_promotes_when_no_more_tools_follow():
    state = new_native_stream_state()
    on_tool_call_start(state)
    on_tool_result_end(state)
    events = on_text_delta(state, "# 报告")
    events.extend(on_model_call_end(state))

    assert state["full_content"] == "# 报告"
    assert events == [
        {"type": "process_narration", "content": "# 报告"},
        {"type": "process_narration_promote", "content": "# 报告"},
    ]
    assert agent_text_is_process_narration(state, "# 报告") is False


def test_whitespace_only_text_delta_does_not_create_process_narration():
    state = new_native_stream_state()

    assert on_text_delta(state, " \n\t\u200b") == []
    assert state["pending_reply_text"] == ""


def test_mid_stream_whitespace_delta_is_kept_until_promote():
    state = new_native_stream_state()
    events = []
    events.extend(on_text_delta(state, "上一段。"))
    events.extend(on_text_delta(state, "\n\n"))
    events.extend(on_text_delta(state, "下一段。"))
    events.extend(on_model_call_end(state))

    assert [event["type"] for event in events] == [
        "process_narration",
        "process_narration",
        "process_narration",
        "process_narration_promote",
    ]
    assert [event["content"] for event in events[:3]] == ["上一段。", "\n\n", "下一段。"]
    assert events[-1]["content"] == "上一段。\n\n下一段。"
    assert state["full_content"] == "上一段。\n\n下一段。"


def test_multi_block_narration_without_separator_reconciles_only_answer():
    """AgentState 块间无分隔拼接时，多块旁白也能被剥离，正文不重复补发。"""
    state = new_native_stream_state()
    on_text_delta(state, "我先查一下企业信息")
    on_tool_call_start(state)
    on_tool_result_end(state)
    on_text_delta(state, "让我再看看财务数据")
    on_tool_call_start(state)
    on_tool_result_end(state)

    # AgentState 把两个 TEXT_BLOCK 用 "".join 拼接（无 \n\n），而旁白以 \n\n 连接
    agent_text = "我先查一下企业信息让我再看看财务数据# 晋景新能综合分析报告\n正文"
    assert extract_agent_answer_after_process_narration(
        state,
        agent_text,
    ) == "# 晋景新能综合分析报告\n正文"


def test_multi_block_narration_without_answer_triggers_synthesis_not_replay():
    """多块旁白 + 最终无正文：剥离后为空，reconcile 不补发旁白，synthesis 兜底可触发。"""
    state = new_native_stream_state()
    on_text_delta(state, "我先查一下企业信息")
    on_tool_call_start(state)
    on_tool_result_end(state)
    on_text_delta(state, "让我再看看财务数据")
    on_tool_call_start(state)
    on_tool_result_end(state)

    agent_text = "我先查一下企业信息让我再看看财务数据"
    assert extract_agent_answer_after_process_narration(state, agent_text) == ""


def test_narration_prefix_with_crlf_and_blank_lines_is_stripped():
    """旁白与正文之间的 \r\n / 多余空行不阻塞剥离。"""
    state = new_native_stream_state()
    on_text_delta(state, "我先查一下")
    on_tool_call_start(state)
    on_tool_result_end(state)
    on_text_delta(state, "查完结果如下")
    on_tool_call_start(state)
    on_tool_result_end(state)

    assert extract_agent_answer_after_process_narration(
        state,
        "我先查一下查完结果如下\r\n\r\n\r\n正文",
    ) == "正文"


def test_narration_with_regex_special_chars_is_stripped():
    """旁白含正则特殊字符（括号/星号）时仍能安全剥离。"""
    state = new_native_stream_state()
    on_text_delta(state, "查一下 (2024) 的数据*")
    on_tool_call_start(state)
    on_tool_result_end(state)

    assert extract_agent_answer_after_process_narration(
        state,
        "查一下 (2024) 的数据*正文来了",
    ) == "正文来了"


def test_unrelated_agent_text_is_kept_verbatim():
    """旁白与 AgentState 文本完全对不上时，保持原样（不误删正文）。"""
    state = new_native_stream_state()
    on_text_delta(state, "我先查一下")
    on_tool_call_start(state)
    on_tool_result_end(state)

    assert extract_agent_answer_after_process_narration(
        state,
        "# 完全不同的正文",
    ) == "# 完全不同的正文"
