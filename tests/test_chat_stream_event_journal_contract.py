"""契约：聊天流 producer 必须把流式事件写入会话事件日志，并暴露增量拉取端点。"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

CHAT_ENDPOINT = Path(__file__).resolve().parents[1] / "app/api/v1/endpoints/chat.py"


def _source() -> str:
    return CHAT_ENDPOINT.read_text(encoding="utf-8")


def test_producer_writes_events_even_after_client_disconnect():
    source = _source()
    assert "conversation_stream_journal" in source
    # 写入必须独立于 client_disconnected_event 判定，否则断开后无日志可拉
    assert "await _flush_stream_journal(force=True)" in source
    assert "def _record_stream_event(" in source


def test_producer_coalesces_high_frequency_deltas():
    source = _source()
    assert "STREAM_JOURNAL_COALESCE_INTERVAL_SECONDS" in source
    assert "STREAM_JOURNAL_COALESCE_MAX_CHARS" in source
    assert "STREAM_JOURNAL_COALESCED_TYPES" in source


def test_stream_events_endpoint_reads_journal_after_cursor():
    source = _source()
    assert '"/conversation/{conversation_id}/stream-events"' in source
    assert "class ConversationStreamEventsResponse(BaseModel):" in source
    assert "after_seq: int = 0" in source


def test_stream_events_endpoint_reuses_run_status_auth_and_shape():
    source = _source()
    assert "async def get_conversation_stream_events(" in source
    assert "user_info: Dict[str, Any] = Depends(require_api_key)" in source
    assert "_require_chat_user_id(user_info)" in source
    assert "StandardResponse(data=ConversationStreamEventsResponse(**payload))" in source
    assert "run_active" in source


def test_producer_clears_previous_turn_journal_before_streaming():
    """每轮开跑前必须清空上一轮日志与序号计数器。

    否则 Redis 里会同时存着多轮事件：一旦续显游标为 0（新实例 / 刷新 / 快照被终态
    清掉），after_seq=0 会把上一轮的事件一并拉回来，被当成「本轮过程」重放出来，
    表现为界面上重复出现上一轮内容。
    """
    source = _source()
    assert "await conversation_stream_journal.clear(lane_user_id, conversation_id)" in source
