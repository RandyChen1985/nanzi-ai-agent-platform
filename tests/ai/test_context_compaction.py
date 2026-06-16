import pytest

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
