import pytest

pytestmark = pytest.mark.no_infrastructure

from app.services.ai.empty_result_filter_diagnostic import (
    build_distinct_diagnostic_sql,
    extract_string_filter_literals,
    format_empty_filter_guard_message,
    format_repair_diagnostic_block,
    looks_like_generic_sql_failure_reply,
    parse_distinct_values,
    sql_has_string_literal_filters,
    suggest_close_values,
)


def test_sql_has_string_literal_filters_detects_equality_filter():
    sql = (
        "SELECT jfmc AS room_name FROM zf_view_resroom "
        "WHERE gxqy = '上海' ORDER BY jfmc"
    )
    assert sql_has_string_literal_filters(sql) is True
    filters = extract_string_filter_literals(sql)
    assert len(filters) == 1
    assert filters[0].column.lower() == "gxqy"
    assert filters[0].values == ("上海",)


def test_sql_has_string_literal_filters_ignores_numeric_filter():
    sql = "SELECT * FROM demo WHERE id = -1"
    assert sql_has_string_literal_filters(sql) is False


def test_build_distinct_diagnostic_sql():
    sql = build_distinct_diagnostic_sql(table="zf_view_resroom", column="gxqy")
    assert "SELECT DISTINCT gxqy FROM zf_view_resroom LIMIT 20" == sql


def test_suggest_close_values_for_partial_region_name():
    suggestions = suggest_close_values("上海", ["上海市", "北京市", "广州"])
    assert suggestions == ["上海市"]


def test_format_repair_diagnostic_block_includes_candidates():
    from app.services.ai.empty_result_filter_diagnostic import FilterDiagnosticResult

    block = format_repair_diagnostic_block(
        [
            FilterDiagnosticResult(
                column="gxqy",
                table="zf_view_resroom",
                operator="=",
                used_values=("上海",),
                diagnostic_sql="SELECT DISTINCT gxqy FROM zf_view_resroom LIMIT 20",
                candidates=["上海市", "北京市"],
                suggested_values=["上海市"],
            )
        ]
    )
    assert "平台自动筛选诊断" in block
    assert "gxqy" in block
    assert "上海市" in block
    assert "技术故障" in block


def test_format_empty_filter_guard_message_when_value_already_in_candidates():
    from app.services.ai.empty_result_filter_diagnostic import FilterDiagnosticResult

    message = format_empty_filter_guard_message(
        [
            FilterDiagnosticResult(
                column="status",
                table="demo",
                operator="=",
                used_values=("error",),
                diagnostic_sql="SELECT DISTINCT status FROM demo LIMIT 20",
                candidates=["error", "failed", "pending", "success"],
                suggested_values=[],
            )
        ]
    )
    assert "未返回数据" in message
    assert "该取值在库内存在" in message
    assert "上海" not in message


def test_format_empty_filter_guard_message():
    from app.services.ai.empty_result_filter_diagnostic import FilterDiagnosticResult

    message = format_empty_filter_guard_message(
        [
            FilterDiagnosticResult(
                column="gxqy",
                table="zf_view_resroom",
                operator="=",
                used_values=("上海",),
                diagnostic_sql="SELECT DISTINCT gxqy FROM zf_view_resroom LIMIT 20",
                candidates=["上海市", "北京市"],
                suggested_values=["上海市"],
            )
        ]
    )
    assert "未返回数据" in message
    assert "上海市" in message
    assert "上海」vs「上海市" not in message
    assert "技术问题" not in message


def test_parse_distinct_values_from_items():
    parsed = {
        "columns": [{"name": "gxqy"}],
        "items": [["上海市"], ["北京市"]],
    }
    assert parse_distinct_values(parsed, column="gxqy") == ["上海市", "北京市"]


def test_looks_like_generic_sql_failure_reply():
    assert looks_like_generic_sql_failure_reply("数据查询遇到了一些技术问题，暂时无法获取结果。") is True
    assert looks_like_generic_sql_failure_reply("按条件未查到数据") is False


def test_suggest_alternative_filter_columns_prefers_dimension_like_names():
    from app.services.ai.empty_result_filter_diagnostic import suggest_alternative_filter_columns

    alternatives = suggest_alternative_filter_columns(
        table="zf_view_resroom",
        used_column="gxqy",
        schema_table_columns={
            "zf_view_resroom": ["gxqy", "jfmc", "region_name", "upszrl", "id"],
        },
    )
    assert "region_name" in alternatives
    assert "gxqy" not in alternatives


def test_should_escalate_empty_after_value_correction():
    from app.services.ai.empty_result_filter_diagnostic import should_escalate_empty_after_value_correction

    assert should_escalate_empty_after_value_correction(
        [{"suggested_values": ["上海市"], "used_values": ["上海"]}]
    ) is True
    assert should_escalate_empty_after_value_correction(
        [{"candidates": ["上海市"], "used_values": ["上海"], "suggested_values": []}]
    ) is False


def test_format_repair_diagnostic_block_flags_wrong_column():
    from app.services.ai.empty_result_filter_diagnostic import FilterDiagnosticResult

    block = format_repair_diagnostic_block(
        [
            FilterDiagnosticResult(
                column="gxqy",
                table="zf_view_resroom",
                operator="=",
                used_values=("上海",),
                diagnostic_sql="SELECT DISTINCT gxqy FROM zf_view_resroom LIMIT 20",
                candidates=["001", "002"],
                suspect_wrong_column=True,
                matched_alternative_column="region_name",
            )
        ]
    )
    assert "疑似 WHERE 字段选错" in block
    assert "region_name" in block


def test_build_automatic_filter_corrections_prefers_column_swap():
    from app.services.ai.empty_result_filter_diagnostic import (
        FilterDiagnosticResult,
        build_automatic_filter_corrections,
    )

    corrections = build_automatic_filter_corrections(
        [
            FilterDiagnosticResult(
                column="gxqy",
                table="zf_view_resroom",
                operator="=",
                used_values=("上海",),
                diagnostic_sql="x",
                matched_alternative_column="region_name",
                matched_alternative_values=["上海市"],
                suggested_values=["上海市"],
            )
        ]
    )
    assert len(corrections) == 1
    assert corrections[0].kind == "column_swap"
    assert corrections[0].new_column == "region_name"
    assert corrections[0].new_values == ("上海市",)


def test_rewrite_sql_with_filter_corrections_column_and_value():
    from app.services.ai.empty_result_filter_diagnostic import (
        FilterCorrection,
        rewrite_sql_with_filter_corrections,
    )

    sql = "SELECT jfmc FROM zf_view_resroom WHERE gxqy = '上海' ORDER BY jfmc"
    rewritten = rewrite_sql_with_filter_corrections(
        sql,
        [
            FilterCorrection(
                kind="column_swap",
                column="gxqy",
                operator="=",
                old_values=("上海",),
                new_column="region_name",
                new_values=("上海市",),
            )
        ],
        dialect="mysql",
    )
    assert rewritten == "SELECT jfmc FROM zf_view_resroom WHERE region_name = '上海市' ORDER BY jfmc"


def test_build_automatic_filter_retry_plans_prioritizes_value_swap_for_empty_literal():
    from app.services.ai.empty_result_filter_diagnostic import (
        FilterDiagnosticResult,
        build_automatic_filter_retry_plans,
        rewrite_sql_with_filter_corrections,
    )

    sql = "SELECT department_name FROM demo WHERE months_diff = '' AND unpaid_amount > 0"
    plans = build_automatic_filter_retry_plans(
        [
            FilterDiagnosticResult(
                column="months_diff",
                table="demo",
                operator="=",
                used_values=("",),
                diagnostic_sql="x",
                candidates=["-83", "-71", "-59"],
                suspect_wrong_column=True,
                alternative_columns=["customer_name", "department_name", "room_name"],
            )
        ],
        sql=sql,
        dialect="oracle",
    )
    assert plans
    assert len(plans) <= 5
    first_sql = rewrite_sql_with_filter_corrections(sql, plans[0][0], dialect="oracle")
    assert "months_diff = '-83'" in first_sql or "months_diff = \"-83\"" in first_sql
    retain_swaps = [
        desc
        for _corrections, desc in plans
        if "替换为" in desc and "保留条件值" in desc
    ]
    assert len(retain_swaps) <= 2


def test_build_automatic_filter_retry_plans_limits_to_three():
    from app.services.ai.empty_result_filter_diagnostic import (
        FilterDiagnosticResult,
        build_automatic_filter_retry_plans,
    )

    sql = "SELECT room_name FROM demo WHERE gxqy = '上海'"
    plans = build_automatic_filter_retry_plans(
        [
            FilterDiagnosticResult(
                column="gxqy",
                table="demo",
                operator="=",
                used_values=("上海",),
                diagnostic_sql="x",
                matched_alternative_column="region_name",
                matched_alternative_values=["上海市"],
                suggested_values=["上海市", "上海城区"],
                alternative_columns=["region_name", "city_name", "province_name"],
            )
        ],
        sql=sql,
        dialect="mysql",
        max_plans=3,
    )
    assert len(plans) == 3
    sqls = {
        __import__(
            "app.services.ai.empty_result_filter_diagnostic",
            fromlist=["rewrite_sql_with_filter_corrections"],
        ).rewrite_sql_with_filter_corrections(sql, corrections, dialect="mysql")
        for corrections, _ in plans
    }
    assert len(sqls) == 3


@pytest.mark.asyncio
async def test_run_automatic_filter_retry_tries_until_success():
    from app.services.ai.empty_result_filter_diagnostic import (
        FilterDiagnosticResult,
        run_automatic_filter_retry,
    )

    attempts: list[str] = []

    async def fake_execute_sql(*, sql, **kwargs):
        attempts.append(sql)
        if len(attempts) < 3:
            return '{"items": [], "total": 0}'
        return '{"items": [["room-a"]], "total": 1}'

    result = await run_automatic_filter_retry(
        sql="SELECT room_name FROM demo WHERE gxqy = '上海'",
        diagnostics=[
            FilterDiagnosticResult(
                column="gxqy",
                table="demo",
                operator="=",
                used_values=("上海",),
                diagnostic_sql="x",
                suggested_values=["上海市", "上海城区", "上海市区"],
            )
        ],
        data_source="mysql_aiagent",
        dataset_name="demo",
        user_id=1,
        is_admin=False,
        execute_sql=fake_execute_sql,
        max_retries=3,
    )
    assert result.has_rows is True
    assert len(attempts) == 3
    assert "第 3 次重试已返回数据" in result.summary
