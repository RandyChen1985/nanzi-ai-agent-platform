import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Body, Request, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import AsyncGenerator, Awaitable, Callable, List, Any, Dict, Optional
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)

from app.core.orm import get_db_session
from app.services.metadata_service import MetadataService
from app.schemas.metadata import (
    DatasetCreate, DatasetUpdate, DatasetResponse, DatasetDetailResponse, DatasetOptionResponse,
    TableCreate, TableResponse,
    MetricSchema, MetricResponse, MetricRecommendRequest,
    RelationshipSchema, RelationshipResponse, RelationshipRecommendRequest,
    BatchDeleteTablesRequest, BatchDeleteMetricsRequest, BatchDeleteRelationshipsRequest,
    MetaDriftAlertResponse, ResolveDriftAlertRequest, BatchResolveDriftAlertsRequest, DriftSummaryResponse, InspectionStartResponse,
    CronInspectionConfigResponse, CronInspectionConfigRequest,
)
from app.models.user import User
from app.models.permission import Role
from app.models.task import AgentScheduledTask
from app.core.dependencies import require_admin, get_current_user, require_permission
from app.core.errors import ErrorCode
from app.services.permission_service import PermissionService
from app.services.metadata_sync_log_service import metadata_sync_log_service
from app.services.metadata_drift_service import MetadataDriftService
from app.services.metadata_inspection_service import MetadataInspectionService
from app.services.ai.scheduler_service import scheduler_service
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from datetime import datetime

router = APIRouter()

METADATA_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _metadata_sse_event(event: str, data: Dict[str, Any]) -> str:
    """编码元数据 AI SSE 事件，中文内容保持可读以便抓包排查。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def _stream_metadata_recommendation(
    request: Request,
    dataset_id: int,
    recommendation_type: str,
    worker: Callable[[Callable[[Dict[str, Any]], Awaitable[None]]], Awaitable[Dict[str, Any]]],
) -> StreamingResponse:
    """将推荐服务的进度回调转为 SSE，并在客户端断开时清理模型任务。"""
    event_queue: asyncio.Queue[tuple[str, Dict[str, Any]]] = asyncio.Queue()

    async def progress_callback(payload: Dict[str, Any]) -> None:
        event_payload = {
            "status": "running",
            "recommendation_type": recommendation_type,
            **payload,
        }
        logger.info(
            "元数据 AI 进度: type=%s, dataset_id=%s, phase=%s, percent=%s, "
            "completed=%s, total=%s, remaining=%s, batch=%s, result_count=%s, "
            "candidate_pairs=%s, completed_pairs=%s, remaining_pairs=%s",
            recommendation_type,
            dataset_id,
            event_payload.get("phase"),
            event_payload.get("percent"),
            event_payload.get("completed_units"),
            event_payload.get("total_units"),
            event_payload.get("remaining_units"),
            event_payload.get("batch_count"),
            event_payload.get("result_count"),
            event_payload.get("candidate_pair_count"),
            event_payload.get("completed_pair_count"),
            event_payload.get("remaining_pair_count"),
        )
        await event_queue.put(("progress", event_payload))

    async def run_worker() -> None:
        try:
            result = await worker(progress_callback)
            interrupted = isinstance(result, dict) and result.get("_stop_reason") in {
                "partial_batch_error",
                "partial_group_error",
            }
            debug = result.get("_debug", {}) if isinstance(result, dict) else {}
            if recommendation_type == "metrics":
                total_units = 5
                completed_units = 5
                result_count = len(result.get("metrics", [])) if isinstance(result, dict) else 0
            else:
                total_units = int(
                    debug.get("candidate_group_count")
                    or debug.get("schema_table_count")
                    or 0
                )
                completed_units = int(
                    debug.get("completed_group_count")
                    or debug.get("completed_anchor_count")
                    or 0
                )
                result_count = len(result.get("relationships", [])) if isinstance(result, dict) else 0
            remaining_units = int(
                debug.get("remaining_group_count")
                or debug.get("remaining_anchor_count")
                or max(total_units - completed_units, 0)
            )
            await event_queue.put((
                "interrupted" if interrupted else "completed",
                {
                    "status": "interrupted" if interrupted else "completed",
                    "recommendation_type": recommendation_type,
                    "phase": "interrupted" if interrupted else "completed",
                    "message": (
                        "AI 推荐中途发生异常，已返回此前完成的结果"
                        if interrupted
                        else "AI 推荐已完成"
                    ),
                    "percent": (
                        int(95 * completed_units / max(total_units, 1))
                        if interrupted
                        else 100
                    ),
                    "trace_id": result.get("_trace_id") if isinstance(result, dict) else None,
                    "completed_units": completed_units if interrupted else total_units,
                    "total_units": total_units,
                    "remaining_units": remaining_units if interrupted else 0,
                    "batch_count": result.get("_batch_count") if isinstance(result, dict) else None,
                    "result_count": result_count,
                    "candidate_pair_count": debug.get("candidate_pair_count"),
                    "strategy": debug.get("strategy"),
                    "smart_candidate_pair_count": debug.get("smart_candidate_pair_count"),
                    "candidate_pair_limit": debug.get("candidate_pair_limit"),
                    "truncated_pair_count": debug.get("truncated_pair_count"),
                    "completed_pair_count": debug.get("completed_pair_count"),
                    "remaining_pair_count": debug.get("remaining_pair_count"),
                    "fk_relationship_count": debug.get("fk_relationship_count"),
                    "probed_pair_count": debug.get("probed_pair_count"),
                    "confirmed_pair_count": debug.get("confirmed_pair_count"),
                    "unverified_pair_count": debug.get("unverified_pair_count"),
                    "probe_duration_ms": debug.get("probe_duration_ms"),
                    "probe_unavailable_reason": debug.get("probe_unavailable_reason"),
                    "stop_reason": result.get("_stop_reason") if isinstance(result, dict) else None,
                    "result": result,
                },
            ))
        except asyncio.CancelledError:
            logger.warning(
                "元数据 AI SSE 任务已取消: type=%s, dataset_id=%s",
                recommendation_type,
                dataset_id,
            )
            raise
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            logger.error(
                "元数据 AI SSE 任务失败: type=%s, dataset_id=%s, error=%s",
                recommendation_type,
                dataset_id,
                detail,
                exc_info=True,
            )
            await event_queue.put((
                "error",
                {
                    "status": "error",
                    "recommendation_type": recommendation_type,
                    "phase": "error",
                    "message": str(detail),
                    "percent": 0,
                },
            ))

    recommendation_task = asyncio.create_task(
        run_worker(),
        name=f"metadata-{recommendation_type}-stream-{dataset_id}",
    )

    async def event_generator() -> AsyncGenerator[str, None]:
        terminal_event_sent = False
        heartbeat_count = 0
        yield _metadata_sse_event(
            "started",
            {
                "status": "running",
                "recommendation_type": recommendation_type,
                "phase": "preparing",
                "message": "请求已建立，正在准备生成上下文",
                "percent": 1,
            },
        )
        try:
            while True:
                if await request.is_disconnected():
                    logger.warning(
                        "元数据 AI SSE 客户端断开: type=%s, dataset_id=%s",
                        recommendation_type,
                        dataset_id,
                    )
                    break
                try:
                    event, payload = await asyncio.wait_for(event_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    heartbeat_count += 1
                    if heartbeat_count % 10 == 0:
                        yield ": keep-alive\n\n"
                    if recommendation_task.done() and event_queue.empty():
                        break
                    continue

                heartbeat_count = 0
                yield _metadata_sse_event(event, payload)
                if event in {"completed", "interrupted", "error"}:
                    terminal_event_sent = True
                    break
        finally:
            if not recommendation_task.done():
                recommendation_task.cancel()
            try:
                await recommendation_task
            except asyncio.CancelledError:
                pass
            if not terminal_event_sent:
                logger.warning(
                    "元数据 AI SSE 流中断且未发送终态: type=%s, dataset_id=%s",
                    recommendation_type,
                    dataset_id,
                )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=METADATA_SSE_HEADERS,
    )

# --- Dataset APIs ---

@router.get("/datasets", response_model=List[DatasetResponse])
async def list_datasets(
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(require_permission("menu", "menu:metadata"))
):
    """获取所有数据集 (需菜单权限)"""
    datasets = await MetadataService.get_datasets(conn)
    await MetadataService.repair_stale_local_sync_flags(datasets)
    return datasets


@router.get("/datasets/accessible", response_model=List[DatasetOptionResponse])
async def list_accessible_datasets(
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user),
):
    """轻量可访问数据集选项：仅当前用户有权限的启用数据集，供会话资源等场景使用。"""
    user_id = user.get("id") or user.get("user_id")
    try:
        user_id_int = int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        user_id_int = None
    is_admin = user.get("role") == "admin" or user.get("is_admin") is True
    if not is_admin:
        # 兼容 roles 列表口径
        roles = user.get("roles") or []
        if isinstance(roles, str):
            roles = [roles]
        is_admin = "admin" in {str(r).strip().lower() for r in roles}

    datasets = await MetadataService.list_accessible_dataset_options(
        conn,
        user_id=user_id_int,
        is_admin=bool(is_admin),
        status=1,
    )
    return datasets


@router.post("/datasets", response_model=DatasetResponse, dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def create_dataset(
    dataset: DatasetCreate, 
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user)
):
    """创建数据集 (管理员或具有编辑权限的用户)"""
    exists = await MetadataService.get_dataset_by_name(conn, dataset.name)
    if exists:
        raise HTTPException(status_code=400, detail="数据集名称已存在")
    
    return await MetadataService.create_dataset(
        conn, 
        dataset.model_dump(),
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="创建数据集"
    )


@router.get(
    "/datasets/drift-summary",
    response_model=DriftSummaryResponse,
    dependencies=[Depends(require_permission("menu", "menu:metadata"))],
)
async def get_drift_summary(conn: AsyncSession = Depends(get_db_session)):
    """获取所有数据集的未处理 Schema 漂移告警统计概览。"""
    total, dataset_counts = await MetadataDriftService.get_drift_summary(conn)
    return DriftSummaryResponse(total_pending=total, datasets=dataset_counts)


@router.get("/datasets/{dataset_id}", response_model=DatasetDetailResponse)
async def get_dataset(
    dataset_id: int, 
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(require_permission("menu", "menu:metadata"))
):
    """获取单个数据集详情 (需菜单权限)"""
    is_admin = user.get("role") == "admin"
    ds = await MetadataService.get_dataset_by_id(
        conn, 
        dataset_id, 
        user_id=user.get("id"), 
        is_admin=is_admin
    )
    if not ds:
        raise HTTPException(status_code=404, detail="数据集不存在")
    await MetadataService.repair_stale_local_sync_flags([ds])
    return ds


@router.get("/datasets/{dataset_id}/permissions")
async def get_metadata_dataset_permissions(
    dataset_id: int,
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(require_permission("element", "element:metadata:edit")),
):
    """获取元数据数据集授权的所有角色和用户列表"""
    from app.models.permission import ResourcePermission, Role
    from app.models.user import User
    from sqlalchemy import select

    stmt = select(ResourcePermission).where(
        ResourcePermission.resource_type == "metadata",
        ResourcePermission.resource_id == str(dataset_id),
        ResourcePermission.enabled == True
    )
    result = await conn.execute(stmt)
    perms = result.scalars().all()

    user_ids = [p.user_id for p in perms if p.user_id is not None]
    role_ids = [p.role_id for p in perms if p.role_id is not None]

    granted_users = []
    granted_roles = []

    if user_ids:
        user_stmt = select(User.id, User.user_name, User.real_name).where(User.id.in_(user_ids), User.status == 1)
        user_res = await conn.execute(user_stmt)
        granted_users = [{"id": u.id, "user_name": u.user_name, "real_name": u.real_name} for u in user_res.all()]

    if role_ids:
        role_stmt = select(Role.id, Role.code, Role.name).where(Role.id.in_(role_ids))
        role_res = await conn.execute(role_stmt)
        granted_roles = [{"id": r.id, "code": r.code, "name": r.name} for r in role_res.all()]

    return {
        "code": 0,
        "data": {
            "users": granted_users,
            "roles": granted_roles
        }
    }


class AddPermissionsRequest(BaseModel):
    target_type: str  # "user" 或 "role"
    target_ids: List[int]

class DeletePermissionRequest(BaseModel):
    target_type: str  # "user" 或 "role"
    target_id: int


@router.get("/candidates")
async def get_metadata_auth_candidates(
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(require_permission("element", "element:metadata:edit"))
):
    """获取可用于数据集权限分配的活跃角色和用户候选列表 (需数据集编辑权限)"""
    from app.models.permission import Role
    from app.models.user import User
    from sqlalchemy import select

    role_stmt = select(Role.id, Role.code, Role.name)
    role_res = await conn.execute(role_stmt)
    roles = [{"id": r.id, "code": r.code, "name": r.name} for r in role_res.all()]

    user_stmt = select(User.id, User.user_name, User.real_name).where(User.status == 1)
    user_res = await conn.execute(user_stmt)
    users = [{"id": u.id, "user_name": u.user_name, "real_name": u.real_name} for u in user_res.all()]

    return {
        "code": 0,
        "data": {
            "roles": roles,
            "users": users
        }
    }


@router.post("/datasets/{dataset_id}/permissions", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def add_metadata_dataset_permissions(
    dataset_id: int,
    payload: AddPermissionsRequest,
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user)
):
    """添加元数据数据集的授权角色或用户 (需数据集编辑权限)"""
    from app.models.permission import ResourcePermission, UserRoleRelation
    from sqlalchemy import select
    
    perm_service = PermissionService(conn)
    affected_user_ids = set()

    for tid in payload.target_ids:
        # 查询是否已经有对应记录了
        stmt = select(ResourcePermission).where(
            ResourcePermission.resource_type == "metadata",
            ResourcePermission.resource_id == str(dataset_id)
        )
        if payload.target_type == "user":
            stmt = stmt.where(ResourcePermission.user_id == tid)
            affected_user_ids.add(tid)
        else:
            stmt = stmt.where(ResourcePermission.role_id == tid)
            # 获取该角色下的所有关联用户
            r_users_stmt = select(UserRoleRelation.user_id).where(UserRoleRelation.role_id == tid)
            r_users_res = await conn.execute(r_users_stmt)
            for uid in r_users_res.scalars().all():
                affected_user_ids.add(uid)

        result = await conn.execute(stmt)
        record = result.scalar_one_or_none()

        if record:
            record.enabled = True
        else:
            new_perm = ResourcePermission(
                resource_type="metadata",
                resource_id=str(dataset_id),
                enabled=True
            )
            if payload.target_type == "user":
                new_perm.user_id = tid
            else:
                new_perm.role_id = tid
            conn.add(new_perm)

    await conn.flush()

    # 清理受影响用户的缓存，使其变更即刻生效
    if affected_user_ids:
        await perm_service.invalidate_cached_permissions_for_users(affected_user_ids)
        from app.services.ai.config import AgentConfigProvider
        for uid in affected_user_ids:
            await AgentConfigProvider.invalidate_dataset_menu_cache(user_id=uid)

    return {"code": 0, "message": "权限配置已成功添加"}


@router.delete("/datasets/{dataset_id}/permissions", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def delete_metadata_dataset_permission(
    dataset_id: int,
    payload: DeletePermissionRequest,
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user)
):
    """移除元数据数据集关联的某个角色或用户授权 (需数据集编辑权限)"""
    from app.models.permission import ResourcePermission, UserRoleRelation
    from sqlalchemy import select, delete

    perm_service = PermissionService(conn)
    affected_user_ids = set()

    # 查出受影响的用户列表以作缓存清理
    if payload.target_type == "user":
        affected_user_ids.add(payload.target_id)
    else:
        r_users_stmt = select(UserRoleRelation.user_id).where(UserRoleRelation.role_id == payload.target_id)
        r_users_res = await conn.execute(r_users_stmt)
        for uid in r_users_res.scalars().all():
            affected_user_ids.add(uid)

    # 物理清除
    stmt = delete(ResourcePermission).where(
        ResourcePermission.resource_type == "metadata",
        ResourcePermission.resource_id == str(dataset_id)
    )
    if payload.target_type == "user":
        stmt = stmt.where(ResourcePermission.user_id == payload.target_id)
    else:
        stmt = stmt.where(ResourcePermission.role_id == payload.target_id)

    await conn.execute(stmt)
    await conn.flush()

    # 清理权限缓存
    if affected_user_ids:
        await perm_service.invalidate_cached_permissions_for_users(affected_user_ids)
        from app.services.ai.config import AgentConfigProvider
        for uid in affected_user_ids:
            await AgentConfigProvider.invalidate_dataset_menu_cache(user_id=uid)

    return {"code": 0, "message": "权限配置已成功移除"}


@router.put("/datasets/{dataset_id}", response_model=DatasetResponse, dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def update_dataset(
    dataset_id: int, 
    dataset: DatasetUpdate, 
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user)
):
    """更新数据集 (管理员或具有编辑权限的用户)"""
    updated = await MetadataService.update_dataset(
        conn, 
        dataset_id, 
        dataset.model_dump(exclude_unset=True),
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="更新数据集"
    )
    if not updated:
        raise HTTPException(status_code=404, detail="数据集不存在")
    return updated

@router.delete("/datasets/{dataset_id}", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def delete_dataset(
    dataset_id: int, 
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user)
):
    """删除数据集 (管理员或具有编辑权限的用户)"""
    await MetadataService.delete_dataset(
        conn, 
        dataset_id,
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="删除数据集"
    )
    return {"message": "Deleted successfully"}

@router.get("/datasets/{dataset_id}/yaml", dependencies=[Depends(require_permission("element", "element:metadata:view_yaml"))])
async def get_dataset_yaml(dataset_id: int, conn: AsyncSession = Depends(get_db_session)):
    """获取该数据集导出给 AI 的 YAML 文本 (需查看 YAML 权限)"""
    return {
        "code": 200,
        "message": "success",
        "data": await MetadataService.export_dataset_yaml(conn, dataset_id)
    }

from app.services.metadata_rag_service import MetadataRagService
from fastapi import BackgroundTasks

@router.post("/datasets/{dataset_id}/rag/sync", dependencies=[Depends(require_permission("element", "element:metadata:sync"))])
async def sync_dataset_to_rag(
    dataset_id: int, 
    background_tasks: BackgroundTasks,
    conn: AsyncSession = Depends(get_db_session)
):
    """
    手动同步数据集元数据到 RAGFlow (管理员或具有同步权限的用户)
    """
    # 1. Verify exists
    ds = await MetadataService.get_dataset_by_id(conn, dataset_id, is_admin=True)
    if not ds:
        raise HTTPException(status_code=404, detail="数据集不存在")
    
    # 2. Check status
    if ds.status != 1:
        return {"code": 400, "message": "该数据集已禁用，无法同步"}
    
    if ds.rag_sync_status == 1:
        return {"code": 400, "message": "该数据集正在同步中，请勿重复操作"}

    # 3. Trigger async task
    # We pass dataset_id. The service will open its own session or we use a factory.
    # To keep it simple and safe for BackgroundTasks, the service will handle its session.
    from app.core.orm import AsyncSessionLocal

    task = await metadata_sync_log_service.create_task(dataset_id)
    await metadata_sync_log_service.publish(
        task.task_id,
        event="started",
        stage="queued",
        message="同步任务已开始",
        progress=0,
    )
    
    async def run_sync():
        async with AsyncSessionLocal() as session:
            await MetadataRagService.sync_dataset(session, dataset_id, task_id=task.task_id)

    background_tasks.add_task(run_sync)
    
    return {
        "code": 200, 
        "message": "同步任务已启动",
        "data": {"rag_sync_status": 1, "task_id": task.task_id}
    }


@router.get(
    "/datasets/{dataset_id}/rag/sync/{task_id}/events",
    dependencies=[Depends(require_permission("element", "element:metadata:sync"))],
)
async def metadata_sync_events(dataset_id: int, task_id: str):
    """订阅当前元数据同步任务的临时实时日志。"""
    if not await metadata_sync_log_service.belongs_to_dataset(task_id, dataset_id):
        raise HTTPException(status_code=404, detail="同步任务不存在")

    async def event_stream():
        last_id = "0-0"
        try:
            while True:
                events = await metadata_sync_log_service.read_events(task_id, after_id=last_id)
                if not events:
                    events = await metadata_sync_log_service.read_new_events(
                        task_id, after_id=last_id, block_ms=1000
                    )
                for item in events:
                    event_id = item.pop("id", None)
                    if event_id:
                        last_id = event_id
                    event_name = item.get("event", "progress")
                    yield f"id: {last_id}\nevent: {event_name}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                    if event_name in metadata_sync_log_service.TERMINAL_EVENTS:
                        return
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# --- Schema Drift & Physical Inspection APIs ---

@router.get(
    "/drift-alerts",
    response_model=List[MetaDriftAlertResponse],
    dependencies=[Depends(require_permission("menu", "menu:metadata"))],
)
async def get_all_drift_alerts(
    dataset_id: Optional[int] = Query(None, description="数据集过滤"),
    status: Optional[int] = Query(None, description="状态过滤: 0-待处理, 1-已处理, 2-已忽略"),
    conn: AsyncSession = Depends(get_db_session),
):
    """获取全库或指定数据集的 Schema 漂移告警清单（大盘模式）。"""
    alerts = await MetadataDriftService.get_all_drift_alerts(conn, status=status, dataset_id=dataset_id)
    return alerts


@router.get(
    "/datasets/{dataset_id}/drift-alerts",
    response_model=List[MetaDriftAlertResponse],
    dependencies=[Depends(require_permission("menu", "menu:metadata"))],
)
async def get_dataset_drift_alerts(
    dataset_id: int,
    status: Optional[int] = Query(None, description="状态过滤: 0-待处理, 1-已处理, 2-已忽略"),
    conn: AsyncSession = Depends(get_db_session),
):
    """获取指定数据集下的 Schema 漂移告警清单。"""
    alerts = await MetadataDriftService.get_dataset_drift_alerts(conn, dataset_id, status=status)
    return alerts


@router.post(
    "/drift-alerts/{alert_id}/resolve",
    dependencies=[Depends(require_permission("element", "element:metadata:edit"))],
)
async def resolve_drift_alert(
    alert_id: int,
    payload: ResolveDriftAlertRequest,
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user),
):
    """管理员对漂移告警进行人机协同处置（下线字段 / 录入元数据 / 忽略）。"""
    try:
        user_id = int(user.get("user_id") or 0) if user else None
        user_name = user.get("user_name") if user else None
        res = await MetadataDriftService.resolve_alert(
            conn, alert_id, payload.action, user_id=user_id, user_name=user_name
        )
        return {"code": 200, "data": res, "message": res.get("message")}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("处置漂移告警失败")
        raise HTTPException(status_code=500, detail=f"处置失败: {str(e)}")


@router.post(
    "/drift-alerts/batch-resolve",
    dependencies=[Depends(require_permission("element", "element:metadata:edit"))],
)
async def batch_resolve_all_drift_alerts(
    payload: BatchResolveDriftAlertsRequest,
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user),
):
    """全局跨数据集批量处置漂移告警。"""
    try:
        user_id = int(user.get("user_id") or 0) if user else None
        user_name = user.get("user_name") if user else None
        res = await MetadataDriftService.batch_resolve_alerts_global(
            conn,
            action=payload.action,
            drift_type=payload.drift_type,
            alert_ids=payload.alert_ids,
            user_id=user_id,
            user_name=user_name,
        )
        return {"code": 200, "data": res, "message": res.get("message")}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("全局批量处置漂移告警失败")
        raise HTTPException(status_code=500, detail=f"批量处置失败: {str(e)}")


@router.post(
    "/datasets/{dataset_id}/drift-alerts/batch-resolve",
    dependencies=[Depends(require_permission("element", "element:metadata:edit"))],
)
async def batch_resolve_drift_alerts(
    dataset_id: int,
    payload: BatchResolveDriftAlertsRequest,
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user),
):
    """管理员对漂移告警进行批量人机协同处置（批量下线 / 批量录入元数据 / 批量忽略）。"""
    try:
        user_id = int(user.get("user_id") or 0) if user else None
        user_name = user.get("user_name") if user else None
        res = await MetadataDriftService.batch_resolve_alerts(
            conn,
            dataset_id=dataset_id,
            action=payload.action,
            drift_type=payload.drift_type,
            alert_ids=payload.alert_ids,
            user_id=user_id,
            user_name=user_name,
        )
        return {"code": 200, "data": res, "message": res.get("message")}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("批量处置漂移告警失败")
        raise HTTPException(status_code=500, detail=f"批量处置失败: {str(e)}")


@router.post(
    "/inspect-all",
    response_model=InspectionStartResponse,
    dependencies=[Depends(require_permission("menu", "menu:metadata"))],
)
async def trigger_all_datasets_inspection(
    background_tasks: BackgroundTasks,
    conn: AsyncSession = Depends(get_db_session),
):
    """手动触发全库所有数据集的批量物理结构巡检任务。"""
    from app.core.orm import AsyncSessionLocal

    task = await metadata_sync_log_service.create_task(0)
    await metadata_sync_log_service.publish(
        task.task_id,
        event="started",
        stage="queued",
        message="全量数据集批量物理结构巡检任务已启动，正在初始化...",
        progress=0,
    )

    async def run_inspection():
        async with AsyncSessionLocal() as session:
            await MetadataInspectionService.inspect_all_datasets(session, task_id=task.task_id)

    background_tasks.add_task(run_inspection)

    return InspectionStartResponse(
        task_id=task.task_id,
        dataset_id=0,
        message="全量巡检任务已启动",
    )


@router.post(
    "/datasets/{dataset_id}/inspect-schema",
    response_model=InspectionStartResponse,
    dependencies=[Depends(require_permission("menu", "menu:metadata"))],
)
async def trigger_dataset_inspection(
    dataset_id: int,
    background_tasks: BackgroundTasks,
    conn: AsyncSession = Depends(get_db_session),
):
    """手动触发单数据集物理结构一致性巡检，创建流式任务。"""
    ds = await MetadataService.get_dataset_by_id(conn, dataset_id)
    if not ds:
        raise HTTPException(status_code=404, detail="数据集不存在")

    from app.core.orm import AsyncSessionLocal

    task = await metadata_sync_log_service.create_task(dataset_id)
    await metadata_sync_log_service.publish(
        task.task_id,
        event="started",
        stage="queued",
        message=f"数据集【{ds.name}】物理结构巡检已加入队列...",
        progress=0,
    )

    async def run_inspection():
        async with AsyncSessionLocal() as session:
            await MetadataInspectionService.inspect_dataset(session, dataset_id, task_id=task.task_id)

    background_tasks.add_task(run_inspection)

    return InspectionStartResponse(
        task_id=task.task_id,
        dataset_id=dataset_id,
        message="巡检任务已启动",
    )


@router.get(
    "/inspect/{task_id}/events",
    dependencies=[Depends(require_permission("menu", "menu:metadata"))],
)
async def global_metadata_inspection_events(task_id: str):
    """订阅全局或任意巡检任务的实时流式日志与进度。"""
    task = await metadata_sync_log_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="巡检任务不存在")

    async def event_stream():
        last_id = "0-0"
        try:
            while True:
                events = await metadata_sync_log_service.read_events(task_id, after_id=last_id)
                if not events:
                    events = await metadata_sync_log_service.read_new_events(
                        task_id, after_id=last_id, block_ms=1000
                    )
                for item in events:
                    event_id = item.pop("id", None)
                    if event_id:
                        last_id = event_id
                    event_name = item.get("event", "progress")
                    yield f"id: {last_id}\nevent: {event_name}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                    if event_name in metadata_sync_log_service.TERMINAL_EVENTS:
                        return
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/datasets/{dataset_id}/inspect/{task_id}/events",
    dependencies=[Depends(require_permission("menu", "menu:metadata"))],
)
async def metadata_inspection_events(dataset_id: int, task_id: str):
    """订阅指定巡检任务的实时流式日志与进度。"""
    if not await metadata_sync_log_service.belongs_to_dataset(task_id, dataset_id):
        raise HTTPException(status_code=404, detail="巡检任务不存在")

    async def event_stream():
        last_id = "0-0"
        try:
            while True:
                events = await metadata_sync_log_service.read_events(task_id, after_id=last_id)
                if not events:
                    events = await metadata_sync_log_service.read_new_events(
                        task_id, after_id=last_id, block_ms=1000
                    )
                for item in events:
                    event_id = item.pop("id", None)
                    if event_id:
                        last_id = event_id
                    event_name = item.get("event", "progress")
                    yield f"id: {last_id}\nevent: {event_name}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                    if event_name in metadata_sync_log_service.TERMINAL_EVENTS:
                        return
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# --- Cron Inspection (Scheduled Metadata Consistency Inspection) ---

@router.get(
    "/cron-inspection",
    response_model=CronInspectionConfigResponse,
    dependencies=[Depends(require_admin)],
)
async def get_cron_inspection_config(conn: AsyncSession = Depends(get_db_session)):
    """获取元数据定时巡检配置与最新运行状态（仅管理员）。"""
    stmt = (
        select(AgentScheduledTask)
        .where(AgentScheduledTask.name == "全量元数据物理结构巡检")
        .limit(1)
    )
    res = await conn.execute(stmt)
    task = res.scalar_one_or_none()

    if not task:
        return CronInspectionConfigResponse(
            enabled=False,
            cron_expr="0 2 * * *",
            task_id=None,
            next_run_at=None,
            last_run_at=None,
            run_count=0,
            health_status="unknown",
            last_status=None,
            last_message=None,
            last_error=None,
        )

    next_run = scheduler_service.get_next_run_time(task.id)
    task_cfg = task.config if isinstance(task.config, dict) else {}
    metrics = task_cfg.get("metrics", {})
    channels = list(task_cfg.get("notification_channels") or ["portal"])
    if "portal" not in channels:
        channels.insert(0, "portal")

    return CronInspectionConfigResponse(
        enabled=(task.status == 1),
        cron_expr=task.cron_expr or "0 2 * * *",
        task_id=task.id,
        next_run_at=next_run or task.next_run_at,
        last_run_at=task.last_run_at,
        run_count=task.run_count or 0,
        health_status=metrics.get("health_status", "unknown"),
        last_status=metrics.get("last_status"),
        last_message=metrics.get("last_message"),
        last_error=metrics.get("last_error"),
        notification_channels=channels,
    )


@router.post(
    "/cron-inspection",
    response_model=CronInspectionConfigResponse,
    dependencies=[Depends(require_admin)],
)
async def update_cron_inspection_config(
    payload: CronInspectionConfigRequest,
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user),
):
    """开启/关闭或更新元数据全量定时巡检配置。"""
    cron_parts = (payload.cron_expr or "").strip().split()
    if len(cron_parts) not in (5, 6):
        raise HTTPException(status_code=400, detail="Cron 表达式格式不正确，需为 5 位或 6 位空格分隔格式")
    try:
        if len(cron_parts) == 6:
            CronTrigger.from_crontab(" ".join(cron_parts[:5]))
        else:
            CronTrigger.from_crontab(payload.cron_expr.strip())
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"非法的 Cron 表达式: {str(e)}")

    user_id = int(user.get("user_id") or 1)

    stmt = (
        select(AgentScheduledTask)
        .where(AgentScheduledTask.name == "全量元数据物理结构巡检")
        .limit(1)
    )
    res = await conn.execute(stmt)
    task = res.scalar_one_or_none()

    # 规范化通知渠道：站内信 portal 强制必选
    channels = list(payload.notification_channels or ["portal"])
    if "portal" not in channels:
        channels.insert(0, "portal")

    task_config = {
        "task_type": "metadata_inspection",
        "is_system": True,
        "notification_channels": channels,
    }

    if task:
        existing_metrics = (task.config or {}).get("metrics", {}) if isinstance(task.config, dict) else {}
        task_config["metrics"] = existing_metrics
        task.cron_expr = payload.cron_expr.strip()
        task.status = 1 if payload.enabled else 0
        task.config = task_config
        task.updated_at = datetime.now()
        await conn.commit()
        await conn.refresh(task)
    else:
        task = AgentScheduledTask(
            name="全量元数据物理结构巡检",
            user_id=user_id,
            agent_id="system_inspection",
            conversation_id="system_metadata_inspection",
            cron_expr=payload.cron_expr.strip(),
            prompt="执行全量元数据物理结构一致性巡检",
            source="system",
            status=1 if payload.enabled else 0,
            config=task_config,
        )
        conn.add(task)
        await conn.commit()
        await conn.refresh(task)

    await scheduler_service.upsert_task(task)

    next_run = scheduler_service.get_next_run_time(task.id)
    metrics = (task.config or {}).get("metrics", {}) if isinstance(task.config, dict) else {}

    return CronInspectionConfigResponse(
        enabled=(task.status == 1),
        cron_expr=task.cron_expr,
        task_id=task.id,
        next_run_at=next_run or task.next_run_at,
        last_run_at=task.last_run_at,
        run_count=task.run_count or 0,
        health_status=metrics.get("health_status", "unknown"),
        last_status=metrics.get("last_status"),
        last_message=metrics.get("last_message"),
        last_error=metrics.get("last_error"),
        notification_channels=channels,
    )


@router.post(
    "/cron-inspection/run",
    dependencies=[Depends(require_admin)],
)
async def trigger_cron_inspection_immediately(
    background_tasks: BackgroundTasks,
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user),
):
    """立即手动触发一次定时巡检任务执行。"""
    stmt = (
        select(AgentScheduledTask)
        .where(AgentScheduledTask.name == "全量元数据物理结构巡检")
        .limit(1)
    )
    res = await conn.execute(stmt)
    task = res.scalar_one_or_none()
    if not task:
        user_id = int(user.get("user_id") or 1)
        task = AgentScheduledTask(
            name="全量元数据物理结构巡检",
            user_id=user_id,
            agent_id="system_inspection",
            conversation_id="system_metadata_inspection",
            cron_expr="0 2 * * *",
            prompt="执行全量元数据物理结构一致性巡检",
            source="system",
            status=0,
            config={
                "task_type": "metadata_inspection",
                "is_system": True,
                "notification_channels": ["portal"],
            },
        )
        conn.add(task)
        await conn.commit()
        await conn.refresh(task)

    task_id = task.id
    background_tasks.add_task(scheduler_service.run_task, task_id, is_manual=True)

    return {
        "code": 200,
        "message": "定时巡检任务已触发立即执行",
        "data": {"task_id": task_id},
    }


# --- Metric APIs ---

@router.post("/datasets/{dataset_id}/metrics", response_model=MetricResponse, dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def create_metric(dataset_id: int, metric: MetricSchema, conn: AsyncSession = Depends(get_db_session), user: dict = Depends(get_current_user)):
    """创建指标"""
    return await MetadataService.create_metric(
        conn, dataset_id, metric.model_dump(),
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="创建指标"
    )

@router.put("/metrics/{metric_id}", response_model=MetricResponse, dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def update_metric(metric_id: int, metric: MetricSchema, conn: AsyncSession = Depends(get_db_session), user: dict = Depends(get_current_user)):
    """更新指标"""
    updated = await MetadataService.update_metric(
        conn, metric_id, metric.model_dump(exclude_unset=True),
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="更新指标"
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Metric not found")
    return updated

@router.delete("/metrics/{metric_id}", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def delete_metric(metric_id: int, conn: AsyncSession = Depends(get_db_session), user: dict = Depends(get_current_user)):
    """删除指标"""
    await MetadataService.delete_metric(
        conn, metric_id,
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="删除指标"
    )
    return {"message": "Metric deleted"}

@router.post("/metrics/batch-delete", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def batch_delete_metrics(
    req: BatchDeleteMetricsRequest,
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user)
):
    """批量删除指标"""
    count = await MetadataService.batch_delete_metrics(
        conn, req.metric_ids,
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="批量删除业务指标"
    )
    return {"message": f"成功删除 {count} 个指标", "deleted_count": count}

@router.get("/datasets/{dataset_id}/metrics", response_model=List[MetricResponse])
async def list_metrics(dataset_id: int, conn: AsyncSession = Depends(get_db_session)):
    """获取数据集下的所有指标"""
    return await MetadataService.get_metrics_by_dataset(conn, dataset_id)

# --- Relationship APIs ---

@router.post("/datasets/{dataset_id}/relationships", response_model=RelationshipResponse, dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def create_relationship(dataset_id: int, rel: RelationshipSchema, conn: AsyncSession = Depends(get_db_session)):
    """创建表关联关系"""
    return await MetadataService.create_relationship(conn, dataset_id, rel.model_dump())

@router.put("/relationships/{rel_id}", response_model=RelationshipResponse, dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def update_relationship(rel_id: int, rel: RelationshipSchema, conn: AsyncSession = Depends(get_db_session), user: dict = Depends(get_current_user)):
    """更新关联关系"""
    updated = await MetadataService.update_relationship(
        conn, rel_id, rel.model_dump(exclude_unset=True),
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="更新关系"
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Relationship not found")
    return updated

@router.delete("/relationships/{rel_id}", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def delete_relationship(rel_id: int, conn: AsyncSession = Depends(get_db_session), user: dict = Depends(get_current_user)):
    """删除关联关系"""
    await MetadataService.delete_relationship(
        conn, rel_id,
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="删除关系"
    )
    return {"message": "Relationship deleted"}

@router.post("/relationships/batch-delete", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def batch_delete_relationships(
    req: BatchDeleteRelationshipsRequest,
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user)
):
    """批量删除实体关系"""
    count = await MetadataService.batch_delete_relationships(
        conn, req.relationship_ids,
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="批量删除实体关系"
    )
    return {"message": f"成功删除 {count} 条关联关系", "deleted_count": count}

@router.get("/datasets/{dataset_id}/relationships", response_model=List[RelationshipResponse])
async def list_relationships(dataset_id: int, conn: AsyncSession = Depends(get_db_session)):
    """获取数据集相关的关系"""
    return await MetadataService.get_relationships_by_dataset(conn, dataset_id)


@router.get("/all-tables")
async def list_all_tables(
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(require_permission("menu", "menu:metadata")),
):
    """获取所有有权限数据集及其表，按数据集分组，用于跨数据集关联关系配置的目标表选择器。

    返回格式：
    [
      {
        "dataset_id": 1,
        "dataset_name": "hr_data",
        "display_name": "HR 人员数据",
        "tables": [
          {"id": 10, "physical_name": "employees", "term": "员工信息表"}
        ]
      }
    ]
    """
    user_id = int(user.get("user_id") or 0) or None
    is_admin = user.get("role") == "admin"
    return await MetadataService.get_all_tables_with_dataset(
        conn,
        user_id=user_id,
        is_admin=is_admin,
    )

# --- Table APIs ---

@router.post("/datasets/{dataset_id}/tables", response_model=TableResponse, dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def save_table(dataset_id: int, table: TableCreate, conn: AsyncSession = Depends(get_db_session), user: dict = Depends(get_current_user)):
    """保存或更新表结构"""
    # Verify dataset exists
    ds = await MetadataService.get_dataset_by_id(conn, dataset_id, is_admin=True)
    if not ds:
         raise HTTPException(status_code=404, detail="数据集不存在")
         
    return await MetadataService.save_table_metadata(
        conn, 
        dataset_id, 
        table.model_dump(),
        user_id=int(user.get('user_id') or 0),
        user_name=user.get('user_name'),
        reason="保存表结构"
    )

@router.delete("/datasets/{dataset_id}/tables/{table_name}", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def delete_table(dataset_id: int, table_name: str, conn: AsyncSession = Depends(get_db_session), user: dict = Depends(get_current_user)):
    """从数据集中删除表结构"""
    await MetadataService.delete_table_metadata(
        conn, dataset_id, table_name,
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason="删除表"
    )
    return {"message": "Table deleted successfully"}

@router.post("/datasets/{dataset_id}/tables/batch-delete", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def batch_delete_tables(
    dataset_id: int,
    req: BatchDeleteTablesRequest,
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user)
):
    """批量删除表结构"""
    count = await MetadataService.batch_delete_table_metadata(
        conn, dataset_id, req.table_names,
        user_id=int(user.get("user_id") or 0),
        user_name=user.get("user_name"),
        reason=f"批量删除表: {', '.join(req.table_names)}"
    )
    return {"message": f"成功删除 {count} 张表", "deleted_count": count}

@router.post("/datasets/{dataset_id}/metrics/recommend", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def recommend_metrics(
    dataset_id: int,
    req: MetricRecommendRequest = Body(default_factory=MetricRecommendRequest),
    conn: AsyncSession = Depends(get_db_session)
):
    """
    智能推荐指标 (返回建议值，不直接入库，支持按表范围筛选与自定义提示词及10分钟去重)
    """
    # 1. Get Schema Context
    from app.services.metadata_service import MetadataService
    from app.services.metadata_generator import MetadataGeneratorService
    
    # Verify dataset exists and get context
    ds = await MetadataService.get_dataset_by_id(conn, dataset_id, is_admin=True)
    if not ds:
        raise HTTPException(status_code=404, detail="数据集不存在")
         
    schema_yaml = await MetadataService.export_dataset_yaml(conn, dataset_id, table_names=req.table_names)
    existing_metrics = await MetadataService.get_metrics_by_dataset(conn, dataset_id)
    
    # 2. Call Generator
    result = await MetadataGeneratorService.recommend_metrics(
        dataset_id=dataset_id,
        schema_context=schema_yaml,
        user_prompt=req.user_prompt,
        existing_metrics=existing_metrics,
        data_source=ds.data_source,
    )
    
    return {
        "code": 200,
        "message": "success",
        "data": result
    }


@router.post("/datasets/{dataset_id}/metrics/recommend/stream", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def recommend_metrics_stream(
    dataset_id: int,
    request: Request,
    req: MetricRecommendRequest = Body(default_factory=MetricRecommendRequest),
    conn: AsyncSession = Depends(get_db_session),
):
    """通过 SSE 推送业务指标 AI 生成的真实阶段进度与最终结果。"""
    from app.services.metadata_service import MetadataService
    from app.services.metadata_generator import MetadataGeneratorService

    ds = await MetadataService.get_dataset_by_id(conn, dataset_id, is_admin=True)
    if not ds:
        raise HTTPException(status_code=404, detail="数据集不存在")

    schema_yaml = await MetadataService.export_dataset_yaml(
        conn,
        dataset_id,
        table_names=req.table_names,
    )
    existing_metrics = await MetadataService.get_metrics_by_dataset(conn, dataset_id)
    logger.warning(
        "指标推荐 SSE 请求入口: dataset_id=%s, requested_table_count=%s, "
        "schema_len=%s, existing_metric_count=%s, user_prompt=%s",
        dataset_id,
        len(req.table_names or []),
        len(schema_yaml),
        len(existing_metrics),
        bool(req.user_prompt),
    )

    async def worker(progress_callback):
        return await MetadataGeneratorService.recommend_metrics(
            dataset_id=dataset_id,
            schema_context=schema_yaml,
            user_prompt=req.user_prompt,
            existing_metrics=existing_metrics,
            data_source=ds.data_source,
            progress_callback=progress_callback,
        )

    return await _stream_metadata_recommendation(
        request,
        dataset_id,
        "metrics",
        worker,
    )

@router.post("/datasets/{dataset_id}/relationships/recommend", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def recommend_relationships(
    dataset_id: int,
    request: Request,
    req: RelationshipRecommendRequest = Body(default_factory=RelationshipRecommendRequest),
    conn: AsyncSession = Depends(get_db_session)
):
    """
    智能推荐实体（表）之间的关联关系 (返回建议值 + 置信度，不直接入库)
    支持指定表名范围 (table_names) 和自定义偏好提示词 (user_prompt)
    """
    from app.services.metadata_service import MetadataService
    from app.services.metadata_generator import MetadataGeneratorService

    # Verify dataset exists and get context
    ds = await MetadataService.get_dataset_by_id(conn, dataset_id, is_admin=True)
    if not ds:
        raise HTTPException(status_code=404, detail="数据集不存在")

    table_names = req.table_names if req and req.table_names else None
    user_prompt = req.user_prompt if req and req.user_prompt else None
    strategy = req.strategy if req else "strict"

    # 获取当前已存在的关系列表
    existing_rels = await MetadataService.get_relationships_by_dataset(conn, dataset_id)
    existing_rel_strs = []
    for r in existing_rels:
        src = getattr(r.source_table, 'physical_name', '') if r.source_table else ''
        tgt = getattr(r.target_table, 'physical_name', '') if r.target_table else ''
        cond = getattr(r, 'join_condition', '')
        if src and tgt:
            existing_rel_strs.append(f"{src} <-> {tgt} ({cond})")

    schema_yaml = await MetadataService.export_dataset_yaml(conn, dataset_id, table_names=table_names)
    schema_table_names = MetadataGeneratorService._extract_schema_table_names(schema_yaml)
    logger.warning(
        "关系推荐请求入口: dataset_id=%s, requested_table_count=%s, schema_table_count=%s, "
        "schema_len=%s, user_prompt=%s",
        dataset_id,
        len(table_names or []),
        len(schema_table_names),
        len(schema_yaml),
        bool(user_prompt),
    )

    # 独立运行生成任务并轮询客户端连接，用户取消或页面关闭后及时停止后续模型调用。
    recommendation_task = asyncio.create_task(
        MetadataGeneratorService.recommend_relationships(
            dataset_id,
            schema_yaml,
            user_prompt=user_prompt,
            existing_relationships=existing_rel_strs,
            data_source=ds.data_source,
            strategy=strategy,
        ),
        name=f"metadata-relationship-recommend-{dataset_id}",
    )
    try:
        while not recommendation_task.done():
            done, _ = await asyncio.wait({recommendation_task}, timeout=0.5)
            if recommendation_task in done:
                break
            if await request.is_disconnected():
                logger.warning(
                    "关系推荐客户端已断开，取消后端生成任务: dataset_id=%s, "
                    "requested_table_count=%s",
                    dataset_id,
                    len(table_names or []),
                )
                recommendation_task.cancel()
                try:
                    await recommendation_task
                except asyncio.CancelledError:
                    pass
                raise HTTPException(status_code=499, detail="客户端已断开，关系推荐任务已取消")

        result = await recommendation_task
    except asyncio.CancelledError:
        # 服务端请求协程被取消时同步清理模型任务，防止任务脱离请求生命周期继续运行。
        if not recommendation_task.done():
            recommendation_task.cancel()
        logger.warning("关系推荐请求协程已取消: dataset_id=%s", dataset_id)
        raise

    # 输出接口层汇总，便于区分“生成仍在运行”和“结果已成功写回 HTTP 响应”。
    logger.warning(
        "关系推荐响应完成: dataset_id=%s, trace_id=%s, relationship_count=%s, "
        "batch_count=%s, stop_reason=%s",
        dataset_id,
        result.get("_trace_id") if isinstance(result, dict) else None,
        len(result.get("relationships", [])) if isinstance(result, dict) else 0,
        result.get("_batch_count") if isinstance(result, dict) else None,
        result.get("_stop_reason") if isinstance(result, dict) else None,
    )

    return {
        "code": 200,
        "message": "success",
        "data": result
    }


@router.post("/datasets/{dataset_id}/relationships/recommend/stream", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def recommend_relationships_stream(
    dataset_id: int,
    request: Request,
    req: RelationshipRecommendRequest = Body(default_factory=RelationshipRecommendRequest),
    conn: AsyncSession = Depends(get_db_session),
):
    """通过 SSE 推送实体关系逐表扫描进度、剩余表数、中断状态与最终结果。"""
    from app.services.metadata_service import MetadataService
    from app.services.metadata_generator import MetadataGeneratorService

    ds = await MetadataService.get_dataset_by_id(conn, dataset_id, is_admin=True)
    if not ds:
        raise HTTPException(status_code=404, detail="数据集不存在")

    table_names = req.table_names if req and req.table_names else None
    user_prompt = req.user_prompt if req and req.user_prompt else None
    strategy = req.strategy if req else "strict"
    existing_rels = await MetadataService.get_relationships_by_dataset(conn, dataset_id)
    existing_rel_strs = []
    for relationship in existing_rels:
        source_name = (
            getattr(relationship.source_table, "physical_name", "")
            if relationship.source_table
            else ""
        )
        target_name = (
            getattr(relationship.target_table, "physical_name", "")
            if relationship.target_table
            else ""
        )
        condition = getattr(relationship, "join_condition", "")
        if source_name and target_name:
            existing_rel_strs.append(
                f"{source_name} <-> {target_name} ({condition})"
            )

    schema_yaml = await MetadataService.export_dataset_yaml(
        conn,
        dataset_id,
        table_names=table_names,
    )
    schema_table_names = MetadataGeneratorService._extract_schema_table_names(schema_yaml)
    logger.warning(
        "关系推荐 SSE 请求入口: dataset_id=%s, requested_table_count=%s, "
        "schema_table_count=%s, schema_len=%s, user_prompt=%s",
        dataset_id,
        len(table_names or []),
        len(schema_table_names),
        len(schema_yaml),
        bool(user_prompt),
    )

    async def worker(progress_callback):
        return await MetadataGeneratorService.recommend_relationships(
            dataset_id=dataset_id,
            schema_context=schema_yaml,
            user_prompt=user_prompt,
            existing_relationships=existing_rel_strs,
            data_source=ds.data_source,
            strategy=strategy,
            progress_callback=progress_callback,
        )

    return await _stream_metadata_recommendation(
        request,
        dataset_id,
        "relationships",
        worker,
    )

@router.post("/datasets/{dataset_id}/enhance-metadata", dependencies=[Depends(require_permission("element", "element:metadata:edit"))])
async def enhance_dataset_metadata(dataset_id: int, conn: AsyncSession = Depends(get_db_session)):
    """
    AI 辅助生成元数据: 根据数据集下的表信息自动生成描述和标签
    """
    from app.services.metadata_service import MetadataService
    from app.services.metadata_generator import MetadataGeneratorService
    
    # 1. 获取数据集详情（包含表列表）
    ds = await MetadataService.get_dataset_by_id(conn, dataset_id, is_admin=True)
    if not ds:
        raise HTTPException(status_code=404, detail="数据集不存在")
    
    # 2. 构建表摘要信息供 AI 分析
    table_list = []
    if hasattr(ds, "tables") and ds.tables:
        for tbl in ds.tables:
            table_list.append(f"- 物理名: {tbl.physical_name}, 业务术语: {tbl.term}")
    
    if not table_list:
        raise HTTPException(status_code=400, detail="该数据集尚未添加任何表，AI 无法分析")
        
    tables_summary = "\n".join(table_list)
    
    # 3. 调用 AI 生成器
    result = await MetadataGeneratorService.enhance_dataset_metadata(dataset_id, tables_summary)
    
    return {
        "code": 200,
        "message": "success",
        "data": result
    }

from app.services.metadata_generator import MetadataGeneratorService

@router.post("/tables/import", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def import_ddl(ddl: dict): # Expects {"ddl": "..."}
    """
    智能辅助: 解析 DDL 并返回预览用的 Metadata 结构 (管理员或具有智能导入权限的用户)
    注意：此接口不保存数据，只返回 AI 生成的建议值供前端填充表单
    """
    content = ddl.get("ddl", "")
    if not content:
        raise HTTPException(status_code=400, detail="DDL content cannot be empty")
        
    logger.info(f"🚀 [元数据智能导入] 收到 DDL 分析请求 | 长度: {len(content)} 字符 | 数据源: {ddl.get('data_source') or 'default'}")
    try:
        # 将导入向导选定的数据源传给生成器，确保指标 SQL 使用目标数据库方言。
        result = await MetadataGeneratorService.generate_from_ddl(
            content,
            data_source=ddl.get("data_source"),
        )
        logger.info("✅ [元数据智能导入] DDL 智能分析推导完成")
        return {
            "code": 200,
            "message": "success",
            "data": result
        }
    except asyncio.CancelledError:
        logger.warning("⏹️ [元数据智能导入] 客户端主动断开连接 / 用户取消了智能导入识别")
        raise
    except Exception as e:
        logger.error(f"❌ [元数据智能导入] 分析失败: {e}", exc_info=True)
        raise

from app.services.db_import_service import DBImportService
from app.schemas.metadata import DBConnectionConfig, DDLRequest

@router.post("/db/test-connection", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def test_db_connection(config: DBConnectionConfig):
    """测试外部数据库连接"""
    try:
        if config.type == "mysql":
            await DBImportService.test_mysql_connection(config.model_dump())
        elif config.type == "clickhouse":
            await DBImportService.test_clickhouse_connection(config.model_dump())
        elif config.type == "oracle":
            await DBImportService.test_oracle_connection(config.model_dump())
        elif config.type in DBImportService._sqlserver_type_aliases():
            await DBImportService.test_sqlserver_connection(config.model_dump())
        elif config.type in DBImportService._postgresql_type_aliases():
            await DBImportService.test_postgresql_connection(config.model_dump())
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported DB type: {config.type}")
        return {"code": 200, "message": "Connection successful"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/db/tables", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def list_db_tables(config: DBConnectionConfig):
    """获取外部数据库表列表"""
    try:
        if config.type == "mysql":
            tables = await DBImportService.get_mysql_tables(config.model_dump())
        elif config.type == "clickhouse":
            tables = await DBImportService.get_clickhouse_tables(config.model_dump())
        elif config.type == "oracle":
            tables = await DBImportService.get_oracle_tables(config.model_dump())
        elif config.type in DBImportService._sqlserver_type_aliases():
            tables = await DBImportService.get_sqlserver_tables(config.model_dump())
        elif config.type in DBImportService._postgresql_type_aliases():
            tables = await DBImportService.get_postgresql_tables(config.model_dump())
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported DB type: {config.type}")
        return {"code": 200, "data": tables}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/db/ddl", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def get_db_ddl(request: DDLRequest):
    """获取指定表的 DDL"""
    try:
        config = request.config
        if config.type == "mysql":
            ddl = await DBImportService.get_mysql_ddl(config.model_dump(), request.tables)
        elif config.type == "clickhouse":
            ddl = await DBImportService.get_clickhouse_ddl(config.model_dump(), request.tables)
        elif config.type == "oracle":
            ddl = await DBImportService.get_oracle_ddl(config.model_dump(), request.tables)
        elif config.type in DBImportService._sqlserver_type_aliases():
            ddl = await DBImportService.get_sqlserver_ddl(config.model_dump(), request.tables)
        elif config.type in DBImportService._postgresql_type_aliases():
            ddl = await DBImportService.get_postgresql_ddl(config.model_dump(), request.tables)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported DB type: {config.type}")
        return {"code": 200, "data": ddl}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- DB Connection Config APIs ---

from app.schemas.db_connection import (
    DbConnectionConfigCreate,
    DbConnectionConfigSafeResponse,
    DbProfileTaskResponse,
    DbTableProfileResponse,
    DbTableProfileStatsResponse,
    DbTableProfilePageResponse,
    ProfileImportPreviewRequest,
)
from app.services.db_connection_service import DbConnectionService

@router.get("/db/connection-configs", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def list_db_connection_configs(
    conn: AsyncSession = Depends(get_db_session)
):
    """获取所有已保存的数据库连接配置"""
    configs = await DbConnectionService.list_configs(conn)
    result = [DbConnectionConfigSafeResponse.model_validate(c) for c in configs]
    return {"code": 200, "data": [r.model_dump() for r in result]}


@router.post("/db/connection-configs", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def create_db_connection_config(
    payload: DbConnectionConfigCreate,
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user)
):
    """保存一条数据库连接配置（连接测试通过后调用）"""
    try:
        config = await DbConnectionService.create_config(
            conn,
            payload.model_dump(),
            user_id=int(user.get("user_id") or 0)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"code": 200, "data": DbConnectionConfigSafeResponse.model_validate(config).model_dump()}


@router.put("/db/connection-configs/{config_id}", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def update_db_connection_config(
    config_id: int,
    payload: DbConnectionConfigCreate,
    conn: AsyncSession = Depends(get_db_session)
):
    """更新指定数据库连接配置"""
    try:
        config = await DbConnectionService.update_config(conn, config_id, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not config:
        raise HTTPException(status_code=404, detail="连接配置不存在")
    from app.services.pool_manager import DataSourcePoolManager
    await DataSourcePoolManager.invalidate_pool(config_id)
    return {"code": 200, "data": DbConnectionConfigSafeResponse.model_validate(config).model_dump()}


@router.delete("/db/connection-configs/{config_id}", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def delete_db_connection_config(
    config_id: int,
    conn: AsyncSession = Depends(get_db_session)
):
    """删除指定连接配置"""
    deleted = await DbConnectionService.delete_config(conn, config_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="连接配置不存在")
    from app.services.pool_manager import DataSourcePoolManager
    await DataSourcePoolManager.invalidate_pool(config_id)
    return {"code": 200, "message": "已删除"}


class DebugSqlRequest(BaseModel):
    sql: str
    limit: int = 100
    include_total: bool = False


@router.post("/db/connection-configs/{config_id}/preview", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def preview_db_connection_sql(
    config_id: int,
    payload: DebugSqlRequest,
    conn: AsyncSession = Depends(get_db_session)
):
    """
    数据源直连在线调试 SQL 执行接口 (不经过外部 API HTTP 转发，直接通过本地自研 Adapter)
    """
    config = await DbConnectionService.get_config(conn, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="数据库配置不存在")

    try:
        from app.services.data_adapter.factory import get_adapter
        adapter = await get_adapter(config.name)
    except Exception as e:
        logger.exception("Failed to initialize database adapter for debug")
        raise HTTPException(status_code=400, detail=f"初始化数据库适配器失败: {str(e)}")

    try:
        # 执行带有 SELECT 强安全性拦截与行数截断保护的 preview 方法
        preview_kwargs: Dict[str, Any] = {
            "limit": min(max(int(payload.limit or 100), 1), 1000),
            "include_total": bool(payload.include_total),
        }
        res = await adapter.preview(payload.sql, **preview_kwargs)
        return {"code": 200, "data": res}
    except Exception as e:
        logger.exception("Failed to execute debug SQL query")
        raise HTTPException(status_code=400, detail=f"执行 SQL 失败: {str(e)}")


from fastapi import BackgroundTasks
from app.services.db_profile_service import DbProfileService

@router.post("/db/connection-configs/{config_id}/profile", response_model=DbProfileTaskResponse, dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def trigger_db_table_profiling(
    config_id: int,
    background_tasks: BackgroundTasks,
    full: bool = False,
    conn: AsyncSession = Depends(get_db_session)
):
    """触发外部数据库的智能摸排分析后台任务。full=true 时全量重跑，否则断点续跑未完成表。"""
    try:
        task = await DbProfileService.trigger_profiling_task(
            conn, config_id, background_tasks, full_reset=full
        )
        return task
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as e:
        logger.exception("Failed to trigger db table profiling task")
        raise HTTPException(status_code=500, detail=f"触发任务失败: {str(e)}")


@router.post("/db/connection-configs/{config_id}/profile/cancel", response_model=DbProfileTaskResponse, dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def cancel_db_table_profiling(
    config_id: int,
    conn: AsyncSession = Depends(get_db_session)
):
    """中断进行中的摸排任务，已完成的表画像保留。"""
    try:
        task = await DbProfileService.cancel_profiling_task(conn, config_id)
        return task
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as e:
        logger.exception("Failed to cancel db table profiling task")
        raise HTTPException(status_code=500, detail=f"中断任务失败: {str(e)}")


@router.get("/db/connection-configs/{config_id}/profile-task", response_model=Optional[DbProfileTaskResponse], dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def get_db_table_profiling_task(
    config_id: int,
    conn: AsyncSession = Depends(get_db_session)
):
    """获取外部数据库智能摸排主任务的进度与状态（读取时自动校正漏标完成）"""
    task = await DbProfileService.get_task_status_display(conn, config_id)
    return task


@router.get("/db/connection-configs/{config_id}/table-profiles/stats", response_model=DbTableProfileStatsResponse, dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def get_db_table_profile_stats(
    config_id: int,
    conn: AsyncSession = Depends(get_db_session)
):
    """获取数据源表画像聚合统计与标签分布"""
    return await DbProfileService.get_table_profile_stats(conn, config_id)


@router.get("/db/connection-configs/{config_id}/table-profiles", response_model=DbTableProfilePageResponse, dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def list_db_table_profiles(
    config_id: int,
    page: int = 1,
    page_size: int = 200,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    is_ignored: Optional[int] = None,
    status: Optional[int] = None,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = None,
    conn: AsyncSession = Depends(get_db_session)
):
    """分页获取表画像摘要列表（不含 ddl / sample_data）"""
    return await DbProfileService.list_table_profiles_page(
        conn,
        config_id,
        page=page,
        page_size=page_size,
        q=q,
        tag=tag,
        is_ignored=is_ignored,
        status=status,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get("/db/connection-configs/{config_id}/table-profiles/related", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def get_db_table_profile_related(
    config_id: int,
    table: str,
    limit: int = 15,
    conn: AsyncSession = Depends(get_db_session),
):
    """基于摸排画像推断可能关联的表（不依赖 meta_relationships）"""
    return await DbProfileService.get_related_tables(conn, config_id, table, limit)


@router.get("/db/connection-configs/{config_id}/table-profiles/{table_name}", response_model=DbTableProfileResponse, dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def get_db_table_profile_detail(
    config_id: int,
    table_name: str,
    conn: AsyncSession = Depends(get_db_session)
):
    """获取单表完整画像详情（含 ddl、样例、字段画像）"""
    profile = await DbProfileService.get_table_profile_detail(conn, config_id, table_name)
    if not profile:
        raise HTTPException(status_code=404, detail="表画像不存在")
    return profile


@router.post("/db/connection-configs/{config_id}/import-preview-from-profiles", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def import_preview_from_profiles(
    config_id: int,
    payload: ProfileImportPreviewRequest,
    conn: AsyncSession = Depends(get_db_session),
):
    """基于已摸排画像生成元数据导入预览，跳过重复 LLM 分析。"""
    try:
        result = await DbProfileService.build_import_preview_from_profiles(
            conn, config_id, payload.table_names
        )
        return {"code": 200, "message": "success", "data": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Failed to build import preview from profiles")
        raise HTTPException(status_code=500, detail=f"生成导入预览失败: {str(e)}")


class ToggleIgnoreRequest(BaseModel):
    table_name: str
    is_ignored: int


@router.put("/db/connection-configs/{config_id}/table-profiles/ignore", dependencies=[Depends(require_permission("element", "element:metadata:import"))])
async def toggle_table_profile_ignore(
    config_id: int,
    payload: ToggleIgnoreRequest,
    conn: AsyncSession = Depends(get_db_session)
):
    """手动开启/关闭数据源画像中表的忽略状态"""
    profile = await DbProfileService.toggle_ignore(conn, config_id, payload.table_name, payload.is_ignored)
    if not profile:
        raise HTTPException(status_code=404, detail="表画像不存在")
    return {"code": 200, "message": "修改成功", "data": {"is_ignored": profile.is_ignored}}
