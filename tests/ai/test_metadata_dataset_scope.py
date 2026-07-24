import pytest

from app.services.ai.metadata_dataset_scope import (
    merge_request_metadata_dataset_ids,
    resolve_effective_metadata_dataset_ids,
)
from app.services.ai.context_manager import select_data_query_agent_id

pytestmark = pytest.mark.no_infrastructure


def test_request_wins_over_session():
    assert resolve_effective_metadata_dataset_ids(
        request_ids=["1", "2"],
        session_ids=["9"],
    ) == ["1", "2"]


def test_empty_request_falls_back_to_session():
    assert resolve_effective_metadata_dataset_ids(
        request_ids=[],
        session_ids=["9", "8"],
    ) == ["9", "8"]


def test_none_when_both_empty():
    assert resolve_effective_metadata_dataset_ids(
        request_ids=None,
        session_ids=[],
    ) is None


def test_normalizes_and_dedupes_preserve_order():
    assert resolve_effective_metadata_dataset_ids(
        request_ids=[" 1 ", "1", "2", ""],
        session_ids=["3"],
    ) == ["1", "2"]


def test_merge_request_metadata_dataset_ids_from_messages():
    messages = [
        {"role": "user", "content": "hello", "files": [{"type": "metadata_dataset", "url": "53,54"}]},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "query", "files": [{"type": "metadata_dataset", "url": "54,55"}]},
    ]
    assert merge_request_metadata_dataset_ids(request_ids=["53"], messages=messages) == ["53"]


def test_attachment_scope_uses_only_current_user_turn_when_request_ids_absent():
    messages = [
        {"role": "user", "content": "old", "files": [{"type": "metadata_dataset", "url": "53"}]},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "current", "files": [{"type": "metadata_dataset", "url": "54,55"}]},
    ]

    assert resolve_effective_metadata_dataset_ids(
        request_ids=None,
        session_ids=["9"],
        messages=messages,
    ) == ["54", "55"]


def test_selected_metadata_dataset_prefers_enabled_data_query_agent():
    agents = [
        type("Agent", (), {"id": "main", "is_enabled": True, "capabilities": ["chat"]})(),
        type("Agent", (), {"id": "bi", "is_enabled": True, "capabilities": ["data_query"]})(),
    ]

    assert select_data_query_agent_id(agents) == "bi"
