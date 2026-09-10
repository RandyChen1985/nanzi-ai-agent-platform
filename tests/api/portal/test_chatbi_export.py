"""ChatBI 完整明细导出通道的单元测试。

以纯逻辑为主（结果解析、只读校验、行数上限常量），不依赖数据库/基础设施。
"""

import pytest

from app.api.portal.endpoints.chatbi_export import (
    ChatBIExportRequest,
    _extract_rows_columns,
    _md_escape,
    _user_dimensions,
)
from app.services.ai.tools.data_api import MAX_EXPORT_SQL_ROWS, MAX_LOCAL_SQL_ROWS


pytestmark = pytest.mark.no_infrastructure


def test_export_limit_is_sufficiently_larger_than_analysis_limit():
    # 分析口径 1000，导出口径至少放宽 10 倍，且为 10 万
    assert MAX_LOCAL_SQL_ROWS == 1000
    assert MAX_EXPORT_SQL_ROWS == 100000
    assert MAX_EXPORT_SQL_ROWS > MAX_LOCAL_SQL_ROWS * 10


def test_extract_rows_columns_adapter_dict_row_format():
    payload = {
        "columns": [{"name": "id", "type": "UInt64"}, {"name": "name", "type": "String"}],
        "items": [
            {"id": 1, "name": "张三"},
            {"id": 2, "name": "李四"},
        ],
    }
    columns, rows = _extract_rows_columns(payload)
    assert columns == ["id", "name"]
    assert rows == [[1, "张三"], [2, "李四"]]


def test_extract_rows_columns_adapter_list_row_format():
    payload = {
        "columns": [{"name": "id"}, {"name": "amount"}],
        "items": [[10, 100.5], [20, 200.0]],
    }
    columns, rows = _extract_rows_columns(payload)
    assert columns == ["id", "amount"]
    assert rows == [[10, 100.5], [20, 200.0]]


def test_extract_rows_columns_string_json():
    raw = '{"items": [{"a": 1, "b": 2}], "columns": [{"name": "a"}, {"name": "b"}]}'
    columns, rows = _extract_rows_columns(raw)
    assert columns == ["a", "b"]
    assert rows == [[1, 2]]


def test_extract_rows_columns_plain_list_of_dicts_without_columns():
    columns, rows = _extract_rows_columns([{"x": 1, "y": 2}])
    assert columns == ["x", "y"]
    assert rows == [[1, 2]]


def test_extract_rows_columns_empty_returns_empty():
    columns, rows = _extract_rows_columns({"items": [], "columns": []})
    assert columns == []
    assert rows == []


def test_user_dimensions_shape():
    dims = _user_dimensions(
        {"user_name": "alice", "real_name": "爱丽丝", "role": "user", "dept_code": "D1"}, 7
    )
    assert dims["id"] == 7
    assert dims["user_name"] == "alice"
    assert dims["role"] == "user"


def test_export_request_requires_sql_and_data_source():
    req = ChatBIExportRequest(sql="SELECT * FROM t", data_source="clickhouse")
    assert req.format == "xlsx"
    assert req.execution_mode == "direct"


def test_export_request_rejects_unknown_format():
    with pytest.raises(Exception):
        ChatBIExportRequest(sql="SELECT 1", data_source="clickhouse", format="pdf")


def test_export_request_accepts_markdown_format():
    req = ChatBIExportRequest(sql="SELECT 1", data_source="clickhouse", format="md")
    assert req.format == "md"


def test_md_escape_protects_markdown_table_structure():
    assert _md_escape("a|b") == "a\\|b"
    assert _md_escape("multi\nline\r\nvalue") == "multi line value"
    assert _md_escape(None) == ""
    assert _md_escape(123) == "123"