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


def test_task_schema_validation_uses_shared_cron_rules():
    schema = _read("app/schemas/task.py")
    assert "validate_standard_crontab(v)" in schema


def test_metadata_inspection_rejects_six_field_expression():
    endpoint = _read("app/api/portal/endpoints/metadata.py")
    assert "validate_standard_crontab(payload.cron_expr.strip())" in endpoint
    # 6 段（带秒）必须拒绝：过去会被「截前 5 段校验」放行，存库后永不调度
    assert "len(cron_parts) != 5" in endpoint
    assert "6 段" in endpoint


def test_subscription_schedule_to_cron_uses_shared_cron_rules():
    service = _read("app/services/saved_report_subscription_service.py")
    assert "validate_standard_crontab(expression)" in service
    # weekday 参数由调用方按标准 crontab 传值（0 = 周日）
    assert "0 = 周日" in service


def test_frontend_rejects_step_wider_than_field_span():
    util = _read("frontend/src/utils/cronSchedule.ts")
    # 与 APScheduler 同口径：*/60 分钟、*/24 小时这类会被调度器整条拒绝
    assert "步长不能大于区间跨度" in util


def test_saved_report_cron_input_validates_inline():
    menu = _read("frontend/src/components/chatbi/DatasetCapabilityMenu.vue")
    # 「高级 Cron」过去是裸输入框，只有点保存打到后端才知道错
    assert 'import { validateCronExpression } from "@/utils/cronSchedule"' in menu
    assert "const savedReportCronValidation = computed(() => validateCronExpression(" in menu
    assert "savedReportCronValidation.message" in menu
    # 保存前拦截，避免非法表达式发到接口
    assert "!savedReportCronValidation.value.valid" in menu


def test_metadata_inspection_custom_cron_validates_inline():
    modal = _read("frontend/src/components/metadata/MetadataCronInspectionModal.vue")
    assert "import { validateCronExpression } from '@/utils/cronSchedule'" in modal
    assert "customCronValidation" in modal
    # 保存时对最终表达式再校验一次（自定义与预设都覆盖）
    assert "validateCronExpression(finalCron)" in modal


def test_frontend_keeps_weekday_seven_for_range_endpoints():
    util = _read("frontend/src/utils/cronSchedule.ts")
    # 7 只能在写入集合时折成 0；若在解析单值时折，`0-7` 会退化成「只有周日」、
    # `1-7` 会被误判为「起点大于终点」
    assert "return normalizeFieldValue(value, def);" not in util
    assert "values.add(normalizeFieldValue(value, def))" in util


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
