"""Unit tests for extracted ChatBI domain modules."""

import pytest

from app.services.ai.runners.chatbi.constants import DATA_REPAIR_BUDGETS
from app.services.ai.runners.chatbi.federated_upgrade import should_upgrade_to_federated_query
from app.services.ai.runners.chatbi.repair_policy import (
    build_repair_message,
    current_repair_kind,
    repair_budget_exhausted,
    reset_state_for_repair,
)
from app.services.ai.runners.chatbi.run_state import DataRunState
from app.services.ai.runners.chatbi.schema_retry import (
    build_controlled_schema_retry_keywords,
    clean_schema_retry_phrase,
    prepare_controlled_schema_retry_keywords,
)
from app.services.ai.runners.chatbi.sql_gates import (
    build_schema_binding_summary,
    build_sql_schema_preflight_error,
    collect_preflight_unknown_tables,
    detect_sql_static_risk,
    extract_schema_table_column_meta,
    extract_schema_table_columns,
    is_schema_gate_block,
    normalize_sql_text,
)
from app.services.ai.runners.chatbi.sql_repair_hints import invalid_identifier_repair_hint
from app.services.ai.runners.chatbi.sql_result_parser import (
    detect_empty_result,
    detect_ratio_anomaly,
    detect_sql_error,
    try_parse_json_output,
)
from app.services.ai.runners.chatbi.state_serialization import pending_state_to_data_run_state
from app.services.ai.runners.chatbi.turn_handlers import EARLY_EXIT_TURN_TYPES
from app.services.ai.runners.chatbi.schema_prefetch import (
    clean_schema_fallback_query,
    is_invalid_schema_search_keywords,
    should_rewrite_contextual_new_data_query,
)
from app.services.ai.runners.chatbi.few_shot import skip_few_shot_log


pytestmark = pytest.mark.no_infrastructure


def test_federated_upgrade_requires_explicit_cross_dataset_intent():
    schema_output = """
dataset: energy_ds
table_name: energy_usage
columns: device_id, power

# [跨数据集关联补全: asset_ds.asset_info]
dataset: asset_ds
table_name: asset_info
columns: id, owner
"""
    assert should_upgrade_to_federated_query(schema_output, "查一下本月能耗超标的设备") is False
    assert should_upgrade_to_federated_query(schema_output, "跨数据集关联能耗数据和资产数据，查维保人员") is True
    assert should_upgrade_to_federated_query(schema_output, "在这个数据集里把这两张表关联查询一下") is False


def test_clean_schema_retry_phrase_filters_emoji_and_ui_stopwords():
    assert clean_schema_retry_phrase("机房 🙋") == "机房"
    cleaned = clean_schema_retry_phrase("为您找到以下数据 机房的 信息 详细")
    assert "为您" not in cleaned
    assert "数据" not in cleaned
    assert "机房" in cleaned


def test_prepare_controlled_schema_retry_keywords():
    state = DataRunState()
    state.last_schema_keywords = "机房"
    prepare_controlled_schema_retry_keywords(
        state,
        schema_search_keywords="机房 列表",
        standalone_query="为您 到以下数据 机房的 信息 🙋",
        user_question="为您 到以下数据 机房的 信息 🙋 机房详情",
    )
    keywords = state.controlled_schema_retry_keywords
    assert "机房" in keywords
    assert "为您" not in keywords
    assert "🙋" not in keywords


def test_build_controlled_schema_retry_keywords_fallback():
    keywords = build_controlled_schema_retry_keywords("为您 到以下数据 机房的 信息 🙋")
    assert "机房" in keywords
    assert "为您" not in keywords


def test_detect_sql_static_risk_join_and_order_by():
    assert detect_sql_static_risk("SELECT a FROM t JOIN u") != ""
    assert detect_sql_static_risk("SELECT * FROM demo") == ""
    assert "ORDER BY 后不能接 AND ROWNUM" in detect_sql_static_risk(
        "SELECT * FROM demo ORDER BY id AND ROWNUM <= 10"
    )


def test_sql_schema_preflight_allows_order_by_select_alias_multi_table():
    schema = {
        "view_ai_visit_log": ["ID", "FOLLOW_UP_PERSON", "FOLLOW_UP_DATE"],
        "hrmresource": ["ID", "ACCOUNTNAME"],
    }
    sql = (
        "SELECT hr.ACCOUNTNAME AS sales_name, COUNT(v.ID) AS visit_count "
        "FROM VIEW_AI_VISIT_LOG v "
        "LEFT JOIN HRMRESOURCE hr ON v.FOLLOW_UP_PERSON = hr.ID "
        "WHERE v.FOLLOW_UP_DATE >= '2026-05-01' AND v.FOLLOW_UP_DATE <= '2026-05-31' "
        "GROUP BY hr.ACCOUNTNAME "
        "ORDER BY visit_count DESC"
    )
    err = build_sql_schema_preflight_error(sql, schema, dialect="oracle")
    assert err == ""


def test_sql_schema_preflight_allows_order_by_select_alias_single_table():
    schema = {"hrmresource": ["SUPDEPID", "BELONGTO", "MANAGERID"]}
    sql = "SELECT SUPDEPID AS aaa FROM HRMRESOURCE WHERE ROWNUM <= 10 ORDER BY aaa"
    err = build_sql_schema_preflight_error(sql, schema, dialect="oracle")
    assert err == ""


def test_sql_schema_preflight_still_blocks_unknown_column_with_order_by_alias():
    schema = {
        "view_ai_visit_log": ["ID", "FOLLOW_UP_PERSON", "FOLLOW_UP_DATE"],
        "hrmresource": ["ID", "ACCOUNTNAME"],
    }
    sql = (
        "SELECT hr.ACCOUNTNAME AS sales_name, COUNT(v.ID) AS visit_count "
        "FROM VIEW_AI_VISIT_LOG v "
        "LEFT JOIN HRMRESOURCE hr ON v.FOLLOW_UP_PERSON = hr.ID "
        "ORDER BY unknown_col DESC"
    )
    err = build_sql_schema_preflight_error(sql, schema, dialect="oracle")
    assert "unknown_col" in err.lower()


def test_sql_schema_preflight_error_unknown_column():
    schema = {"demo": ["id", "status"]}
    sql = "SELECT d.missing FROM demo d"
    err = build_sql_schema_preflight_error(sql, schema)
    assert "SQL 预检失败" in err
    assert "missing" in err


def test_sql_schema_preflight_error_unqualified_unknown_column():
    schema = {
        "view_ai_culeopp": [
            "OPP_CODE",
            "CLUE_CODE",
            "OPP_STATUS",
            "opp_customer_name",
            "CREATE_DATE",
        ]
    }
    sql = (
        "SELECT customer_name, opp_status FROM VIEW_AI_CULEOPP "
        "WHERE create_date >= DATE '2026-05-01'"
    )
    err = build_sql_schema_preflight_error(sql, schema, dialect="oracle")
    assert "SQL 预检失败" in err
    assert "customer_name" in err.lower()
    assert "opp_customer_name" in err or "OPP_CODE" in err


def test_sql_schema_preflight_allows_unqualified_known_column():
    schema = {"view_ai_culeopp": ["OPP_CODE", "OPP_STATUS", "CREATE_DATE"]}
    sql = (
        "SELECT opp_code, opp_status FROM VIEW_AI_CULEOPP "
        "WHERE create_date >= '2026-05-01'"
    )
    err = build_sql_schema_preflight_error(sql, schema, dialect="oracle")
    assert err == ""


def test_collect_preflight_unknown_tables():
    schema = {"view_ai_visit_log": ["id", "follow_up_date"]}
    sql = (
        "SELECT vl.id FROM view_ai_visit_log vl "
        "LEFT JOIN hrmresource hr ON vl.follow_up_person = hr.id"
    )
    unknown = collect_preflight_unknown_tables(sql, schema)
    assert "hrmresource" in unknown
    assert unknown["hrmresource"] == "hrmresource"


def test_collect_preflight_unknown_tables_with_dialect_uses_subquery_tables():
    schema = {"outer_t": ["id"]}
    sql = (
        "SELECT o.id FROM outer_t o "
        "JOIN (SELECT id FROM inner_t) i ON o.id = i.id"
    )
    unknown = collect_preflight_unknown_tables(sql, schema, dialect="oracle")
    assert "inner_t" in unknown
    assert unknown["inner_t"] == "inner_t"


def test_build_sql_schema_preflight_error_with_dialect_detects_unknown_column():
    schema = {"outer_t": ["id", "name"]}
    sql = "SELECT o.missing FROM outer_t o"
    err = build_sql_schema_preflight_error(sql, schema, dialect="oracle")
    assert "SQL 预检失败" in err
    assert "missing" in err


def test_sql_schema_preflight_allows_permission_fallback_table():
    schema = {"view_ai_visit_log": ["id", "follow_up_date", "follow_up_person"]}
    sql = (
        "SELECT vl.id, hr.username FROM view_ai_visit_log vl "
        "LEFT JOIN hrmresource hr ON vl.follow_up_person = hr.id"
    )
    err = build_sql_schema_preflight_error(sql, schema, extra_allowed_tables={"hrmresource"})
    assert err == ""


def test_sql_schema_preflight_still_blocks_unauthorized_table():
    schema = {"view_ai_visit_log": ["id"]}
    sql = "SELECT id FROM facility_management"
    err = build_sql_schema_preflight_error(sql, schema)
    assert "SQL 预检失败" in err
    assert "facility_management" in err
    assert "权限集内" in err


def test_extract_schema_table_columns_from_inline_format():
    output = "table_name: demo\ncolumns: id, status, total"
    cols = extract_schema_table_columns(output)
    assert "demo" in cols
    assert "id" in cols["demo"]


def test_extract_schema_table_column_meta_includes_type_and_examples():
    output = (
        "table_name: view_ai_visit_log\n"
        "columns:\n"
        "  - name: FOLLOW_UP_DATE\n"
        "    type: VARCHAR2\n"
        "    examples:\n"
        "      - '2026-06-22 10:30:00'\n"
        "  - name: ID\n"
        "    type: NUMBER\n"
    )
    meta = extract_schema_table_column_meta(output)
    assert meta["view_ai_visit_log"][0].name == "FOLLOW_UP_DATE"
    assert meta["view_ai_visit_log"][0].col_type == "VARCHAR2"
    assert meta["view_ai_visit_log"][0].examples == ["2026-06-22 10:30:00"]


def test_schema_binding_summary_includes_type_examples_and_date_hint():
    output = (
        "table_name: view_ai_visit_log\n"
        "columns:\n"
        "  - name: FOLLOW_UP_DATE\n"
        "    type: VARCHAR2\n"
        "    examples:\n"
        "      - '2026-06-22 10:30:00'\n"
    )
    summary = build_schema_binding_summary(output)
    assert "FOLLOW_UP_DATE (VARCHAR2" in summary
    assert "2026-06-22 10:30:00" in summary
    assert "字符串日期列" in summary
    assert "禁止 DATE/TO_DATE/TO_CHAR" in summary


def test_sql_result_parser_empty_and_error():
    assert detect_empty_result({"rows": []}) is not None
    ok, _ = detect_sql_error('[{"id": 1}]')
    assert ok is False
    err, msg = detect_sql_error("ORA-00904: invalid identifier")
    assert err is True
    assert "ORA" in msg


def test_detect_ratio_anomaly():
    parsed = {"rows": [{"success_rate": 2.5}]}
    anomaly, reason = detect_ratio_anomaly(parsed)
    assert anomaly is True
    assert "success_rate" in reason

    columns_items = {
        "columns": ["success_rate"],
        "items": [[2.5]],
    }
    anomaly, reason = detect_ratio_anomaly(columns_items)
    assert anomaly is True
    assert "success_rate" in reason


def test_invalid_identifier_repair_hint():
    hint = invalid_identifier_repair_hint('ORA-00904: "T"."BAD_COL": invalid identifier')
    assert "BAD_COL" in hint


def test_repair_policy_kind_and_budget():
    state = DataRunState(requires_fresh_data=True, sql_before_schema=True)
    assert current_repair_kind(state) == "sql_before_schema"
    assert repair_budget_exhausted(state) is False
    state.repair_attempts["sql_before_schema"] = DATA_REPAIR_BUDGETS["sql_before_schema"]
    assert repair_budget_exhausted(state) is True


def test_diagnostic_sql_pending_final_repair_budget_is_two():
    state = DataRunState(diagnostic_sql_pending_final=True, blocked_content="sample")
    assert current_repair_kind(state) == "diagnostic_sql_pending_final"
    assert DATA_REPAIR_BUDGETS["diagnostic_sql_pending_final"] == 2
    assert repair_budget_exhausted(state) is False
    state.repair_attempts["diagnostic_sql_pending_final"] = 1
    assert repair_budget_exhausted(state) is False
    state.repair_attempts["diagnostic_sql_pending_final"] = 2
    assert repair_budget_exhausted(state) is True


def test_build_repair_message_for_schema_miss():
    state = DataRunState(schema_miss=True, controlled_schema_retry_keywords="机房 列表")
    msg = build_repair_message(state)
    assert "Schema 重试要求" in msg
    assert "机房 列表" in msg


def test_reset_state_for_repair_clears_sql_flags():
    state = DataRunState(
        sql_completed=True,
        sql_error=True,
        empty_sql_result=True,
        full_content="draft",
    )
    reset_state_for_repair(state)
    assert state.sql_completed is False
    assert state.sql_error is False
    assert state.full_content == ""


def test_pending_state_roundtrip():
    state = DataRunState(schema_completed=True, last_schema_keywords="demo")
    pending = {"data_run_state": {"schema_completed": True, "last_schema_keywords": "demo"}}
    restored, meta = pending_state_to_data_run_state(pending)
    assert restored.schema_completed is True
    assert restored.last_schema_keywords == "demo"
    assert meta == {}


def test_is_schema_gate_block_prefix():
    assert is_schema_gate_block("[SCHEMA_GATE] blocked") is True
    assert is_schema_gate_block("ok") is False


def test_normalize_sql_text():
    assert normalize_sql_text("  SELECT  1  ") == "select 1"


def test_try_parse_json_output_list():
    assert try_parse_json_output('[{"a": 1}]') == [{"a": 1}]


def test_early_exit_turn_types():
    from app.services.ai.data_query_turn_classifier import DataQueryTurnType

    assert DataQueryTurnType.FORMAT_CORRECTION in EARLY_EXIT_TURN_TYPES
    assert DataQueryTurnType.REUSE_PREVIOUS_RESULT in EARLY_EXIT_TURN_TYPES
    assert DataQueryTurnType.CLARIFICATION_OR_NON_DATA in EARLY_EXIT_TURN_TYPES
    assert DataQueryTurnType.NON_DATA_REQUEST in EARLY_EXIT_TURN_TYPES
    assert DataQueryTurnType.CLARIFICATION_REQUIRED in EARLY_EXIT_TURN_TYPES
    assert DataQueryTurnType.NEW_DATA_QUERY not in EARLY_EXIT_TURN_TYPES


def test_clean_schema_fallback_query():
    assert "上海机房" in clean_schema_fallback_query("查询上海机房 PUE 趋势")
    assert "查询" not in clean_schema_fallback_query("查询上海机房 PUE 趋势")


def test_is_invalid_schema_search_keywords():
    assert is_invalid_schema_search_keywords("") is True
    assert is_invalid_schema_search_keywords("关键词") is True
    assert is_invalid_schema_search_keywords("机房 PUE") is False


def test_should_rewrite_contextual_new_data_query():
    from app.services.ai.runtime.agentscope.compat import HumanMessage

    messages = [HumanMessage(content="查上周 PUE"), HumanMessage(content="那本月呢")]
    assert should_rewrite_contextual_new_data_query("那本月呢", messages) is True
    assert should_rewrite_contextual_new_data_query("查询上海机房本月 PUE", messages) is False


def test_skip_few_shot_log_shape():
    log = skip_few_shot_log()
    assert log["title"] == "跳过经验库检索"
    assert log["type"] == "log"


@pytest.mark.asyncio
async def test_generate_non_data_response_uses_llm_lead_and_agent_name(monkeypatch):
    from types import SimpleNamespace

    from app.services.ai.runners.chatbi import clarification as chatbi_clarification

    class _FakeChat:
        async def generate_text(self, _messages):
            return (
                "你好！我是测试经营分析助手。"
                "我可以帮你做经营数据查询、指标对比和趋势解读，"
                "也可以基于结果做可视化说明。\n\n"
                "你可以试试「本月各区域销售额」或「近 30 天关键经营指标变化」。"
                "把对象、指标和时间说清楚会更好。"
            )

    async def fake_llm(**_kwargs):
        return object()

    monkeypatch.setattr(
        "app.services.ai.runners.chatbi.clarification.AgentConfigProvider.get_configured_llm",
        fake_llm,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.chatbi.clarification.chat_client_from_handle",
        lambda _handle: _FakeChat(),
    )

    runner = SimpleNamespace(
        config=SimpleNamespace(
            agent_display_name="测试经营分析助手",
            agent_name="biz-analyst",
            system_prompt="专注经营分析与指标解读。",
        )
    )
    content = await chatbi_clarification.generate_non_data_response(
        runner,
        user_question="你好",
    )
    assert "测试经营分析助手" in content
    assert "数据智能助手" not in content
    assert "查看我能查哪些数据" in content


@pytest.mark.asyncio
async def test_generate_non_data_response_falls_back_when_llm_omits_name(monkeypatch):
    from types import SimpleNamespace

    from app.services.ai.runners.chatbi import clarification as chatbi_clarification

    class _FakeChat:
        async def generate_text(self, _messages):
            return "你好呀，有什么可以帮你的？"

    async def fake_llm(**_kwargs):
        return object()

    monkeypatch.setattr(
        "app.services.ai.runners.chatbi.clarification.AgentConfigProvider.get_configured_llm",
        fake_llm,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.chatbi.clarification.chat_client_from_handle",
        lambda _handle: _FakeChat(),
    )

    runner = SimpleNamespace(
        config=SimpleNamespace(
            agent_display_name="测试经营分析助手",
            agent_name="biz-analyst",
            system_prompt="",
        )
    )
    content = await chatbi_clarification.generate_non_data_response(
        runner,
        user_question="你好",
    )
    assert "测试经营分析助手" in content
    assert content.startswith("你好！我是测试经营分析助手")
    assert "自然语言" in content or "例如" in content
