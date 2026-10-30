"""契约：瞬态事件（工具进度）不进会话事件日志。

两处都必须挡住，缺一处都会出问题：
* `conversation_stream_journal.append` 兜底（任何调用方都安全）；
* `chat.py` 的 `_record_stream_event` 提前返回——否则每条进度帧都会 `force` 一次
  正文合并窗口，事件日志被写得又碎又慢。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHAT = "app/api/v1/endpoints/chat.py"
JOURNAL = "app/services/ai/runtime/conversation_stream_journal.py"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_journal_filters_transient_types():
    source = _read(JOURNAL)

    assert 'TRANSIENT_EVENT_TYPES = frozenset({"tool_progress"})' in source
    assert "not in TRANSIENT_EVENT_TYPES" in source


def test_stream_recorder_returns_before_coalescing():
    source = _read(CHAT)
    body = source.split("async def _record_stream_event", 1)[1].split("async def _producer_task", 1)[0]

    transient_index = body.index("in TRANSIENT_EVENT_TYPES")
    coalesced_index = body.index("in STREAM_JOURNAL_COALESCED_TYPES")
    assert transient_index < coalesced_index, "瞬态判断必须在合并分支之前"
    assert "return" in body[transient_index:coalesced_index]
