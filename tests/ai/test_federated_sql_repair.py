from app.services.ai.federated_sql_repair import (
    build_repair_schema_search_keywords,
    build_sql_repair_guidance,
    cross_dataset_scope_repair_hint,
    detect_sql_error,
    extract_cross_dataset_violation,
    infer_select_columns_regex_fallback,
    is_cross_dataset_scope_sql_error,
    is_invalid_number_sql_error,
    is_retryable_sql_error,
    merge_repair_schema_snippets,
    normalize_sql_text,
    parse_fixed_sql_from_llm_response,
    sanitize_repaired_sql_content,
    try_deterministic_invalid_identifier_repair,
)


def test_detect_sql_error_recognizes_tool_error():
    is_err, msg = detect_sql_error("[TOOL_ERROR] ORA-01861: literal does not match format string")
    assert is_err is True
    assert "ORA-01861" in msg


def test_is_retryable_sql_error_rejects_permission():
    assert is_retryable_sql_error("[Permission Denied] no access") is False


def test_is_retryable_sql_error_accepts_validation_failed():
    assert is_retryable_sql_error("[Validation Failed] unknown column 'foo'") is True


def test_build_sql_repair_guidance_includes_where_probe_summary_for_federated():
    guidance = build_sql_repair_guidance(
        "ORA-01861: literal does not match format string",
        "SELECT * FROM t WHERE d >= DATE '2026-01-01'",
        for_federated_node=True,
        where_probe_summary="【平台自动 WHERE 样例探查】d='2026-01-15'",
    )
    assert "WHERE 条件样例探查" in guidance
    assert "2026-01-15" in guidance


def test_build_sql_repair_guidance_includes_taxonomy_for_federated():
    guidance = build_sql_repair_guidance(
        "ORA-01861: literal does not match format string",
        "SELECT * FROM t WHERE d = TO_DATE('2026-05-01','YYYY-MM-DD')",
        for_federated_node=True,
    )
    assert "SQL Repair Taxonomy" in guidance
    assert "DATE_FORMAT_SQL_ERROR_REPAIR_GUIDE" in guidance or "YYYY-MM-DD" in guidance


def test_build_sql_repair_guidance_includes_memory_join_column_hint_for_binder_error():
    guidance = build_sql_repair_guidance(
        'Binder Error: Values list "v" does not have a column named "ID"',
        "SELECT v.CUSTOMER_NAME FROM t_visit_log v ORDER BY v.ID DESC",
        for_federated_node=True,
    )
    assert "memory_join 字段约束" in guidance
    assert "v.ID" in guidance or "去掉" in guidance
    assert is_retryable_sql_error('Binder Error: does not have a column named "ID"') is True


def test_normalize_sql_text_collapses_whitespace():
    assert normalize_sql_text("SELECT  1") == normalize_sql_text("select 1")


def test_invalid_number_sql_error_triggers_dedicated_guide():
    guidance = build_sql_repair_guidance(
        "[TOOL_ERROR] ORA-01722: invalid number",
        "SELECT 1 FROM t WHERE TO_CHAR(create_date, 'YYYY-MM') = '2026-05'",
        for_federated_node=True,
    )
    assert is_invalid_number_sql_error("ORA-01722: invalid number")
    assert "INVALID_NUMBER" in guidance or "invalid number" in guidance.lower()
    assert "TO_CHAR" in guidance or "字符串" in guidance


def test_build_repair_schema_search_keywords_includes_dataset_and_tables():
    keywords = build_repair_schema_search_keywords(
        "SELECT opp_code FROM VIEW_AI_CULEOPP WHERE create_date >= '2026-05-01'",
        dataset_name="meta_yes_crm_ds",
        error_text="ORA-01722: invalid number",
        sql_dialect="oracle",
    )
    assert "meta_yes_crm_ds" in keywords
    assert "VIEW_AI_CULEOPP" in keywords


def test_merge_repair_schema_snippets_appends_refreshed_chunk():
    merged = merge_repair_schema_snippets(
        "dataset: crm_ds\ntable_name: t1",
        "dataset: crm_ds\ntable_name: VIEW_AI_CULEOPP\ncolumns:\n  - name: create_date\n    type: VARCHAR",
    )
    assert "repair 按需 get_dataset_schema 补充" in merged
    assert "VIEW_AI_CULEOPP" in merged


def test_merge_repair_schema_snippets_skips_tool_error():
    base = "dataset: crm_ds"
    merged = merge_repair_schema_snippets(base, "[Tool Error] Failed to retrieve metadata")
    assert merged == base


def test_cross_dataset_scope_hint_requires_plan_split():
    err = (
        "[Validation Failed] 表 'VIEW_AI_VISIT_LOG' 不属于当前指定的数据集 'HR_ds'，"
        "普通 execute_sql_query 严禁跨数据集或凭空猜表。"
    )
    assert is_cross_dataset_scope_sql_error(err)
    table, dataset = extract_cross_dataset_violation(err)
    assert table == "VIEW_AI_VISIT_LOG"
    assert dataset == "HR_ds"
    hint = cross_dataset_scope_repair_hint(err)
    assert "memory_join" in hint
    assert "VIEW_AI_VISIT_LOG" in hint
    assert "整计划重构" in hint


def test_try_deterministic_invalid_identifier_repair_replaces_missing_column():
    failed_sql = """
    SELECT
        co.OPP_CODE,
        co.CLUE_CODE,
        co.opp_customer_name,
        co.CUSTOMER_NAME
    FROM VIEW_AI_CULEOPP co
    WHERE co.OPP_CODE IS NOT NULL
    """
    err = '[TOOL_ERROR] ORA-00904: "CO"."CUSTOMER_NAME": invalid identifier'
    fixed = try_deterministic_invalid_identifier_repair(
        failed_sql,
        err,
        sql_dialect="oracle",
    )
    assert fixed is not None
    assert "co.CUSTOMER_NAME" not in fixed.upper().replace(" ", "")
    assert "CUSTOMER_NAME" in fixed.upper()
    assert normalize_sql_text(failed_sql) != normalize_sql_text(fixed)


def test_try_deterministic_invalid_identifier_repair_strips_where_predicate():
    failed_sql = """
    SELECT co.OPP_CODE, co.CUSTOMER_NAME
    FROM VIEW_AI_CULEOPP co
    WHERE co.OPP_CODE IS NOT NULL AND co.CUSTOMER_NAME = 'ACME'
    """
    err = '[TOOL_ERROR] ORA-00904: "CO"."CUSTOMER_NAME": invalid identifier'
    fixed = try_deterministic_invalid_identifier_repair(
        failed_sql,
        err,
        sql_dialect="oracle",
    )
    assert fixed is not None
    assert "CUSTOMER_NAME = 'ACME'" not in fixed.upper().replace(" ", "")
    assert "OPP_CODE" in fixed.upper()


def test_infer_select_columns_regex_fallback_extracts_aliases():
    sub_sql = """
    SELECT co.OPP_CODE, co.CLUE_CODE, co.OPP_STATUS, co.opp_customer_name, co.CUSTOMER_NAME
    FROM VIEW_AI_CULEOPP co
    """
    cols = infer_select_columns_regex_fallback(sub_sql)
    assert cols == ["OPP_CODE", "CLUE_CODE", "OPP_STATUS", "opp_customer_name", "CUSTOMER_NAME"]


def test_parse_fixed_sql_from_llm_response_strips_prompt_leakage():
    raw = """<fixed_sql><![CDATA[
WITH t AS (SELECT 1 AS id)
SELECT id FROM t
]]></fixed_sql>
memory_join 只能引用各 sub_query 已 SELECT 的列
"""
    sql = parse_fixed_sql_from_llm_response(raw)
    assert sql.startswith("WITH t AS")
    assert "只能引用" not in sql


def test_parse_fixed_sql_from_llm_response_rejects_unclosed_prompt_echo():
    raw = """WITH t AS (SELECT 1) SELECT * FROM t
memory_join 只能引用各 sub_query 已 SELECT 的列
请只修正下方失败 SQL
"""
    try:
        parse_fixed_sql_from_llm_response(raw)
        assert False, "should raise"
    except ValueError as exc:
        assert "未能找到" in str(exc) or "不是有效 SQL" in str(exc)


def test_sanitize_repaired_sql_content_truncates_at_prompt_marker():
    dirty = "SELECT 1\n-- comment\nmemory_join 只能引用各 sub_query"
    cleaned = sanitize_repaired_sql_content(dirty)
    assert cleaned == "SELECT 1\n-- comment"
