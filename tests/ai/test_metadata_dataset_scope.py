import pytest

from app.services.ai.metadata_dataset_scope import resolve_effective_metadata_dataset_ids

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
