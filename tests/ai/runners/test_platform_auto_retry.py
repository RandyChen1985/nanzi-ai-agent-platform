"""Tests for platform auto-retry budget and trace helpers."""

import pytest

from app.services.ai.runners.chatbi.constants import MAX_PLATFORM_AUTO_SQL_RETRIES
from app.services.ai.runners.chatbi.platform_auto_retry import (
    format_platform_auto_retry_details,
    format_platform_auto_retry_title,
    platform_auto_retry_budget_exhausted,
    platform_auto_retry_budget_exhausted_counter,
    record_platform_auto_sql_attempt,
    record_platform_auto_sql_attempt_counter,
)
from app.services.ai.runners.chatbi.run_state import DataRunState


pytestmark = pytest.mark.no_infrastructure


def test_platform_auto_retry_budget_and_trace_on_state():
    state = DataRunState()
    assert not platform_auto_retry_budget_exhausted(state)

    for expected in range(1, MAX_PLATFORM_AUTO_SQL_RETRIES + 1):
        attempt = record_platform_auto_sql_attempt(state)
        assert attempt == expected
        title = format_platform_auto_retry_title("平台自动修正筛选并重试", attempt)
        assert f"{attempt}/{MAX_PLATFORM_AUTO_SQL_RETRIES}" in title

    assert platform_auto_retry_budget_exhausted(state)


def test_platform_auto_retry_counter_list_helpers():
    counter = [0]
    assert not platform_auto_retry_budget_exhausted_counter(counter)

    attempt = record_platform_auto_sql_attempt_counter(counter)
    assert attempt == 1
    details = format_platform_auto_retry_details("修正 SQL 完成", attempt)
    assert "【平台自动 SQL 重试 1/" in details
    assert "修正 SQL 完成" in details

    counter[0] = MAX_PLATFORM_AUTO_SQL_RETRIES
    assert platform_auto_retry_budget_exhausted_counter(counter)
