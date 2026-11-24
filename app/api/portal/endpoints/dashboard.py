"""
Dashboard API endpoints for statistics and overview data.
"""
import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, case, text, or_
from app.core.dependencies import require_api_key, require_admin
from app.core.orm import get_db_session
from app.core.redis import get_redis
from app.models.audit import AccessLog, AgentExecutionTrace, AgentExecutionHistory
from app.utils.image_usage import IMAGE_USAGE_TOOL_NAMES, image_usage_from_tool_output
from app.models.user import User
from app.models.agent import AIAgent
from app.services.online_presence_service import OnlinePresenceService
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

# 暂停态轮次（等待用户回答/授权等）会写入历史表以便回放，但它们既不是成功也
# 不是失败，不应计入 Agent 成功率的分母，否则主动提问会无端拉低成功率。
SUSPENDED_HISTORY_STATUSES = (
    "awaiting_user",
    "awaiting_permission",
    "awaiting_external_execution",
    "interrupted",
)

router = APIRouter()

def is_admin(user: dict) -> bool:
    """Check if user is admin"""
    return user.get("role") == "admin"


def _hour_key_expression(column, dialect_name: str):
    """Build the hourly grouping key for the configured platform database."""
    normalized_dialect = str(dialect_name or "").strip().lower()
    if normalized_dialect in {"postgres", "postgresql", "pg"}:
        return func.to_char(
            func.date_trunc("hour", column),
            "YYYY-MM-DD HH24:00:00",
        )
    return func.date_format(column, "%Y-%m-%d %H:00:00")


def _session_dialect_name(db: AsyncSession) -> str:
    return db.get_bind().dialect.name


def _resolve_token_stats_range(
    days: int = 7,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> tuple[datetime, datetime, int]:
    """解析 Token 统计时间范围，返回 (start, end, span_days)。"""
    now = datetime.now()
    if start_date and end_date:
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d").replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            end = datetime.strptime(end_date, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, microsecond=999999
            )
        except ValueError:
            # 前端日历传的是纯日期；格式不对是调用方问题，不该冒成 500。
            raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")
        if end < start:
            raise HTTPException(status_code=400, detail="结束日期不能早于开始日期")
        span_days = (end.date() - start.date()).days + 1
        if span_days > 90:
            raise HTTPException(status_code=400, detail="时间范围不能超过 90 天")
        return start, end, span_days

    span_days = min(max(int(days or 7), 1), 90)
    start = (now - timedelta(days=span_days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start, end, span_days


def _resolve_stats_window(
    period: str = "today",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> tuple[datetime, datetime]:
    """统计窗口：日期区间优先，否则回落到 period 枚举。

    Token 统计、概览、运营分析三个页面的时间筛选都改成日历后，后端只需要
    一套解析：日历传 `start_date`/`end_date`，旧调用方继续传 `period`。
    显式日期只给一端视为调用错误——静默按 period 兜底会让「选了半截区间」
    变成「悄悄返回今日数据」。
    """
    if start_date or end_date:
        if not (start_date and end_date):
            raise HTTPException(status_code=400, detail="start_date 与 end_date 必须同时提供")
        start, end, _ = _resolve_token_stats_range(0, start_date, end_date)
        return start, end

    now = datetime.now()
    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start = now - timedelta(days=7)
    else:
        start = now - timedelta(days=30)
    return start, now


@router.get("/admin-stats")
async def get_admin_stats(
    period: str = Query("today", pattern="^(today|week|month)$"),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    admin_flag = is_admin(user)
    user_name = user["user_name"]

    range_start, range_end = _resolve_stats_window(period, start_date, end_date)

    # 1. User Stats (Admin Only)
    total_users = None
    active_users = None

    if admin_flag:
        count_users = await db.execute(select(func.count()).select_from(User).where(User.status == 1))
        total_users = count_users.scalar()
        
        # 活跃用户必须限定在「当前有效账号」里。
        #
        # 访问日志会一直保留历史账号（已禁用、已删除）的调用记录，直接
        # count(distinct user_name) 会把它们一并算进来，于是出现
        # 「总用户 16 / 活跃 6050」这种自相矛盾的读数——分子比分母还大，
        # 页面上没法解读，也算不出活跃率。
        count_active = await db.execute(
            select(func.count(func.distinct(AccessLog.user_name)))
            .select_from(AccessLog)
            .join(User, User.user_name == AccessLog.user_name)
            .where(
                # 活跃数跟随所选区间：否则切到「上月」时活跃用户仍是近 7 天，
                # 与同一张卡上的调用量不是同一个时间口径。
                AccessLog.created_at >= range_start,
                AccessLog.created_at <= range_end,
                User.status == 1,
            )
        )
        active_users = count_active.scalar()

    # 2. API Stats
    stmt = select(
        func.count().label("total"),
        func.avg(AccessLog.process_time_ms).label("avg_time"),
        func.sum(case(((AccessLog.status_code >= 200) & (AccessLog.status_code < 300), 1), else_=0)).label("success"),
        func.sum(case((AccessLog.status_code >= 400, 1), else_=0)).label("error")
    ).where(
        AccessLog.created_at >= range_start,
        AccessLog.created_at <= range_end,
    )

    if not admin_flag:
        stmt = stmt.where(AccessLog.user_name == user_name)
        
    res = (await db.execute(stmt)).one()
    
    total_calls = res.total or 0
    avg_time = float(res.avg_time) if res.avg_time else 0
    success_count = res.success or 0
    error_count = res.error or 0
    
    success_rate = (success_count / total_calls * 100) if total_calls > 0 else 0
    error_rate = (error_count / total_calls * 100) if total_calls > 0 else 0

    # 3.5 Token Stats
    stmt_tokens = select(
        func.coalesce(func.sum(AgentExecutionHistory.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(AgentExecutionHistory.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(AgentExecutionHistory.total_tokens), 0).label("total_tokens"),
    ).where(
        AgentExecutionHistory.created_at >= range_start,
        AgentExecutionHistory.created_at <= range_end,
    )
    if not admin_flag:
        stmt_tokens = stmt_tokens.where(AgentExecutionHistory.username == user_name)
    tokens_row = (await db.execute(stmt_tokens)).one()
    
    result = {
        "api_calls": {
            "period": period,
            "total": total_calls,
            "success": success_count,
            "errors": error_count
        },
        "avg_response_time": round(avg_time, 2),
        "success_rate": round(success_rate, 2),
        "error_rate": round(error_rate, 2),
        "prompt_tokens": int(tokens_row.prompt_tokens or 0),
        "completion_tokens": int(tokens_row.completion_tokens or 0),
        "total_tokens": int(tokens_row.total_tokens or 0),
    }
    
    if admin_flag:
        result["total_users"] = total_users
        result["active_users"] = active_users
        
    return result

@router.get("/user-stats")
async def get_user_stats(
    period: str = Query("today", pattern="^(today|week|month)$"),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    user_name = user["user_name"]
    
    # Check status
    res = await db.execute(select(User.status).where(User.user_name == user_name))
    status_val = res.scalar_one_or_none()
    api_key_status = "active" if status_val == 1 else "inactive"
    
    range_start, range_end = _resolve_stats_window(period, start_date, end_date)

    # Stats
    stmt = select(
        func.count().label("total"),
        func.avg(AccessLog.process_time_ms).label("avg_time"),
        func.sum(case(((AccessLog.status_code >= 200) & (AccessLog.status_code < 300), 1), else_=0)).label("success"),
        func.max(AccessLog.created_at).label("last_call")
    ).where(
        AccessLog.user_name == user_name,
        AccessLog.created_at >= range_start,
        AccessLog.created_at <= range_end,
    )
    
    row = (await db.execute(stmt)).one()
    total_calls = row.total or 0
    avg_time = float(row.avg_time) if row.avg_time else 0
    success_count = row.success or 0
    last_call = row.last_call
    
    success_rate = (success_count / total_calls * 100) if total_calls > 0 else 0
    
    # User Token Stats
    stmt_tokens = select(
        func.coalesce(func.sum(AgentExecutionHistory.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(AgentExecutionHistory.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(AgentExecutionHistory.total_tokens), 0).label("total_tokens"),
    ).where(
        AgentExecutionHistory.created_at >= range_start,
        AgentExecutionHistory.created_at <= range_end,
        AgentExecutionHistory.username == user_name,
    )
    tokens_row = (await db.execute(stmt_tokens)).one()

    return {
        "api_key_status": api_key_status,
        "api_calls": {
            "period": period,
            "total": total_calls,
            "success": success_count
        },
        "avg_response_time": round(avg_time, 2),
        "success_rate": round(success_rate, 2),
        "last_call_time": last_call.isoformat() if last_call else None,
        "prompt_tokens": int(tokens_row.prompt_tokens or 0),
        "completion_tokens": int(tokens_row.completion_tokens or 0),
        "total_tokens": int(tokens_row.total_tokens or 0),
    }

@router.get("/api-trends")
async def get_api_trends(
    days: int = Query(7, ge=1, le=90),
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    admin_flag = is_admin(user)
    user_name = user["user_name"]
    
    start_date = (datetime.now() - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Group by Date
    # Note: func.date() is MySQL specific but standard enough.
    stmt = select(
        func.date(AccessLog.created_at).label("date"),
        func.count().label("total"),
        func.sum(case(((AccessLog.status_code >= 200) & (AccessLog.status_code < 300), 1), else_=0)).label("success"),
        func.sum(case((AccessLog.status_code >= 400, 1), else_=0)).label("error")
    ).where(AccessLog.created_at >= start_date)
    
    if not admin_flag:
        stmt = stmt.where(AccessLog.user_name == user_name)
        
    stmt = stmt.group_by(func.date(AccessLog.created_at)).order_by("date")
    
    rows = (await db.execute(stmt)).all()
    
    trends = []
    for row in rows:
        total = row.total or 0
        success = row.success or 0
        errors = row.error or 0
        success_rate = (success / total * 100) if total > 0 else 0
        
        trends.append({
            "date": str(row.date),
            "total_calls": total,
            "success_calls": success,
            "error_calls": errors,
            "success_rate": round(success_rate, 2)
        })
        
    return trends

@router.get("/api-trends-24h")
async def get_api_trends_24h(
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    admin_flag = is_admin(user)
    user_name = user["user_name"]
    
    now = datetime.now()
    start_time = (now - timedelta(hours=23)).replace(minute=0, second=0, microsecond=0)
    
    hour_key_expr = _hour_key_expression(
        AccessLog.created_at,
        _session_dialect_name(db),
    )
    
    stmt = select(
        hour_key_expr.label("hour_key"),
        func.count().label("total"),
        func.sum(case(((AccessLog.status_code >= 200) & (AccessLog.status_code < 300), 1), else_=0)).label("success")
    ).where(AccessLog.created_at >= start_time)
    
    if not admin_flag:
        stmt = stmt.where(AccessLog.user_name == user_name)
        
    stmt = stmt.group_by("hour_key").order_by("hour_key")
    
    rows = (await db.execute(stmt)).all()
    
    results_map = {}
    for row in rows:
        results_map[str(row.hour_key)] = {
            "total": row.total or 0,
            "success": row.success or 0
        }
        
    trends = []
    for i in range(24):
        current_hour = start_time + timedelta(hours=i)
        current_hour_str = current_hour.strftime('%Y-%m-%d %H:00:00')
        
        stats = results_map.get(current_hour_str, {"total": 0, "success": 0})
        
        trends.append({
            "hour": current_hour.strftime('%H:00'),
            "timestamp": current_hour_str,
            "total_calls": int(stats["total"]),
            "success_calls": int(stats["success"])
        })
        
    return trends

@router.get("/recent-activities")
async def get_recent_activities(
    limit: int = Query(10, ge=1, le=50),
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    admin_flag = is_admin(user)
    user_name = user["user_name"]
    result = {}
    
    # Active Users: one row per user, ordered by their latest access-log time.
    if admin_flag:
        latest_activity = (
            select(
                AccessLog.user_name.label("user_name"),
                func.max(AccessLog.created_at).label("last_active"),
            )
            .where(AccessLog.user_name.is_not(None))
            .group_by(AccessLog.user_name)
            .subquery()
        )
        stmt_users = (
            select(User, latest_activity.c.last_active)
            .join(latest_activity, User.user_name == latest_activity.c.user_name)
            .order_by(desc(latest_activity.c.last_active), desc(User.id))
            .limit(min(limit, 5))
        )
        user_rows = (await db.execute(stmt_users)).all()
        result["recent_users"] = [
            {
                "user_name": user.user_name,
                "role": user.role,
                "last_active": last_active.isoformat() if last_active else None,
            }
            for user, last_active in user_rows
        ]
        
    # Recent Calls
    stmt_calls = select(AccessLog).order_by(desc(AccessLog.created_at), desc(AccessLog.id)).limit(limit)
    if not admin_flag:
        stmt_calls = stmt_calls.where(AccessLog.user_name == user_name)
        
    calls = (await db.execute(stmt_calls)).scalars().all()
    result["recent_calls"] = [
        {
            "id": c.id, "user_name": c.user_name, "endpoint": c.endpoint, 
            "method": c.method, "status_code": c.status_code, 
            "process_time_ms": c.process_time_ms, 
            "created_at": c.created_at.isoformat() if c.created_at else None
        }
        for c in calls
    ]
    
    # Recent Errors
    if admin_flag:
        stmt_errors = select(AccessLog).where(AccessLog.status_code >= 400).order_by(desc(AccessLog.created_at), desc(AccessLog.id)).limit(min(limit, 5))
        errors = (await db.execute(stmt_errors)).scalars().all()
        result["recent_errors"] = [
            {
                "id": e.id, "user_name": e.user_name, "endpoint": e.endpoint,
                "method": e.method, "status_code": e.status_code,
                "error_message": e.error_message,
                "created_at": e.created_at.isoformat() if e.created_at else None
            }
            for e in errors
        ]
        
    return result

@router.get("/online-users")
async def get_online_users(
    user: dict = Depends(require_api_key)
):
    redis = await get_redis()
    if not redis: return {"count": 0, "users": []}
    
    admin_flag = is_admin(user)
    try:
        online_users = await OnlinePresenceService.list_active_users(
            redis_client=redis
        )
        
        return {
            "count": len(online_users),
            "users": online_users if admin_flag else []
        }
    except Exception as e:
        print(f"Redis error in get_online_users: {e}")
        return {"count": 0, "users": []}

@router.get("/agent-stats")
async def get_agent_stats(
    period: str = Query("today", pattern="^(today|week|month)$"),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    admin_flag = is_admin(user)
    user_name = user["user_name"]

    now = datetime.now()
    range_start, range_end = _resolve_stats_window(period, start_date, end_date)
        
    # 1. Health Stats
    stmt = select(
        func.count().label("total"),
        func.sum(case((AgentExecutionTrace.status == 'success', 1), else_=0)).label("success"),
        func.avg(AgentExecutionTrace.execution_time_ms).label("avg_lat")
    ).where(
        AgentExecutionTrace.created_at >= range_start,
        AgentExecutionTrace.created_at <= range_end,
        AgentExecutionTrace.event_type.in_(['tool_call', 'tool_result'])
    )
    
    # For non-admin users, filter by user through trace_id
    if not admin_flag:
        # Subquery to get trace_ids for the current user
        user_trace_ids = select(AgentExecutionHistory.trace_id).where(
            AgentExecutionHistory.username == user_name,
            AgentExecutionHistory.created_at >= range_start,
            AgentExecutionHistory.created_at <= range_end,
        )
        stmt = stmt.where(AgentExecutionTrace.trace_id.in_(user_trace_ids))
        
    row = (await db.execute(stmt)).one()
    total_steps = row.total or 0
    success_count = row.success or 0
    avg_latency = float(row.avg_lat) if row.avg_lat else 0
    
    # 2. Tool Usage
    stmt_usage = select(
        AgentExecutionTrace.tool_name, func.count().label("count")
    ).where(
        AgentExecutionTrace.created_at >= range_start,
        AgentExecutionTrace.created_at <= range_end,
        AgentExecutionTrace.tool_name != None
    )
    
    # For non-admin users, filter by user through trace_id
    if not admin_flag:
        user_trace_ids = select(AgentExecutionHistory.trace_id).where(
            AgentExecutionHistory.username == user_name,
            AgentExecutionHistory.created_at >= range_start,
            AgentExecutionHistory.created_at <= range_end,
        )
        stmt_usage = stmt_usage.where(AgentExecutionTrace.trace_id.in_(user_trace_ids))
        
    stmt_usage = stmt_usage.group_by(AgentExecutionTrace.tool_name).order_by(desc("count"))
    
    tool_usage = [{"name": r.tool_name, "value": r.count} for r in (await db.execute(stmt_usage)).all()]
    
    # 3. Performance Trend
    trend_start = (now - timedelta(hours=23)).replace(minute=0, second=0, microsecond=0)
    hour_key_expr = _hour_key_expression(
        AgentExecutionTrace.created_at,
        _session_dialect_name(db),
    )
    stmt_trend = select(
        hour_key_expr.label("hour_key"),
        func.avg(AgentExecutionTrace.execution_time_ms).label("avg_ms")
    ).where(AgentExecutionTrace.created_at >= trend_start)
    
    # For non-admin users, filter by user through trace_id
    if not admin_flag:
        user_trace_ids = select(AgentExecutionHistory.trace_id).where(
            AgentExecutionHistory.username == user_name,
            AgentExecutionHistory.created_at >= trend_start
        )
        stmt_trend = stmt_trend.where(AgentExecutionTrace.trace_id.in_(user_trace_ids))
        
    stmt_trend = stmt_trend.group_by("hour_key")
    
    trend_rows = (await db.execute(stmt_trend)).all()
    trend_map = {str(r.hour_key): float(r.avg_ms) if r.avg_ms else 0 for r in trend_rows}
    
    performance_trend = []
    for i in range(24):
        h = trend_start + timedelta(hours=i)
        h_str = h.strftime('%Y-%m-%d %H:00:00')
        performance_trend.append({
            "hour": h.strftime('%H:00'),
            "avg_ms": round(trend_map.get(h_str, 0), 2)
        })
        
    # 4. Recent Errors
    stmt_err = select(AgentExecutionTrace).where(AgentExecutionTrace.status == 'error')
    
    # For non-admin users, filter by user through trace_id
    if not admin_flag:
        user_trace_ids = select(AgentExecutionHistory.trace_id).where(
            AgentExecutionHistory.username == user_name
        )
        stmt_err = stmt_err.where(AgentExecutionTrace.trace_id.in_(user_trace_ids))
        
    stmt_err = stmt_err.order_by(desc(AgentExecutionTrace.created_at)).limit(5)
    recent_errors = []
    for r in (await db.execute(stmt_err)).scalars().all():
        msg = r.error_message
        if msg and len(msg) > 100: msg = msg[:100] + "..."
        recent_errors.append({
            "trace_id": r.trace_id, "tool": r.tool_name, "message": msg,
            "time": r.created_at.isoformat() if r.created_at else None
        })
        
    # 5. Agent Performance
    stmt_perf = select(
        AgentExecutionHistory.agent_id,
        AIAgent.display_name,
        AgentExecutionHistory.agent_version,
        func.count().label("calls"),
        func.avg(AgentExecutionHistory.execution_time_ms).label("avg_lat"),
        func.sum(case((AgentExecutionHistory.status == 'success', 1), else_=0)).label("success")
    ).outerjoin(AIAgent, AgentExecutionHistory.agent_id == AIAgent.id).where(
        AgentExecutionHistory.created_at >= range_start,
        AgentExecutionHistory.created_at <= range_end,
        or_(
            AgentExecutionHistory.status.is_(None),
            AgentExecutionHistory.status.notin_(SUSPENDED_HISTORY_STATUSES),
        ),
    )
    
    # For non-admin users, filter by user
    if not admin_flag:
        stmt_perf = stmt_perf.where(AgentExecutionHistory.username == user_name)
        
    stmt_perf = stmt_perf.group_by(
        AgentExecutionHistory.agent_id, AIAgent.display_name, AgentExecutionHistory.agent_version
    ).order_by(desc("calls"))
    
    agent_performance = []
    for r in (await db.execute(stmt_perf)).all():
        total = r.calls or 0
        sc = r.success or 0
        rate = (sc / total * 100) if total > 0 else 0
        agent_performance.append({
            "agent_id": r.agent_id,
            "name": r.display_name or r.agent_id,
            "version": r.agent_version or "Unknown",
            "calls": total,
            "avg_latency": round(float(r.avg_lat), 2) if r.avg_lat else 0,
            "success_rate": round(rate, 2)
        })
        
    return {
        "health_stats": {
            "success_rate": round(success_count / total_steps * 100, 2) if total_steps > 0 else 100,
            "total_tool_calls": int(total_steps / 2),
            "avg_latency": round(avg_latency, 2)
        },
        "tool_usage": tool_usage,
        "performance_trend": performance_trend,
        "recent_errors": recent_errors,
        "agent_performance": agent_performance
    }

@router.get("/token-stats/trends")
async def get_token_stats_trends(
    days: int = Query(7, ge=1, le=90),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    admin_flag = is_admin(user)
    user_name = user["user_name"]
    range_start, range_end, span_days = _resolve_token_stats_range(days, start_date, end_date)

    stmt = select(
        func.date(AgentExecutionHistory.created_at).label("date"),
        func.count().label("calls"),
        func.coalesce(func.sum(AgentExecutionHistory.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(AgentExecutionHistory.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(AgentExecutionHistory.total_tokens), 0).label("total_tokens"),
    ).where(
        AgentExecutionHistory.created_at >= range_start,
        AgentExecutionHistory.created_at <= range_end,
    )

    if not admin_flag:
        stmt = stmt.where(AgentExecutionHistory.username == user_name)

    stmt = stmt.group_by(func.date(AgentExecutionHistory.created_at)).order_by("date")
    rows = (await db.execute(stmt)).all()

    results_map = {}
    for r in rows:
        results_map[str(r.date)] = {
            "calls": r.calls or 0,
            "prompt_tokens": int(r.prompt_tokens or 0),
            "completion_tokens": int(r.completion_tokens or 0),
            "total_tokens": int(r.total_tokens or 0),
        }

    trends = []
    empty_stats = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for i in range(span_days):
        d = range_start + timedelta(days=i)
        if d > range_end:
            break
        d_str = d.strftime("%Y-%m-%d")
        stats = results_map.get(d_str, empty_stats)
        trends.append({
            "date": d_str,
            "calls": stats["calls"],
            "prompt_tokens": stats["prompt_tokens"],
            "completion_tokens": stats["completion_tokens"],
            "total_tokens": stats["total_tokens"],
        })

    return trends


@router.get("/token-stats/records")
async def get_token_stats_records(
    days: int = Query(7, ge=1, le=90),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """当前用户的 Token 消耗明细（按会话/交互粒度）。"""
    admin_flag = is_admin(user)
    user_name = user["user_name"]
    range_start, range_end, _ = _resolve_token_stats_range(days, start_date, end_date)

    base_filters = [
        AgentExecutionHistory.created_at >= range_start,
        AgentExecutionHistory.created_at <= range_end,
    ]
    if not admin_flag:
        base_filters.append(AgentExecutionHistory.username == user_name)

    count_stmt = select(func.count()).select_from(AgentExecutionHistory).where(*base_filters)
    total = int((await db.execute(count_stmt)).scalar() or 0)

    stmt = (
        select(
            AgentExecutionHistory.id,
            AgentExecutionHistory.created_at,
            AgentExecutionHistory.agent_id,
            AIAgent.display_name,
            AgentExecutionHistory.model_id,
            AgentExecutionHistory.prompt_tokens,
            AgentExecutionHistory.completion_tokens,
            AgentExecutionHistory.total_tokens,
            AgentExecutionHistory.status,
        )
        .outerjoin(AIAgent, AgentExecutionHistory.agent_id == AIAgent.id)
        .where(*base_filters)
        .order_by(desc(AgentExecutionHistory.created_at))
        .offset((page - 1) * size)
        .limit(size)
    )
    rows = (await db.execute(stmt)).all()

    items = []
    for r in rows:
        prompt = int(r.prompt_tokens or 0)
        completion = int(r.completion_tokens or 0)
        total_tokens = prompt + completion if (prompt + completion) > 0 else int(r.total_tokens or 0)
        items.append({
            "id": r.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "agent_id": r.agent_id,
            "agent_name": r.display_name or r.agent_id,
            "model_id": r.model_id or "",
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total_tokens,
            "status": r.status or "success",
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
    }

@router.get("/token-stats/agents")
async def get_token_stats_agents(
    period: str = Query("today", pattern="^(today|week|month)$"),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    admin_flag = is_admin(user)
    user_name = user["user_name"]
    range_start, range_end = _resolve_stats_window(period, start_date, end_date)

    stmt = select(
        AgentExecutionHistory.agent_id,
        AIAgent.display_name,
        func.count().label("calls"),
        func.coalesce(func.sum(AgentExecutionHistory.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(AgentExecutionHistory.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(AgentExecutionHistory.total_tokens), 0).label("total_tokens"),
    ).outerjoin(AIAgent, AgentExecutionHistory.agent_id == AIAgent.id).where(
        AgentExecutionHistory.created_at >= range_start,
        AgentExecutionHistory.created_at <= range_end,
    )
    
    if not admin_flag:
        stmt = stmt.where(AgentExecutionHistory.username == user_name)
        
    stmt = stmt.group_by(AgentExecutionHistory.agent_id, AIAgent.display_name).order_by(desc("total_tokens"))
    rows = (await db.execute(stmt)).all()
    
    results = []
    for r in rows:
        results.append({
            "agent_id": r.agent_id,
            "name": r.display_name or r.agent_id,
            "calls": r.calls or 0,
            "prompt_tokens": int(r.prompt_tokens or 0),
            "completion_tokens": int(r.completion_tokens or 0),
            "total_tokens": int(r.total_tokens or 0),
        })
    return results

@router.get("/token-stats/users")
async def get_token_stats_users(
    period: str = Query("today", pattern="^(today|week|month)$"),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    admin_flag = is_admin(user)
    user_name = user["user_name"]
    range_start, range_end = _resolve_stats_window(period, start_date, end_date)

    stmt = select(
        AgentExecutionHistory.username,
        func.count().label("calls"),
        func.coalesce(func.sum(AgentExecutionHistory.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(AgentExecutionHistory.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(AgentExecutionHistory.total_tokens), 0).label("total_tokens"),
    ).where(
        AgentExecutionHistory.created_at >= range_start,
        AgentExecutionHistory.created_at <= range_end,
    )
    
    if not admin_flag:
        stmt = stmt.where(AgentExecutionHistory.username == user_name)
        
    stmt = stmt.group_by(AgentExecutionHistory.username).order_by(desc("total_tokens"))
    rows = (await db.execute(stmt)).all()
    
    real_names = {}
    try:
        users_res = await db.execute(select(User.user_name, User.real_name))
        for u in users_res.all():
            real_names[u.user_name] = u.real_name
    except Exception:
        pass
        
    results = []
    for r in rows:
        results.append({
            "username": r.username,
            "real_name": real_names.get(r.username) or r.username,
            "calls": r.calls or 0,
            "prompt_tokens": int(r.prompt_tokens or 0),
            "completion_tokens": int(r.completion_tokens or 0),
            "total_tokens": int(r.total_tokens or 0),
        })
        
    total_all = sum(x["total_tokens"] for x in results)
    for x in results:
        x["ratio"] = round((x["total_tokens"] / total_all * 100), 2) if total_all > 0 else 0
        
    return results


# --------------------------------------------------------------------------- #
# 图片用量（生图 / 改图）
#
# 图像接口的计量留痕在轨迹的 `meta_info.image_usage` 里（见
# `image_generation_tool._build_image_usage`：张数 / 尺寸 / 模型 / 上游折算 token /
# 耗时），并且**刻意不进 `total_tokens`** —— 那个数驱动月度配额，混进去会让
# 「消息显示的用量」和「配额实际消耗」变成两套算法。
#
# 代价是现场只统计 Token 的页面里看不到图片，只能一轮一轮点进轨迹查。这段从轨迹表
# 直接聚合，**零 DDL、零新采集**。
#
# 三条刻意的口径，改之前先确认需求：
# 1. 上游没返回 usage 是常态（OpenAI images 端点根本不返回），记 0 会被读成「这次
#    没花钱」，所以单独计数 `tokens_missing_calls`；
# 2. 失败张数 = `requested - succeeded`：一次要 2 张只出 1 张，那一张也真扣了钱；
# 3. 用量必须能归属到某个用户/会话才展示：轨迹表没有 username，靠 trace_id 关联
#    历史表（INNER JOIN）。关联不上的（如历史未落库的极端轮次）不计入——宁可少算
#    也不能把别人的消耗记到当前用户名下。
# --------------------------------------------------------------------------- #

def _json_obj(raw: Any) -> Optional[Dict[str, Any]]:
    """JSON 列在不同方言/驱动下回出来可能是 dict、str 或 bytes，统一成 dict。"""
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    return raw if isinstance(raw, dict) else None


def _usage_int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _new_usage_bucket() -> Dict[str, Any]:
    return {
        "calls": 0,
        "images": 0,
        "requested": 0,
        "failed": 0,
        "upstream_tokens": 0,
        "tokens_missing_calls": 0,
        "_elapsed_sum": 0,
        "last_at": None,
    }


def _accumulate_usage(
    bucket: Dict[str, Any],
    usage: Dict[str, Any],
    elapsed_ms: Any,
    created_at: Any,
) -> None:
    """把一次工具调用的 `image_usage` 并入一个聚合桶。"""
    succeeded = _usage_int(usage.get("succeeded_count"))
    # 老轨迹只写了 succeeded 时，请求张数按已出张数兜底，不能凭空记 0 个请求
    requested_raw = usage.get("requested_count")
    requested = succeeded if requested_raw is None else _usage_int(requested_raw)

    bucket["calls"] += 1
    bucket["images"] += succeeded
    bucket["requested"] += requested
    bucket["failed"] += max(requested - succeeded, 0)

    upstream = _json_obj(usage.get("upstream_tokens"))
    if upstream:
        bucket["upstream_tokens"] += _usage_int(upstream.get("total_tokens"))
    else:
        bucket["tokens_missing_calls"] += 1

    bucket["_elapsed_sum"] += _usage_int(elapsed_ms)
    if created_at and (bucket["last_at"] is None or created_at > bucket["last_at"]):
        bucket["last_at"] = created_at


def _finalize_usage_bucket(
    bucket: Dict[str, Any],
    *,
    key: Optional[str] = None,
    label: Optional[str] = None,
) -> Dict[str, Any]:
    calls = bucket["calls"]
    payload: Dict[str, Any] = {
        "images": bucket["images"],
        "requested": bucket["requested"],
        "failed": bucket["failed"],
        "calls": calls,
        "upstream_tokens": bucket["upstream_tokens"],
        # 0 不等于「没花钱」：这些调用上游压根没回 usage，界面上要单独说明
        "tokens_missing_calls": bucket["tokens_missing_calls"],
        "avg_elapsed_ms": round(bucket["_elapsed_sum"] / calls) if calls else 0,
        "last_at": bucket["last_at"].isoformat() if bucket["last_at"] else None,
    }
    if key is not None:
        payload = {"key": key, "label": label or key, **payload}
    return payload


def _image_usage_row(row: Any, usage: Optional[dict]) -> Dict[str, Any]:
    """把一行查询结果压成聚合循环需要的扁平结构。"""
    return {
        "usage": usage,
        "elapsed_ms": getattr(row, "execution_time_ms", None),
        "created_at": getattr(row, "created_at", None),
        "username": getattr(row, "username", None),
        "agent_id": getattr(row, "agent_id", None),
        "agent_name": getattr(row, "agent_name", None),
        "agent_display_name": getattr(row, "agent_display_name", None),
    }


async def _load_image_usage_rows(
    db: AsyncSession,
    *,
    range_start: datetime,
    range_end: datetime,
    username: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """拉取区间内的图像用量行（只取聚合需要的那几列）。

    `meta_info` 里还有决策快照等大对象，整列拖回来只为抽一个小 JSON 不划算，
    所以只 select `meta_info['image_usage']` 这一个键——SQLAlchemy 会按方言生成
    `JSON_EXTRACT(...)` 或 `->`，两套主库都不用分支。

    分两段查：先取 `meta_info` 里已经有量的正常轨迹；再把「只在 `tool_output` 里留了量」
    的历史轨迹回捞一遍（见 `app/utils/image_usage`）。历史那段的过滤条件里带
    `meta_info.image_usage IS NULL`，所以同一次调用不可能被算两次。
    """
    usage_col = AgentExecutionTrace.meta_info["image_usage"]
    base_columns = (
        AgentExecutionTrace.trace_id,
        AgentExecutionTrace.tool_name,
        AgentExecutionTrace.execution_time_ms,
        AgentExecutionTrace.created_at,
        AgentExecutionHistory.username,
        AgentExecutionHistory.agent_id,
        AgentExecutionHistory.agent_name,
        AgentExecutionHistory.agent_display_name,
    )

    def _stmt(*where: Any):
        stmt = (
            select(*base_columns)
            .join(
                AgentExecutionHistory,
                AgentExecutionHistory.trace_id == AgentExecutionTrace.trace_id,
            )
            .where(
                AgentExecutionTrace.created_at >= range_start,
                AgentExecutionTrace.created_at <= range_end,
                *where,
            )
        )
        if username:
            stmt = stmt.where(AgentExecutionHistory.username == username)
        return stmt

    rows: List[Dict[str, Any]] = []
    live_stmt = _stmt(usage_col.isnot(None)).add_columns(usage_col.label("image_usage"))
    for row in (await db.execute(live_stmt)).all():
        usage = _json_obj(getattr(row, "image_usage", None))
        if usage:
            rows.append(_image_usage_row(row, usage))

    # 历史轨迹：只有 tool_output 里留着量的那些图像调用
    legacy_stmt = _stmt(
        usage_col.is_(None),
        AgentExecutionTrace.tool_name.in_(sorted(IMAGE_USAGE_TOOL_NAMES)),
        AgentExecutionTrace.tool_output.isnot(None),
    ).add_columns(AgentExecutionTrace.tool_output)
    for row in (await db.execute(legacy_stmt)).all():
        usage = image_usage_from_tool_output(getattr(row, "tool_output", None))
        if usage:
            rows.append(_image_usage_row(row, usage))

    return rows


async def _build_image_usage_stats(
    db: AsyncSession,
    *,
    user: dict,
    range_start: datetime,
    range_end: datetime,
    span_days: int,
    group_by: str,
) -> Dict[str, Any]:
    """汇总 + 按天趋势 + 分组明细。

    页面与 CSV 导出共用这一个函数：两边各写一套聚合，迟早会变成「页面数字和导出
    数字对不上」的工单。
    """
    admin_flag = is_admin(user)
    rows = await _load_image_usage_rows(
        db,
        range_start=range_start,
        range_end=range_end,
        username=None if admin_flag else user.get("user_name"),
    )

    summary_bucket = _new_usage_bucket()
    day_buckets: Dict[str, Dict[str, Any]] = {}
    group_buckets: Dict[str, Dict[str, Any]] = {}
    group_labels: Dict[str, str] = {}

    for row in rows:
        usage = row["usage"]
        elapsed = row["elapsed_ms"]
        created_at = row["created_at"]
        _accumulate_usage(summary_bucket, usage, elapsed, created_at)

        day_key = created_at.strftime("%Y-%m-%d") if created_at else ""
        _accumulate_usage(day_buckets.setdefault(day_key, _new_usage_bucket()), usage, elapsed, created_at)

        if group_by == "user":
            group_key = row["username"] or ""
            group_label = row["username"] or "未知用户"
        else:
            group_key = row["agent_id"] or ""
            group_label = (
                row["agent_display_name"]
                or row["agent_name"]
                or row["agent_id"]
                or "未知智能体"
            )
        _accumulate_usage(group_buckets.setdefault(group_key, _new_usage_bucket()), usage, elapsed, created_at)
        group_labels[group_key] = group_label

    # 趋势必须按天补齐空档：缺的天直接不返回，前端会把 3 天画成 1 天
    trends: List[Dict[str, Any]] = []
    for i in range(max(span_days, 1)):
        day = range_start + timedelta(days=i)
        if day > range_end:
            break
        day_key = day.strftime("%Y-%m-%d")
        trends.append(
            {"date": day_key, **_finalize_usage_bucket(day_buckets.get(day_key) or _new_usage_bucket())}
        )

    groups = [
        _finalize_usage_bucket(bucket, key=key, label=group_labels.get(key))
        for key, bucket in group_buckets.items()
    ]
    # 出图多的排前面；同为 0 张时按调用次数，最后按名字稳定排序（避免刷新跳动）
    groups.sort(key=lambda item: (-item["images"], -item["calls"], item["label"] or ""))

    return {
        "range": {
            "start_date": range_start.strftime("%Y-%m-%d"),
            "end_date": range_end.strftime("%Y-%m-%d"),
        },
        "group_by": group_by,
        "summary": _finalize_usage_bucket(summary_bucket),
        "trends": trends,
        "groups": groups,
    }


def _resolve_image_usage_window(
    days: int, start_date: Optional[str], end_date: Optional[str]
) -> tuple[datetime, datetime, int]:
    """图像用量的时间窗；半开区间视为调用错误。

    直接套 `_resolve_token_stats_range` 会把「只给了开始日期」静默当成「最近 days
    天」——用户以为区间生效了，实际看到的是另一段数据。
    """
    if bool(start_date) != bool(end_date):
        raise HTTPException(status_code=400, detail="start_date 与 end_date 必须同时提供")
    return _resolve_token_stats_range(days, start_date, end_date)


@router.get("/token-stats/image-usage")
async def get_image_usage_stats(
    days: int = Query(30, ge=1, le=90),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    group_by: str = Query("agent", pattern="^(agent|user)$", description="分组维度"),
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """图片用量汇总（生图/改图的张数、上游折算 token、耗时）。

    图片不计入 `total_tokens`，也不会体现在月度配额消耗里——本接口是查看它的唯一
    汇总入口。非管理员只能看到自己的消耗。
    """
    if group_by == "user" and not is_admin(user):
        raise HTTPException(status_code=400, detail="按用户分组仅管理员可用")

    range_start, range_end, span_days = _resolve_image_usage_window(days, start_date, end_date)
    return await _build_image_usage_stats(
        db,
        user=user,
        range_start=range_start,
        range_end=range_end,
        span_days=span_days,
        group_by=group_by,
    )


@router.get("/token-stats/image-usage/export")
async def export_image_usage_stats(
    days: int = Query(30, ge=1, le=90),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    group_by: str = Query("agent", pattern="^(agent|user)$", description="分组维度"),
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """把图片用量明细导成 CSV（与页面同一套聚合口径）。"""
    if group_by == "user" and not is_admin(user):
        raise HTTPException(status_code=400, detail="按用户分组仅管理员可用")

    range_start, range_end, span_days = _resolve_image_usage_window(days, start_date, end_date)
    stats = await _build_image_usage_stats(
        db,
        user=user,
        range_start=range_start,
        range_end=range_end,
        span_days=span_days,
        group_by=group_by,
    )

    dimension = "用户" if group_by == "user" else "智能体"
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            dimension,
            "出图张数",
            "请求张数",
            "失败张数",
            "调用次数",
            "上游Token",
            "上游未返回次数",
            "平均耗时ms",
            "最近一次",
        ]
    )
    for row in stats["groups"]:
        writer.writerow(
            [
                row["label"],
                row["images"],
                row["requested"],
                row["failed"],
                row["calls"],
                row["upstream_tokens"],
                row["tokens_missing_calls"],
                row["avg_elapsed_ms"],
                (row["last_at"] or "").replace("T", " ")[:19],
            ]
        )
    summary = stats["summary"]
    writer.writerow(
        [
            "合计",
            summary["images"],
            summary["requested"],
            summary["failed"],
            summary["calls"],
            summary["upstream_tokens"],
            summary["tokens_missing_calls"],
            summary["avg_elapsed_ms"],
            (summary["last_at"] or "").replace("T", " ")[:19],
        ]
    )

    # BOM 是为了 Excel 打开中文表头不乱码；不带它 Excel 会按 GBK 解读
    body = "\ufeff" + buffer.getvalue()
    filename = f"image-usage-{stats['range']['start_date']}_{stats['range']['end_date']}.csv"
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
