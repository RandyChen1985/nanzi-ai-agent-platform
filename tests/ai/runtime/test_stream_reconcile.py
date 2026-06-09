import pytest

from app.services.ai.runtime.agentscope.stream_reconcile import (
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


def test_needs_synthesis_when_tools_and_short_reply():
    assert needs_tool_synthesis_fallback(
        "短句",
        "短句",
        used_tools=True,
        min_complete_chars=32,
    )


def test_no_synthesis_without_tools():
    assert not needs_tool_synthesis_fallback("", "", used_tools=False)


def test_no_synthesis_when_reply_long_enough():
    long_text = "这是一段足够长的最终回答，用于说明查询结果与后续建议。"
    assert not needs_tool_synthesis_fallback(long_text, long_text, used_tools=True)


def test_truncate_for_context():
    assert truncate_for_context("x" * 100, max_len=20).endswith("[输出已截断]")


def test_synthesis_for_transitional_sentence_after_tools():
    transition = "企业信息查询需要实时数据，让我尝试通过搜索来获取："
    assert needs_tool_synthesis_fallback(
        transition,
        transition,
        used_tools=True,
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
