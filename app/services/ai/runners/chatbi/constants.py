"""Shared ChatBI runner constants."""

SCHEMA_GATE_PREFIX = "[SCHEMA_GATE]"
SQL_REPEAT_GATE_PREFIX = "[SQL_REPEAT_GATE]"
SQL_STATIC_GATE_PREFIX = "[SQL_STATIC_GATE]"
FAILED_SQL_REPEAT_GATE_PREFIX = "[FAILED_SQL_REPEAT_GATE]"
SQL_PLAN_GATE_PREFIX = "[SQL_PLAN_GATE]"
TOOL_LOOP_FUSE_THRESHOLD = 3
FAILED_SQL_REPEAT_THRESHOLD = 2
DELAY_SECONDS_EXTREME_THRESHOLD = 7 * 24 * 60 * 60
MAX_DATA_REPAIR_ROUNDS = 2
# 平台在工具回调内自动执行的 SQL 重试上限（empty_filter / WHERE 探查），不计入 LLM repair 轮次。
MAX_PLATFORM_AUTO_SQL_RETRIES = 5
DATA_REPAIR_BUDGETS = {
    "sql_before_schema": 1,
    "schema_miss": 1,
    "schema_refinement": 1,
    "schema_ambiguous": 1,
    "sql_plan_missing": 1,
    "sql_static_risk": 1,
    "time_range_anomaly": 1,
    "sql_sandbox_blocked": 2,
    "sql_error": 8,
    "failed_sql_repeat": 1,
    "schema_refresh_after_sql_error": 2,
    "empty_sql_result": 2,
    "duration_anomaly": 1,
    "tool_loop_fuse": 1,
    "diagnostic_sql_pending_final": 2,
    "missing_schema": 1,
    "missing_sql": 2,
}
SCHEMA_RETRY_STOPWORDS = (
    "帮我",
    "请",
    "查询",
    "查",
    "看",
    "看看",
    "统计",
    "分析",
    "一下",
    "所有",
    "全部",
    "列表",
    "清单",
    "今天",
    "昨天",
    "前天",
    "本周",
    "上周",
    "本月",
    "上月",
    "最近",
    "为您",
    "到以下",
    "以下",
    "数据",
    "信息",
    "详情",
    "quick",
    "详细",
    "筛选",
    "状态",
    "正常",
    "导出",
    "点击",
    "确认",
    "选择",
    "按钮",
    "卡片",
    "为您找到",
)
SCHEMA_RETRY_SUFFIXES = ("列表", "清单", "明细", "统计")
_SQL_RESULT_DISPLAY_MAX_ROWS = 15
_SQL_RESULT_ROW_KEYS = ("items", "rows", "data", "records")
_SQL_TOOL_RESULT_DELIMITER = "--- 结果 ---"
_SQL_TOOL_ERROR_DELIMITER = "--- 错误 ---"
