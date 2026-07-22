import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

import pytz

from app.services.ai.time_anchor import build_data_query_time_anchor_block, get_default_timezone

pytestmark = pytest.mark.no_infrastructure

TZ_NAME = "Asia/Shanghai"


def test_get_default_timezone_honors_tz_env(monkeypatch):
    get_default_timezone.cache_clear()
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    assert get_default_timezone() == "Asia/Shanghai"
    get_default_timezone.cache_clear()


def test_timezone_from_localtime_link_returns_iana_name():
    from app.services.ai.time_anchor import _timezone_from_localtime_link

    name = _timezone_from_localtime_link()
    if name is None:
        pytest.skip("/etc/localtime not available in this environment")
    ZoneInfo(name)


def test_build_data_query_time_anchor_block_uses_anchor_dates():
    tz = pytz.timezone(TZ_NAME)
    now = tz.localize(datetime(2026, 6, 10, 14, 30, 0))

    block = build_data_query_time_anchor_block(timezone=TZ_NAME, now=now)

    assert "[当前时间锚点]" in block
    assert "当前时刻：2026-06-10 14:30:00 星期三" in block
    assert "今天：2026-06-10" in block
    assert "昨天：2026-06-09" in block
    assert "本周（周一至今天）：2026-06-08 至 2026-06-10" in block
    assert "上周（周一至周日）：2026-06-01 至 2026-06-07" in block
    assert "本月/当月（月初至今天）：2026-06-01 至 2026-06-10" in block
    assert "本月完整自然月：2026-06-01 至 2026-06-30" in block
    assert "上月：2026-05-01 至 2026-05-31" in block
    assert "今年（年初至今天）：2026-01-01 至 2026-06-10" in block
    assert "去年：2025-01-01 至 2025-12-31" in block
    assert "禁止臆测年份或月份" in block


def test_build_data_query_time_anchor_block_year_boundary_month():
    tz = pytz.timezone(TZ_NAME)
    now = tz.localize(datetime(2026, 1, 5, 9, 0, 0))

    block = build_data_query_time_anchor_block(timezone=TZ_NAME, now=now)

    assert "上月：2025-12-01 至 2025-12-31" in block
    assert "本周（周一至今天）：2026-01-05 至 2026-01-05" in block


def test_resolve_near_days_expectation():
    from app.services.ai.time_anchor import resolve_relative_time_expectation

    tz = pytz.timezone(TZ_NAME)
    now = tz.localize(datetime(2026, 6, 10, 12, 0, 0))
    exp = resolve_relative_time_expectation("统计近7天活跃", timezone=TZ_NAME, now=now)
    assert exp is not None
    assert exp.label == "近7天"
    assert exp.start.isoformat() == "2026-06-04"
    assert exp.end.isoformat() == "2026-06-10"


def test_append_time_anchor_for_relative_question():
    from app.services.ai.time_anchor import append_time_anchor_for_user_question

    tz = pytz.timezone(TZ_NAME)
    now = tz.localize(datetime(2026, 6, 10, 12, 0, 0))
    out = append_time_anchor_for_user_question(
        "你是助手。",
        "帮我看近7天趋势",
        timezone=TZ_NAME,
        now=now,
    )
    assert out.startswith("[当前时间锚点]")
    assert "【本轮问题时间解读】「近7天」" in out
    assert "2026-06-04" in out
    assert "你是助手。" in out


def test_append_time_anchor_skips_plain_greeting():
    from app.services.ai.time_anchor import append_time_anchor_for_user_question

    base = "系统提示"
    assert append_time_anchor_for_user_question(base, "你好") == base


def test_resolve_relative_date_phrases_batch():
    from app.services.ai.time_anchor import resolve_relative_date_phrases

    tz = pytz.timezone(TZ_NAME)
    now = tz.localize(datetime(2026, 6, 10, 12, 0, 0))
    rows = resolve_relative_date_phrases(["近7天", "下周五"], timezone=TZ_NAME, now=now)
    assert rows[0]["status"] == "ok" and rows[0]["start"] == "2026-06-04"
    assert rows[1]["status"] == "ok" and rows[1]["label"] == "下五"
