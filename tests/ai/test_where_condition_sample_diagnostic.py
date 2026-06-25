"""WHERE 条件样例探查诊断单元测试。"""

import pytest

from app.services.ai.where_condition_sample_diagnostic import (
    SchemaColumnHint,
    build_where_condition_probe_repair_hint,
    build_where_format_corrected_sql,
    build_where_sample_probe_plans,
    extract_where_predicate_columns_from_sql,
    format_where_condition_repair_block,
    infer_probe_columns_from_schema,
    is_where_condition_sql_error,
    parse_sample_rows,
    rewrite_where_date_literals_as_strings,
    rewrite_where_numeric_literals_as_strings,
    WhereSampleDiagnosticResult,
)

pytestmark = pytest.mark.no_infrastructure


FAILED_SQL = """
SELECT opp.*
FROM VIEW_AI_CULEOPP opp
LEFT JOIN VIEW_AI_CUSTOMER c ON opp.opp_customer_name = c.ID
WHERE (opp.opp_status = 1 OR opp.stage = 4)
  AND opp.CREATE_DATE >= DATE '2026-05-01'
  AND opp.CREATE_DATE < DATE '2026-06-01'
ORDER BY opp.CREATE_DATE DESC
"""


def test_is_where_condition_sql_error_matches_ora01861():
    assert is_where_condition_sql_error("ORA-01861: literal does not match format string")
    assert is_where_condition_sql_error("[TOOL_ERROR] ORA-01722: invalid number")
    assert not is_where_condition_sql_error("unknown column foo")


def test_extract_where_predicate_columns_from_failed_sql():
    refs = extract_where_predicate_columns_from_sql(FAILED_SQL, dialect="oracle")
    columns = {item.column.upper() for item in refs}
    assert "CREATE_DATE" in columns
    assert "OPP_STATUS" in columns or "STAGE" in columns


def test_build_where_sample_probe_plans_oracle():
    plans = build_where_sample_probe_plans(FAILED_SQL, dialect="oracle")
    assert plans
    table, columns, probe_sql = plans[0]
    assert table == "VIEW_AI_CULEOPP"
    assert "CREATE_DATE" in columns
    assert "ROWNUM" in probe_sql.upper()
    assert "CREATE_DATE" in probe_sql.upper()
    assert probe_sql.upper().count("SELECT") >= 2  # 外层 ROWNUM 子查询


def test_build_where_sample_probe_plans_clickhouse():
    plans = build_where_sample_probe_plans(FAILED_SQL, dialect="clickhouse")
    assert plans
    _table, _columns, probe_sql = plans[0]
    assert "LIMIT 5" in probe_sql
    assert "ROWNUM" not in probe_sql.upper()


def test_build_where_condition_probe_repair_hint_includes_probe_sql():
    hint = build_where_condition_probe_repair_hint(FAILED_SQL, dialect="oracle")
    assert "WHERE 条件探查" in hint
    assert "CREATE_DATE" in hint
    assert "```sql" in hint


def test_format_where_condition_repair_block_with_samples():
    block = format_where_condition_repair_block(
        [
            WhereSampleDiagnosticResult(
                table="VIEW_AI_CULEOPP",
                columns=["CREATE_DATE"],
                sample_rows=[{"CREATE_DATE": "2026-05-01 10:00:00"}],
            )
        ]
    )
    assert "平台自动 WHERE 样例探查" in block
    assert "2026-05-01 10:00:00" in block


def test_rewrite_where_date_literals_as_strings():
    sql = (
        "SELECT * FROM VIEW_AI_CULEOPP opp "
        "WHERE opp.CREATE_DATE >= DATE '2026-05-01' AND opp.CREATE_DATE < DATE '2026-06-01'"
    )
    corrected = rewrite_where_date_literals_as_strings(
        sql,
        columns=["CREATE_DATE"],
        sample_values={"create_date": ["2026-05-01 10:00:00"]},
    )
    assert corrected is not None
    assert "DATE '2026-05-01'" not in corrected
    assert "'2026-05-01 00:00:00'" in corrected
    assert "'2026-06-01 00:00:00'" in corrected


def test_build_where_format_corrected_sql_from_diagnostics():
    diagnostics = [
        WhereSampleDiagnosticResult(
            table="VIEW_AI_CULEOPP",
            columns=["CREATE_DATE"],
            sample_rows=[{"CREATE_DATE": "2026-05-01 10:00:00"}],
        )
    ]
    sql = "SELECT * FROM VIEW_AI_CULEOPP opp WHERE opp.CREATE_DATE >= DATE '2026-05-01'"
    corrected = build_where_format_corrected_sql(
        sql,
        diagnostics,
        error_message="ORA-01861: literal does not match format string",
    )
    assert corrected is not None
    assert "DATE '" not in corrected


def test_infer_probe_columns_from_schema_prioritizes_sql_mentions_and_date_types():
    schema_hints = {
        "VIEW_AI_SERVICE_MZKXQ_SU": [
            SchemaColumnHint(name="billing_status_description", col_type="varchar"),
            SchemaColumnHint(name="contract_end_date", col_type="varchar", examples=["2026-01-15"]),
            SchemaColumnHint(name="receivable_year_month", col_type="varchar", examples=["2026-06"]),
            SchemaColumnHint(name="is_frozen", col_type="varchar"),
        ]
    }
    sql = (
        "SELECT * FROM VIEW_AI_SERVICE_MZKXQ_SU "
        "WHERE contract_end_date >= DATE '2026-01-01' AND receivable_year_month = '2026-06'"
    )
    columns = infer_probe_columns_from_schema(
        "VIEW_AI_SERVICE_MZKXQ_SU",
        sql,
        error_message="ORA-01861: literal does not match format string",
        schema_column_hints=schema_hints,
    )
    assert columns[0].lower() == "contract_end_date"
    assert "receivable_year_month" in {item.lower() for item in columns}


def test_extract_where_predicate_columns_uses_schema_when_sql_parse_sparse():
    schema_columns = {
        "VIEW_AI_SERVICE_MZKXQ_SU": [
            "contract_end_date",
            "receivable_year_month",
            "billing_status_description",
            "is_frozen",
        ]
    }
    sql = "SELECT * FROM VIEW_AI_SERVICE_MZKXQ_SU WHERE contract_end_date >= DATE '2026-01-01'"
    refs = extract_where_predicate_columns_from_sql(
        sql,
        dialect="oracle",
        schema_table_columns=schema_columns,
        error_message="ORA-01861",
    )
    columns = {item.column.lower() for item in refs}
    assert "contract_end_date" in columns


def test_build_where_sample_probe_plans_uses_schema_when_where_unparseable():
    schema_hints = {
        "demo_view": [
            SchemaColumnHint(name="event_time", col_type="varchar", examples=["2026-05-01 10:00:00"]),
            SchemaColumnHint(name="region_name", col_type="varchar"),
        ]
    }
    sql = "SELECT * FROM demo_view WHERE broken where clause syntax {{"
    plans = build_where_sample_probe_plans(
        sql,
        dialect="oracle",
        schema_column_hints=schema_hints,
        error_message="ORA-01861",
    )
    assert plans
    _table, columns, probe_sql = plans[0]
    assert "event_time" in columns
    assert "event_time" in probe_sql


def test_extract_where_predicate_columns_prioritizes_date_columns():
    sql = (
        "SELECT * FROM VIEW_AI_SERVICE_MZKXQ_SU "
        "WHERE billing_status_description != 'x' "
        "AND receivable_year_month = '2026-06' "
        "AND contract_end_date >= DATE '2026-01-01'"
    )
    refs = extract_where_predicate_columns_from_sql(sql, dialect="oracle")
    columns = [item.column for item in refs]
    assert columns[0].lower() == "contract_end_date"


def test_build_where_format_corrected_sql_blind_ora01861():
    sql = "SELECT * FROM t WHERE contract_end_date >= DATE '2026-01-01'"
    diagnostics = [
        WhereSampleDiagnosticResult(
            table="t",
            columns=["contract_end_date"],
            sample_rows=[],
        )
    ]
    corrected = build_where_format_corrected_sql(
        sql,
        diagnostics,
        error_message="ORA-01861: literal does not match format string",
    )
    assert corrected is not None
    assert "DATE '" not in corrected
    assert "'2026-01-01'" in corrected


def test_build_where_format_corrected_sql_ora01722_numeric_flags():
    sql = (
        "SELECT * FROM t WHERE TO_CHAR(contract_end_date, 'YYYY-MM-DD') >= '2026-01-01' "
        "AND (is_frozen = 0 OR is_frozen IS NULL)"
    )
    diagnostics = [
        WhereSampleDiagnosticResult(
            table="t",
            columns=["contract_end_date", "is_frozen"],
            sample_rows=[],
        )
    ]
    corrected = build_where_format_corrected_sql(
        sql,
        diagnostics,
        error_message="ORA-01722: invalid number",
    )
    assert corrected is not None
    assert "TO_CHAR" not in corrected
    assert "is_frozen = '0'" in corrected


def test_rewrite_where_numeric_literals_as_strings():
    sql = "WHERE (is_partner_project = 0 OR is_partner_project IS NULL)"
    corrected = rewrite_where_numeric_literals_as_strings(sql)
    assert corrected is not None
    assert "is_partner_project = '0'" in corrected


def test_build_where_probe_schema_context_for_dataset_filters_by_dataset():
    from app.services.ai.where_condition_sample_diagnostic import (
        build_where_probe_schema_context_for_dataset,
    )

    schema_output = """
dataset: ds_a
data_source: oracle
table_name: VIEW_A
columns:
  - contract_end_date (varchar, 例: '2026-01-15')
dataset: ds_b
data_source: oracle
table_name: VIEW_B
columns:
  - other_col (varchar)
"""
    cols, hints = build_where_probe_schema_context_for_dataset("ds_a", schema_output=schema_output)
    assert cols is not None
    flat_cols = {name for names in cols.values() for name in names}
    assert "contract_end_date" in flat_cols
    assert "other_col" not in flat_cols
    assert hints is not None
    flat_hint_names = {item.name for items in hints.values() for item in items}
    assert "contract_end_date" in flat_hint_names


def test_parse_sample_rows_from_dict_items():
    parsed = {
        "columns": ["CREATE_DATE", "OPP_STATUS"],
        "items": [
            {"CREATE_DATE": "2026-05-01", "OPP_STATUS": "1"},
        ],
    }
    rows = parse_sample_rows(parsed, columns=["CREATE_DATE", "OPP_STATUS"])
    assert rows == [{"CREATE_DATE": "2026-05-01", "OPP_STATUS": "1"}]
