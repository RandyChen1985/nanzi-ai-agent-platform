"""Unit tests for 方案 A — database-error-driven schema-stale correction."""

import pytest

from app.services.ai.chatbi_sql_query_binding import (
    bindings_to_table_columns,
    extract_schema_table_bindings,
)
from app.services.ai.runners.chatbi.schema_stale_correction import (
    build_stale_correction,
    build_stale_repair_hint,
    classify_stale,
    is_stale_driver_error,
)
from app.services.ai.runners.chatbi.repair_policy import (
    current_repair_kind,
    reset_state_for_repair,
)
from app.services.ai.runners.chatbi.run_state import DataRunState


pytestmark = pytest.mark.no_infrastructure

_SCHEMA = """
dataset: pue_metrics
table_name: device_pue
columns: device_id, region, cpu_power, cpu_power_old, pue

dataset: room_assets
table_name: asset_info
columns: id, asset_name, owner
"""


@pytest.fixture
def schema_state():
    schema_output = _SCHEMA.strip()
    table_bindings = extract_schema_table_bindings(schema_output)
    schema_table_columns = bindings_to_table_columns(table_bindings)
    return schema_output, table_bindings, schema_table_columns


# ---------------------------------------------------------------- 驱动条件

def test_is_stale_driver_error_recognizes_unknown_column():
    assert is_stale_driver_error("Error: unknown column 'cpu_power_old'")
    assert is_stale_driver_error("column 'region' does not exist")
    assert is_stale_driver_error('ORA-00904: "CPU_POWER": invalid identifier')


def test_is_stale_driver_error_rejects_other_errors():
    assert is_stale_driver_error("syntax error near 'SELECT'") is False
    assert is_stale_driver_error("division by zero") is False


# ---------------------------------------------------------------- 编造 vs 真实过时

def test_classify_stale_drops_only_schema_declared_column(schema_state):
    _, _, schema_columns = schema_state
    table_keys = set(schema_columns.keys())

    real = classify_stale("cpu_power_old", table_keys=table_keys, schema_columns=schema_columns)
    assert real.safe_to_drop is True
    assert real.table_key is not None

    fabricated = classify_stale("made_up_power", table_keys=table_keys, schema_columns=schema_columns)
    assert fabricated.safe_to_drop is False  # schema 未声明 -> 编造，绝不删

    qualified = classify_stale("asset_info.owner", table_keys=table_keys, schema_columns=schema_columns)
    assert qualified.safe_to_drop is True


def test_build_stale_correction_no_drop_when_only_fabricated(schema_state):
    _, table_bindings, schema_columns = schema_state
    correction = build_stale_correction(
        "unknown column 'totally_made_up'",
        table_bindings=table_bindings,
        schema_table_columns=schema_columns,
    )
    assert correction.usable is False
    assert correction.corrected_schema_output == ""
    assert [s for s in correction.stale_columns if s.safe_to_drop] == []


def test_build_stale_correction_drops_real_stale_column(schema_state):
    schema_output, table_bindings, schema_columns = schema_state
    correction = build_stale_correction(
        "unknown column 'cpu_power_old'",
        table_bindings=table_bindings,
        schema_table_columns=schema_columns,
    )
    assert correction.usable is True
    assert correction.corrected_schema_output != ""
    # 剔除后不应再包含失效列
    assert "cpu_power_old" not in correction.corrected_schema_output
    # 剩余列里应仍有该表其余列
    assert any("cpu_power" in cols for cols in correction.remaining_table_columns.values())
    assert "cpu_power_old" not in sum(correction.remaining_table_columns.values(), [])


def test_build_stale_correction_drops_multiple_columns_in_same_table(schema_state):
    """测试单表同时剔除多个失效列，验证所有失效列均被移除且不发生字典覆盖。"""
    schema_output, table_bindings, schema_columns = schema_state
    correction = build_stale_correction(
        "unknown column 'cpu_power_old' and unknown column 'region'",
        table_bindings=table_bindings,
        schema_table_columns=schema_columns,
    )
    assert correction.usable is True
    assert "cpu_power_old" not in correction.corrected_schema_output
    assert "region" not in correction.corrected_schema_output
    # device_pue 剩余列中不应含这二者，但应保留其他列
    device_cols = correction.remaining_table_columns.get("device_pue", [])
    assert "cpu_power_old" not in device_cols
    assert "region" not in device_cols
    assert "device_id" in device_cols
    assert "cpu_power" in device_cols


def test_classify_stale_with_table_prefix_prefers_exact_table():
    """测试带表名前缀时优先精准匹配对应表，避免被无序遍历或同名字段误伤。"""
    schema_columns = {
        "device_pue": ["id", "status", "cpu_power"],
        "asset_info": ["id", "status", "owner"],
    }
    table_keys = set(schema_columns.keys())

    # asset_info.status 明确指定了 asset_info
    col = classify_stale("asset_info.status", table_keys=table_keys, schema_columns=schema_columns)
    assert col.safe_to_drop is True
    assert col.table_key == "asset_info"

    # device_pue.status 明确指定了 device_pue
    col2 = classify_stale("device_pue.status", table_keys=table_keys, schema_columns=schema_columns)
    assert col2.safe_to_drop is True
    assert col2.table_key == "device_pue"


def test_build_stale_repair_hint_lists_dropped_and_remaining(schema_state):
    _, table_bindings, schema_columns = schema_state
    correction = build_stale_correction(
        "unknown column 'cpu_power_old'",
        table_bindings=table_bindings,
        schema_table_columns=schema_columns,
    )
    hint = build_stale_repair_hint(correction)
    assert "cpu_power_old" in hint
    assert "device_pue" in hint and "cpu_power" in hint
    assert "禁止" in hint


# ---------------------------------------------------------------- repair policy / 熔断

def test_repair_kind_stale_column_corrected(schema_state):
    _, table_bindings, schema_columns = schema_state
    correction = build_stale_correction(
        "unknown column 'cpu_power_old'",
        table_bindings=table_bindings,
        schema_table_columns=schema_columns,
    )
    state = DataRunState()
    state.stale_columns_dropped = [
        {"field_name": s.field_name, "table_key": s.table_key} for s in correction.stale_columns if s.safe_to_drop
    ]
    state.corrected_schema_output = correction.corrected_schema_output
    state.corrected_table_columns = correction.remaining_table_columns
    state.stale_correction_applied = True

    assert current_repair_kind(state) == "stale_column_corrected"


def test_repair_kind_suppressed_after_consumed(schema_state):
    _, table_bindings, schema_columns = schema_state
    correction = build_stale_correction(
        "unknown column 'cpu_power_old'",
        table_bindings=table_bindings,
        schema_table_columns=schema_columns,
    )
    state = DataRunState()
    state.corrected_schema_output = correction.corrected_schema_output
    state.corrected_table_columns = correction.remaining_table_columns
    state.stale_correction_applied = True
    assert current_repair_kind(state) == "stale_column_corrected"

    # 进入修复（reset_state_for_repair）后熔断为已消费，不再反复触发。
    reset_state_for_repair(state)
    assert state.stale_repair_consumed is True
    assert current_repair_kind(state) != "stale_column_corrected"


def test_stale_correction_not_applied_when_nothing_to_drop(schema_state):
    # 全为编造列 -> 不设 corrected_schema_output -> 不进入 stale 修复 kind
    _, table_bindings, schema_columns = schema_state
    correction = build_stale_correction(
        "unknown column 'ghost_field'",
        table_bindings=table_bindings,
        schema_table_columns=schema_columns,
    )
    assert correction.usable is False
    state = DataRunState()
    # 模拟 tool_result_handlers 只在 usable 时才设置这些字段
    if correction.usable:
        state.corrected_schema_output = correction.corrected_schema_output
        state.stale_correction_applied = True
    assert current_repair_kind(state) != "stale_column_corrected"


def test_apply_sql_tool_result_syncs_schema_table_columns(schema_state):
    """测试 apply_sql_tool_result 在过时列剔除时，同步将 state.schema_table_columns 更新为剩余有效列。"""
    from unittest.mock import MagicMock
    from app.services.ai.runners.chatbi.tool_result_handlers import apply_sql_tool_result

    schema_output, table_bindings, schema_columns = schema_state
    state = DataRunState()
    state.schema_completed = True
    state.table_bindings = table_bindings
    state.schema_table_columns = dict(schema_columns)

    runner = MagicMock()
    runner._is_sql_repeat_gate_block.return_value = False
    runner._is_sql_static_gate_block.return_value = False
    runner._is_time_range_gate_block.return_value = False
    runner._is_sql_sandbox_gate_block.return_value = False
    runner._is_sql_plan_gate_block.return_value = False
    runner._is_failed_sql_repeat_gate_block.return_value = False
    runner._is_schema_gate_block.return_value = False
    runner._try_parse_json_output.return_value = {}
    runner._detect_empty_result.return_value = ""
    runner._is_diagnostic_sql.return_value = False
    runner._detect_sql_error.return_value = (True, "Error: unknown column 'cpu_power_old'")
    runner._is_sql_fatal_error.return_value = False
    runner._normalize_sql_text.return_value = "select cpu_power_old from device_pue"
    runner._is_sql_schema_preflight_error.return_value = False
    runner._is_schema_reference_sql_error.return_value = True

    apply_sql_tool_result(
        runner,
        state,
        tool_args={"sql": "select cpu_power_old from device_pue"},
        output="Error: unknown column 'cpu_power_old'",
    )

    assert state.stale_correction_applied is True
    # 验证 schema_table_columns 已被同步更新，剔除了失效列
    assert "cpu_power_old" not in state.schema_table_columns["device_pue"]
    assert "cpu_power" in state.schema_table_columns["device_pue"]
    assert state.schema_table_columns == state.corrected_table_columns
    assert state.schema_refresh_required is False