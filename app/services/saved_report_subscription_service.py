from dataclasses import dataclass
from typing import Any, Optional

from app.services.cron_compat import validate_standard_crontab


@dataclass(frozen=True)
class AlertEvaluation:
    hit: bool
    evidence: dict[str, Any]
    next_state: dict[str, Any]


def _result_rows(snapshot: Any) -> list[dict[str, Any]]:
    if isinstance(snapshot, list):
        return [row for row in snapshot if isinstance(row, dict)]
    if not isinstance(snapshot, dict):
        return []
    for key in ("rows", "data", "result", "items", "records"):
        value = snapshot.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = _result_rows(value)
            if nested:
                return nested
    return []


def _compare(actual: float, operator: str, expected: float) -> bool:
    return {
        ">": actual > expected,
        ">=": actual >= expected,
        "<": actual < expected,
        "<=": actual <= expected,
        "==": actual == expected,
        "!=": actual != expected,
    }.get(operator, False)


def evaluate_alert_condition(
    condition: Optional[dict[str, Any]],
    result_snapshot: Any,
    previous_state: Optional[dict[str, Any]],
) -> AlertEvaluation:
    """Evaluate versioned report alerts and return auditable trigger evidence."""
    spec = dict(condition or {})
    state = dict(previous_state or {})
    condition_type = str(spec.get("type") or "always")
    rows = _result_rows(result_snapshot) if isinstance(result_snapshot, dict) else _result_rows(result_snapshot)
    if condition_type == "always":
        return AlertEvaluation(True, {"condition_type": "always", "row_count": len(rows)}, state)
    if condition_type == "no_data":
        raw_hit = not rows
        evidence = {"condition_type": condition_type, "row_count": len(rows)}
        next_state = {**state, "last_row_count": len(rows)}
    else:
        field = str(spec.get("field") or "").strip()
        values = [float(row[field]) for row in rows if field in row and isinstance(row[field], (int, float))]
        actual = values[-1] if values else None
        expected = float(spec.get("value") or 0)
        operator = str(spec.get("operator") or ">=")
        if condition_type == "rate_of_change":
            baseline = state.get("last_value")
            change = None if baseline in (None, 0) or actual is None else round((actual - float(baseline)) / abs(float(baseline)) * 100, 4)
            raw_hit = change is not None and _compare(change, operator, expected)
            evidence = {
                "condition_type": condition_type, "field": field, "value": actual,
                "baseline": baseline, "change_percent": change, "operator": operator, "threshold": expected,
            }
        else:
            raw_hit = actual is not None and _compare(actual, operator, expected)
            evidence = {
                "condition_type": condition_type, "field": field, "value": actual,
                "operator": operator, "threshold": expected,
            }
        next_state = {**state, "last_value": actual}
    required_hits = max(1, int(spec.get("consecutive_hits") or 1))
    consecutive = int(state.get("consecutive_hits") or 0) + 1 if raw_hit else 0
    next_state["consecutive_hits"] = consecutive
    hit = bool(raw_hit and consecutive >= required_hits)
    evidence.update({"raw_hit": bool(raw_hit), "consecutive_hits": consecutive, "required_hits": required_hits, "hit": hit})
    return AlertEvaluation(hit, evidence, next_state)


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
            validate_standard_crontab(expression)
        except Exception as exc:
            raise ValueError(f"Cron 表达式无效：{exc}") from exc
        return expression

    hour, minute = _parse_time(time_value)
    if schedule_type == "daily":
        return f"{minute} {hour} * * *"
    if schedule_type == "weekly":
        # weekday 采用标准 crontab 语义：0 = 周日，1 = 周一 … 6 = 周六
        if weekday is None or not 0 <= weekday <= 6:
            raise ValueError("星期必须在 0 到 6 之间（0 为周日）")
        return f"{minute} {hour} * * {weekday}"
    if schedule_type == "monthly":
        if monthday is None or not 1 <= monthday <= 31:
            raise ValueError("每月日期必须在 1 到 31 之间")
        return f"{minute} {hour} {monthday} * *"
    raise ValueError("不支持的订阅频率")
