"""标准 crontab 与 APScheduler 调度语义的兼容层。

nanzi 的 cron 表达式在数据库、前端可视化生成器（``cronScenes.ts``）、下次执行预览
（``cronSchedule.ts``）和 ``cronstrue`` 描述里一律遵循**标准 crontab**（vixie cron）
语义：星期字段 ``0``/``7`` = 周日，``1`` = 周一 … ``6`` = 周六。

但真正排期的 ``apscheduler.triggers.cron.CronTrigger`` 用的是 ``day_of_week`` 字段，
其基准是 ``0`` = 周一 … ``6`` = 周日（别名 ``mon``…``sun``）。直接把 crontab 交给
``CronTrigger.from_crontab()`` 会让「周一~周五」实际落在「周二~周六」，且周一漏跑。

本模块只重写星期字段，把它翻译成 APScheduler 的别名写法（别名与数字不会混用，
因此也规避了 APScheduler「numeric and symbolic 混用」的报错），其余四段原样透传。
"""

from typing import Iterable, Set

#: 标准 crontab 星期别名 → 标准取值（0 = 周日）
_STANDARD_WEEKDAY_ALIASES = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}

#: 标准 crontab 取值 → APScheduler 星期别名
_APSCHEDULER_WEEKDAY_NAMES = {
    0: "sun",
    1: "mon",
    2: "tue",
    3: "wed",
    4: "thu",
    5: "fri",
    6: "sat",
}

_WEEKDAY_FIELD_INDEX = 4


def _parse_weekday_value(token: str, expr: str) -> int:
    """解析单个星期取值，保留标准 crontab 的原始数字（其中 7 视为周日）。"""
    text = token.strip().lower()
    if not text:
        raise ValueError(f"Cron 星期字段存在空取值：{expr!r}")
    if text in _STANDARD_WEEKDAY_ALIASES:
        return _STANDARD_WEEKDAY_ALIASES[text]
    if text.isdigit():
        value = int(text)
        if 0 <= value <= 7:
            return value
        raise ValueError(f"Cron 星期取值需在 0-7（0/7 为周日）之间，实际为 {value!r}：{expr!r}")
    raise ValueError(f"Cron 星期取值「{token}」无法识别：{expr!r}")


def _normalize_weekday(value: int) -> int:
    """把 7 折叠回 0（周日），使集合内取值落在 0-6。"""
    return 0 if value == 7 else value


def _expand_weekday_field(field: str, expr: str) -> Set[int]:
    """把星期字段展开为标准 crontab 取值集合（0 = 周日）。"""
    values: Set[int] = set()
    for chunk in field.split(","):
        part = chunk.strip()
        if not part:
            raise ValueError(f"Cron 星期字段存在空列表项：{expr!r}")

        base, separator, step_text = part.partition("/")
        step = 1
        if separator:
            if not step_text.strip().isdigit() or int(step_text) <= 0:
                raise ValueError(f"Cron 星期步长「{step_text}」不合法：{expr!r}")
            step = int(step_text)

        base = base.strip()
        if base in ("*", "?"):
            start, end = 0, 7
        elif "-" in base:
            left, _, right = base.partition("-")
            start = _parse_weekday_value(left, expr)
            end = _parse_weekday_value(right, expr)
        else:
            start = _parse_weekday_value(base, expr)
            # `a/n` 在标准 crontab 中表示从 a 到最大值按步长取
            end = 7 if separator else start

        if start > end:
            raise ValueError(f"Cron 星期范围起点不能大于终点（{base}）：{expr!r}")
        values.update(_normalize_weekday(value) for value in range(start, end + 1, step))

    if not values:
        raise ValueError(f"Cron 星期字段没有可用取值：{expr!r}")
    return values


def _render_weekday_field(values: Iterable[int]) -> str:
    ordered = sorted(set(values))
    if len(ordered) == 7:
        return "*"
    names = [_APSCHEDULER_WEEKDAY_NAMES[value] for value in ordered]
    is_consecutive = ordered == list(range(ordered[0], ordered[-1] + 1))
    if is_consecutive and len(ordered) >= 3:
        return f"{names[0]}-{names[-1]}"
    return ",".join(names)


def to_apscheduler_crontab(expr: str) -> str:
    """把标准 5 段 crontab 转成 APScheduler 能正确排期的等价表达式。

    只改写星期字段；``*``/``?``、步长、范围、别名等写法在转换后语义不变。
    表达式非法时抛 ``ValueError``，错误信息带原始表达式，便于前端直接展示。
    """
    text = str(expr or "").strip()
    parts = text.split()
    if len(parts) != 5:
        raise ValueError(f"Cron 表达式必须是 5 段（分 时 日 月 周），当前 {len(parts)} 段：{text!r}")

    fields = list(parts)
    fields[_WEEKDAY_FIELD_INDEX] = _render_weekday_field(
        _expand_weekday_field(fields[_WEEKDAY_FIELD_INDEX], text)
    )
    return " ".join(fields)


def build_cron_trigger(expr: str, timezone=None):
    """按标准 crontab 语义构造 APScheduler 的 ``CronTrigger``。"""
    from apscheduler.triggers.cron import CronTrigger

    return CronTrigger.from_crontab(to_apscheduler_crontab(expr), timezone=timezone)
