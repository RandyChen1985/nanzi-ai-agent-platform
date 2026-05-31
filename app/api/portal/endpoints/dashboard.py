"""
Dashboard API endpoints for statistics and overview data.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, case, text
from app.core.dependencies import require_api_key, require_admin
from app.core.orm import get_db_session
from app.core.redis import get_redis
from app.models.audit import AccessLog, AgentExecutionTrace, AgentExecutionHistory
from app.models.user import User
from app.models.agent import AIAgent
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

router = APIRouter()

def is_admin(user: dict) -> bool:
    """Check if user is admin"""
    return user.get("role") == "admin"

@router.get("/admin-stats")
async def get_admin_stats(
    period: str = Query("today", pattern="^(today|week|month)$"),
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    admin_flag = is_admin(user)
    user_name = user["user_name"]
    
    # 1. User Stats (Admin Only)
    total_users = None
    active_users = None
    
    if admin_flag:
        count_users = await db.execute(select(func.count()).select_from(User).where(User.status == 1))
        total_users = count_users.scalar()
        
        seven_days_ago = datetime.now() - timedelta(days=7)
        count_active = await db.execute(
            select(func.count(func.distinct(AccessLog.user_name)))
            .where(AccessLog.created_at >= seven_days_ago)
        )
        active_users = count_active.scalar()

    # 2. Time Range
    now = datetime.now()
    if period == "today":
        start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_time = now - timedelta(days=7)
    else:
        start_time = now - timedelta(days=30)
        
    # 3. API Stats
    stmt = select(
        func.count().label("total"),
        func.avg(AccessLog.process_time_ms).label("avg_time"),
        func.sum(case(((AccessLog.status_code >= 200) & (AccessLog.status_code < 300), 1), else_=0)).label("success"),
        func.sum(case((AccessLog.status_code >= 400, 1), else_=0)).label("error")
    ).where(AccessLog.created_at >= start_time)
    
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
    ).where(AgentExecutionHistory.created_at >= start_time)
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
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    user_name = user["user_name"]
    
    # Check status
    res = await db.execute(select(User.status).where(User.user_name == user_name))
    status_val = res.scalar_one_or_none()
    api_key_status = "active" if status_val == 1 else "inactive"
    
    # Time Range
    now = datetime.now()
    if period == "today":
        start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_time = now - timedelta(days=7)
    else:
        start_time = now - timedelta(days=30)
        
    # Stats
    stmt = select(
        func.count().label("total"),
        func.avg(AccessLog.process_time_ms).label("avg_time"),
        func.sum(case(((AccessLog.status_code >= 200) & (AccessLog.status_code < 300), 1), else_=0)).label("success"),
        func.max(AccessLog.created_at).label("last_call")
    ).where(
        AccessLog.user_name == user_name,
        AccessLog.created_at >= start_time
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
        AgentExecutionHistory.created_at >= start_time,
        AgentExecutionHistory.username == user_name
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
    
    # MySQL specific date format
    hour_key_expr = func.date_format(AccessLog.created_at, '%Y-%m-%d %H:00:00')
    
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
    
    # Recent Users
    if admin_flag:
        stmt_users = select(User).order_by(desc(User.created_at)).limit(min(limit, 5))
        users = (await db.execute(stmt_users)).scalars().all()
        result["recent_users"] = [
            {"user_name": u.user_name, "role": u.role, "created_at": u.created_at.isoformat() if u.created_at else None}
            for u in users
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
        cursor = 0
        keys = []
        while True:
            cursor, batch = await redis.scan(cursor, match="auth:api_key:*", count=100)
            keys.extend(batch)
            if cursor == 0: break
        
        count = len(keys)
        online_users = []
        
        # Only return detailed list for admins
        if admin_flag and keys:
            # Use pipeline to fetch all hash data efficiently
            pipe = redis.pipeline()
            for key in keys:
                pipe.hgetall(key)
            
            hash_results = await pipe.execute()
            
            for u_data in hash_results:
                if u_data:
                    # Redis hash results are dicts
                    online_users.append({
                        "user_name": u_data.get("user_name"),
                        "real_name": u_data.get("real_name") or u_data.get("user_name"),
                        "role": u_data.get("role"),
                        "last_active": datetime.now().isoformat()
                    })
        
        return {
            "count": count,
            "users": online_users if admin_flag else []
        }
    except Exception as e:
        print(f"Redis error in get_online_users: {e}")
        return {"count": 0, "users": []}

@router.get("/agent-stats")
async def get_agent_stats(
    period: str = Query("today", pattern="^(today|week|month)$"),
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    admin_flag = is_admin(user)
    user_name = user["user_name"]
    
    now = datetime.now()
    if period == "today":
        start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_time = now - timedelta(days=7)
    else:
        start_time = now - timedelta(days=30)
        
    # 1. Health Stats
    stmt = select(
        func.count().label("total"),
        func.sum(case((AgentExecutionTrace.status == 'success', 1), else_=0)).label("success"),
        func.avg(AgentExecutionTrace.execution_time_ms).label("avg_lat")
    ).where(
        AgentExecutionTrace.created_at >= start_time,
        AgentExecutionTrace.event_type.in_(['tool_call', 'tool_result'])
    )
    
    # For non-admin users, filter by user through trace_id
    if not admin_flag:
        # Subquery to get trace_ids for the current user
        user_trace_ids = select(AgentExecutionHistory.trace_id).where(
            AgentExecutionHistory.username == user_name,
            AgentExecutionHistory.created_at >= start_time
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
        AgentExecutionTrace.created_at >= start_time,
        AgentExecutionTrace.tool_name != None
    )
    
    # For non-admin users, filter by user through trace_id
    if not admin_flag:
        user_trace_ids = select(AgentExecutionHistory.trace_id).where(
            AgentExecutionHistory.username == user_name,
            AgentExecutionHistory.created_at >= start_time
        )
        stmt_usage = stmt_usage.where(AgentExecutionTrace.trace_id.in_(user_trace_ids))
        
    stmt_usage = stmt_usage.group_by(AgentExecutionTrace.tool_name).order_by(desc("count"))
    
    tool_usage = [{"name": r.tool_name, "value": r.count} for r in (await db.execute(stmt_usage)).all()]
    
    # 3. Performance Trend
    trend_start = (now - timedelta(hours=23)).replace(minute=0, second=0, microsecond=0)
    hour_key_expr = func.date_format(AgentExecutionTrace.created_at, '%Y-%m-%d %H:00:00')
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
        AgentExecutionHistory.created_at >= start_time
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
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    admin_flag = is_admin(user)
    user_name = user["user_name"]
    start_date = (datetime.now() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    
    stmt = select(
        func.date(AgentExecutionHistory.created_at).label("date"),
        func.count().label("calls"),
        func.coalesce(func.sum(AgentExecutionHistory.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(AgentExecutionHistory.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(AgentExecutionHistory.total_tokens), 0).label("total_tokens"),
    ).where(AgentExecutionHistory.created_at >= start_date)
    
    if not admin_flag:
        stmt = stmt.where(AgentExecutionHistory.username == user_name)
        
    stmt = stmt.group_by(func.date(AgentExecutionHistory.created_at)).order_by("date")
    rows = (await db.execute(stmt)).all()
    
    # 构造默认无数据的空值，确保前端折线图有连续日期
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
    for i in range(days):
        d = start_date + timedelta(days=i)
        d_str = d.strftime('%Y-%m-%d')
        stats = results_map.get(d_str, empty_stats)
        trends.append({
            "date": d_str,
            "calls": stats["calls"],
            "prompt_tokens": stats["prompt_tokens"],
            "completion_tokens": stats["completion_tokens"],
            "total_tokens": stats["total_tokens"],
        })
        
    return trends

@router.get("/token-stats/agents")
async def get_token_stats_agents(
    period: str = Query("today", pattern="^(today|week|month)$"),
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    admin_flag = is_admin(user)
    user_name = user["user_name"]
    now = datetime.now()
    if period == "today":
        start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_time = now - timedelta(days=7)
    else:
        start_time = now - timedelta(days=30)
        
    stmt = select(
        AgentExecutionHistory.agent_id,
        AIAgent.display_name,
        func.count().label("calls"),
        func.coalesce(func.sum(AgentExecutionHistory.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(AgentExecutionHistory.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(AgentExecutionHistory.total_tokens), 0).label("total_tokens"),
    ).outerjoin(AIAgent, AgentExecutionHistory.agent_id == AIAgent.id).where(
        AgentExecutionHistory.created_at >= start_time
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
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    admin_flag = is_admin(user)
    user_name = user["user_name"]
    now = datetime.now()
    if period == "today":
        start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_time = now - timedelta(days=7)
    else:
        start_time = now - timedelta(days=30)
        
    stmt = select(
        AgentExecutionHistory.username,
        func.count().label("calls"),
        func.coalesce(func.sum(AgentExecutionHistory.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(AgentExecutionHistory.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(AgentExecutionHistory.total_tokens), 0).label("total_tokens"),
    ).where(AgentExecutionHistory.created_at >= start_time)
    
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