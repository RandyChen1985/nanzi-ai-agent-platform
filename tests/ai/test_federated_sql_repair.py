from app.services.ai.federated_sql_repair import (
    build_repair_schema_search_keywords,
    build_sql_repair_guidance,
    cross_dataset_scope_repair_hint,
    detect_sql_error,
    extract_cross_dataset_violation,
    is_cross_dataset_scope_sql_error,
    is_invalid_number_sql_error,
    is_retryable_sql_error,
    merge_repair_schema_snippets,
    normalize_sql_text,
)


def test_detect_sql_error_recognizes_tool_error():
    is_err, msg = detect_sql_error("[TOOL_ERROR] ORA-01861: literal does not match format string")
    assert is_err is True
    assert "ORA-01861" in msg


def test_is_retryable_sql_error_rejects_permission():
    assert is_retryable_sql_error("[Permission Denied] no access") is False


def test_is_retryable_sql_error_accepts_validation_failed():
    assert is_retryable_sql_error("[Validation Failed] unknown column 'foo'") is True


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
