from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytz

_WEEKDAYS_CN = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
TIME_RANGE_GATE_PREFIX = "[TIME_RANGE_GATE]"


@lru_cache(maxsize=1)
def get_default_timezone() -> str:
    """Return host IANA timezone for ChatBI time anchors (cached after first resolve)."""
    tz_env = (os.environ.get("TZ") or "").strip()
    if tz_env:
        candidate = tz_env.lstrip(":")
        if candidate:
            try:
                ZoneInfo(candidate)
                return candidate
            except ZoneInfoNotFoundError:
                pass

    local_tz = datetime.now().astimezone().tzinfo
    if local_tz is not None:
        key = getattr(local_tz, "key", None)
        if isinstance(key, str) and key:
            return key

    from_localtime = _timezone_from_localtime_link()
    if from_localtime:
        return from_localtime

    return "UTC"


def _timezone_from_localtime_link() -> str | None:
    """Resolve IANA timezone from /etc/localtime symlink (macOS/Linux)."""
    link = Path("/etc/localtime")
    if not link.exists():
        return None
    try:
        target = link.resolve(strict=False)
    except OSError:
        return None
    parts = target.parts
    if "zoneinfo" not in parts:
        return None
    idx = parts.index("zoneinfo")
    name = "/".join(parts[idx + 1 :])
    if not name:
        return None
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return None
    return name


def _coalesce_timezone(timezone: str | None) -> str:
    return timezone or get_default_timezone()


def _resolve_now(timezone: str, now: datetime | None) -> datetime:
    tz = pytz.timezone(timezone)
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return tz.localize(now)
    return now.astimezone(tz)


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _next_month_start(day: date) -> date:
    if day.month == 12:
        return day.replace(year=day.year + 1, month=1, day=1)
    return day.replace(month=day.month + 1, day=1)


def build_data_query_time_anchor_block(
    timezone: str | None = None,
    now: datetime | None = None,
) -> str:
    """Build a deterministic time anchor block for agents that need calendar grounding."""
    tz_name = _coalesce_timezone(timezone)
    resolved = _resolve_now(tz_name, now)
    today = resolved.date()
    weekday_str = _WEEKDAYS_CN[today.weekday()]
    now_str = resolved.strftime(f"%Y-%m-%d %H:%M:%S {weekday_str}")

    cal = _anchor_calendar(tz_name, now)
    yesterday = cal["yesterday"]
    week_start = cal["week_start"]
    last_week_start = cal["last_week_start"]
    last_week_end = cal["last_week_end"]
    month_start = cal["month_start"]
    month_end = cal["month_end"]
    last_month_start = cal["last_month_start"]
    last_month_end = cal["last_month_end"]
    year_start = cal["year_start"]
    last_year_start = cal["last_year_start"]
    last_year_end = cal["last_year_end"]

    rolling_lines = []
    for days in (7, 14, 30):
        start = today - timedelta(days=days - 1)
        rolling_lines.append(
            f"- 近{days}天/最近{days}天（含今天）：{start.isoformat()} 至 {today.isoformat()}"
        )

    return (
        "[当前时间锚点]\n"
        f"- 基准时区：{tz_name}\n"
        f"- 当前时刻：{now_str}\n"
        f"- 今天：{today.isoformat()}\n"
        f"- 明天：{cal['tomorrow'].isoformat()}\n"
        f"- 后天：{cal['day_after_tomorrow'].isoformat()}\n"
        f"- 昨天：{yesterday.isoformat()}\n"
        f"- 本周（周一至今天）：{week_start.isoformat()} 至 {today.isoformat()}\n"
        f"- 上周（周一至周日）：{last_week_start.isoformat()} 至 {last_week_end.isoformat()}\n"
        f"- 下周（周一至周日）：{cal['next_week_start'].isoformat()} 至 {cal['next_week_end'].isoformat()}\n"
        + "\n".join(rolling_lines)
        + "\n"
        f"- 本月/当月（月初至今天）：{month_start.isoformat()} 至 {today.isoformat()}\n"
        f"- 本月完整自然月：{month_start.isoformat()} 至 {month_end.isoformat()}\n"
        f"- 上月：{last_month_start.isoformat()} 至 {last_month_end.isoformat()}\n"
        f"- 今年（年初至今天）：{year_start.isoformat()} 至 {today.isoformat()}\n"
        f"- 去年：{last_year_start.isoformat()} 至 {last_year_end.isoformat()}\n"
        "\n"
        "【相对时间使用规则】\n"
        "1. 用户问题含相对时间时，必须使用以上锚点换算为具体 YYYY-MM-DD 起止日（含 API/SQL/MCP 参数），禁止臆测年份或月份。\n"
        "2. 「本月/当月」默认按「月初至今（含今天）」；若用户明确要求整月，使用「本月完整自然月」起止日。\n"
        "3. 已注入本锚点时，优先直接引用锚点日期；每轮用户问题内 get_current_time / resolve_relative_dates 最多各调用 1 次，禁止重复取时。\n"
        "4. ChatBI 执行 SQL 时，WHERE 须写出具体日期字面量，不得只写「本月」等模糊词。"
    )


@dataclass(frozen=True)
class RelativeTimeExpectation:
    """用户相对时间表述对应的期望日期区间。"""

    label: str
    start: date
    end: date


_EXPLICIT_ABSOLUTE_TIME_PATTERNS = (
    re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月"),  # 2025年5月
    re.compile(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}"),  # 2025-05-01 / 2025/5/1
    re.compile(r"\d{4}-\d{2}-\d{2}"),  # ISO 日期
)

_RELATIVE_TIME_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"最近\s*(\d+)\s*(?:天|日)"), "recent_days"),
    (re.compile(r"(?:近|过去)\s*(\d+)\s*(?:天|日)"), "near_days"),
    (re.compile(r"(?:今天|当日)"), "today"),
    (re.compile(r"(?:明天|明日)"), "tomorrow"),
    (re.compile(r"后天"), "day_after_tomorrow"),
    (re.compile(r"大后天"), "day_after_after_tomorrow"),
    (re.compile(r"昨天"), "yesterday"),
    (re.compile(r"前天"), "day_before_yesterday"),
    (re.compile(r"(?:上周|上星期)"), "last_week"),
    (re.compile(r"(?:本周|这周|本星期)"), "this_week"),
    (re.compile(r"(?:下周|下星期)"), "next_week"),
    (re.compile(r"(?:上月|上个月|上個月)"), "last_month"),
    (re.compile(r"(?:本月|当月|这个月)"), "this_month"),
    (re.compile(r"去年"), "last_year"),
    (re.compile(r"今年"), "this_year"),
)

# 用于判断用户是否提及相对时间（注入时间锚点、与绝对日期并存时仍识别相对词）
_RELATIVE_TIME_LANGUAGE = re.compile(
    r"(?:"
    r"今天|当日|昨天|前天|明天|明日|后天|大后天|"
    r"本周|这周|本星期|上周|上星期|下周|下星期|"
    r"本月|当月|这个月|上月|上个月|"
    r"今年|去年|"
    r"最近|近\s*\d+|过去\s*\d+|\d+\s*(?:天|日)(?!前)|"
    r"周[一二三四五六日天]|星期[一二三四五六日天]"
    r")"
)

_WEEKDAY_CN_TO_INDEX = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
}

_THIS_OR_NEXT_WEEKDAY = re.compile(
    r"(?P<prefix>本|这|下)(?:周|星期)(?P<wd>[一二三四五六日天])"
)

_SQL_DATE_LITERAL_PATTERNS = (
    re.compile(r"DATE\s+'(\d{4})-(\d{2})-(\d{2})'", re.IGNORECASE),
    re.compile(r"TO_DATE\s*\(\s*'(\d{4})-(\d{2})-(\d{2})'", re.IGNORECASE),
    re.compile(r"TO_DATE\s*\(\s*'(\d{4})/(\d{2})/(\d{2})'", re.IGNORECASE),
    re.compile(r"'(\d{4})-(\d{2})-(\d{2})'"),
    re.compile(r"'(\d{4})/(\d{2})/(\d{2})'"),
)


def has_explicit_absolute_time(text: str) -> bool:
    """用户问题是否已给出明确绝对年月日（此时不做相对时间门禁）。"""
    q = str(text or "").strip()
    if not q:
        return False
    return any(pattern.search(q) for pattern in _EXPLICIT_ABSOLUTE_TIME_PATTERNS)


def _anchor_calendar(
    timezone: str | None = None,
    now: datetime | None = None,
) -> dict[str, date]:
    tz_name = _coalesce_timezone(timezone)
    resolved = _resolve_now(tz_name, now)
    today = resolved.date()
    yesterday = today - timedelta(days=1)
    week_start = today - timedelta(days=today.weekday())
    last_week_end = week_start - timedelta(days=1)
    last_week_start = last_week_end - timedelta(days=6)
    month_start = _month_start(today)
    month_end = _next_month_start(today) - timedelta(days=1)
    last_month_end = month_start - timedelta(days=1)
    last_month_start = _month_start(last_month_end)
    year_start = today.replace(month=1, day=1)
    last_year_start = today.replace(year=today.year - 1, month=1, day=1)
    last_year_end = today.replace(year=today.year - 1, month=12, day=31)
    next_week_start = week_start + timedelta(days=7)
    next_week_end = next_week_start + timedelta(days=6)
    return {
        "today": today,
        "yesterday": yesterday,
        "day_before_yesterday": today - timedelta(days=2),
        "tomorrow": today + timedelta(days=1),
        "day_after_tomorrow": today + timedelta(days=2),
        "day_after_after_tomorrow": today + timedelta(days=3),
        "week_start": week_start,
        "last_week_start": last_week_start,
        "last_week_end": last_week_end,
        "next_week_start": next_week_start,
        "next_week_end": next_week_end,
        "month_start": month_start,
        "month_end": month_end,
        "last_month_start": last_month_start,
        "last_month_end": last_month_end,
        "year_start": year_start,
        "last_year_start": last_year_start,
        "last_year_end": last_year_end,
    }


def _weekday_on_or_after(anchor: date, weekday_index: int) -> date:
    """Return the first date on or after ``anchor`` with the given weekday (Mon=0)."""
    delta = (weekday_index - anchor.weekday()) % 7
    return anchor + timedelta(days=delta)


def _resolve_this_or_next_weekday(
    text: str,
    *,
    timezone: str | None = None,
    now: datetime | None = None,
) -> RelativeTimeExpectation | None:
    match = _THIS_OR_NEXT_WEEKDAY.search(str(text or ""))
    if not match:
        return None
    wd_char = match.group("wd")
    weekday_index = _WEEKDAY_CN_TO_INDEX.get(wd_char)
    if weekday_index is None:
        return None
    cal = _anchor_calendar(timezone, now)
    prefix = match.group("prefix")
    if prefix in {"本", "这"}:
        target = _weekday_on_or_after(cal["week_start"], weekday_index)
        return RelativeTimeExpectation(label=f"本{wd_char}", start=target, end=target)
    target = _weekday_on_or_after(cal["next_week_start"], weekday_index)
    return RelativeTimeExpectation(label=f"下{wd_char}", start=target, end=target)


def mentions_relative_time_language(text: str) -> bool:
    """用户文本是否包含相对时间表述（用于按需注入时间锚点）。"""
    q = str(text or "").strip()
    if not q:
        return False
    return bool(_RELATIVE_TIME_LANGUAGE.search(q))


def should_attach_time_anchor_block(user_question: str | None) -> bool:
    """是否在 system prompt 中附加【当前时间锚点】（泛化规则，非场景硬编码）。"""
    q = str(text or "").strip() if (text := user_question) else ""
    if not q:
        return False
    if mentions_relative_time_language(q):
        return True
    return resolve_relative_time_expectation(q) is not None


def append_time_anchor_for_user_question(
    system_content: str,
    user_question: str | None,
    *,
    timezone: str | None = None,
    now: datetime | None = None,
) -> str:
    """在需要时将时间锚点块前置到 system prompt（已含锚点时跳过）。"""
    base = str(system_content or "")
    if not should_attach_time_anchor_block(user_question):
        return base
    if "[当前时间锚点]" in base:
        return base
    block = build_data_query_time_anchor_block(timezone=timezone, now=now)
    extra = ""
    expectation = resolve_relative_time_expectation(
        str(user_question or ""),
        timezone=timezone,
        now=now,
    )
    if expectation is None:
        expectation = _resolve_this_or_next_weekday(
            str(user_question or ""),
            timezone=timezone,
            now=now,
        )
    if expectation is not None:
        extra = (
            f"\n【本轮问题时间解读】「{expectation.label}」"
            f"→ {expectation.start.isoformat()} 至 {expectation.end.isoformat()}\n"
        )
    return f"{block}{extra}\n\n{base}"


def resolve_relative_time_expectation(
    user_question: str,
    *,
    timezone: str | None = None,
    now: datetime | None = None,
) -> RelativeTimeExpectation | None:
    """从用户问题解析最主要的相对时间期望区间；无相对词或仅有绝对日期时返回 None。"""
    q = str(user_question or "").strip()
    if not q:
        return None
    if has_explicit_absolute_time(q) and not mentions_relative_time_language(q):
        return None

    weekday_exp = _resolve_this_or_next_weekday(q, timezone=timezone, now=now)
    if weekday_exp:
        return weekday_exp

    cal = _anchor_calendar(timezone, now)
    for pattern, kind in _RELATIVE_TIME_RULES:
        match = pattern.search(q)
        if not match:
            continue
        if kind in {"recent_days", "near_days"}:
            days = max(int(match.group(1)), 1)
            start = cal["today"] - timedelta(days=days - 1)
            label = f"近{days}天" if kind == "near_days" else f"最近{days}天"
            return RelativeTimeExpectation(label=label, start=start, end=cal["today"])
        if kind == "today":
            return RelativeTimeExpectation(label="今天", start=cal["today"], end=cal["today"])
        if kind == "tomorrow":
            day = cal["tomorrow"]
            return RelativeTimeExpectation(label="明天", start=day, end=day)
        if kind == "day_after_tomorrow":
            day = cal["day_after_tomorrow"]
            return RelativeTimeExpectation(label="后天", start=day, end=day)
        if kind == "day_after_after_tomorrow":
            day = cal["day_after_after_tomorrow"]
            return RelativeTimeExpectation(label="大后天", start=day, end=day)
        if kind == "yesterday":
            return RelativeTimeExpectation(label="昨天", start=cal["yesterday"], end=cal["yesterday"])
        if kind == "day_before_yesterday":
            day = cal["day_before_yesterday"]
            return RelativeTimeExpectation(label="前天", start=day, end=day)
        if kind == "last_week":
            return RelativeTimeExpectation(
                label="上周",
                start=cal["last_week_start"],
                end=cal["last_week_end"],
            )
        if kind == "this_week":
            return RelativeTimeExpectation(
                label="本周",
                start=cal["week_start"],
                end=cal["today"],
            )
        if kind == "next_week":
            return RelativeTimeExpectation(
                label="下周",
                start=cal["next_week_start"],
                end=cal["next_week_end"],
            )
        if kind == "last_month":
            return RelativeTimeExpectation(
                label="上月",
                start=cal["last_month_start"],
                end=cal["last_month_end"],
            )
        if kind == "this_month":
            return RelativeTimeExpectation(
                label="本月",
                start=cal["month_start"],
                end=cal["today"],
            )
        if kind == "last_year":
            return RelativeTimeExpectation(
                label="去年",
                start=cal["last_year_start"],
                end=cal["last_year_end"],
            )
        if kind == "this_year":
            return RelativeTimeExpectation(
                label="今年",
                start=cal["year_start"],
                end=cal["today"],
            )
    return None


def resolve_relative_date_phrases(
    phrases: list[str],
    *,
    timezone: str | None = None,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """将若干相对日期短语解析为起止日期（与 ChatBI 时间锚点同源逻辑）。"""
    results: list[dict[str, str]] = []
    for raw in phrases or []:
        phrase = str(raw or "").strip()
        if not phrase:
            continue
        exp = resolve_relative_time_expectation(phrase, timezone=timezone, now=now)
        if exp is None:
            results.append({"phrase": phrase, "status": "unresolved"})
            continue
        results.append(
            {
                "phrase": phrase,
                "status": "ok",
                "label": exp.label,
                "start": exp.start.isoformat(),
                "end": exp.end.isoformat(),
            }
        )
    return results


def extract_sql_date_literals(sql: str) -> list[date]:
    """从 SQL 文本中提取 YYYY-MM-DD / YYYY/MM/DD 字面量（忽略字符串内注释需 mask，此处用正则粗提取）。"""
    text = str(sql or "")
    if not text.strip():
        return []

    dates: list[date] = []
    seen: set[date] = set()
    for pattern in _SQL_DATE_LITERAL_PATTERNS:
        for match in pattern.finditer(text):
            try:
                parsed = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                continue
            if parsed not in seen:
                seen.add(parsed)
                dates.append(parsed)
    return dates


def detect_time_range_mismatch(
    user_question: str,
    sql: str,
    *,
    timezone: str | None = None,
    now: datetime | None = None,
) -> str:
    """
    检测 SQL 时间过滤是否与用户相对时间 + 当前时间锚点不一致。
    返回非空字符串表示应拦截并进入 repair；空字符串表示通过或未校验。
    """
    tz_name = _coalesce_timezone(timezone)
    expectation = resolve_relative_time_expectation(
        user_question,
        timezone=tz_name,
        now=now,
    )
    if expectation is None:
        return ""

    sql_dates = extract_sql_date_literals(sql)
    if not sql_dates:
        return ""

    sql_min = min(sql_dates)
    sql_max = max(sql_dates)
    if sql_max >= expectation.start and sql_min <= expectation.end:
        return ""

    expected_range = f"{expectation.start.isoformat()} 至 {expectation.end.isoformat()}"
    actual_range = f"{sql_min.isoformat()} 至 {sql_max.isoformat()}"
    if sql_min.year != expectation.start.year and sql_max.year != expectation.end.year:
        return (
            f"用户问题含相对时间「{expectation.label}」，按当前时间锚点应为 {expected_range}，"
            f"但 SQL 中日期字面量为 {actual_range}，年份与锚点不一致（常见错误：把「上月/本月」写成去年同月）。"
        )
    return (
        f"用户问题含相对时间「{expectation.label}」，按当前时间锚点应为 {expected_range}，"
        f"但 SQL 中日期字面量 {actual_range} 与该区间无交集。"
    )


def build_time_range_gate_message(reason: str) -> str:
    """构造统一的相对时间范围拦截消息（单源/联邦共用）。"""
    text = str(reason or "").strip()
    return (
        f"{TIME_RANGE_GATE_PREFIX} SQL 时间范围与用户相对时间不一致，已阻止执行：{text}\n"
        "请严格按【当前时间锚点】重写 WHERE 中的日期起止条件后再执行查询。"
    )
