import json
import logging
import inspect
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.models.metadata import MetaDataset, MetaTable
from app.models.permission import Role, UserRoleRelation
from app.models.saved_report import (
    PortalSavedReport,
    PortalSavedReportDigestDelivery,
    PortalSavedReportRun,
    PortalSavedReportSubscription,
    PortalSavedReportShare,
    PortalSavedReportUserPref,
)
from app.models.user import User
from app.schemas.response import StandardResponse
from app.services.ai.chatbi_sql_user_messages import map_sql_tool_error_for_user
from app.services.sql_query_execution_service import (
    attach_permission_notice_to_payload,
    execute_sql_query_core,
)
from app.services.saved_report_subscription_service import schedule_to_cron

logger = logging.getLogger(__name__)

router = APIRouter()

class SaveReportRequest(BaseModel):
    title: str = Field(..., description="报表自定义标题")
    description: Optional[str] = Field(None, description="报表描述")
    sql_content: str = Field(..., description="只读 SELECT 语句")
    dataset_id: Optional[int] = Field(None, description="数据集 ID")
    data_source: str = Field(..., description="数据源标识，如 default_clickhouse、mysql_oa")
    original_query: Optional[str] = Field(None, description="原始提问语句")
    mode: str = Field("static_sql", description="报表模式：static_sql 或 param_sql")
    sql_template: Optional[str] = Field(None, description="参数化 SQL 模板")
    params_schema: List[Dict[str, Any]] = Field(default_factory=list, description="运行参数定义")
    default_params: Dict[str, Any] = Field(default_factory=dict, description="默认运行参数")
    analysis_mode: str = Field("manual", description="执行后分析模式：manual 或 auto")
    tags: List[str] = Field(default_factory=list, description="报表标签")


class ExecuteReportRequest(BaseModel):
    params: Dict[str, Any] = Field(default_factory=dict, description="本次运行参数")
    analysis_mode: Optional[str] = Field(None, description="覆盖报表默认分析模式")


class SavedReportSubscriptionRequest(BaseModel):
    schedule_type: str
    time_value: Optional[str] = None
    weekday: Optional[int] = None
    monthday: Optional[int] = None
    cron_expr: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    ai_analysis_enabled: bool = True
    analysis_instruction: Optional[str] = Field(None, max_length=500)
    notify_on_success: bool = False
    notify_on_failure: bool = True
    external_channels: List[str] = Field(default_factory=list)


class SavedReportPreview(BaseModel):
    report_id: str
    rendered_sql: str
    params: Dict[str, Any] = Field(default_factory=dict)
    data_source: str
    dataset_id: Optional[int] = None
    permission_status: str = "unknown"
    permission_message: Optional[str] = None
    can_run: bool = True


class UpdateReportRequest(BaseModel):
    title: Optional[str] = Field(None, description="报表自定义标题")
    description: Optional[str] = Field(None, description="报表描述")
    sql_content: Optional[str] = Field(None, description="只读 SELECT 语句")
    dataset_id: Optional[int] = Field(None, description="数据集 ID")
    data_source: Optional[str] = Field(None, description="数据源标识，如 default_clickhouse、mysql_oa")
    original_query: Optional[str] = Field(None, description="原始提问语句")
    mode: Optional[str] = Field(None, description="报表模式：static_sql 或 param_sql")
    sql_template: Optional[str] = Field(None, description="参数化 SQL 模板")
    params_schema: Optional[List[Dict[str, Any]]] = Field(None, description="运行参数定义")
    default_params: Optional[Dict[str, Any]] = Field(None, description="默认运行参数")
    analysis_mode: Optional[str] = Field(None, description="执行后分析模式：manual 或 auto")
    tags: Optional[List[str]] = Field(None, description="报表标签")


class ShareTarget(BaseModel):
    target_type: str = Field(..., description="共享目标类型：user 或 role")
    target_id: int = Field(..., description="共享目标 ID")
    permission: str = Field("run", description="共享权限，当前仅支持 run")
    target_name: Optional[str] = Field(None, description="共享目标展示名称")


class UpdateReportSharesRequest(BaseModel):
    targets: List[ShareTarget] = Field(default_factory=list, description="共享目标列表")


class UpdateReportPreferenceRequest(BaseModel):
    is_favorite: Optional[bool] = Field(None, description="是否收藏")
    pinned: Optional[bool] = Field(None, description="是否置顶")


class SavedReportItem(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    sql_content: str
    dataset_id: Optional[int]
    data_source: str
    original_query: Optional[str]
    created_at: str
    updated_at: Optional[str] = None
    mode: str = "static_sql"
    sql_template: Optional[str] = None
    params_schema: List[Dict[str, Any]] = Field(default_factory=list)
    default_params: Dict[str, Any] = Field(default_factory=dict)
    analysis_mode: str = "manual"
    tags: List[str] = Field(default_factory=list)
    owner_user_id: Optional[int] = None
    owner_name: Optional[str] = None
    visibility: str = "private"
    status: str = "active"
    last_run_at: Optional[str] = None
    last_success_at: Optional[str] = None
    last_error: Optional[str] = None
    is_owner: bool = False
    share_targets: List[ShareTarget] = Field(default_factory=list)
    share_summary: Optional[str] = None
    run_permission_status: str = "unknown"
    run_permission_message: Optional[str] = None
    can_run: bool = True
    is_favorite: bool = False
    pinned_at: Optional[str] = None
    last_viewed_at: Optional[str] = None
    user_run_count: int = 0
    user_last_run_at: Optional[str] = None
    subscription_status: Optional[str] = None
    subscription_cron_expr: Optional[str] = None
    subscription_next_run_at: Optional[str] = None


class SavedReportRunSummary(BaseModel):
    id: int
    report_id: str
    user_id: int
    trigger_type: str = "manual"
    task_id: Optional[int] = None
    status: str
    resolved_params: Dict[str, Any] = Field(default_factory=dict)
    row_count: Optional[int] = None
    snapshot_row_count: int = 0
    permission_notice: Optional[Dict[str, Any]] = None
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class SavedReportRunDetail(SavedReportRunSummary):
    executed_sql: Optional[str] = None
    data_source: Optional[str] = None
    dataset_name: Optional[str] = None
    result_snapshot: Optional[Dict[str, Any]] = None
    deliveries: List[Dict[str, Any]] = Field(default_factory=list)


class ReportParameterError(ValueError):
    pass


_SQL_GATE_ERROR_PREFIXES = ("[Validation Failed]", "[Permission Denied]", "[Security Error]")
_DATE_LITERAL_PATTERN = r"'(\d{4}-\d{2}-\d{2})(?:\s+\d{2}:\d{2}:\d{2})?'"
_MONTH_LITERAL_PATTERN = r"'(\d{4}-\d{2})'"


def _saved_report_sql_error_detail(raw_error: Any) -> str:
    raw_text = raw_error if isinstance(raw_error, str) else json.dumps(raw_error, ensure_ascii=False)
    return map_sql_tool_error_for_user(raw_text).content


def _saved_report_sql_error_status(raw_error: Any) -> int:
    raw_text = raw_error if isinstance(raw_error, str) else json.dumps(raw_error, ensure_ascii=False)
    if "[Permission Denied]" in raw_text or "[Security Error]" in raw_text:
        return status.HTTP_403_FORBIDDEN
    return status.HTTP_400_BAD_REQUEST


def _clean_tags(tags: Optional[List[str]]) -> List[str]:
    cleaned: List[str] = []
    seen = set()
    for raw in tags or []:
        tag = str(raw or "").strip()
        if not tag or tag in seen:
            continue
        cleaned.append(tag[:32])
        seen.add(tag)
        if len(cleaned) >= 12:
            break
    return cleaned


def _dt_to_iso(value: Any) -> Optional[str]:
    return value.isoformat() if value else None


def _build_saved_report_result_snapshot(
    parsed: Any,
    *,
    limit: int = 200,
) -> Tuple[Dict[str, Any], int, int]:
    columns: List[Any] = []
    rows: List[Any] = []
    explicit_total: Optional[int] = None

    if isinstance(parsed, list):
        rows = parsed
    elif isinstance(parsed, dict):
        raw_columns = parsed.get("columns")
        if isinstance(raw_columns, list):
            columns = raw_columns
        for key in ("rows", "items", "data", "records", "result"):
            candidate = parsed.get(key)
            if isinstance(candidate, list):
                rows = candidate
                break
        for key in ("total", "total_count", "row_count"):
            candidate = parsed.get(key)
            if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0:
                explicit_total = candidate
                break
    elif parsed is not None:
        rows = [{"value": str(parsed)[:10000]}]

    if not columns and rows and isinstance(rows[0], dict):
        columns = list(rows[0].keys())
    snapshot_rows = rows[: max(0, limit)]
    row_count = max(len(rows), explicit_total or 0)
    return {"columns": columns, "rows": snapshot_rows}, row_count, len(snapshot_rows)


def _saved_report_permission_snapshot(permission_notice: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    allowed = ("row_filter_applied", "dataset_name", "rule_count", "message")
    snapshot = {key: permission_notice[key] for key in allowed if key in permission_notice}
    return snapshot or None


def _saved_report_run_to_summary(row: PortalSavedReportRun) -> SavedReportRunSummary:
    return SavedReportRunSummary(
        id=int(row.id),
        report_id=row.report_id,
        user_id=int(row.user_id),
        trigger_type=row.trigger_type or "manual",
        task_id=row.task_id,
        status=row.status,
        resolved_params=row.resolved_params or {},
        row_count=row.row_count,
        snapshot_row_count=row.snapshot_row_count or 0,
        permission_notice=row.permission_notice,
        duration_ms=row.duration_ms,
        error_message=row.error_message,
        started_at=_dt_to_iso(row.started_at),
        finished_at=_dt_to_iso(row.finished_at),
    )


def _saved_report_run_to_detail(
    row: PortalSavedReportRun,
    deliveries: Optional[List[PortalSavedReportDigestDelivery]] = None,
) -> SavedReportRunDetail:
    summary = _saved_report_run_to_summary(row)
    return SavedReportRunDetail(
        **summary.model_dump(),
        executed_sql=row.executed_sql,
        data_source=row.data_source,
        dataset_name=row.dataset_name,
        result_snapshot=row.result_snapshot,
        deliveries=[
            {
                "id": int(delivery.id),
                "channel": delivery.channel,
                "title": delivery.title,
                "content": delivery.content,
                "status": delivery.status,
                "error_message": delivery.error_message,
                "ai_status": delivery.ai_status,
                "analysis_instruction": (delivery.digest_payload or {}).get("analysis_instruction"),
                "sent_at": _dt_to_iso(delivery.sent_at),
            }
            for delivery in deliveries or []
        ],
    )


def _finish_saved_report_run_error(run: PortalSavedReportRun, error: Any, started: float) -> None:
    run.status = "error"
    run.error_message = str(error)[:10000]
    run.duration_ms = max(0, int((time.perf_counter() - started) * 1000))
    run.finished_at = datetime.now()


def _finish_saved_report_run_success(
    run: PortalSavedReportRun,
    parsed: Any,
    permission_notice: Dict[str, Any],
    started: float,
) -> None:
    snapshot, row_count, snapshot_row_count = _build_saved_report_result_snapshot(parsed)
    run.status = "success"
    run.row_count = row_count
    run.snapshot_row_count = snapshot_row_count
    run.result_snapshot = snapshot
    run.permission_notice = _saved_report_permission_snapshot(permission_notice)
    run.duration_ms = max(0, int((time.perf_counter() - started) * 1000))
    run.finished_at = datetime.now()


def _chatbi_user_dimensions(user_info: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    return {
        "id": user_id,
        "user_name": user_info.get("user_name"),
        "real_name": user_info.get("real_name"),
        "role": user_info.get("role"),
        "dept_code": user_info.get("dept_code"),
        "org_path": user_info.get("org_path"),
        "extra_data": user_info.get("extra_data"),
    }


async def _dataset_name_from_id(db: AsyncSession, dataset_id: Optional[int]) -> Optional[str]:
    if not dataset_id:
        return None
    result = await db.execute(select(MetaDataset.name).where(MetaDataset.id == dataset_id))
    dataset_name = result.scalar_one_or_none()
    if inspect.isawaitable(dataset_name):
        dataset_name = await dataset_name
    if not isinstance(dataset_name, str):
        return None
    return str(dataset_name).strip() if dataset_name else None


def _attach_permission_notice(payload: Dict[str, Any], permission_notice: Dict[str, Any]) -> Dict[str, Any]:
    return attach_permission_notice_to_payload(payload, permission_notice)


def _detect_default_date_range_template(sql: str) -> Tuple[Optional[str], List[Dict[str, Any]], Dict[str, Any]]:
    matches = list(re.finditer(_DATE_LITERAL_PATTERN, sql))
    if len(matches) >= 2:
        first, second = matches[0], matches[1]
        first_has_time = bool(re.search(r"\d{2}:\d{2}:\d{2}", first.group(0)))
        second_has_time = bool(re.search(r"\d{2}:\d{2}:\d{2}", second.group(0)))
        start_param = "start_datetime" if first_has_time else "start_date"
        end_param = "end_datetime" if second_has_time else "end_date"
        template = f"{sql[:first.start()]}{{{{{start_param}}}}}{sql[first.end():second.start()]}{{{{{end_param}}}}}{sql[second.end():]}"
        params_schema = [
            {
                "name": "date_range",
                "type": "date_range",
                "label": "日期范围",
                "default": "month_start_to_today",
                "options": ["today", "yesterday", "last_7_days", "month_start_to_today", "custom_range"],
            }
        ]
        return template, params_schema, {"date_range": "month_start_to_today"}

    month_matches = list(re.finditer(_MONTH_LITERAL_PATTERN, sql))
    if len(month_matches) < 2:
        return None, [], {}

    first, second = month_matches[0], month_matches[1]
    template = f"{sql[:first.start()]}{{{{start_month}}}}{sql[first.end():second.start()]}{{{{end_month}}}}{sql[second.end():]}"
    params_schema = [
        {
            "name": "month_range",
            "type": "month_range",
            "label": "月份范围",
            "default": "last_6_completed_months",
            "options": ["last_6_completed_months", "year_start_to_current_month", "custom_month_range"],
        }
    ]
    return template, params_schema, {"month_range": "last_6_completed_months"}


def _build_saved_report_item(
    *,
    report_id: str,
    title: str,
    description: Optional[str],
    sql_clean: str,
    dataset_id: Optional[int],
    data_source: str,
    original_query: Optional[str],
    created_at: str,
    updated_at: Optional[str] = None,
    owner_user_id: Optional[int] = None,
    owner_name: Optional[str] = None,
    visibility: str = "private",
    status_value: str = "active",
    tags: Optional[List[str]] = None,
    last_run_at: Optional[str] = None,
    last_success_at: Optional[str] = None,
    last_error: Optional[str] = None,
    is_owner: bool = False,
    share_targets: Optional[List[ShareTarget]] = None,
    body: SaveReportRequest,
) -> SavedReportItem:
    mode = body.mode if body.mode in {"static_sql", "param_sql"} else "static_sql"
    sql_template = body.sql_template
    params_schema = list(body.params_schema or [])
    default_params = dict(body.default_params or {})

    if not sql_template and not params_schema:
        detected_template, detected_schema, detected_defaults = _detect_default_date_range_template(sql_clean)
        if detected_template:
            mode = "param_sql"
            sql_template = detected_template
            params_schema = detected_schema
            default_params = detected_defaults

    if sql_template or params_schema:
        mode = "param_sql"

    analysis_mode = body.analysis_mode if body.analysis_mode in {"manual", "auto"} else "manual"

    return SavedReportItem(
        id=report_id,
        title=title,
        description=description,
        sql_content=sql_clean,
        dataset_id=dataset_id,
        data_source=data_source,
        original_query=original_query,
        created_at=created_at,
        updated_at=updated_at,
        mode=mode,
        sql_template=sql_template,
        params_schema=params_schema,
        default_params=default_params,
        analysis_mode=analysis_mode,
        tags=_clean_tags(tags if tags is not None else body.tags),
        owner_user_id=owner_user_id,
        owner_name=owner_name,
        visibility=visibility,
        status=status_value,
        last_run_at=last_run_at,
        last_success_at=last_success_at,
        last_error=last_error,
        is_owner=is_owner,
        share_targets=share_targets or [],
    )


def _normalize_json_list(value: Any) -> List[Dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return list(value) if isinstance(value, list) else []


def _normalize_json_dict(value: Any) -> Dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _share_targets_from_report(report: PortalSavedReport) -> List[ShareTarget]:
    return [
        ShareTarget(
            target_type=share.target_type,
            target_id=int(share.target_id),
            permission=share.permission or "run",
        )
        for share in (report.shares or [])
    ]


def _format_share_summary(share_targets: List[ShareTarget]) -> Optional[str]:
    user_count = sum(1 for target in share_targets if target.target_type == "user")
    role_count = sum(1 for target in share_targets if target.target_type == "role")
    parts: List[str] = []
    if user_count:
        parts.append(f"{user_count} 人")
    if role_count:
        parts.append(f"{role_count} 个角色")
    return f"已共享给 {' / '.join(parts)}" if parts else None


def _report_row_to_item(report: PortalSavedReport, *, current_user_id: int) -> SavedReportItem:
    share_targets = _share_targets_from_report(report)
    is_owner = int(report.owner_user_id) == int(current_user_id)
    return SavedReportItem(
        id=report.id,
        title=report.title,
        description=report.description,
        sql_content=report.sql_content,
        dataset_id=report.dataset_id,
        data_source=report.data_source,
        original_query=report.original_query,
        created_at=_dt_to_iso(report.created_at) or "",
        updated_at=_dt_to_iso(report.updated_at),
        mode=report.mode or "static_sql",
        sql_template=report.sql_template,
        params_schema=_normalize_json_list(report.params_schema),
        default_params=_normalize_json_dict(report.default_params),
        analysis_mode=report.analysis_mode or "manual",
        tags=_clean_tags(report.tags if isinstance(report.tags, list) else []),
        owner_user_id=int(report.owner_user_id) if report.owner_user_id is not None else None,
        owner_name=report.owner_name,
        visibility=report.visibility or "private",
        status=report.status or "active",
        last_run_at=_dt_to_iso(report.last_run_at),
        last_success_at=_dt_to_iso(report.last_success_at),
        last_error=report.last_error,
        is_owner=is_owner,
        share_targets=share_targets,
        share_summary=_format_share_summary(share_targets),
        run_permission_status="allowed" if is_owner else "unknown",
        run_permission_message=None,
        can_run=True,
    )


def _parse_iso_date_param(value: Any, name: str) -> date:
    if not isinstance(value, str):
        raise ReportParameterError(f"参数 {name} 必须是 YYYY-MM-DD 日期")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ReportParameterError(f"参数 {name} 必须是 YYYY-MM-DD 日期") from exc


def _parse_iso_month_param(value: Any, name: str) -> date:
    if not isinstance(value, str):
        raise ReportParameterError(f"参数 {name} 必须是 YYYY-MM 月份")
    try:
        return date.fromisoformat(f"{value}-01")
    except ValueError as exc:
        raise ReportParameterError(f"参数 {name} 必须是 YYYY-MM 月份") from exc


def _add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    return date(month_index // 12, month_index % 12 + 1, 1)


def _format_month(value: date) -> str:
    return value.strftime("%Y-%m")


def _resolve_builtin_date_range(params: Dict[str, Any], today: date) -> Dict[str, str]:
    date_range = str(params.get("date_range") or "month_start_to_today")
    if date_range == "today":
        start = today
        end = today + timedelta(days=1)
        end_inclusive = today
    elif date_range == "yesterday":
        start = today - timedelta(days=1)
        end = today
        end_inclusive = start
    elif date_range == "last_7_days":
        start = today - timedelta(days=6)
        end = today + timedelta(days=1)
        end_inclusive = today
    elif date_range == "month_start_to_today":
        start = today.replace(day=1)
        end = today + timedelta(days=1)
        end_inclusive = today
    elif date_range == "custom_range":
        start = _parse_iso_date_param(params.get("start_date"), "start_date")
        end = _parse_iso_date_param(params.get("end_date"), "end_date")
        if end <= start:
            raise ReportParameterError("结束日期必须晚于开始日期")
        end_inclusive = end
    else:
        raise ReportParameterError("不支持的日期范围")

    return {
        "date_range": date_range,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "start_datetime": f"{start.isoformat()} 00:00:00",
        "end_datetime": f"{end_inclusive.isoformat()} 23:59:59",
    }


def _resolve_builtin_month_range(params: Dict[str, Any], today: date) -> Dict[str, str]:
    month_range = str(params.get("month_range") or "last_6_completed_months")
    current_month = today.replace(day=1)
    if month_range == "last_6_completed_months":
        end = _add_months(current_month, -1)
        start = _add_months(end, -5)
    elif month_range == "year_start_to_current_month":
        start = date(today.year, 1, 1)
        end = current_month
    elif month_range == "custom_month_range":
        start = _parse_iso_month_param(params.get("start_month"), "start_month")
        end = _parse_iso_month_param(params.get("end_month"), "end_month")
        if end < start:
            raise ReportParameterError("结束月份不能早于开始月份")
    else:
        raise ReportParameterError("不支持的月份范围")

    return {
        "month_range": month_range,
        "start_month": _format_month(start),
        "end_month": _format_month(end),
    }


def _allowed_template_params(report: SavedReportItem) -> set[str]:
    allowed = set()
    for item in report.params_schema:
        name = str(item.get("name") or "").strip()
        param_type = str(item.get("type") or "").strip()
        if name:
            allowed.add(name)
        if param_type == "date_range" or name == "date_range":
            allowed.update({"date_range", "start_date", "end_date", "start_datetime", "end_datetime"})
        if param_type == "month_range" or name == "month_range":
            allowed.update({"month_range", "start_month", "end_month"})
    return allowed


def _resolve_report_sql(
    report: SavedReportItem,
    *,
    body: ExecuteReportRequest,
    today: Optional[date] = None,
) -> Tuple[str, Dict[str, Any]]:
    if report.mode != "param_sql":
        return report.sql_content, {}

    template = report.sql_template or report.sql_content
    allowed = _allowed_template_params(report)
    placeholders = set(re.findall(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", template))
    unknown = placeholders - allowed
    if unknown:
        raise ReportParameterError(f"不允许的报表参数: {', '.join(sorted(unknown))}")

    merged_params = dict(report.default_params or {})
    merged_params.update(body.params or {})

    resolved_params: Dict[str, Any] = {}
    date_placeholder_names = {"start_date", "end_date", "start_datetime", "end_datetime"}
    month_placeholder_names = {"start_month", "end_month"}
    if {"start_date", "end_date", "start_datetime", "end_datetime"} & placeholders or merged_params.get("date_range"):
        resolved_params.update(_resolve_builtin_date_range(merged_params, today or date.today()))
    if month_placeholder_names & placeholders or merged_params.get("month_range"):
        resolved_params.update(_resolve_builtin_month_range(merged_params, today or date.today()))
    for key, value in merged_params.items():
        if key not in resolved_params:
            resolved_params[key] = value

    def replace_placeholder(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        if name in {"start_date", "end_date", "start_datetime", "end_datetime"}:
            return f"'{resolved_params[name]}'"
        if name in {"start_month", "end_month"}:
            return f"'{resolved_params[name]}'"
        raise ReportParameterError(f"参数 {name} 暂不支持直接写入 SQL 模板")

    rendered_sql = re.sub(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", replace_placeholder, template)
    used_date_placeholders = placeholders & date_placeholder_names
    used_month_placeholders = placeholders & month_placeholder_names
    returned_params: Dict[str, Any] = {}
    if "date_range" in resolved_params and (used_date_placeholders or merged_params.get("date_range")):
        returned_params["date_range"] = resolved_params["date_range"]
    if "month_range" in resolved_params and (used_month_placeholders or merged_params.get("month_range")):
        returned_params["month_range"] = resolved_params["month_range"]
    for name in sorted(used_date_placeholders):
        returned_params[name] = resolved_params[name]
    for name in sorted(used_month_placeholders):
        returned_params[name] = resolved_params[name]
    for key, value in merged_params.items():
        if key not in returned_params and key not in date_placeholder_names and key not in month_placeholder_names:
            returned_params[key] = value

    return rendered_sql, returned_params


async def _precheck_saved_report_sql_permissions(
    db: AsyncSession,
    *,
    report: SavedReportItem,
    user_info: Dict[str, Any],
    user_id: int,
) -> None:
    try:
        sql_to_check, _ = _resolve_report_sql(
            report,
            body=ExecuteReportRequest(params=report.default_params or {}),
        )
    except ReportParameterError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    try:
        dataset_name = await _dataset_name_from_id(db, report.dataset_id)
        result_str = await execute_sql_query_core(
            db,
            sql=sql_to_check,
            data_source=report.data_source,
            dataset_name=dataset_name,
            user_id=user_id,
            user_dimensions=_chatbi_user_dimensions(user_info, user_id),
            trace_logs=None,
            api_key=None,
            agent_context=None,
            dry_run=False,
            auth_check_only=True,
            is_admin=user_info.get("role") == "admin",
            bypass_table_auth=False,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to precheck saved report SQL permissions: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"报表 SQL 权限校验失败: {e}",
        ) from e

    raw_res = str(result_str or "").strip()
    if raw_res.startswith(_SQL_GATE_ERROR_PREFIXES):
        raise HTTPException(
            status_code=_saved_report_sql_error_status(raw_res),
            detail=_saved_report_sql_error_detail(raw_res),
        )

    try:
        parsed = json.loads(raw_res)
    except json.JSONDecodeError:
        return

    if isinstance(parsed, dict) and parsed.get("allowed") is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_saved_report_sql_error_detail(parsed),
        )


async def _enrich_saved_report_share_targets(db: AsyncSession, reports: List[SavedReportItem]) -> None:
    user_ids = sorted({
        int(target.target_id)
        for report in reports
        for target in report.share_targets
        if target.target_type == "user"
    })
    role_ids = sorted({
        int(target.target_id)
        for report in reports
        for target in report.share_targets
        if target.target_type == "role"
    })

    user_names: Dict[int, str] = {}
    role_names: Dict[int, str] = {}
    if user_ids:
        result = await db.execute(select(User).where(User.id.in_(user_ids)))
        for user in result.scalars().all():
            user_names[int(user.id)] = user.real_name or user.user_name or f"用户 {user.id}"
    if role_ids:
        result = await db.execute(select(Role).where(Role.id.in_(role_ids)))
        for role in result.scalars().all():
            role_names[int(role.id)] = role.name or role.code or f"角色 {role.id}"

    for report in reports:
        for target in report.share_targets:
            if target.target_type == "user":
                target.target_name = user_names.get(int(target.target_id)) or f"用户 {target.target_id}"
            elif target.target_type == "role":
                target.target_name = role_names.get(int(target.target_id)) or f"角色 {target.target_id}"
        report.share_summary = _format_share_summary(report.share_targets)


async def _annotate_saved_report_run_permissions(
    db: AsyncSession,
    reports: List[SavedReportItem],
    *,
    user_info: Dict[str, Any],
    user_id: int,
) -> None:
    for report in reports:
        if report.is_owner:
            report.run_permission_status = "allowed"
            report.run_permission_message = None
            report.can_run = True
            continue

        try:
            await _precheck_saved_report_sql_permissions(db, report=report, user_info=user_info, user_id=user_id)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_403_FORBIDDEN:
                report.run_permission_status = "denied"
                report.run_permission_message = str(exc.detail or "暂无该报表所需数据权限，无法运行。")
                report.can_run = False
            else:
                report.run_permission_status = "unknown"
                report.run_permission_message = str(exc.detail or "运行权限暂未确认。")
                report.can_run = True
        except Exception as exc:
            logger.warning("Failed to precheck saved report run permission for %s: %s", report.id, exc)
            report.run_permission_status = "unknown"
            report.run_permission_message = "运行权限暂未确认，点击执行时会再次校验。"
            report.can_run = True
        else:
            report.run_permission_status = "allowed"
            report.run_permission_message = None
            report.can_run = True


def _apply_saved_report_pref(item: SavedReportItem, pref: Any) -> None:
    if not pref:
        return
    item.is_favorite = bool(getattr(pref, "is_favorite", False))
    item.pinned_at = _dt_to_iso(getattr(pref, "pinned_at", None))
    item.last_viewed_at = _dt_to_iso(getattr(pref, "last_viewed_at", None))
    item.user_run_count = int(getattr(pref, "run_count", 0) or 0)
    item.user_last_run_at = _dt_to_iso(getattr(pref, "last_run_at", None))


def _sort_saved_reports_for_user(reports: List[SavedReportItem]) -> List[SavedReportItem]:
    def sort_key(report: SavedReportItem) -> Tuple[int, str, str]:
        return (
            1 if report.pinned_at else 0,
            report.pinned_at or report.updated_at or report.created_at or "",
            report.user_last_run_at or report.updated_at or report.created_at or "",
        )

    return sorted(reports, key=sort_key, reverse=True)


async def _get_report_user_prefs(
    db: AsyncSession,
    *,
    user_id: int,
    report_ids: List[str],
) -> Dict[str, PortalSavedReportUserPref]:
    if not report_ids:
        return {}
    result = await db.execute(
        select(PortalSavedReportUserPref).where(
            PortalSavedReportUserPref.user_id == user_id,
            PortalSavedReportUserPref.report_id.in_(report_ids),
        )
    )
    return {str(pref.report_id): pref for pref in result.scalars().all()}


async def _enrich_saved_report_user_prefs(
    db: AsyncSession,
    reports: List[SavedReportItem],
    *,
    user_id: int,
) -> None:
    prefs = await _get_report_user_prefs(db, user_id=user_id, report_ids=[report.id for report in reports])
    for report in reports:
        _apply_saved_report_pref(report, prefs.get(report.id))


def _apply_saved_report_subscription_summaries(
    reports: List[SavedReportItem],
    subscriptions: List[PortalSavedReportSubscription],
) -> None:
    by_report_id = {subscription.report_id: subscription for subscription in subscriptions}
    for report in reports:
        subscription = by_report_id.get(report.id) if report.is_owner else None
        report.subscription_status = subscription.status if subscription else None
        report.subscription_cron_expr = subscription.cron_expr if subscription else None
        report.subscription_next_run_at = _dt_to_iso(subscription.next_run_at) if subscription else None


async def _enrich_saved_report_subscriptions(
    db: AsyncSession,
    reports: List[SavedReportItem],
) -> None:
    owner_report_ids = [report.id for report in reports if report.is_owner]
    if not owner_report_ids:
        return
    result = await db.execute(
        select(PortalSavedReportSubscription).where(
            PortalSavedReportSubscription.report_id.in_(owner_report_ids)
        )
    )
    _apply_saved_report_subscription_summaries(reports, list(result.scalars().all()))


async def _get_or_create_report_user_pref(
    db: AsyncSession,
    *,
    report_id: str,
    user_id: int,
) -> PortalSavedReportUserPref:
    result = await db.execute(
        select(PortalSavedReportUserPref).where(
            PortalSavedReportUserPref.report_id == report_id,
            PortalSavedReportUserPref.user_id == user_id,
        )
    )
    pref = result.scalar_one_or_none()
    if pref:
        return pref
    try:
        async with db.begin_nested():
            pref = PortalSavedReportUserPref(report_id=report_id, user_id=user_id)
            db.add(pref)
            await db.flush()
    except IntegrityError:
        result = await db.execute(
            select(PortalSavedReportUserPref).where(
                PortalSavedReportUserPref.report_id == report_id,
                PortalSavedReportUserPref.user_id == user_id,
            )
        )
        pref = result.scalar_one()
    return pref


async def _touch_saved_report_view(db: AsyncSession, *, report_id: str, user_id: int) -> None:
    pref = await _get_or_create_report_user_pref(db, report_id=report_id, user_id=user_id)
    pref.last_viewed_at = datetime.now()
    await db.flush()


async def _record_saved_report_run(db: AsyncSession, *, report_id: str, user_id: int) -> None:
    pref = await _get_or_create_report_user_pref(db, report_id=report_id, user_id=user_id)
    pref.run_count = int(pref.run_count or 0) + 1
    pref.last_run_at = datetime.now()


async def _get_user_role_ids(db: AsyncSession, user_id: int) -> List[int]:
    result = await db.execute(select(UserRoleRelation.role_id).where(UserRoleRelation.user_id == user_id))
    return [int(role_id) for role_id in result.scalars().all()]


async def _get_report_for_user(
    db: AsyncSession,
    *,
    report_id: str,
    user_id: int,
    role_ids: Optional[List[int]] = None,
    require_owner: bool = False,
) -> PortalSavedReport:
    stmt = (
        select(PortalSavedReport)
        .options(selectinload(PortalSavedReport.shares))
        .where(PortalSavedReport.id == report_id)
    )
    result = await db.execute(stmt)
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报表不存在或无权访问")

    if int(report.owner_user_id) == int(user_id):
        return report
    if require_owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有报表所有人可以执行该操作")

    role_id_set = set(role_ids if role_ids is not None else await _get_user_role_ids(db, user_id))
    for share in report.shares or []:
        if share.target_type == "user" and int(share.target_id) == int(user_id):
            return report
        if share.target_type == "role" and int(share.target_id) in role_id_set:
            return report

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报表不存在或无权访问")


async def _validate_share_targets(db: AsyncSession, targets: List[ShareTarget]) -> None:
    user_ids = sorted({int(target.target_id) for target in targets if target.target_type == "user"})
    role_ids = sorted({int(target.target_id) for target in targets if target.target_type == "role"})

    if user_ids:
        result = await db.execute(select(User.id).where(User.id.in_(user_ids)).where(User.status == 1))
        found_user_ids = {int(user_id) for user_id in result.scalars().all()}
        missing = [str(user_id) for user_id in user_ids if user_id not in found_user_ids]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"共享用户不存在或已禁用: {', '.join(missing)}",
            )

    if role_ids:
        result = await db.execute(select(Role.id).where(Role.id.in_(role_ids)))
        found_role_ids = {int(role_id) for role_id in result.scalars().all()}
        missing = [str(role_id) for role_id in role_ids if role_id not in found_role_ids]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"共享角色不存在: {', '.join(missing)}",
            )


async def _infer_dataset_and_data_source(
    db: AsyncSession,
    *,
    sql_content: str,
    dataset_id: Optional[int],
    data_source: Optional[str],
) -> Tuple[Optional[int], str]:
    normalized_data_source = str(data_source or "").strip()
    is_placeholder_source = not normalized_data_source or normalized_data_source == "default_clickhouse"
    if dataset_id and is_placeholder_source:
        try:
            res = await db.execute(select(MetaDataset.data_source).where(MetaDataset.id == dataset_id))
            inferred_source = res.scalar_one_or_none()
            if inferred_source:
                return dataset_id, str(inferred_source)
        except Exception as ex:
            logger.warning("Failed to auto-detect dataset source for saved report: %s", ex)
    if dataset_id or not is_placeholder_source:
        return dataset_id, normalized_data_source or "default_clickhouse"

    words = set(re.findall(r"\b[a-zA-Z0-9_]+\b", sql_content.lower()))
    if words:
        try:
            stmt = (
                select(MetaTable.dataset_id, MetaDataset.data_source)
                .join(MetaDataset, MetaTable.dataset_id == MetaDataset.id)
                .where(MetaTable.physical_name.in_(words))
                .where(MetaTable.status == 1)
                .limit(1)
            )
            res = await db.execute(stmt)
            row = res.first()
            if row:
                return row[0], row[1] or "default_clickhouse"
        except Exception as ex:
            logger.warning("Failed to auto-detect dataset for saved report: %s", ex)

    return dataset_id, normalized_data_source or "default_clickhouse"


def _apply_report_item_to_row(report: PortalSavedReport, item: SavedReportItem) -> None:
    report.title = item.title
    report.description = item.description
    report.sql_content = item.sql_content
    report.dataset_id = item.dataset_id
    report.data_source = item.data_source
    report.original_query = item.original_query
    report.mode = item.mode
    report.sql_template = item.sql_template
    report.params_schema = item.params_schema
    report.default_params = item.default_params
    report.analysis_mode = item.analysis_mode
    report.tags = item.tags
    report.visibility = item.visibility
    report.status = item.status


async def _raise_saved_report_sql_error(db: AsyncSession, report_row: PortalSavedReport, raw_error: Any) -> None:
    raw_text = raw_error if isinstance(raw_error, str) else json.dumps(raw_error, ensure_ascii=False)
    report_row.last_run_at = datetime.now()
    report_row.last_error = raw_text
    report_row.status = "error"
    await db.flush()
    raise HTTPException(
        status_code=_saved_report_sql_error_status(raw_text),
        detail=_saved_report_sql_error_detail(raw_text),
    )

@router.post(
    "",
    response_model=StandardResponse[SavedReportItem],
    summary="暂存黄金 SQL 报表到数据门户",
)
async def save_report(
    body: SaveReportRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = int(user_info["user_id"])
    dataset_id, data_source = await _infer_dataset_and_data_source(
        db,
        sql_content=body.sql_content,
        dataset_id=body.dataset_id,
        data_source=body.data_source,
    )
    report_id = f"rpt_{uuid.uuid4().hex[:12]}"
    now_str = datetime.now(timezone.utc).isoformat()

    sql_clean = body.sql_content.strip()
    if sql_clean.startswith("[Executed SQL]:"):
        sql_clean = sql_clean[len("[Executed SQL]:"):].strip()

    report_item = _build_saved_report_item(
        report_id=report_id,
        title=body.title.strip(),
        description=(body.description or "").strip() or None,
        sql_clean=sql_clean,
        dataset_id=dataset_id,
        data_source=data_source,
        original_query=body.original_query,
        created_at=now_str,
        owner_user_id=user_id,
        owner_name=user_info.get("real_name") or user_info.get("user_name"),
        tags=body.tags,
        is_owner=True,
        body=body,
    )

    await _precheck_saved_report_sql_permissions(db, report=report_item, user_info=user_info, user_id=user_id)

    report_row = PortalSavedReport(
        id=report_item.id,
        owner_user_id=user_id,
        owner_name=report_item.owner_name,
    )
    _apply_report_item_to_row(report_row, report_item)
    db.add(report_row)
    await db.flush()

    return StandardResponse(data=report_item)

@router.get(
    "",
    response_model=StandardResponse[List[SavedReportItem]],
    summary="获取当前用户的黄金 SQL 报表暂存列表",
)
async def get_saved_reports(
    scope: str = Query("all", pattern="^(all|my|shared)$"),
    tag: Optional[str] = None,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = int(user_info["user_id"])
    role_ids = await _get_user_role_ids(db, user_id)
    visible_conditions = [PortalSavedReport.owner_user_id == user_id]
    if role_ids:
        visible_conditions.append(
            PortalSavedReport.shares.any(
                (PortalSavedReportShare.target_type == "role")
                & (PortalSavedReportShare.target_id.in_(role_ids))
            )
        )
    visible_conditions.append(
        PortalSavedReport.shares.any(
            (PortalSavedReportShare.target_type == "user")
            & (PortalSavedReportShare.target_id == user_id)
        )
    )

    stmt = select(PortalSavedReport).options(selectinload(PortalSavedReport.shares))
    if scope == "my":
        stmt = stmt.where(PortalSavedReport.owner_user_id == user_id)
    elif scope == "shared":
        shared_conditions = [
            PortalSavedReport.shares.any(
                (PortalSavedReportShare.target_type == "user")
                & (PortalSavedReportShare.target_id == user_id)
            )
        ]
        if role_ids:
            shared_conditions.append(
                PortalSavedReport.shares.any(
                    (PortalSavedReportShare.target_type == "role")
                    & (PortalSavedReportShare.target_id.in_(role_ids))
                )
            )
        stmt = stmt.where(PortalSavedReport.owner_user_id != user_id).where(or_(*shared_conditions))
    else:
        stmt = stmt.where(or_(*visible_conditions))

    stmt = stmt.order_by(desc(PortalSavedReport.updated_at), desc(PortalSavedReport.created_at))
    result = await db.execute(stmt)
    reports = [_report_row_to_item(row, current_user_id=user_id) for row in result.scalars().unique().all()]
    if tag:
        reports = [report for report in reports if tag in report.tags]
    await _enrich_saved_report_share_targets(db, reports)
    await _annotate_saved_report_run_permissions(db, reports, user_info=user_info, user_id=user_id)
    await _enrich_saved_report_user_prefs(db, reports, user_id=user_id)
    await _enrich_saved_report_subscriptions(db, reports)
    return StandardResponse(data=_sort_saved_reports_for_user(reports))


@router.get(
    "/{report_id}",
    response_model=StandardResponse[SavedReportItem],
    summary="获取黄金 SQL 报表详情",
)
async def get_saved_report_detail(
    report_id: str,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = int(user_info["user_id"])
    role_ids = await _get_user_role_ids(db, user_id)
    report_row = await _get_report_for_user(db, report_id=report_id, user_id=user_id, role_ids=role_ids)
    item = _report_row_to_item(report_row, current_user_id=user_id)
    reports = [item]
    await _enrich_saved_report_share_targets(db, reports)
    await _annotate_saved_report_run_permissions(db, reports, user_info=user_info, user_id=user_id)
    await _enrich_saved_report_user_prefs(db, reports, user_id=user_id)
    await _enrich_saved_report_subscriptions(db, reports)
    try:
        await _touch_saved_report_view(db, report_id=report_id, user_id=user_id)
    except Exception as exc:
        logger.warning("Failed to record saved report view for %s: %s", report_id, exc)
    return StandardResponse(data=item)


@router.post(
    "/{report_id}/preview",
    response_model=StandardResponse[SavedReportPreview],
    summary="预览黄金 SQL 报表本次运行 SQL 与权限状态",
)
async def preview_saved_report(
    report_id: str,
    body: ExecuteReportRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = int(user_info["user_id"])
    role_ids = await _get_user_role_ids(db, user_id)
    report_row = await _get_report_for_user(db, report_id=report_id, user_id=user_id, role_ids=role_ids)
    report = _report_row_to_item(report_row, current_user_id=user_id)
    try:
        rendered_sql, rendered_params = _resolve_report_sql(report, body=body)
    except ReportParameterError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    preview = SavedReportPreview(
        report_id=report.id,
        rendered_sql=rendered_sql,
        params=rendered_params,
        data_source=report.data_source,
        dataset_id=report.dataset_id,
        permission_status="unknown",
        permission_message="运行权限暂未确认，执行时会再次校验。",
        can_run=True,
    )
    auth_report = report.model_copy(
        update={
            "sql_content": rendered_sql,
            "sql_template": None,
            "mode": "static_sql",
            "default_params": {},
        }
    )
    try:
        await _precheck_saved_report_sql_permissions(db, report=auth_report, user_info=user_info, user_id=user_id)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            preview.permission_status = "denied"
            preview.permission_message = str(exc.detail or "暂无该报表所需数据权限，无法运行。")
            preview.can_run = False
        else:
            preview.permission_status = "unknown"
            preview.permission_message = str(exc.detail or "运行权限暂未确认，执行时会再次校验。")
            preview.can_run = True
    else:
        preview.permission_status = "allowed"
        preview.permission_message = None
        preview.can_run = True
    return StandardResponse(data=preview)


@router.put(
    "/{report_id}/prefs",
    response_model=StandardResponse[SavedReportItem],
    summary="更新黄金 SQL 报表个人偏好",
)
async def update_saved_report_preferences(
    report_id: str,
    body: UpdateReportPreferenceRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = int(user_info["user_id"])
    role_ids = await _get_user_role_ids(db, user_id)
    report_row = await _get_report_for_user(db, report_id=report_id, user_id=user_id, role_ids=role_ids)
    pref = await _get_or_create_report_user_pref(db, report_id=report_id, user_id=user_id)
    now = datetime.now()
    if body.is_favorite is not None:
        pref.is_favorite = bool(body.is_favorite)
    if body.pinned is not None:
        pref.pinned_at = now if body.pinned else None
    await db.flush()

    item = _report_row_to_item(report_row, current_user_id=user_id)
    reports = [item]
    await _enrich_saved_report_share_targets(db, reports)
    await _annotate_saved_report_run_permissions(db, reports, user_info=user_info, user_id=user_id)
    _apply_saved_report_pref(item, pref)
    return StandardResponse(data=item)

@router.delete(
    "/{report_id}",
    response_model=StandardResponse[Dict[str, bool]],
    summary="删除暂存的黄金 SQL 报表",
)
async def delete_saved_report(
    report_id: str,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = int(user_info["user_id"])
    report = await _get_report_for_user(db, report_id=report_id, user_id=user_id, require_owner=True)
    await db.delete(report)
    await db.flush()
    return StandardResponse(data={"deleted": True})


@router.put(
    "/{report_id}",
    response_model=StandardResponse[SavedReportItem],
    summary="编辑暂存的黄金 SQL 报表",
)
async def update_saved_report(
    report_id: str,
    body: UpdateReportRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = int(user_info["user_id"])
    report_row = await _get_report_for_user(db, report_id=report_id, user_id=user_id, require_owner=True)
    existing = _report_row_to_item(report_row, current_user_id=user_id)

    fields_set = body.model_fields_set
    sql_clean = (body.sql_content if "sql_content" in fields_set and body.sql_content is not None else existing.sql_content).strip()
    if sql_clean.startswith("[Executed SQL]:"):
        sql_clean = sql_clean[len("[Executed SQL]:"):].strip()

    title = (body.title if "title" in fields_set and body.title is not None else existing.title).strip()
    description = (body.description if "description" in fields_set else existing.description)
    if description is not None:
        description = description.strip() or None
    data_source = body.data_source if "data_source" in fields_set and body.data_source is not None else existing.data_source
    dataset_id = body.dataset_id if "dataset_id" in fields_set else existing.dataset_id
    original_query = body.original_query if "original_query" in fields_set else existing.original_query
    tags = body.tags if "tags" in fields_set and body.tags is not None else existing.tags

    if data_source == "default_clickhouse":
        dataset_id, data_source = await _infer_dataset_and_data_source(
            db,
            sql_content=sql_clean,
            dataset_id=dataset_id,
            data_source=data_source,
        )

    mode = body.mode if "mode" in fields_set and body.mode is not None else existing.mode
    sql_template = body.sql_template if "sql_template" in fields_set else existing.sql_template
    params_schema = body.params_schema if "params_schema" in fields_set and body.params_schema is not None else existing.params_schema
    default_params = body.default_params if "default_params" in fields_set and body.default_params is not None else existing.default_params
    analysis_mode = body.analysis_mode if "analysis_mode" in fields_set and body.analysis_mode is not None else existing.analysis_mode

    if "sql_content" in fields_set and "sql_template" not in fields_set and "params_schema" not in fields_set:
        detected_template, detected_schema, detected_defaults = _detect_default_date_range_template(sql_clean)
        if detected_template:
            mode = "param_sql"
            sql_template = detected_template
            params_schema = detected_schema
            default_params = detected_defaults
        else:
            mode = "static_sql" if "mode" not in fields_set else mode
            sql_template = None if "sql_template" not in fields_set else sql_template
            params_schema = [] if "params_schema" not in fields_set else (params_schema or [])
            default_params = {} if "default_params" not in fields_set else (default_params or {})

    update_body = SaveReportRequest(
        title=title,
        description=description,
        sql_content=sql_clean,
        dataset_id=dataset_id,
        data_source=data_source or "default_clickhouse",
        original_query=original_query,
        mode=mode or "static_sql",
        sql_template=sql_template,
        params_schema=params_schema or [],
        default_params=default_params or {},
        analysis_mode=analysis_mode or "manual",
        tags=tags or [],
    )
    report_item = _build_saved_report_item(
        report_id=existing.id,
        title=title,
        description=description,
        sql_clean=sql_clean,
        dataset_id=dataset_id,
        data_source=data_source or "default_clickhouse",
        original_query=original_query,
        created_at=existing.created_at,
        updated_at=existing.updated_at,
        owner_user_id=existing.owner_user_id,
        owner_name=existing.owner_name,
        visibility=existing.visibility,
        status_value=existing.status,
        tags=tags,
        last_run_at=existing.last_run_at,
        last_success_at=existing.last_success_at,
        last_error=existing.last_error,
        is_owner=True,
        share_targets=existing.share_targets,
        body=update_body,
    )

    await _precheck_saved_report_sql_permissions(
        db,
        report=report_item,
        user_info=user_info,
        user_id=user_id,
    )

    _apply_report_item_to_row(report_row, report_item)
    report_row.updated_at = datetime.now()
    await db.flush()
    return StandardResponse(data=report_item)


@router.put(
    "/{report_id}/shares",
    response_model=StandardResponse[SavedReportItem],
    summary="更新黄金 SQL 报表共享范围",
)
async def update_saved_report_shares(
    report_id: str,
    body: UpdateReportSharesRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = int(user_info["user_id"])
    report = await _get_report_for_user(db, report_id=report_id, user_id=user_id, require_owner=True)

    normalized: List[ShareTarget] = []
    seen = set()
    for target in body.targets:
        target_type = str(target.target_type or "").strip().lower()
        if target_type not in {"user", "role"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="共享目标类型仅支持 user 或 role")
        if int(target.target_id) <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="共享目标 ID 必须大于 0")
        permission = str(target.permission or "run").strip().lower()
        if permission != "run":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前仅支持 run 共享权限")
        key = (target_type, int(target.target_id))
        if key in seen:
            continue
        seen.add(key)
        normalized.append(ShareTarget(target_type=target_type, target_id=int(target.target_id), permission="run"))

    await _validate_share_targets(db, normalized)
    await db.execute(delete(PortalSavedReportShare).where(PortalSavedReportShare.report_id == report.id))
    for target in normalized:
        db.add(
            PortalSavedReportShare(
                report_id=report.id,
                target_type=target.target_type,
                target_id=target.target_id,
                permission=target.permission,
                created_by=user_id,
            )
        )
    report.visibility = "shared" if normalized else "private"
    report.updated_at = datetime.now()
    await db.flush()

    item = _report_row_to_item(report, current_user_id=user_id)
    item.share_targets = normalized
    item.visibility = report.visibility
    return StandardResponse(data=item)


@router.post(
    "/{report_id}/copy",
    response_model=StandardResponse[SavedReportItem],
    summary="复制共享黄金 SQL 报表为我的报表",
)
async def copy_saved_report(
    report_id: str,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = int(user_info["user_id"])
    role_ids = await _get_user_role_ids(db, user_id)
    source = await _get_report_for_user(db, report_id=report_id, user_id=user_id, role_ids=role_ids)
    source_item = _report_row_to_item(source, current_user_id=user_id)

    new_id = f"rpt_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    copy_body = SaveReportRequest(
        title=f"{source_item.title} 副本",
        description=source_item.description,
        sql_content=source_item.sql_content,
        dataset_id=source_item.dataset_id,
        data_source=source_item.data_source,
        original_query=source_item.original_query,
        mode=source_item.mode,
        sql_template=source_item.sql_template,
        params_schema=source_item.params_schema,
        default_params=source_item.default_params,
        analysis_mode=source_item.analysis_mode,
        tags=source_item.tags,
    )
    copied_item = _build_saved_report_item(
        report_id=new_id,
        title=copy_body.title,
        description=copy_body.description,
        sql_clean=source_item.sql_content,
        dataset_id=source_item.dataset_id,
        data_source=source_item.data_source,
        original_query=source_item.original_query,
        created_at=now,
        updated_at=now,
        owner_user_id=user_id,
        owner_name=user_info.get("real_name") or user_info.get("user_name"),
        visibility="private",
        tags=source_item.tags,
        is_owner=True,
        body=copy_body,
    )
    await _precheck_saved_report_sql_permissions(db, report=copied_item, user_info=user_info, user_id=user_id)

    copied_row = PortalSavedReport(
        id=copied_item.id,
        owner_user_id=user_id,
        owner_name=copied_item.owner_name,
    )
    _apply_report_item_to_row(copied_row, copied_item)
    db.add(copied_row)
    await db.flush()
    return StandardResponse(data=copied_item)


@router.get(
    "/{report_id}/runs",
    response_model=StandardResponse[List[SavedReportRunSummary]],
    summary="查看黄金报表运行历史",
)
async def list_saved_report_runs(
    report_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = int(user_info["user_id"])
    role_ids = await _get_user_role_ids(db, user_id)
    report = await _get_report_for_user(db, report_id=report_id, user_id=user_id, role_ids=role_ids)
    stmt = select(PortalSavedReportRun).where(PortalSavedReportRun.report_id == report_id)
    if int(report.owner_user_id) != user_id:
        stmt = stmt.where(PortalSavedReportRun.user_id == user_id)
    result = await db.execute(stmt.order_by(desc(PortalSavedReportRun.started_at)).offset(offset).limit(limit))
    return StandardResponse(data=[_saved_report_run_to_summary(row) for row in result.scalars().all()])


@router.get(
    "/{report_id}/runs/{run_id}",
    response_model=StandardResponse[SavedReportRunDetail],
    summary="查看黄金报表单次运行详情",
)
async def get_saved_report_run(
    report_id: str,
    run_id: int,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = int(user_info["user_id"])
    role_ids = await _get_user_role_ids(db, user_id)
    report = await _get_report_for_user(db, report_id=report_id, user_id=user_id, role_ids=role_ids)
    stmt = select(PortalSavedReportRun).where(
        PortalSavedReportRun.report_id == report_id,
        PortalSavedReportRun.id == run_id,
    )
    if int(report.owner_user_id) != user_id:
        stmt = stmt.where(PortalSavedReportRun.user_id == user_id)
    result = await db.execute(stmt)
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="运行记录不存在")
    delivery_result = await db.execute(
        select(PortalSavedReportDigestDelivery)
        .where(PortalSavedReportDigestDelivery.run_id == run.id)
        .order_by(PortalSavedReportDigestDelivery.created_at.asc())
    )
    return StandardResponse(data=_saved_report_run_to_detail(run, list(delivery_result.scalars().all())))


def _saved_report_subscription_data(row: PortalSavedReportSubscription) -> Dict[str, Any]:
    return {
        "id": int(row.id), "report_id": row.report_id, "schedule_type": row.schedule_type,
        "cron_expr": row.cron_expr, "timezone": row.timezone, "params": row.params or {},
        "ai_analysis_enabled": bool(row.ai_analysis_enabled),
        "analysis_instruction": row.analysis_instruction,
        "notify_on_success": bool(row.notify_on_success), "notify_on_failure": bool(row.notify_on_failure),
        "external_channels": row.external_channels or [], "status": row.status,
        "consecutive_failures": row.consecutive_failures or 0, "last_run_id": row.last_run_id,
        "last_run_at": _dt_to_iso(row.last_run_at), "next_run_at": _dt_to_iso(row.next_run_at),
        "last_error": row.last_error,
    }


async def _owned_report(db: AsyncSession, report_id: str, user_id: int):
    role_ids = await _get_user_role_ids(db, user_id)
    return await _get_report_for_user(db, report_id=report_id, user_id=user_id, role_ids=role_ids, require_owner=True)


async def _subscription_for_report(db: AsyncSession, report_id: str):
    result = await db.execute(select(PortalSavedReportSubscription).where(PortalSavedReportSubscription.report_id == report_id))
    return result.scalar_one_or_none()


@router.get("/{report_id}/subscription", summary="获取黄金报表订阅")
async def get_saved_report_subscription(report_id: str, user_info=Depends(require_api_key), db: AsyncSession = Depends(get_db_session)):
    user_id = int(user_info["user_id"])
    await _owned_report(db, report_id, user_id)
    row = await _subscription_for_report(db, report_id)
    return StandardResponse(data=_saved_report_subscription_data(row) if row else None)


@router.put("/{report_id}/subscription", summary="保存黄金报表订阅")
async def put_saved_report_subscription(report_id: str, body: SavedReportSubscriptionRequest,
                                        user_info=Depends(require_api_key), db: AsyncSession = Depends(get_db_session)):
    user_id = int(user_info["user_id"])
    await _owned_report(db, report_id, user_id)
    try:
        cron_expr = schedule_to_cron(body.schedule_type, time_value=body.time_value, weekday=body.weekday,
                                     monthday=body.monthday, cron_expr=body.cron_expr)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    channels = list(dict.fromkeys(body.external_channels))
    if any(channel not in {"dingtalk", "wechat_work", "email"} for channel in channels):
        raise HTTPException(status_code=400, detail="通知渠道无效")
    row = await _subscription_for_report(db, report_id)
    if row is None:
        row = PortalSavedReportSubscription(report_id=report_id, user_id=user_id)
        db.add(row)
    row.schedule_type = body.schedule_type
    row.cron_expr = cron_expr
    row.params = body.params
    row.ai_analysis_enabled = body.ai_analysis_enabled
    row.analysis_instruction = str(body.analysis_instruction or "").strip()[:500] or None
    row.notify_on_success = body.notify_on_success
    row.notify_on_failure = body.notify_on_failure
    row.external_channels = channels
    row.status = "active"
    row.last_error = None
    await db.flush()
    from app.services.ai.scheduler_service import scheduler_service
    await scheduler_service.upsert_saved_report_subscription(row)
    row.next_run_at = scheduler_service.get_saved_report_subscription_next_run_time(row.id)
    await db.flush()
    return StandardResponse(data=_saved_report_subscription_data(row))


@router.post("/{report_id}/subscription/pause", summary="暂停黄金报表订阅")
async def pause_saved_report_subscription(report_id: str, user_info=Depends(require_api_key), db: AsyncSession = Depends(get_db_session)):
    await _owned_report(db, report_id, int(user_info["user_id"]))
    row = await _subscription_for_report(db, report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    row.status = "paused"
    row.next_run_at = None
    from app.services.ai.scheduler_service import scheduler_service
    await scheduler_service.remove_saved_report_subscription(row.id)
    await db.flush()
    return StandardResponse(data=_saved_report_subscription_data(row))


@router.post("/{report_id}/subscription/resume", summary="恢复黄金报表订阅")
async def resume_saved_report_subscription(report_id: str, user_info=Depends(require_api_key), db: AsyncSession = Depends(get_db_session)):
    await _owned_report(db, report_id, int(user_info["user_id"]))
    row = await _subscription_for_report(db, report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    row.status = "active"
    from app.services.ai.scheduler_service import scheduler_service
    await scheduler_service.upsert_saved_report_subscription(row)
    row.next_run_at = scheduler_service.get_saved_report_subscription_next_run_time(row.id)
    await db.flush()
    return StandardResponse(data=_saved_report_subscription_data(row))


@router.post("/{report_id}/subscription/run", summary="立即运行黄金报表订阅")
async def run_saved_report_subscription(report_id: str, user_info=Depends(require_api_key), db: AsyncSession = Depends(get_db_session)):
    await _owned_report(db, report_id, int(user_info["user_id"]))
    row = await _subscription_for_report(db, report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    from app.services.ai.scheduler_service import _saved_report_subscription_wrapper
    await _saved_report_subscription_wrapper(row.id, is_manual=True)
    return StandardResponse(data={"message": "订阅已执行，请在运行历史查看结果"})


@router.delete("/{report_id}/subscription", summary="删除黄金报表订阅")
async def delete_saved_report_subscription(report_id: str, user_info=Depends(require_api_key), db: AsyncSession = Depends(get_db_session)):
    await _owned_report(db, report_id, int(user_info["user_id"]))
    row = await _subscription_for_report(db, report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    from app.services.ai.scheduler_service import scheduler_service
    await scheduler_service.remove_saved_report_subscription(row.id)
    await db.delete(row)
    await db.flush()
    return StandardResponse(data={"deleted": True})


@router.post(
    "/{report_id}/execute",
    response_model=StandardResponse[Any],
    summary="免模型极速安全运行暂存的黄金 SQL 报表",
)
async def execute_saved_report(
    report_id: str,
    body: Optional[ExecuteReportRequest] = None,
    conversation_id: Optional[str] = None, # 接收 conversation_id 以便写入缓存复用
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    return await _execute_saved_report_impl(report_id, body, conversation_id, user_info, db)


async def _execute_saved_report_impl(
    report_id: str,
    body: Optional[ExecuteReportRequest],
    conversation_id: Optional[str],
    user_info: Dict[str, Any],
    db: AsyncSession,
    *,
    trigger_type: str = "manual",
    task_id: Optional[int] = None,
):
    user_id = int(user_info["user_id"])
    role_ids = await _get_user_role_ids(db, user_id)
    report_row = await _get_report_for_user(db, report_id=report_id, user_id=user_id, role_ids=role_ids)
    report = _report_row_to_item(report_row, current_user_id=user_id)

    if report.data_source == "default_clickhouse":
        inferred_dataset_id, inferred_data_source = await _infer_dataset_and_data_source(
            db,
            sql_content=report.sql_content,
            dataset_id=report.dataset_id,
            data_source=report.data_source,
        )
        if inferred_data_source and inferred_data_source != report.data_source:
            report.dataset_id = inferred_dataset_id
            report.data_source = inferred_data_source
            report_row.dataset_id = inferred_dataset_id
            report_row.data_source = inferred_data_source

    user_dimensions = _chatbi_user_dimensions(user_info, user_id)
    is_admin = user_info.get("role") == "admin"
    request_body = body or ExecuteReportRequest()

    try:
        sql_to_execute, resolved_params = _resolve_report_sql(report, body=request_body)
    except ReportParameterError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    try:
        # 参数化报表会在执行前重新经过底层 SQL 校验、沙箱与表权限校验。
        dataset_name = await _dataset_name_from_id(db, report.dataset_id)
        permission_notice: Dict[str, Any] = {}
        run_started = time.perf_counter()
        run_row = PortalSavedReportRun(
            report_id=report.id,
            user_id=user_id,
            trigger_type=trigger_type,
            task_id=task_id,
            status="running",
            resolved_params=resolved_params,
            executed_sql=sql_to_execute,
            data_source=report.data_source,
            dataset_name=dataset_name,
            started_at=datetime.now(),
        )
        db.add(run_row)
        await db.flush()
        result_str = await execute_sql_query_core(
            db,
            sql=sql_to_execute,
            data_source=report.data_source,
            dataset_name=dataset_name,
            user_id=user_id,
            user_dimensions=user_dimensions,
            trace_logs=None,
            api_key=None,
            agent_context=None,
            dry_run=False,
            is_admin=is_admin,
            bypass_table_auth=False,
            permission_notice=permission_notice,
        )
    except Exception as e:
        logger.error("Failed to execute SQL from saved report: %s", e)
        report_row.last_run_at = datetime.now()
        report_row.last_error = str(e)
        report_row.status = "error"
        if "run_row" in locals():
            run_error = _saved_report_sql_error_detail(str(e)) if any(
                prefix in str(e) for prefix in _SQL_GATE_ERROR_PREFIXES
            ) else str(e)
            _finish_saved_report_run_error(run_row, run_error, run_started)
            await db.commit()
        if "[Permission Denied]" in str(e) or "[Security Error]" in str(e):
            await _raise_saved_report_sql_error(db, report_row, str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"SQL 执行失败: {e}",
        )

    raw_res = result_str.strip()
    
    # 尝试进行 JSON 结果标准化解析（复用 chatbi_sql_execute 的转换逻辑）
    try:
        parsed = json.loads(raw_res)
        if isinstance(parsed, dict) and ("[Validation Failed]" in raw_res or "[Permission Denied]" in raw_res or "[Security Error]" in raw_res):
            _finish_saved_report_run_error(run_row, _saved_report_sql_error_detail(raw_res), run_started)
            await db.commit()
            await _raise_saved_report_sql_error(db, report_row, raw_res)
            
        # 若传入了会话ID，写入缓存供大模型下一轮做可视化复用
        if conversation_id:
            try:
                from app.services.ai.memory_service import memory_service
                cache_payload = {
                    "sql": sql_to_execute,
                    "data_source": report.data_source,
                    "dataset_name": dataset_name,
                    "rows": parsed,
                    "permission_notice": permission_notice if permission_notice.get("row_filter_applied") is True else None,
                    "params": resolved_params,
                    "report_id": report.id,
                    "report_title": report.title,
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "trace_id": None,
                }
                await memory_service.set_last_data_result(str(user_id), conversation_id, cache_payload)
            except Exception as cache_err:
                logger.warning("Failed to save report data to memory_service cache: %s", cache_err)

        now = datetime.now()
        report_row.last_run_at = now
        report_row.last_success_at = now
        report_row.last_error = None
        report_row.status = "active"
        _finish_saved_report_run_success(run_row, parsed, permission_notice, run_started)
        try:
            await _record_saved_report_run(db, report_id=report.id, user_id=user_id)
        except Exception as pref_err:
            logger.warning("Failed to record saved report run preference for %s: %s", report.id, pref_err)
        await db.flush()
        return StandardResponse(data=_attach_permission_notice(parsed, permission_notice))
    except json.JSONDecodeError:
        pass

    # 如果是文本类输出（非标准 JSON 格式，如报错信息）
    if raw_res.startswith("[Validation Failed]") or raw_res.startswith("[Permission Denied]") or raw_res.startswith("[Security Error]"):
        _finish_saved_report_run_error(run_row, _saved_report_sql_error_detail(raw_res), run_started)
        await db.commit()
        await _raise_saved_report_sql_error(db, report_row, raw_res)
        
    now = datetime.now()
    report_row.last_run_at = now
    report_row.last_success_at = now
    report_row.last_error = None
    report_row.status = "active"
    text_payload = {"rows": [{"value": raw_res}]}
    _finish_saved_report_run_success(run_row, text_payload, permission_notice, run_started)
    try:
        await _record_saved_report_run(db, report_id=report.id, user_id=user_id)
    except Exception as pref_err:
        logger.warning("Failed to record saved report run preference for %s: %s", report.id, pref_err)
    await db.flush()
    return StandardResponse(data=_attach_permission_notice({"rows": raw_res}, permission_notice))
