"""契约：cron 星期语义在「人类 / DB / 前端预览」与「APScheduler 调度」之间必须过兼容层。

前端与数据库一律使用标准 crontab 语义（0/7 = 周日，1 = 周一 … 6 = 周六），
APScheduler 的 day_of_week 却是 0 = 周一。任何一处漏掉 cron_compat 转换，
「周一~周五」就会落成「周二~周六」。本文件把这些边界钉死。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_scheduler_boundary_translates_before_building_trigger():
    scheduler = _read("app/services/ai/scheduler_service.py")
    assert "from app.services.cron_compat import to_apscheduler_crontab" in scheduler
    # 任务与报表订阅两条排期链路都必须先翻译
    assert "to_apscheduler_crontab(task.cron_expr)" in scheduler
    assert "to_apscheduler_crontab(subscription.cron_expr)" in scheduler


def test_task_schema_validation_translates_before_validating():
    schema = _read("app/schemas/task.py")
    assert "CronTrigger.from_crontab(to_apscheduler_crontab(v))" in schema


def test_metadata_inspection_validation_translates_before_validating():
    endpoint = _read("app/api/portal/endpoints/metadata.py")
    assert "CronTrigger.from_crontab(to_apscheduler_crontab(payload.cron_expr.strip()))" in endpoint


def test_subscription_schedule_to_cron_validates_advanced_cron_with_compat():
    service = _read("app/services/saved_report_subscription_service.py")
    assert "CronTrigger.from_crontab(to_apscheduler_crontab(expression))" in service
    # weekday 参数由调用方按标准 crontab 传值（0 = 周日）
    assert "0 = 周日" in service


def test_chatbi_dialog_passes_standard_crontab_weekday_numbers():
    dialog = _read("frontend/src/components/chatbi/ChatBIMonitorDialog.vue")
    # 周一 = 1 … 周六 = 6，周日 = 0
    assert "(index + 1) % 7" in dialog


def test_generator_and_preview_keep_standard_crontab_semantics():
    scenes = _read("frontend/src/utils/cronScenes.ts")
    util = _read("frontend/src/utils/cronSchedule.ts")
    assert "`${minute} ${hour} * * 1-5`" in scenes  # 工作日执行
    assert "0/7 = 周日" in util
    assert "calendar.getUTCDay()" in util
