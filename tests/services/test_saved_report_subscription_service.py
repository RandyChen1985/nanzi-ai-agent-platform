import pytest


pytestmark = pytest.mark.no_infrastructure


def test_schedule_to_cron_supports_daily_weekly_monthly_and_advanced_cron():
    from app.services.saved_report_subscription_service import schedule_to_cron

    assert schedule_to_cron("daily", time_value="09:30") == "30 9 * * *"
    assert schedule_to_cron("weekly", time_value="08:05", weekday=1) == "5 8 * * 1"
    assert schedule_to_cron("monthly", time_value="07:00", monthday=15) == "0 7 15 * *"
    assert schedule_to_cron("cron", cron_expr="0 */2 * * *") == "0 */2 * * *"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"schedule_type": "daily", "time_value": "25:00"},
        {"schedule_type": "weekly", "time_value": "09:00", "weekday": 8},
        {"schedule_type": "monthly", "time_value": "09:00", "monthday": 32},
        {"schedule_type": "cron", "cron_expr": "bad cron"},
    ],
)
def test_schedule_to_cron_rejects_invalid_schedule(kwargs):
    from app.services.saved_report_subscription_service import schedule_to_cron

    with pytest.raises(ValueError):
        schedule_to_cron(**kwargs)
