"""标准 crontab 与 APScheduler 调度语义的兼容层。

nanzi 的 cron 表达式在数据库、前端可视化生成器（``cronScenes.ts``）、下次执行预览
（``cronSchedule.ts``）和 ``cronstrue`` 描述里一律遵循**标准 crontab**（vixie cron）
语义：星期字段 ``0``/``7`` = 周日，``1`` = 周一 … ``6`` = 周六。

但真正排期的 ``apscheduler.triggers.cron.CronTrigger`` 用的是 ``day_of_week`` 字段，
其基准是 ``0`` = 周一 … ``6`` = 周日（别名 ``mon``…``sun``）。直接把 crontab 交给
``CronTrigger.from_crontab()`` 会让「周一~周五」实际落在「周二~周六」，且周一漏跑。

本模块只重写星期字段，把它翻译成纯 APScheduler 别名（``mon``…``sun``），其余四段
原样透传。统一输出别名是为了消除三处实测差异：

* 数字基准不同 —— APScheduler 里 ``1`` 是周二，``7`` 直接越界报错；
* 数字与别名可混用但不会报错 —— ``1,mon`` 在 crontab 里是周一，在 APScheduler 里
  会变成「周二 + 周一」，属于静默偏移，比报错更难发现；
* 跨周范围 —— crontab 的 ``sun-mon`` 合法，APScheduler 会报
  ``minimum value in a range must not be higher than the maximum``。
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
_DAY_FIELD_INDEX = 2

#: 标准 crontab 取值 → APScheduler 内部数值（mon=0 … sun=6），用于判断能否写字符范围
_APSCHEDULER_WEEKDAY_ORDER = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}


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
        if base == "*":
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
    # 用字符范围时必须以 APScheduler 的数值顺序也连续为前提：它的 sun=6 > fri=4，
    # 一旦写成 `sun-fri` 会被判为「范围起点大于终点」。含周日的连续集合只能列出来。
    apscheduler_values = [_APSCHEDULER_WEEKDAY_ORDER[value] for value in ordered]
    is_consecutive = apscheduler_values == list(
        range(apscheduler_values[0], apscheduler_values[-1] + 1)
    )
    if is_consecutive and len(ordered) >= 3:
        return f"{names[0]}-{names[-1]}"
    return ",".join(names)


def _normalize_placeholder(field: str, expr: str) -> str:
    """``?`` 是 Quartz 的「不指定」，在 5 段 crontab 里等价于 ``*``。

    前端允许它只出现在「日」或「周」字段，且必须独占该字段（不接受 ``?/2``、``1,?``）。
    """
    text = field.strip()
    if "?" not in text:
        return text
    if text != "?":
        raise ValueError(f"Cron 表达式中的 ? 只能单独占一个字段：{expr!r}")
    return "*"


def to_apscheduler_crontab(expr: str) -> str:
    """把标准 5 段 crontab 转成 APScheduler 能正确排期的等价表达式。

    只改写星期字段与 ``?`` 占位符；``*``、步长、范围、别名等写法在转换后语义不变。
    表达式非法时抛 ``ValueError``，错误信息带原始表达式，便于前端直接展示。
    """
    text = str(expr or "").strip()
    parts = text.split()
    if len(parts) != 5:
        raise ValueError(f"Cron 表达式必须是 5 段（分 时 日 月 周），当前 {len(parts)} 段：{text!r}")

    fields = list(parts)
    fields[_DAY_FIELD_INDEX] = _normalize_placeholder(fields[_DAY_FIELD_INDEX], text)
    fields[_WEEKDAY_FIELD_INDEX] = _render_weekday_field(
        _expand_weekday_field(_normalize_placeholder(fields[_WEEKDAY_FIELD_INDEX], text), text)
    )
    return " ".join(fields)


def validate_standard_crontab(expr: str) -> None:
    """按平台口径校验一条 cron：必须是 5 段、字段合法、且「日」与「周」不同时限定。

    后者不是风格问题：标准 crontab 对「日 + 周」按 OR 匹配，而 APScheduler 按 AND，
    同时限定会让任务变成「两个条件都满足」才跑（例如「每月 1 号且是周二」），
    几乎不执行且不会报错。前端早已拦住，后端此前漏了这道校验。
    """
    converted = to_apscheduler_crontab(expr)
    text = str(expr or "").strip()
    parts = text.split()
    # 任务表 cron_expr 是 VARCHAR(50)，超长会在入库时抛数据库错误（500）；
    # 这里提前用更严格的长度拒掉，给出可读提示。
    if len(text) > 50:
        raise ValueError(f"Cron 表达式过长（{len(text)} 字符，最多 50）：{text!r}")
    day_field = parts[_DAY_FIELD_INDEX].strip()
    weekday_field = parts[_WEEKDAY_FIELD_INDEX].strip()
    if day_field not in ("*", "?") and weekday_field not in ("*", "?"):
        raise ValueError(
            f"Cron 表达式不支持同时限定「日」和「周」：标准 crontab 按 OR 匹配、"
            f"调度器按 AND 匹配，同时指定会几乎不执行，请只保留一个维度：{expr!r}"
        )

    from apscheduler.triggers.cron import CronTrigger

    try:
        CronTrigger.from_crontab(converted)
    except Exception as exc:
        raise ValueError(f"Cron 表达式无法被调度器解析：{exc}") from exc
