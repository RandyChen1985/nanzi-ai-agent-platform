"""契约：自定义 Cron 的可视化生成面板（只改前端，不动调度语义）。"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]

TASK_CENTER = (ROOT / "frontend/src/views/TaskCenter.vue").read_text(encoding="utf-8")
CRON_BUILDER = (ROOT / "frontend/src/components/task/CronAdvancedBuilder.vue").read_text(encoding="utf-8")
CRON_UTIL = (ROOT / "frontend/src/utils/cronSchedule.ts").read_text(encoding="utf-8")
CRON_SCENES = (ROOT / "frontend/src/utils/cronScenes.ts").read_text(encoding="utf-8")


def test_generator_is_opened_from_custom_mode_without_new_tab():
    # 保留原有 5 个周期模式，不新增 tab
    assert "['daily','weekly','monthly','interval', 'custom']" in TASK_CENTER
    assert "'advanced'" not in TASK_CENTER
    # 自定义输入框旁提供可视化入口
    assert "可视化生成" in TASK_CENTER
    assert "openCronBuilder" in TASK_CENTER
    assert "showCronBuilder" in TASK_CENTER
    assert "CronAdvancedBuilder" in TASK_CENTER
    assert ':z-index="80"' in TASK_CENTER
    assert "点击「应用」回填到表达式输入框" in TASK_CENTER


def test_generator_panel_supports_scenes_and_preview():
    assert "CRON_SCENE_TABS" in CRON_BUILDER
    assert "CRON_SCENES" in CRON_BUILDER
    assert "scenesOfTab" in CRON_BUILDER
    assert "buildSceneExpression" in CRON_BUILDER
    assert "inferScene" in CRON_BUILDER
    assert "describeScene" in CRON_BUILDER
    assert "nextCronRunTimes" in CRON_BUILDER
    assert "formatCronRunTime" in CRON_BUILDER
    assert "WEEKDAY_LABELS" in CRON_BUILDER


def test_scene_model_covers_common_scenarios():
    for scene_id in (
        "daily", "workday", "weekend", "weekly", "monthly",
        "multiMonthday", "multiHourly", "hourly",
        "everyMinute", "everyHour", "everyDay", "hourRange",
        "quarter", "yearly", "monthOnly", "custom",
    ):
        assert f"'{scene_id}'" in CRON_SCENES
    assert "0,6" in CRON_SCENES
    assert "1,4,7,10" in CRON_SCENES
    assert "toggleDay" in CRON_BUILDER
    assert "toggleHour" in CRON_BUILDER
    assert "toggleWeekday" in CRON_BUILDER
    assert "每周指定几天" in CRON_SCENES
    assert "weekdays" in CRON_SCENES


def test_cron_util_covers_validation_and_next_runs():
    assert "expandCronField" in CRON_UTIL
    assert "validateCronExpression" in CRON_UTIL
    assert "nextCronRunTimes" in CRON_UTIL
    assert "0/7 = 周日" in CRON_UTIL
    assert "同时限定" in CRON_UTIL


def test_scheduler_semantics_untouched():
    """本次只做生成器，后端调度与 ChatBI 周选择保持原样。"""
    scheduler = (ROOT / "app/services/ai/scheduler_service.py").read_text(encoding="utf-8")
    chatbi = (ROOT / "frontend/src/components/chatbi/ChatBIMonitorDialog.vue").read_text(encoding="utf-8")

    assert "CronTrigger.from_crontab(" in scheduler
    assert not (ROOT / "app/services/cron_schedule.py").exists()
    assert 'const weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]' in chatbi


def test_schedule_column_truncates_and_uses_custom_tooltip():
    assert "cronScheduleTip" in TASK_CENTER
    assert "formatTaskScheduleFull" in TASK_CENTER
    assert "HoverTip" in TASK_CENTER
    assert "max-w-full truncate" in TASK_CENTER
    assert "w-[15%]" in TASK_CENTER
