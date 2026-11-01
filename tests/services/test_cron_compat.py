"""标准 crontab 语义 ↔ APScheduler 调度语义的兼容契约。

nanzi 的 cron 表达式（DB、前端可视化生成器、预览、cronstrue 描述）一律按标准
crontab：星期 0/7 = 周日，1 = 周一 … 6 = 周六。APScheduler 的 day_of_week 是
0 = 周一 … 6 = 周日。任何直接 `CronTrigger.from_crontab(expr)` 都会让
「周一~周五」变成「周二~周六」。本文件锁定转换后的真实触发日。
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.cron_compat import to_apscheduler_crontab, validate_standard_crontab

pytestmark = pytest.mark.no_infrastructure

TZ = "Asia/Shanghai"
# 2025-01-05 是周日，作为扫描起点
BASE = datetime(2025, 1, 5, 0, 0, tzinfo=ZoneInfo(TZ))
WEEKDAY_CN = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]


def _fire_weekdays(expr: str, count: int = 14) -> list[str]:
    """返回前 count 次触发的星期（按标准 crontab 命名），用于断言真实执行日。"""
    from apscheduler.triggers.cron import CronTrigger

    trigger = CronTrigger.from_crontab(to_apscheduler_crontab(expr), timezone=TZ)
    prev = BASE - timedelta(minutes=1)
    cur = BASE - timedelta(minutes=1)
    days: list[str] = []
    for _ in range(count):
        nxt = trigger.get_next_fire_time(prev, cur)
        assert nxt is not None
        days.append(WEEKDAY_CN[(nxt.weekday() + 1) % 7])
        prev = cur = nxt
    return days


def test_numeric_weekday_range_is_shifted_to_mon_fri():
    # 标准 crontab 1-5 = 周一…周五；APScheduler 原生会解析成周二…周六
    assert to_apscheduler_crontab("0 8 * * 1-5") == "0 8 * * mon-fri"
    assert _fire_weekdays("0 8 * * 1-5", 7) == [
        "周一", "周二", "周三", "周四", "周五", "周一", "周二",
    ]


def test_numeric_weekday_range_saturday_is_not_scheduled():
    days = _fire_weekdays("0 8 * * 1-5", 30)
    assert "周六" not in days
    assert "周日" not in days
    assert "周一" in days


def test_weekend_scene_maps_to_saturday_and_sunday():
    assert to_apscheduler_crontab("0 8 * * 0,6") == "0 8 * * sun,sat"
    days = set(_fire_weekdays("0 8 * * 0,6", 6))
    assert days == {"周六", "周日"}


def test_single_weekday_number_is_sunday_based():
    assert to_apscheduler_crontab("0 8 * * 1") == "0 8 * * mon"
    assert _fire_weekdays("0 8 * * 1", 3) == ["周一", "周一", "周一"]
    assert to_apscheduler_crontab("0 8 * * 5") == "0 8 * * fri"
    assert _fire_weekdays("0 8 * * 5", 2) == ["周五", "周五"]


def test_seven_is_accepted_as_sunday():
    # 标准 crontab 允许 7 表示周日；APScheduler 自身会因超过 6 报错
    assert to_apscheduler_crontab("0 8 * * 7") == "0 8 * * sun"
    assert _fire_weekdays("0 8 * * 7", 2) == ["周日", "周日"]


def test_wildcard_and_full_range_stay_wildcard():
    assert to_apscheduler_crontab("0 8 * * *") == "0 8 * * *"
    assert to_apscheduler_crontab("0 8 * * 0-6") == "0 8 * * *"
    assert to_apscheduler_crontab("0 8 * * 0-7") == "0 8 * * *"


def test_symbolic_names_are_kept_as_standard_crontab_weekdays():
    assert to_apscheduler_crontab("0 8 * * mon-fri") == "0 8 * * mon-fri"
    # 取值集合会按标准顺序归一，避免同一语义出现多种写法
    assert to_apscheduler_crontab("0 8 * * SAT,SUN") == "0 8 * * sun,sat"


def test_mixed_numeric_and_symbolic_does_not_break_apscheduler():
    # APScheduler 禁止数字与别名混用，转换后统一成别名
    assert to_apscheduler_crontab("0 8 * * mon,2") == "0 8 * * mon,tue"
    assert _fire_weekdays("0 8 * * 1,wed", 4) == ["周一", "周三", "周一", "周三"]


def test_step_expression_uses_sunday_based_origin():
    # 标准 crontab */2 = 周日,周二,周四,周六
    assert to_apscheduler_crontab("0 8 * * */2") == "0 8 * * sun,tue,thu,sat"
    assert set(_fire_weekdays("0 8 * * */2", 8)) == {"周日", "周二", "周四", "周六"}


def test_range_with_step_is_expanded():
    assert to_apscheduler_crontab("0 8 * * 1-5/2") == "0 8 * * mon,wed,fri"
    assert _fire_weekdays("0 8 * * 1-5/2", 6) == ["周一", "周三", "周五", "周一", "周三", "周五"]


def test_ranges_starting_at_sunday_must_not_use_symbolic_range():
    """APScheduler 的 sun=6 > fri=4，写成 sun-fri 会被它判为非法范围。

    因此凡是「含周日且与其它天连续」的集合只能输出逗号列表。
    """
    assert to_apscheduler_crontab("0 8 * * 1-5,0") == "0 8 * * sun,mon,tue,wed,thu,fri"
    assert to_apscheduler_crontab("0 8 * * 0-5") == "0 8 * * sun,mon,tue,wed,thu,fri"
    assert to_apscheduler_crontab("0 8 * * 0,1,2") == "0 8 * * sun,mon,tue"
    assert set(_fire_weekdays("0 8 * * 1-5,0", 8)) == {"周日", "周一", "周二", "周三", "周四", "周五"}
    # 不含周日的连续区间仍保留可读的范围写法
    assert to_apscheduler_crontab("0 8 * * 1-5") == "0 8 * * mon-fri"


def test_question_mark_in_day_field_behaves_like_wildcard():
    """前端允许「日」或「周」单独用 ?（Quartz 的“不指定”），后端要能认。"""
    assert to_apscheduler_crontab("0 9 ? * *") == "0 9 * * *"
    assert to_apscheduler_crontab("0 9 ? * 1") == "0 9 * * mon"
    assert to_apscheduler_crontab("0 9 ? * ?") == "0 9 * * *"
    assert _fire_weekdays("0 9 ? * 1", 2) == ["周一", "周一"]


def test_question_mark_cannot_carry_a_step():
    # 与前端校验同口径：? 只能单独使用
    for expr in ("0 9 ?/2 * *", "0 9 * * ?/2"):
        with pytest.raises(ValueError):
            to_apscheduler_crontab(expr)


@pytest.mark.parametrize(
    "expr",
    [
        "0 9 1 * 1",       # 日 + 周同时限定：crontab 是 OR，APScheduler 是 AND
        "0 9 */2 * mon",
        "0 9 15 * 5",
    ],
)
def test_validate_rejects_day_and_weekday_together(expr):
    with pytest.raises(ValueError, match="同时限定"):
        validate_standard_crontab(expr)


def test_validate_accepts_day_with_explicit_weekday_placeholder():
    # 日指定 + 周用 ? 表示不限定，是合法组合
    validate_standard_crontab("0 9 1 * ?")
    validate_standard_crontab("0 9 ? * 1")
    validate_standard_crontab("0 9 * * 1-5")


def test_validate_rejects_six_field_seconds_expression():
    with pytest.raises(ValueError):
        validate_standard_crontab("0 0 2 * * *")


@pytest.mark.parametrize(
    "expr",
    [
        "0 8 * * 8",
        "0 8 * * abc",
        "0 8 * * 6-0",
        "0 8 * * 1--5",
        "0 8 * * */0",
        "0 8 * * 1,",
        "0 8",
        "",
    ],
)
def test_invalid_expressions_raise_value_error(expr):
    with pytest.raises(ValueError):
        to_apscheduler_crontab(expr)


def test_other_fields_are_passed_through_untouched():
    assert to_apscheduler_crontab("*/5 9-18 1,15 jan-mar 1-5") == "*/5 9-18 1,15 jan-mar mon-fri"


def test_validate_rejects_overlong_expression():
    # 任务表 cron_expr 是 VARCHAR(50)，超长会在入库时变成数据库错误（500）
    long_expr = "0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20 * * * *"
    with pytest.raises(ValueError, match="过长"):
        validate_standard_crontab(long_expr)
    validate_standard_crontab("0 8 * * 1-5")
