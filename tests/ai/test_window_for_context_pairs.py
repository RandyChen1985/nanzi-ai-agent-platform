"""上下文窗口裁剪必须保护「提问——回答」配对。

提问（ask_user_question）轮的 assistant 正文往往为空，真正承载问题的是该轮的
工具转录；其后的用户回答是一条以「【用户回答】」开头的独立 user 消息。窗口按
消息边界从最新往回收时，切点可能正好落在这条 assistant 上，留下一条没有来由的
用户回答，模型只能重新猜问题。
"""

import pytest

from app.services.ai.context.compactor import window_for_context

pytestmark = pytest.mark.no_infrastructure

RECEIPT = (
    "【用户回答】\n"
    "interaction_type: question\n"
    "question_id: uq_1\n"
    'selected_option_ids: ["a"]\n'
    "custom_input: "
)


def _history(*, assistant_content: str, receipt: str = RECEIPT):
    return [
        {"role": "user", "content": "帮我配扣费规则" * 50},
        {"role": "assistant", "content": assistant_content},
        {"role": "user", "content": receipt},
    ]


def test_window_keeps_question_assistant_when_cut_lands_on_it():
    """切点落在承载提问的 assistant 上时，必须把它和回答一起保留。"""
    history = _history(assistant_content="我需要你补充确认：" + "封顶设多少？" * 60)

    window = window_for_context(history, max_context_messages=10, max_tokens=40)

    assert [m["role"] for m in window] == ["assistant", "user"]
    assert window[0]["content"].startswith("我需要你补充确认：")
    assert window[1]["content"].startswith("【用户回答】")


def test_window_keeps_pair_when_latest_receipt_alone_exceeds_budget():
    """最新一条回执本身已超预算时，兜底也不得把它前面的提问截掉。"""
    history = _history(assistant_content="我需要你补充确认：封顶设多少？")

    window = window_for_context(history, max_context_messages=10, max_tokens=1)

    assert [m["role"] for m in window] == ["assistant", "user"]


def test_window_does_not_keep_extra_message_for_plain_user_turn():
    """普通用户消息不受配对保护影响，不得额外多留一条。"""
    history = [
        {"role": "assistant", "content": "上一轮的回复" * 100},
        {"role": "user", "content": "普通追问"},
    ]

    window = window_for_context(history, max_context_messages=10, max_tokens=20)

    assert [m["role"] for m in window] == ["user"]
    assert window[0]["content"] == "普通追问"


def test_window_keeps_full_history_when_within_budget():
    """预算充足时不改变原有行为。"""
    history = _history(assistant_content="我需要你补充确认：封顶设多少？")

    window = window_for_context(history, max_context_messages=10, max_tokens=100000)

    assert window == history
