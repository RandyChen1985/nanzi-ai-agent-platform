from typing import Optional

from apscheduler.triggers.cron import CronTrigger


def _parse_time(time_value: Optional[str]) -> tuple[int, int]:
    try:
        hour_text, minute_text = str(time_value or "").split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (TypeError, ValueError) as exc:
        raise ValueError("执行时间格式必须为 HH:mm") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("执行时间超出有效范围")
    return hour, minute


def schedule_to_cron(
    schedule_type: str,
    *,
    time_value: Optional[str] = None,
    weekday: Optional[int] = None,
    monthday: Optional[int] = None,
    cron_expr: Optional[str] = None,
) -> str:
    if schedule_type == "cron":
        expression = str(cron_expr or "").strip()
        try:
            CronTrigger.from_crontab(expression)
        except Exception as exc:
            raise ValueError("Cron 表达式无效") from exc
        return expression

    hour, minute = _parse_time(time_value)
    if schedule_type == "daily":
        return f"{minute} {hour} * * *"
    if schedule_type == "weekly":
        if weekday is None or not 0 <= weekday <= 6:
            raise ValueError("星期必须在 0 到 6 之间")
        return f"{minute} {hour} * * {weekday}"
    if schedule_type == "monthly":
        if monthday is None or not 1 <= monthday <= 31:
            raise ValueError("每月日期必须在 1 到 31 之间")
        return f"{minute} {hour} {monthday} * *"
    raise ValueError("不支持的订阅频率")
