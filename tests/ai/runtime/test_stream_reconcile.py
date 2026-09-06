import pytest

from app.services.ai.runtime.agentscope.stream_reconcile import (
    build_tool_review_lines,
    collapse_repeated_reply,
    compute_stream_reconcile_gap,
    needs_tool_synthesis_fallback,
    truncate_for_context,
)

pytestmark = pytest.mark.no_infrastructure


def test_compute_gap_when_streamed_empty():
    assert compute_stream_reconcile_gap("", "完整回答") == "完整回答"


def test_compute_gap_prefix_extension():
    streamed = "让我尝试搜索："
    agent = "让我尝试搜索：最终结论如下。"
    assert compute_stream_reconcile_gap(streamed, agent) == "最终结论如下。"


def test_compute_gap_no_extra_when_equal():
    text = "同样的正文"
    assert compute_stream_reconcile_gap(text, text) == ""


def test_compute_gap_does_not_replay_when_only_whitespace_differs():
    streamed = "好的，根据已获取的公开信息，为您整理晋景新能。\n\n## 财务业绩"
    agent = "好的，根据已获取的公开信息，为您整理晋景新能。## 财务业绩"
    assert compute_stream_reconcile_gap(streamed, agent) == ""


def test_compute_gap_keeps_unique_suffix_when_whitespace_differs():
    streamed = "Hello world. More text here that is long."
    agent = "Hello world.\nMore text here that is long. UNIQUE CONCLUSION."
    gap = compute_stream_reconcile_gap(streamed, agent)
    assert "UNIQUE CONCLUSION" in gap
    assert "Hello world" not in gap


def test_compute_gap_does_not_replay_when_agent_text_wraps_the_same_answer():
    answer = "好的，根据已获取的公开信息，为您整理晋景新能 (01783.HK) 的最新情况。\n\n" + ("正文段落。" * 12)
    agent = "Let me fetch detailed reports." + answer.replace("\n\n", "")
    gap = compute_stream_reconcile_gap(answer, agent)
    assert "好的，根据已获取的公开信息" not in gap
    assert "正文段落" not in gap


def test_compute_gap_still_fills_when_streamed_is_a_short_stub():
    stub = "正在整理。"
    agent = "这是最终完整回答，包含足够长的结论与建议。" * 3
    assert compute_stream_reconcile_gap(stub, agent) == agent


def test_needs_synthesis_when_tools_returned_but_no_reply_exists():
    assert needs_tool_synthesis_fallback(
        "",
        "",
        used_tools=True,
        tool_outputs={"search": "结果"},
        min_complete_chars=32,
    )


def test_todo_write_is_not_a_synthesis_data_source():
    assert not needs_tool_synthesis_fallback(
        "",
        "",
        used_tools=True,
        tool_names={"todo-1": "todo_write"},
        tool_outputs={"todo-1": '{"todos":[{"content":"整理报告","status":"completed"}]}'},
    )


def test_business_tool_still_triggers_synthesis_when_todo_also_ran():
    assert needs_tool_synthesis_fallback(
        "",
        "",
        used_tools=True,
        tool_names={"todo-1": "todo_write", "search-1": "browser_read_visible"},
        tool_outputs={"todo-1": '{"todos":[]}', "search-1": "北京天气：晴"},
    )


def test_tool_review_lines_exclude_todo_write_output():
    assert build_tool_review_lines(
        {"todo-1": "todo_write", "search-1": "browser_read_visible"},
        {"todo-1": '{"todos":[]}', "search-1": "北京天气：晴"},
    ) == ["- browser_read_visible: 北京天气：晴"]


def test_tool_review_lines_exclude_non_final_tool_results():
    assert build_tool_review_lines(
        {"ok": "search", "failed": "search", "pending": "search"},
        {"ok": "最终数据", "failed": "失败文本", "pending": "中间输出"},
        tool_result_states={"ok": "success", "failed": "error"},
    ) == ["- search: 最终数据"]


def test_tool_review_lines_exclude_error_payload_with_success_state():
    assert build_tool_review_lines(
        {"call": "search"},
        {"call": '{"status":"error","message":"上游失败，订单数 100"}'},
        tool_result_states={"call": "success"},
    ) == []


def test_tool_review_lines_keep_legacy_payload_when_final_state_map_is_empty():
    assert build_tool_review_lines(
        {"call": "search"},
        {"call": "最终数据"},
        tool_result_states={},
    ) == ["- search: 最终数据"]


def test_no_synthesis_without_tools():
    assert not needs_tool_synthesis_fallback("", "", used_tools=False)


def test_synthesis_is_not_required_when_final_reply_exists():
    long_text = "这是一段足够长的最终回答，用于说明查询结果与后续建议。"
    assert not needs_tool_synthesis_fallback(
        long_text,
        long_text,
        used_tools=True,
        tool_outputs={"search": "结果"},
    )


def test_truncate_for_context():
    assert truncate_for_context("x" * 100, max_len=20).endswith("[输出已截断]")


def test_synthesis_is_not_required_for_any_streamed_text_after_tools():
    transition = "企业信息查询需要实时数据，让我尝试通过搜索来获取："
    assert not needs_tool_synthesis_fallback(
        transition,
        transition,
        used_tools=True,
        tool_outputs={"search": "结果"},
        min_complete_chars=32,
    )


def test_collapse_repeated_reply_exact_duplicate_halves():
    block = "### 核心结论\n" + ("业务洞察内容 " * 30)
    duplicated = block + "\n\n" + block
    collapsed = collapse_repeated_reply(duplicated)
    assert collapsed.strip() == block.strip()


def test_collapse_repeated_reply_keeps_unique_content():
    text = "唯一回答 " * 20
    assert collapse_repeated_reply(text) == text


def test_compute_gap_no_replay_when_agent_just_prepends_narration_to_streamed_body():
    # 工具环：AgentState 把过程旁白（“两个数据都拿到了…”）拼在最终正文前，
    # 而 streamed 已从正文中部开始展示、最终正文全部在流式阶段发出（agent 以 streamed 结尾）。
    body = "全部完成！以下是最终汇总：\n\n## 结论\n" + ("正文段落内容 " * 30)
    streamed = "整理中..." + body
    agent = "两个数据都拿到了。让我获取更多数据。\n\n文件已存在，先读取。\n\n" + streamed
    assert compute_stream_reconcile_gap(streamed, agent) == ""


def test_compute_gap_still_emits_real_suffix_even_when_prefix_narration_exists():
    # 即使 agent 用旁白作前缀，只要尾部相对 streamed 确有新增，仍应补发该新增。
    streamed = "开头正文。结论已经给出。"
    agent = "让我先查询。\n\n" + streamed + "\n\n补充：数据更新时间为 09:00。"
    gap = compute_stream_reconcile_gap(streamed, agent)
    assert gap == "\n\n补充：数据更新时间为 09:00。"
