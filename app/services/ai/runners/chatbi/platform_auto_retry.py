"""Platform-initiated SQL auto-retry trace and budget helpers."""

from __future__ import annotations

from app.services.ai.runners.chatbi.constants import MAX_PLATFORM_AUTO_SQL_RETRIES
from app.services.ai.runners.chatbi.run_state import DataRunState


def platform_auto_retry_budget_exhausted(state: DataRunState) -> bool:
    return state.platform_auto_sql_attempts >= MAX_PLATFORM_AUTO_SQL_RETRIES


def record_platform_auto_sql_attempt(state: DataRunState) -> int:
    state.platform_auto_sql_attempts += 1
    return state.platform_auto_sql_attempts


def format_platform_auto_retry_title(base_title: str, attempt: int) -> str:
    return f"{base_title}（平台自动重试 {attempt}/{MAX_PLATFORM_AUTO_SQL_RETRIES}）"


def format_platform_auto_retry_details(details: str, attempt: int) -> str:
    prefix = f"【平台自动 SQL 重试 {attempt}/{MAX_PLATFORM_AUTO_SQL_RETRIES}】"
    text = str(details or "").strip()
    if text.startswith("【平台"):
        return text
    return f"{prefix}\n{text}" if text else prefix


def platform_auto_retry_budget_exhausted_counter(counter: list[int]) -> bool:
    return bool(counter) and counter[0] >= MAX_PLATFORM_AUTO_SQL_RETRIES


def record_platform_auto_sql_attempt_counter(counter: list[int]) -> int:
    if not counter:
        counter.append(0)
    counter[0] += 1
    return counter[0]
