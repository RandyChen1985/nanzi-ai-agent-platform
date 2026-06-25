"""Tests for lightweight schema prefetch fatal probe."""

import pytest
from unittest.mock import MagicMock

from app.services.ai.runners.chatbi.schema_prefetch_fatal import (
    build_prefetch_fatal_probe_state,
    is_prefetch_schema_fatal,
)


pytestmark = pytest.mark.no_infrastructure


def test_is_prefetch_schema_fatal_without_full_apply():
    runner = MagicMock()
    runner._is_schema_service_unavailable.return_value = False
    runner._is_no_authorized_schema.return_value = False
    runner._is_rag_not_synced.return_value = False

    assert not is_prefetch_schema_fatal(runner, {"tables": []})

    runner._is_no_authorized_schema.return_value = True
    assert is_prefetch_schema_fatal(runner, {"error": "no auth"})

    runner._is_no_authorized_schema.return_value = False
    state = build_prefetch_fatal_probe_state(runner, {})
    assert state.schema_miss_count == 0
    assert not state.no_authorized_schema
