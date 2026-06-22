import json

import pytest

from app.services.ai.chatbi_citation_utils import (
    build_federated_chatbi_citation_event,
    count_result_rows,
    format_chatbi_sql_citation_content,
    maybe_build_chatbi_sql_citation_event,
)
from app.services.ai.runners.data_agent_runner import _DataRunState


pytestmark = pytest.mark.no_infrastructure


def test_format_chatbi_sql_citation_content_includes_sql_and_sample():
    parsed = {
        "columns": [{"name": "id"}, {"name": "name"}],
        "items": [[1, "Alice"], [2, "Bob"]],
    }
    content = format_chatbi_sql_citation_content(
        sql="SELECT id, name FROM demo LIMIT 2",
        dataset_name="demo_ds",
        data_source="mysql",
        parsed_output=parsed,
    )
    assert "SELECT id, name FROM demo LIMIT 2" in content
    assert "demo_ds" in content
    assert "mysql" in content
    assert "返回 2 行" in content
    assert "Alice" in content


def test_count_result_rows_supports_items_and_rows():
    assert count_result_rows({"items": [[1], [2]]}) == 2
    assert count_result_rows({"rows": []}) == 0


def test_maybe_build_chatbi_sql_citation_event_deduplicates_same_sql():
    state = _DataRunState()
    tool_args = {
        "sql": "SELECT 1",
        "dataset_name": "demo",
        "data_source": "mysql",
    }
    parsed = {"columns": [{"name": "x"}], "items": [[1]]}

    first = maybe_build_chatbi_sql_citation_event(
        state,
        tool_call_id="tool-1",
        tool_args=tool_args,
        parsed_output=parsed,
    )
    second = maybe_build_chatbi_sql_citation_event(
        state,
        tool_call_id="tool-1",
        tool_args=tool_args,
        parsed_output=parsed,
    )

    assert first is not None
    assert first["type"] == "citation"
    assert len(first["data"]) == 1
    assert first["data"][0]["doc_name"] == "demo · 查数 SQL"
    assert second is None
    assert state.sql_citation_counter == 1


def test_build_federated_chatbi_citation_event_contains_subquery_and_join():
    event = build_federated_chatbi_citation_event(
        tool_call_id="fed-1",
        subquery_sources=[
            {
                "dataset_name": "sales",
                "data_source": "mysql",
                "sql": "SELECT id FROM sales_order",
                "row_count": 1,
                "columns": ["id"],
                "items": [[1]],
            }
        ],
        join_sql="SELECT * FROM t_sales JOIN t_hr ON ...",
        final_data={"columns": [{"name": "id"}], "items": [[1]]},
        dataset_names=["sales", "hr"],
    )
    assert event is not None
    assert len(event["data"]) == 2
    assert event["data"][0]["doc_name"] == "sales · 联邦子查询"
    assert event["data"][1]["doc_name"].endswith("联邦聚合 SQL")
    assert "SELECT id FROM sales_order" in event["data"][0]["content"]
