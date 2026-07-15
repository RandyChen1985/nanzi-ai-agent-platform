import json

import pytest

from app.services.ai.chatbi_sql_query_binding import TableBinding
from app.services.ai.runners.chatbi.insight_meta import (
    build_chatbi_insight_meta,
    build_federated_chatbi_insight_meta,
    take_chatbi_insight_meta_event,
)
from app.services.ai.runners.chatbi.run_state import DataRunState


pytestmark = pytest.mark.no_infrastructure


def _state(rows: list[dict]) -> DataRunState:
    state = DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        sql_completed=True,
        last_successful_sql_output=json.dumps({"rows": rows}, ensure_ascii=False),
        last_successful_sql_args={
            "sql": "SELECT room_name, stat_date, pue FROM pue_daily",
        },
    )
    state.table_bindings = {
        "pue_daily": TableBinding(
            physical_name="pue_daily",
            dataset_name="机房能耗数据集",
            data_source="clickhouse",
        )
    }
    return state


def test_build_insight_uses_executed_sql_and_permission_notice():
    state = _state([{"room_name": "上海一号", "pue": 1.42}])
    state.last_successful_sql_output = json.dumps(
        {
            "rows": [{"room_name": "上海一号", "pue": 1.42}],
            "permission_notice": {
                "row_filter_applied": True,
                "dataset_name": "机房能耗数据集",
                "rule_count": 2,
                "message": "已按你的数据权限自动过滤结果",
                "executed_sql": "SELECT room_name, pue FROM pue_daily WHERE region = '华东'",
            },
        },
        ensure_ascii=False,
    )

    event = build_chatbi_insight_meta(state)

    assert event["type"] == "chatbi_insight_meta"
    data = event["data"]
    assert data["execution"]["row_count"] == 1
    assert data["execution"]["mode"] == "direct"
    assert data["permission"]["row_filter_applied"] is True
    assert data["final_sql"].endswith("WHERE region = '华东'")
    assert data["sources"] == [
        {
            "dataset_name": "机房能耗数据集",
            "data_source": "clickhouse",
            "tables": [{"physical_name": "pue_daily"}],
        }
    ]


def test_build_insight_marks_repaired_and_limits_actions():
    state = _state(
        [
            {"stat_date": "2026-07-14", "room_name": "上海一号", "pue": 1.42},
            {"stat_date": "2026-07-15", "room_name": "上海二号", "pue": 1.51},
        ]
    )
    state.repair_attempts = {"sql_error": 1}

    data = build_chatbi_insight_meta(state)["data"]

    assert data["execution"]["mode"] == "repaired"
    assert len(data["actions"]) <= 4
    action_ids = {action["id"] for action in data["actions"]}
    assert "trend" in action_ids
    assert "visualize" in action_ids
    assert "ranking" in action_ids


def test_build_insight_returns_none_without_successful_query():
    assert build_chatbi_insight_meta(DataRunState()) is None


def test_take_insight_event_emits_only_once():
    state = _state([{"room_name": "上海一号", "pue": 1.42}])

    assert take_chatbi_insight_meta_event(state)["type"] == "chatbi_insight_meta"
    assert take_chatbi_insight_meta_event(state) is None


def test_build_insight_supports_nested_and_column_row_results():
    state = _state([])
    state.last_successful_sql_output = json.dumps(
        {
            "result": {
                "columns": ["stat_date", "pue"],
                "rows": [["2026-07-15", 1.51]],
            }
        }
    )

    data = build_chatbi_insight_meta(state)["data"]

    assert data["execution"]["row_count"] == 1
    assert data["actions"][0]["id"] == "trend"


def test_build_federated_insight_lists_datasets_and_join_result():
    event = build_federated_chatbi_insight_meta(
        final_data={"columns": ["room_name", "pue"], "rows": [["上海一号", 1.51]]},
        dataset_names=["资产数据集", "能耗数据集"],
        final_sql="SELECT * FROM asset JOIN energy USING (room_id)",
    )

    assert event["data"]["execution"]["mode"] == "federated"
    assert event["data"]["execution"]["row_count"] == 1
    assert [item["dataset_name"] for item in event["data"]["sources"]] == ["资产数据集", "能耗数据集"]


def test_text_only_result_does_not_recommend_visualization():
    state = _state([{"status": "正常", "description": "服务运行正常"}])

    actions = build_chatbi_insight_meta(state)["data"]["actions"]

    assert [action["id"] for action in actions] == ["summary"]
