import json
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.api.portal.endpoints import saved_reports

pytestmark = pytest.mark.no_infrastructure


def test_saved_report_list_items_receive_owner_subscription_summary():
    report = saved_reports._report_row_to_item(_report_row(), current_user_id=7)
    subscription = SimpleNamespace(
        report_id="rpt_5", status="active", cron_expr="0 9 * * *",
        next_run_at=datetime(2026, 7, 16, 9, 0, 0),
    )

    saved_reports._apply_saved_report_subscription_summaries([report], [subscription])

    assert report.subscription_status == "active"
    assert report.subscription_cron_expr == "0 9 * * *"
    assert report.subscription_next_run_at == "2026-07-16T09:00:00"


def _report_row(**overrides):
    data = {
        "id": "rpt_5",
        "title": "动态月报",
        "description": "订单动态报表",
        "sql_content": "SELECT * FROM orders",
        "sql_template": "SELECT * FROM orders WHERE created_at >= {{start_date}} AND created_at < {{end_date}}",
        "mode": "param_sql",
        "params_schema": [{"name": "date_range", "type": "date_range", "label": "日期范围"}],
        "default_params": {"date_range": "month_start_to_today"},
        "data_source": "mysql_test",
        "dataset_id": None,
        "original_query": "本月订单",
        "created_at": datetime(2026, 6, 27, 0, 0, 0),
        "updated_at": datetime(2026, 6, 27, 0, 0, 0),
        "analysis_mode": "auto",
        "tags": ["订单", "月报"],
        "owner_user_id": 7,
        "owner_name": "alice",
        "visibility": "private",
        "status": "active",
        "last_run_at": None,
        "last_success_at": None,
        "last_error": None,
        "shares": [],
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _today_range_sql() -> str:
    start = date.today()
    end = date.fromordinal(start.toordinal() + 1)
    return f"SELECT * FROM orders WHERE created_at >= '{start.isoformat()}' AND created_at < '{end.isoformat()}'"


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values

    def unique(self):
        return self


class _ExecuteResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _ScalarResult(self._values)

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None

    def scalar_one(self):
        return self._values[0]


def test_saved_report_static_sql_stays_compatible():
    report = saved_reports.SavedReportItem.model_validate(
        {
            "id": "rpt_1",
            "title": "固定报表",
            "sql_content": "SELECT * FROM orders WHERE created_at >= '2026-06-01'",
            "dataset_id": None,
            "data_source": "mysql_test",
            "original_query": "查订单",
            "created_at": "2026-06-27T00:00:00+00:00",
        }
    )

    sql, params = saved_reports._resolve_report_sql(
        report,
        body=saved_reports.ExecuteReportRequest.model_validate({}),
        today=date(2026, 6, 27),
    )

    assert sql == "SELECT * FROM orders WHERE created_at >= '2026-06-01'"
    assert params == {}
    assert report.mode == "static_sql"
    assert report.analysis_mode == "manual"


@pytest.mark.asyncio
async def test_get_saved_reports_enriches_owner_share_summary(monkeypatch):
    owner_report = _report_row(
        owner_user_id=7,
        owner_name="alice",
        visibility="shared",
        shares=[
            SimpleNamespace(target_type="user", target_id=9, permission="run"),
            SimpleNamespace(target_type="role", target_id=2, permission="run"),
        ],
    )
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _ExecuteResult([2]),
            _ExecuteResult([owner_report]),
            _ExecuteResult([SimpleNamespace(id=9, user_name="bob", real_name="Bob")]),
            _ExecuteResult([SimpleNamespace(id=2, code="ops", name="运维角色")]),
            _ExecuteResult([]),
            _ExecuteResult([]),
        ]
    )
    monkeypatch.setattr(saved_reports, "execute_sql_query_core", AsyncMock())

    response = await saved_reports.get_saved_reports(
        scope="my",
        tag=None,
        user_info={"user_id": "7", "role": "user", "user_name": "alice"},
        db=db,
    )

    item = response.data[0]
    assert item.run_permission_status == "allowed"
    assert item.share_summary == "已共享给 1 人 / 1 个角色"
    assert item.share_targets[0].target_name == "Bob"
    assert item.share_targets[1].target_name == "运维角色"


@pytest.mark.asyncio
async def test_get_saved_reports_marks_shared_report_denied_when_table_permission_fails(monkeypatch):
    shared_report = _report_row(owner_user_id=1, owner_name="admin", visibility="shared")
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_ExecuteResult([2]), _ExecuteResult([shared_report]), _ExecuteResult([])])

    async def fake_execute_sql_query_core(*args, **kwargs):
        assert kwargs["auth_check_only"] is True
        return "[Permission Denied] 用户 bob 无权访问表 'secret_orders'"

    monkeypatch.setattr(saved_reports, "execute_sql_query_core", fake_execute_sql_query_core)

    response = await saved_reports.get_saved_reports(
        scope="shared",
        tag=None,
        user_info={"user_id": "7", "role": "user", "user_name": "bob"},
        db=db,
    )

    item = response.data[0]
    assert item.run_permission_status == "denied"
    assert item.can_run is False
    assert "没有访问数据表" in item.run_permission_message


@pytest.mark.asyncio
async def test_preview_saved_report_returns_rendered_sql_and_permission_status(monkeypatch):
    report_row = _report_row()
    db = AsyncMock()

    async def fake_execute_sql_query_core(*args, **kwargs):
        assert kwargs["auth_check_only"] is True
        assert kwargs["sql"] == _today_range_sql()
        return json.dumps({"allowed": True})

    monkeypatch.setattr(saved_reports, "_get_user_role_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(saved_reports, "_get_report_for_user", AsyncMock(return_value=report_row))
    monkeypatch.setattr(saved_reports, "execute_sql_query_core", fake_execute_sql_query_core)

    response = await saved_reports.preview_saved_report(
        "rpt_5",
        body=saved_reports.ExecuteReportRequest.model_validate({"params": {"date_range": "today"}}),
        user_info={"user_id": "7", "role": "user", "user_name": "alice"},
        db=db,
    )

    assert response.data.report_id == "rpt_5"
    assert response.data.rendered_sql == _today_range_sql()
    assert response.data.permission_status == "allowed"
    assert response.data.can_run is True
    assert response.data.data_source == "mysql_test"


@pytest.mark.asyncio
async def test_preview_saved_report_marks_permission_denied(monkeypatch):
    report_row = _report_row(owner_user_id=1, owner_name="admin")
    db = AsyncMock()

    async def fake_execute_sql_query_core(*args, **kwargs):
        return "[Permission Denied] 用户 bob 无权访问表 'secret_orders'"

    monkeypatch.setattr(saved_reports, "_get_user_role_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(saved_reports, "_get_report_for_user", AsyncMock(return_value=report_row))
    monkeypatch.setattr(saved_reports, "execute_sql_query_core", fake_execute_sql_query_core)

    response = await saved_reports.preview_saved_report(
        "rpt_5",
        body=saved_reports.ExecuteReportRequest.model_validate({"params": {"date_range": "today"}}),
        user_info={"user_id": "7", "role": "user", "user_name": "bob"},
        db=db,
    )

    assert response.data.permission_status == "denied"
    assert response.data.can_run is False
    assert "没有访问数据表" in response.data.permission_message


@pytest.mark.asyncio
async def test_get_saved_report_detail_includes_user_preferences(monkeypatch):
    report_row = _report_row()
    pref = SimpleNamespace(
        report_id="rpt_5",
        user_id=7,
        is_favorite=True,
        pinned_at=datetime(2026, 6, 27, 9, 0, 0),
        last_viewed_at=datetime(2026, 6, 27, 10, 0, 0),
        run_count=3,
        last_run_at=datetime(2026, 6, 27, 11, 0, 0),
    )
    db = AsyncMock()
    monkeypatch.setattr(saved_reports, "_get_user_role_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(saved_reports, "_get_report_for_user", AsyncMock(return_value=report_row))
    monkeypatch.setattr(saved_reports, "_get_report_user_prefs", AsyncMock(return_value={"rpt_5": pref}))
    monkeypatch.setattr(saved_reports, "_enrich_saved_report_share_targets", AsyncMock())
    monkeypatch.setattr(saved_reports, "_annotate_saved_report_run_permissions", AsyncMock())
    monkeypatch.setattr(saved_reports, "_enrich_saved_report_subscriptions", AsyncMock())
    monkeypatch.setattr(saved_reports, "_touch_saved_report_view", AsyncMock())

    response = await saved_reports.get_saved_report_detail(
        "rpt_5",
        user_info={"user_id": "7", "role": "user", "user_name": "alice"},
        db=db,
    )

    assert response.data.id == "rpt_5"
    assert response.data.is_favorite is True
    assert response.data.pinned_at == "2026-06-27T09:00:00"
    assert response.data.user_run_count == 3


@pytest.mark.asyncio
async def test_update_saved_report_preferences_sets_favorite_and_pin(monkeypatch):
    report_row = _report_row()
    pref = SimpleNamespace(
        report_id="rpt_5",
        user_id=7,
        is_favorite=False,
        pinned_at=None,
        last_viewed_at=None,
        run_count=0,
        last_run_at=None,
    )
    db = AsyncMock()
    db.flush = AsyncMock()
    monkeypatch.setattr(saved_reports, "_get_user_role_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(saved_reports, "_get_report_for_user", AsyncMock(return_value=report_row))
    monkeypatch.setattr(saved_reports, "_get_or_create_report_user_pref", AsyncMock(return_value=pref))
    monkeypatch.setattr(saved_reports, "_enrich_saved_report_share_targets", AsyncMock())
    monkeypatch.setattr(saved_reports, "_annotate_saved_report_run_permissions", AsyncMock())

    response = await saved_reports.update_saved_report_preferences(
        "rpt_5",
        body=saved_reports.UpdateReportPreferenceRequest.model_validate(
            {"is_favorite": True, "pinned": True}
        ),
        user_info={"user_id": "7", "role": "user", "user_name": "alice"},
        db=db,
    )

    assert pref.is_favorite is True
    assert pref.pinned_at is not None
    assert response.data.is_favorite is True
    assert response.data.pinned_at is not None
    db.flush.assert_awaited_once()


def test_sort_saved_reports_for_user_pins_first():
    unpinned = saved_reports.SavedReportItem.model_validate(
        {
            "id": "rpt_1",
            "title": "普通报表",
            "sql_content": "SELECT 1",
            "dataset_id": None,
            "data_source": "mysql_test",
            "original_query": "q1",
            "created_at": "2026-06-27T12:00:00",
            "updated_at": "2026-06-27T12:00:00",
        }
    )
    pinned = saved_reports.SavedReportItem.model_validate(
        {
            "id": "rpt_2",
            "title": "置顶报表",
            "sql_content": "SELECT 2",
            "dataset_id": None,
            "data_source": "mysql_test",
            "original_query": "q2",
            "created_at": "2026-06-20T12:00:00",
            "updated_at": "2026-06-20T12:00:00",
            "pinned_at": "2026-06-27T09:00:00",
        }
    )

    sorted_reports = saved_reports._sort_saved_reports_for_user([unpinned, pinned])

    assert sorted_reports[0].id == "rpt_2"
    assert sorted_reports[1].id == "rpt_1"


@pytest.mark.asyncio
async def test_get_or_create_report_user_pref_retries_on_integrity_error():
    from sqlalchemy.exc import IntegrityError

    existing = SimpleNamespace(report_id="rpt_5", user_id=7, is_favorite=False, run_count=0)
    execute_calls = {"count": 0}

    async def fake_execute(_stmt):
        execute_calls["count"] += 1
        if execute_calls["count"] == 1:
            return _ExecuteResult([])
        return _ExecuteResult([existing])

    db = AsyncMock()
    db.execute = fake_execute
    nested = AsyncMock()
    nested.__aenter__ = AsyncMock(return_value=nested)
    nested.__aexit__ = AsyncMock(return_value=None)
    db.begin_nested = Mock(return_value=nested)
    db.add = Mock()
    db.flush = AsyncMock(side_effect=IntegrityError("insert", {}, Exception("duplicate")))

    pref = await saved_reports._get_or_create_report_user_pref(db, report_id="rpt_5", user_id=7)

    assert pref is existing
    assert execute_calls["count"] == 2


def test_saved_report_detects_two_date_literals_as_default_date_range_template():
    report = saved_reports._build_saved_report_item(
        report_id="rpt_2",
        title="订单月报",
        description="本月订单",
        sql_clean="SELECT * FROM orders WHERE created_at >= '2026-06-01' AND created_at < '2026-07-01'",
        dataset_id=3,
        data_source="mysql_test",
        original_query="本月订单",
        created_at="2026-06-27T00:00:00+00:00",
        body=saved_reports.SaveReportRequest.model_validate(
            {
                "title": "订单月报",
                "sql_content": "SELECT * FROM orders WHERE created_at >= '2026-06-01' AND created_at < '2026-07-01'",
                "data_source": "mysql_test",
                "analysis_mode": "auto",
            }
        ),
    )

    assert report.mode == "param_sql"
    assert report.sql_template == "SELECT * FROM orders WHERE created_at >= {{start_date}} AND created_at < {{end_date}}"
    assert report.default_params["date_range"] == "month_start_to_today"
    assert report.analysis_mode == "auto"
    assert report.description == "本月订单"


def test_saved_report_detects_datetime_literals_and_preserves_time_suffixes():
    report = saved_reports._build_saved_report_item(
        report_id="rpt_datetime",
        title="用户半年趋势",
        description="用户半年趋势",
        sql_clean=(
            "SELECT DATE(created_at) AS stat_date FROM ai_agent_users "
            "WHERE created_at >= '2025-12-27 00:00:00' AND created_at <= '2026-06-27 23:59:59'"
        ),
        dataset_id=None,
        data_source="mysql_test",
        original_query="查询最近6个月用户数趋势",
        created_at="2026-06-27T00:00:00+00:00",
        body=saved_reports.SaveReportRequest.model_validate(
            {
                "title": "用户半年趋势",
                "sql_content": (
                    "SELECT DATE(created_at) AS stat_date FROM ai_agent_users "
                    "WHERE created_at >= '2025-12-27 00:00:00' AND created_at <= '2026-06-27 23:59:59'"
                ),
                "data_source": "mysql_test",
            }
        ),
    )

    assert report.mode == "param_sql"
    assert "{{start_datetime}}" in (report.sql_template or "")
    assert "{{end_datetime}}" in (report.sql_template or "")

    sql, params = saved_reports._resolve_report_sql(
        report,
        body=saved_reports.ExecuteReportRequest.model_validate({"params": {"date_range": "today"}}),
        today=date(2026, 6, 27),
    )

    assert "created_at >= '2026-06-27 00:00:00'" in sql
    assert "created_at <= '2026-06-27 23:59:59'" in sql
    assert params["start_datetime"] == "2026-06-27 00:00:00"
    assert params["end_datetime"] == "2026-06-27 23:59:59"


def test_saved_report_detects_two_month_literals_as_month_range_template():
    report = saved_reports._build_saved_report_item(
        report_id="rpt_month",
        title="应收月份报表",
        description="应收月份报表",
        sql_clean=(
            "SELECT receivable_year_month, SUM(receivable_amount) FROM VIEW_AI_SERVICE_MZKXQ_SU "
            "WHERE receivable_year_month >= '2025-12' AND receivable_year_month <= '2026-05' "
            "GROUP BY receivable_year_month"
        ),
        dataset_id=None,
        data_source="clickhouse_test",
        original_query="查询最近6个月应收",
        created_at="2026-06-27T00:00:00+00:00",
        body=saved_reports.SaveReportRequest.model_validate(
            {
                "title": "应收月份报表",
                "sql_content": (
                    "SELECT receivable_year_month, SUM(receivable_amount) FROM VIEW_AI_SERVICE_MZKXQ_SU "
                    "WHERE receivable_year_month >= '2025-12' AND receivable_year_month <= '2026-05' "
                    "GROUP BY receivable_year_month"
                ),
                "data_source": "clickhouse_test",
            }
        ),
    )

    assert report.mode == "param_sql"
    assert report.sql_template == (
        "SELECT receivable_year_month, SUM(receivable_amount) FROM VIEW_AI_SERVICE_MZKXQ_SU "
        "WHERE receivable_year_month >= {{start_month}} AND receivable_year_month <= {{end_month}} "
        "GROUP BY receivable_year_month"
    )
    assert report.default_params == {"month_range": "last_6_completed_months"}

    sql, params = saved_reports._resolve_report_sql(
        report,
        body=saved_reports.ExecuteReportRequest.model_validate({}),
        today=date(2026, 6, 27),
    )

    assert "receivable_year_month >= '2025-12'" in sql
    assert "receivable_year_month <= '2026-05'" in sql
    assert params == {
        "month_range": "last_6_completed_months",
        "start_month": "2025-12",
        "end_month": "2026-05",
    }


def test_saved_report_month_template_renders_custom_month_range():
    report = saved_reports.SavedReportItem.model_validate(
        {
            "id": "rpt_month_custom",
            "title": "自定义月份报表",
            "sql_content": "SELECT * FROM receivables",
            "sql_template": "SELECT * FROM receivables WHERE month >= {{start_month}} AND month <= {{end_month}}",
            "mode": "param_sql",
            "params_schema": [{"name": "month_range", "type": "month_range", "label": "月份范围"}],
            "default_params": {"month_range": "custom_month_range", "start_month": "2026-01", "end_month": "2026-03"},
            "data_source": "mysql_test",
            "dataset_id": None,
            "original_query": "自定义月份",
            "created_at": "2026-06-27T00:00:00+00:00",
        }
    )

    sql, params = saved_reports._resolve_report_sql(
        report,
        body=saved_reports.ExecuteReportRequest.model_validate({}),
        today=date(2026, 6, 27),
    )

    assert "month >= '2026-01'" in sql
    assert "month <= '2026-03'" in sql
    assert params["start_month"] == "2026-01"
    assert params["end_month"] == "2026-03"


def test_saved_report_datetime_template_renders_custom_range_end_of_day():
    report = saved_reports.SavedReportItem.model_validate(
        {
            "id": "rpt_datetime_custom",
            "title": "自定义时间报表",
            "sql_content": "SELECT * FROM ai_agent_users",
            "sql_template": "SELECT * FROM ai_agent_users WHERE created_at >= {{start_datetime}} AND created_at <= {{end_datetime}}",
            "mode": "param_sql",
            "params_schema": [{"name": "date_range", "type": "date_range", "label": "日期范围"}],
            "default_params": {"date_range": "custom_range", "start_date": "2026-06-01", "end_date": "2026-06-10"},
            "data_source": "mysql_test",
            "dataset_id": None,
            "original_query": "自定义范围用户",
            "created_at": "2026-06-27T00:00:00+00:00",
        }
    )

    sql, params = saved_reports._resolve_report_sql(
        report,
        body=saved_reports.ExecuteReportRequest.model_validate({}),
        today=date(2026, 6, 27),
    )

    assert "created_at >= '2026-06-01 00:00:00'" in sql
    assert "created_at <= '2026-06-10 23:59:59'" in sql
    assert params["end_datetime"] == "2026-06-10 23:59:59"


def test_saved_report_param_sql_renders_builtin_last_7_days():
    report = saved_reports.SavedReportItem.model_validate(
        {
            "id": "rpt_3",
            "title": "近七天订单",
            "sql_content": "SELECT * FROM orders",
            "sql_template": "SELECT * FROM orders WHERE created_at >= {{start_date}} AND created_at < {{end_date}}",
            "mode": "param_sql",
            "params_schema": [
                {"name": "date_range", "type": "date_range", "label": "日期范围", "default": "last_7_days"}
            ],
            "default_params": {"date_range": "last_7_days"},
            "data_source": "mysql_test",
            "dataset_id": None,
            "original_query": "近七天订单",
            "created_at": "2026-06-27T00:00:00+00:00",
            "analysis_mode": "auto",
        }
    )

    sql, params = saved_reports._resolve_report_sql(
        report,
        body=saved_reports.ExecuteReportRequest.model_validate({}),
        today=date(2026, 6, 27),
    )

    assert sql == "SELECT * FROM orders WHERE created_at >= '2026-06-21' AND created_at < '2026-06-28'"
    assert params == {"start_date": "2026-06-21", "end_date": "2026-06-28", "date_range": "last_7_days"}


def test_saved_report_param_sql_rejects_unknown_placeholder():
    report = saved_reports.SavedReportItem.model_validate(
        {
            "id": "rpt_4",
            "title": "非法模板",
            "sql_content": "SELECT * FROM orders",
            "sql_template": "SELECT * FROM orders WHERE tenant_id = {{tenant_id}}",
            "mode": "param_sql",
            "params_schema": [{"name": "date_range", "type": "date_range", "label": "日期范围"}],
            "default_params": {"date_range": "today"},
            "data_source": "mysql_test",
            "dataset_id": None,
            "original_query": "订单",
            "created_at": "2026-06-27T00:00:00+00:00",
        }
    )

    with pytest.raises(saved_reports.ReportParameterError, match="不允许的报表参数"):
        saved_reports._resolve_report_sql(
            report,
            body=saved_reports.ExecuteReportRequest.model_validate({}),
            today=date(2026, 6, 27),
        )


def test_build_saved_report_result_snapshot_truncates_rows_and_keeps_total():
    parsed = {
        "columns": ["id", "amount"],
        "items": [[index, index * 10] for index in range(205)],
        "total": 999,
    }

    snapshot, row_count, snapshot_row_count = saved_reports._build_saved_report_result_snapshot(parsed)

    assert snapshot["columns"] == ["id", "amount"]
    assert len(snapshot["rows"]) == 200
    assert snapshot["rows"][0] == [0, 0]
    assert snapshot["rows"][-1] == [199, 1990]
    assert row_count == 999
    assert snapshot_row_count == 200


def test_build_saved_report_result_snapshot_supports_plain_row_list():
    parsed = [{"name": "A"}, {"name": "B"}]

    snapshot, row_count, snapshot_row_count = saved_reports._build_saved_report_result_snapshot(parsed)

    assert snapshot == {"columns": ["name"], "rows": parsed}
    assert row_count == 2
    assert snapshot_row_count == 2


@pytest.mark.asyncio
async def test_execute_saved_report_uses_rendered_sql_and_enables_table_auth(monkeypatch):
    captured = {}
    report_row = _report_row()
    db = AsyncMock()
    db.add = Mock()
    db.flush = AsyncMock()

    async def fake_execute_sql_query_core(*args, **kwargs):
        captured.update(kwargs)
        return json.dumps({"items": [{"orders": 3}]})

    monkeypatch.setattr(saved_reports, "_get_user_role_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(saved_reports, "_get_report_for_user", AsyncMock(return_value=report_row))
    monkeypatch.setattr(saved_reports, "_record_saved_report_run", AsyncMock())
    monkeypatch.setattr(saved_reports, "execute_sql_query_core", fake_execute_sql_query_core)

    response = await saved_reports.execute_saved_report(
        "rpt_5",
        body=saved_reports.ExecuteReportRequest.model_validate({"params": {"date_range": "today"}}),
        conversation_id=None,
        user_info={"user_id": "7", "role": "user", "user_name": "alice"},
        db=db,
    )

    assert response.data == {"items": [{"orders": 3}]}
    assert captured["sql"] == _today_range_sql()
    assert captured["bypass_table_auth"] is False
    assert report_row.last_success_at is not None
    run_row = db.add.call_args.args[0]
    assert run_row.report_id == "rpt_5"
    assert run_row.user_id == 7
    assert run_row.status == "success"
    assert run_row.row_count == 1
    assert run_row.snapshot_row_count == 1
    assert run_row.result_snapshot == {"columns": ["orders"], "rows": [{"orders": 3}]}
    assert db.flush.await_count == 2


@pytest.mark.asyncio
async def test_execute_saved_report_records_user_run_preference(monkeypatch):
    report_row = _report_row()
    db = AsyncMock()
    db.add = Mock()
    db.flush = AsyncMock()
    record_run = AsyncMock()

    async def fake_execute_sql_query_core(*args, **kwargs):
        return json.dumps({"items": [{"orders": 3}]})

    monkeypatch.setattr(saved_reports, "_get_user_role_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(saved_reports, "_get_report_for_user", AsyncMock(return_value=report_row))
    monkeypatch.setattr(saved_reports, "_record_saved_report_run", record_run)
    monkeypatch.setattr(saved_reports, "execute_sql_query_core", fake_execute_sql_query_core)

    await saved_reports.execute_saved_report(
        "rpt_5",
        body=saved_reports.ExecuteReportRequest.model_validate({"params": {"date_range": "today"}}),
        conversation_id=None,
        user_info={"user_id": "7", "role": "user", "user_name": "alice"},
        db=db,
    )

    record_run.assert_awaited_once_with(db, report_id="rpt_5", user_id=7)


@pytest.mark.asyncio
async def test_execute_saved_report_reinfers_placeholder_data_source(monkeypatch):
    captured = {}
    report_row = _report_row(data_source="default_clickhouse", dataset_id=None)
    db = AsyncMock()
    db.add = Mock()
    db.flush = AsyncMock()

    async def fake_execute_sql_query_core(*args, **kwargs):
        captured.update(kwargs)
        return json.dumps({"items": [{"users": 3}]})

    monkeypatch.setattr(saved_reports, "_get_user_role_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(saved_reports, "_get_report_for_user", AsyncMock(return_value=report_row))
    monkeypatch.setattr(saved_reports, "_infer_dataset_and_data_source", AsyncMock(return_value=(42, "mysql_agent")))
    monkeypatch.setattr(saved_reports, "_record_saved_report_run", AsyncMock())
    monkeypatch.setattr(saved_reports, "execute_sql_query_core", fake_execute_sql_query_core)

    response = await saved_reports.execute_saved_report(
        "rpt_5",
        body=saved_reports.ExecuteReportRequest.model_validate({"params": {"date_range": "today"}}),
        conversation_id=None,
        user_info={"user_id": "7", "role": "user", "user_name": "alice"},
        db=db,
    )

    assert response.data == {"items": [{"users": 3}]}
    assert captured["data_source"] == "mysql_agent"
    assert report_row.dataset_id == 42
    assert report_row.data_source == "mysql_agent"


@pytest.mark.asyncio
async def test_execute_shared_saved_report_permission_denied_uses_friendly_message(monkeypatch):
    report_row = _report_row(owner_user_id=1, owner_name="admin")
    db = AsyncMock()
    db.add = Mock()
    db.flush = AsyncMock()

    async def fake_execute_sql_query_core(*args, **kwargs):
        return "[Permission Denied] 用户 alice 无权访问表 'ai_agent_users'"

    monkeypatch.setattr(saved_reports, "_get_user_role_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(saved_reports, "_get_report_for_user", AsyncMock(return_value=report_row))
    monkeypatch.setattr(saved_reports, "execute_sql_query_core", fake_execute_sql_query_core)

    with pytest.raises(HTTPException) as exc_info:
        await saved_reports.execute_saved_report(
            "rpt_5",
            body=saved_reports.ExecuteReportRequest.model_validate({"params": {"date_range": "today"}}),
            conversation_id=None,
            user_info={"user_id": "7", "role": "user", "user_name": "alice"},
            db=db,
        )

    assert exc_info.value.status_code == 403
    assert "没有访问数据表" in str(exc_info.value.detail)
    assert "Request failed" not in str(exc_info.value.detail)
    assert "[Permission Denied]" not in str(exc_info.value.detail)
    assert report_row.status == "error"
    run_row = db.add.call_args.args[0]
    assert run_row.status == "error"
    assert "没有访问数据表" in run_row.error_message
    assert "Permission Denied" not in run_row.error_message
    db.commit.assert_awaited_once()
    assert db.flush.await_count == 2


@pytest.mark.asyncio
async def test_copy_shared_saved_report_permission_denied_uses_friendly_message(monkeypatch):
    source = _report_row(owner_user_id=1, owner_name="admin")
    db = AsyncMock()

    async def fake_execute_sql_query_core(*args, **kwargs):
        assert kwargs["auth_check_only"] is True
        return "[Permission Denied] 用户 alice 无权访问表 'ai_agent_users'"

    monkeypatch.setattr(saved_reports, "_get_user_role_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(saved_reports, "_get_report_for_user", AsyncMock(return_value=source))
    monkeypatch.setattr(saved_reports, "execute_sql_query_core", fake_execute_sql_query_core)

    with pytest.raises(HTTPException) as exc_info:
        await saved_reports.copy_saved_report(
            "rpt_5",
            user_info={"user_id": "7", "role": "user", "user_name": "alice"},
            db=db,
        )

    assert exc_info.value.status_code == 403
    assert "没有访问数据表" in str(exc_info.value.detail)
    assert "[Permission Denied]" not in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_update_saved_report_rebuilds_parameter_template(monkeypatch):
    original = _report_row(
        id="rpt_edit",
        title="旧标题",
        description=None,
        sql_content="SELECT * FROM orders WHERE created_at >= '2026-05-01'",
        sql_template=None,
        mode="static_sql",
        params_schema=[],
        default_params={},
        original_query="订单",
        tags=[],
    )
    db = AsyncMock()
    db.flush = AsyncMock()
    monkeypatch.setattr(saved_reports, "_get_report_for_user", AsyncMock(return_value=original))

    async def fake_execute_sql_query_core(*args, **kwargs):
        assert kwargs["auth_check_only"] is True
        assert kwargs["bypass_table_auth"] is False
        return json.dumps({"allowed": True})

    monkeypatch.setattr(saved_reports, "execute_sql_query_core", fake_execute_sql_query_core)

    response = await saved_reports.update_saved_report(
        "rpt_edit",
        body=saved_reports.UpdateReportRequest.model_validate(
            {
                "title": "本月订单",
                "description": "订单本月标准报表",
                "sql_content": "SELECT * FROM orders WHERE created_at >= '2026-06-01' AND created_at < '2026-07-01'",
                "data_source": "mysql_test",
                "analysis_mode": "auto",
                "tags": ["订单", "月报", "订单"],
            }
        ),
        user_info={"user_id": "7", "role": "user"},
        db=db,
    )

    updated = response.data
    assert updated.id == "rpt_edit"
    assert updated.title == "本月订单"
    assert updated.description == "订单本月标准报表"
    assert updated.created_at == original.created_at.isoformat()
    assert updated.mode == "param_sql"
    assert updated.sql_template == "SELECT * FROM orders WHERE created_at >= {{start_date}} AND created_at < {{end_date}}"
    assert updated.analysis_mode == "auto"
    assert updated.tags == ["订单", "月报"]
    assert original.sql_template == updated.sql_template
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_saved_report_rejects_unauthorized_table_before_saving(monkeypatch):
    original = _report_row(id="rpt_edit", title="旧标题", sql_content="SELECT * FROM orders")
    db = AsyncMock()
    db.flush = AsyncMock()
    monkeypatch.setattr(saved_reports, "_get_report_for_user", AsyncMock(return_value=original))

    async def fake_execute_sql_query_core(*args, **kwargs):
        assert kwargs["sql"] == "SELECT * FROM secret_orders"
        assert kwargs["auth_check_only"] is True
        assert kwargs["dry_run"] is False
        assert kwargs["bypass_table_auth"] is False
        return "[Permission Denied] alice(7) 无权访问表 'secret_orders'"

    monkeypatch.setattr(saved_reports, "execute_sql_query_core", fake_execute_sql_query_core)

    with pytest.raises(HTTPException) as exc_info:
        await saved_reports.update_saved_report(
            "rpt_edit",
            body=saved_reports.UpdateReportRequest.model_validate(
                {
                    "title": "越权报表",
                    "sql_content": "SELECT * FROM secret_orders",
                    "data_source": "mysql_test",
                }
            ),
            user_info={"user_id": "7", "role": "user", "user_name": "alice"},
            db=db,
        )

    assert exc_info.value.status_code == 403
    assert "没有访问数据表" in exc_info.value.detail
    assert "[Permission Denied]" not in exc_info.value.detail
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_report_persists_db_report_with_tags(monkeypatch):
    db = AsyncMock()
    db.add = Mock()
    db.flush = AsyncMock()
    captured = {}

    async def fake_execute_sql_query_core(*args, **kwargs):
        return json.dumps({"allowed": True})

    def fake_add(row):
        captured["row"] = row

    db.add.side_effect = fake_add
    monkeypatch.setattr(saved_reports, "_infer_dataset_and_data_source", AsyncMock(return_value=(None, "mysql_test")))
    monkeypatch.setattr(saved_reports, "execute_sql_query_core", fake_execute_sql_query_core)

    response = await saved_reports.save_report(
        body=saved_reports.SaveReportRequest.model_validate(
            {
                "title": "订单报表",
                "description": "订单月度复盘",
                "sql_content": "SELECT * FROM orders",
                "data_source": "mysql_test",
                "tags": ["订单", "月报", "订单"],
            }
        ),
        user_info={"user_id": "7", "role": "user", "user_name": "alice"},
        db=db,
    )

    assert response.data.tags == ["订单", "月报"]
    assert captured["row"].owner_user_id == 7
    assert captured["row"].tags == ["订单", "月报"]
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_infer_dataset_and_data_source_treats_default_clickhouse_as_placeholder():
    class Result:
        def first(self):
            return (42, "mysql_agent")

    db = AsyncMock()
    db.execute = AsyncMock(return_value=Result())

    dataset_id, data_source = await saved_reports._infer_dataset_and_data_source(
        db,
        sql_content="SELECT COUNT(*) FROM ai_agent_users",
        dataset_id=None,
        data_source="default_clickhouse",
    )

    assert dataset_id == 42
    assert data_source == "mysql_agent"
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_saved_report_shares_replaces_user_and_role_targets(monkeypatch):
    report = _report_row(id="rpt_share", visibility="private", shares=[])
    added = []
    db = AsyncMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.add = Mock()
    db.add.side_effect = lambda row: added.append(row)
    monkeypatch.setattr(saved_reports, "_get_report_for_user", AsyncMock(return_value=report))
    monkeypatch.setattr(saved_reports, "_validate_share_targets", AsyncMock())

    response = await saved_reports.update_saved_report_shares(
        "rpt_share",
        body=saved_reports.UpdateReportSharesRequest.model_validate(
            {
                "targets": [
                    {"target_type": "user", "target_id": 9},
                    {"target_type": "role", "target_id": 3},
                    {"target_type": "role", "target_id": 3},
                ]
            }
        ),
        user_info={"user_id": "7", "role": "user"},
        db=db,
    )

    assert report.visibility == "shared"
    assert [(row.target_type, row.target_id, row.permission) for row in added] == [
        ("user", 9, "run"),
        ("role", 3, "run"),
    ]
    assert response.data.visibility == "shared"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_saved_report_shares_rejects_missing_targets(monkeypatch):
    report = _report_row(id="rpt_share", visibility="private", shares=[])
    db = AsyncMock()
    db.flush = AsyncMock()
    monkeypatch.setattr(saved_reports, "_get_report_for_user", AsyncMock(return_value=report))

    async def fake_execute(*args, **kwargs):
        class Result:
            def scalars(self):
                return self

            def all(self):
                return [9]

        return Result()

    db.execute.side_effect = fake_execute

    with pytest.raises(HTTPException) as exc_info:
        await saved_reports.update_saved_report_shares(
            "rpt_share",
            body=saved_reports.UpdateReportSharesRequest.model_validate(
                {
                    "targets": [
                        {"target_type": "user", "target_id": 9},
                        {"target_type": "user", "target_id": 10},
                    ]
                }
            ),
            user_info={"user_id": "7", "role": "user"},
            db=db,
        )

    assert exc_info.value.status_code == 400
    assert "共享用户不存在或已禁用: 10" in exc_info.value.detail
    db.flush.assert_not_awaited()
