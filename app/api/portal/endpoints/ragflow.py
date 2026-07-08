import json
import time
import uuid
import httpx
from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.orm import get_db_session
from app.services.config_service import ConfigService
from app.core.dependencies import require_admin, get_current_user
from app.services.permission_service import PermissionService
from app.services.ai.ragflow_client import RagFlowClient
from app.services.knowledge_base_service import KnowledgeBaseMetadataService
from app.services.audit_service import AuditService
from app.models.permission import ResourcePermission

router = APIRouter()


class CreateDatasetRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    chunk_method: str = "naive"
    owner: Optional[str] = None
    visibility: str = "private"
    tags: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    extra_config: Dict[str, Any] = Field(default_factory=dict)


class UpdateDatasetMetadataRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    owner: Optional[str] = None
    visibility: Optional[str] = None
    tags: Optional[List[str]] = None
    notes: Optional[str] = None
    extra_config: Optional[Dict[str, Any]] = None


class DeleteDatasetsRequest(BaseModel):
    ids: List[str] = Field(default_factory=list)


class DeleteDocumentsRequest(BaseModel):
    ids: List[str] = Field(default_factory=list)


class ParseDocumentsRequest(BaseModel):
    ids: List[str] = Field(default_factory=list)


class RetrievalTestRequest(BaseModel):
    query: str = Field(..., min_length=1)
    dataset_ids: List[str] = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    similarity_threshold: float = Field(default=0.2, ge=0, le=1)
    vector_similarity_weight: float = Field(default=0.3, ge=0, le=1)

async def get_ragflow_client():
    await require_knowledge_base_enabled()
    base_url = await ConfigService.get("knowledge_ragflow_api_url")
    api_key = await ConfigService.get("knowledge_ragflow_api_key")
    
    if not base_url or not api_key:
        raise HTTPException(status_code=500, detail="RAGFlow configuration missing in system settings")
        
    return base_url.rstrip("/"), api_key


async def require_knowledge_base_enabled():
    from app.services.ai.knowledge_utils import is_knowledge_base_enabled

    if not await is_knowledge_base_enabled():
        raise HTTPException(status_code=403, detail="知识库功能未开启，请在系统配置 → 知识库设置中启用")


async def require_element_permission(user: dict, db: AsyncSession, permission_id: str):
    if user.get("role") == "admin":
        return
    service = PermissionService(db)
    allowed = await service.check_permission(int(user["user_id"]), "element", permission_id)
    if not allowed:
        raise HTTPException(status_code=403, detail=f"Permission required: {permission_id}")


async def require_dataset_access(user: dict, db: AsyncSession, dataset_ids: List[str]):
    """可读：管理员 / 权限分配 / 创建人"""
    service = PermissionService(db)
    access = await service.get_knowledge_base_access(
        int(user["user_id"]),
        user.get("user_name"),
    )
    if access["is_admin"]:
        return
    allowed = access["accessible_ids"] or set()
    denied = [dataset_id for dataset_id in dataset_ids if dataset_id not in allowed]
    if denied:
        raise HTTPException(status_code=403, detail=f"No access to dataset: {', '.join(denied)}")


async def require_dataset_write_access(user: dict, db: AsyncSession, dataset_ids: List[str]):
    """可写：管理员 / 创建人（仅被分配的非创建人只读）"""
    service = PermissionService(db)
    access = await service.get_knowledge_base_access(
        int(user["user_id"]),
        user.get("user_name"),
    )
    if access["is_admin"]:
        return
    writable = access["writable_ids"] or set()
    denied = [dataset_id for dataset_id in dataset_ids if dataset_id not in writable]
    if denied:
        raise HTTPException(
            status_code=403,
            detail=f"Read-only or no permission for dataset: {', '.join(denied)}",
        )


def _apply_knowledge_permission_flags(
    items: List[Dict[str, Any]],
    access: Dict[str, Any],
) -> None:
    for item in items:
        ds_id = str(item.get("ragflow_dataset_id") or item.get("id") or "")
        if access.get("is_admin"):
            item["can_write"] = True
            item["can_view_chunks"] = True
            item["is_read_only"] = False
            continue
        writable = access.get("writable_ids") or set()
        accessible = access.get("accessible_ids") or set()
        item["can_write"] = ds_id in writable
        item["can_view_chunks"] = bool(item["can_write"])
        item["is_read_only"] = ds_id in accessible and not item["can_write"]


async def record_knowledge_audit(
    request: Request,
    user: dict,
    action: str,
    status_code: int,
    payload: Dict[str, Any],
    error_message: Optional[str] = None,
    started_at: Optional[float] = None,
):
    safe_payload = dict(payload)
    safe_payload.pop("api_key", None)
    process_time_ms = ((time.time() - started_at) * 1000) if started_at else 0
    await AuditService.log_request_data(
        trace_id=getattr(request.state, "trace_id", str(uuid.uuid4())),
        user_name=user.get("user_name"),
        endpoint=f"/api/portal/ragflow/{action}",
        method=request.method,
        status_code=status_code,
        process_time_ms=process_time_ms,
        client_ip=request.client.host if request.client else None,
        request_params=json.dumps(safe_payload, ensure_ascii=False, default=str),
        response_body=json.dumps({"status": "success" if status_code < 400 else "error"}, ensure_ascii=False),
        error_message=error_message,
    )


def _normalize_ragflow_items(data: Any) -> List[Dict[str, Any]]:
    items = data.get("data") if isinstance(data, dict) else data
    if isinstance(items, dict):
        items = items.get("datasets") or items.get("items") or []
    return items if isinstance(items, list) else []


async def _grant_dataset_to_creator(db: AsyncSession, user: dict, dataset_id: str):
    if user.get("role") == "admin":
        return
    perm = ResourcePermission(
        user_id=int(user["user_id"]),
        resource_type="dataset",
        resource_id=dataset_id,
        enabled=True,
    )
    db.add(perm)
    service = PermissionService(db)
    await service._invalidate_user_cache(int(user["user_id"]))


async def _collect_affected_dataset_permission_user_ids(
    db: AsyncSession,
    dataset_ids: List[str],
) -> set[int]:
    from app.models.permission import ResourcePermission, UserRoleRelation
    from sqlalchemy import select

    if not dataset_ids:
        return set()

    stmt = select(ResourcePermission).where(
        ResourcePermission.resource_type == "dataset",
        ResourcePermission.resource_id.in_(dataset_ids),
        ResourcePermission.enabled == True,
    )
    result = await db.execute(stmt)
    perms = result.scalars().all()

    affected_user_ids = {int(p.user_id) for p in perms if p.user_id is not None}
    role_ids = [int(p.role_id) for p in perms if p.role_id is not None]
    if role_ids:
        role_users_stmt = select(UserRoleRelation.user_id).where(UserRoleRelation.role_id.in_(role_ids))
        role_users_res = await db.execute(role_users_stmt)
        affected_user_ids.update(int(uid) for uid in role_users_res.scalars().all())

    return affected_user_ids


@router.get("/config")
async def get_ragflow_config_summary():
    from app.services.ai.knowledge_utils import is_knowledge_base_enabled

    base_url = await ConfigService.get("knowledge_ragflow_api_url")
    api_key = await ConfigService.get("knowledge_ragflow_api_key")
    metadata_provider = await ConfigService.get("metadata_provider", default="ragflow")
    knowledge_enabled = await is_knowledge_base_enabled()
    api_url = base_url.rstrip("/") + "/" if base_url else ""
    return {
        "code": 0,
        "data": {
            "api_url": api_url,
            "api_key_configured": bool(api_key),
            "configured": bool(api_url and api_key),
            "metadata_provider": metadata_provider,
            "knowledge_base_enabled": knowledge_enabled,
        },
    }


@router.get("/agents", dependencies=[Depends(require_admin)])
async def list_ragflow_agents(
    page: int = 1, 
    page_size: int = 100,
    override_url: Optional[str] = None,
    override_key: Optional[str] = None
):
    """
    Proxy to list RAGFlow agents (assistants).
    """
    # 过滤打码的 key 或是空值
    real_url = override_url if override_url else None
    real_key = override_key if override_key and "****" not in override_key else None
    
    if real_url and real_key:
        base_url = real_url
        api_key = real_key
    else:
        db_url, db_key = await get_ragflow_client()
        base_url = real_url or db_url
        api_key = real_key or db_key
    
    base_url = base_url.rstrip("/")
    url = f"{base_url}/api/v1/agents"
    
    headers = {"Authorization": f"Bearer {api_key}"}
    params = {"page": page, "page_size": page_size}
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 200:
                return resp.json()
            else:
                raise HTTPException(status_code=resp.status_code, detail=f"RAGFlow Error: {resp.text}")
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to RAGFlow: {str(e)}")

@router.get("/datasets")
async def list_ragflow_datasets(
    page: int = 1, 
    page_size: int = 100,
    override_url: Optional[str] = None,
    override_key: Optional[str] = None,
    include_missing: bool = True,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Proxy to list RAGFlow datasets (knowledge bases) with permission filtering.
    """
    service = PermissionService(db)
    access = await service.get_knowledge_base_access(
        int(user["user_id"]),
        user.get("user_name"),
    )
    is_admin = access["is_admin"]
    if (override_url or override_key) and not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required for temporary RAGFlow overrides")

    real_url = override_url if override_url else None
    real_key = override_key if override_key and "****" not in override_key else None

    client = RagFlowClient(
        config_prefix="knowledge_ragflow",
        override_url=real_url,
        override_key=real_key
    )
    metadata_service = KnowledgeBaseMetadataService(db)

    try:
        items = await client.list_datasets(page=page, page_size=page_size)
        items = [
            item for item in items 
            if not item.get("name", "").startswith("meta-")
            and "chatbi-example" not in item.get("name", "").lower()
        ]

        if not is_admin:
            allowed = access["accessible_ids"] or set()
            items = [item for item in items if item.get("id") in allowed]

        # 结合参数决定是否包含失联知识库（如果临时覆写了 RAGFlow 地址则不包含）
        real_include_missing = include_missing and is_admin
        if override_url:
            real_include_missing = False

        merged = await metadata_service.merge_with_ragflow(items, include_missing=real_include_missing)
        _apply_knowledge_permission_flags(merged, access)
        return {"code": 0, "data": merged, "total": len(merged)}
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to connect to RAGFlow: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/datasets/sync")
async def sync_ragflow_datasets(
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    started = time.time()
    await require_element_permission(user, db, "element:knowledge:edit")
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    metadata_service = KnowledgeBaseMetadataService(db)
    try:
        datasets = await client.list_datasets(page=1, page_size=500)
        result = await metadata_service.sync_from_ragflow_datasets(
            datasets,
            user_name=user.get("user_name"),
        )
        await record_knowledge_audit(
            request=request,
            user=user,
            action="datasets:sync",
            status_code=200,
            payload={
                "action": "sync_datasets_from_ragflow",
                "sync_result": result,
            },
            started_at=started,
        )
        return {"code": 0, "data": result, "message": "同步完成"}
    except Exception as e:
        await record_knowledge_audit(
            request=request,
            user=user,
            action="datasets:sync",
            status_code=500,
            payload={"action": "sync_datasets_from_ragflow"},
            error_message=str(e),
            started_at=started,
        )
        raise


@router.post("/datasets")
async def create_ragflow_dataset(
    payload: CreateDatasetRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    started = time.time()
    await require_element_permission(user, db, "element:knowledge:create")
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    metadata_service = KnowledgeBaseMetadataService(db)
    try:
        created = await client.create_dataset(
            name=payload.name,
            description=payload.description,
            chunk_method=payload.chunk_method,
        )
        dataset_id = created.get("id") or created.get("dataset_id")
        if not dataset_id:
            matched = await client.list_datasets(name=payload.name)
            dataset_id = matched[0].get("id") if matched else None
        if not dataset_id:
            raise HTTPException(status_code=502, detail="RAGFlow 创建成功但未返回 Dataset ID")

        record = await metadata_service.upsert_metadata(
            ragflow_dataset_id=dataset_id,
            name=payload.name,
            description=payload.description,
            owner=payload.owner,
            visibility=payload.visibility,
            tags=payload.tags,
            notes=payload.notes,
            extra_config=payload.extra_config,
            user_name=user.get("user_name"),
        )
        await _grant_dataset_to_creator(db, user, dataset_id)
        await record_knowledge_audit(request, user, "datasets:create", 200, {
            "action": "create_dataset",
            "dataset_id": dataset_id,
            "name": payload.name,
        }, started_at=started)
        return {"code": 0, "data": {"ragflow": created, "metadata_id": record.id, "id": dataset_id}}
    except Exception as e:
        await record_knowledge_audit(request, user, "datasets:create", 500, {
            "action": "create_dataset",
            "name": payload.name,
        }, error_message=str(e), started_at=started)
        raise


@router.put("/datasets/{dataset_id}/metadata")
async def update_dataset_metadata(
    dataset_id: str,
    payload: UpdateDatasetMetadataRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    await require_element_permission(user, db, "element:knowledge:edit")
    await require_dataset_write_access(user, db, [dataset_id])
    metadata_service = KnowledgeBaseMetadataService(db)
    record = await metadata_service.update_metadata(
        dataset_id,
        name=payload.name,
        description=payload.description,
        owner=payload.owner,
        visibility=payload.visibility,
        tags=payload.tags,
        notes=payload.notes,
        extra_config=payload.extra_config,
        user_name=user.get("user_name"),
    )
    if not record:
        raise HTTPException(status_code=404, detail="Knowledge base metadata not found")
    return {"code": 0, "data": metadata_service._serialize_metadata(record)}


@router.delete("/datasets")
async def delete_ragflow_datasets(
    payload: DeleteDatasetsRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    started = time.time()
    await require_element_permission(user, db, "element:knowledge:delete")
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No dataset ids provided")
    await require_dataset_write_access(user, db, payload.ids)
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    metadata_service = KnowledgeBaseMetadataService(db)
    try:
        affected_user_ids = await _collect_affected_dataset_permission_user_ids(db, payload.ids)

        # 获取当前 RAGFlow 侧的所有有效数据集，以防删除已不存在的知识库时报错
        try:
            ragflow_datasets = await client.list_datasets(page=1, page_size=500)
            existing_ids = {str(ds.get("id")) for ds in ragflow_datasets if ds.get("id")}
        except Exception:
            # 列表获取失败时的安全降级兜底：假设所有传入 ID 都在 RAGFlow 侧存在
            existing_ids = set(payload.ids)

        to_delete_in_ragflow = [kb_id for kb_id in payload.ids if kb_id in existing_ids]

        if to_delete_in_ragflow:
            await client.delete_datasets(to_delete_in_ragflow)

        # 同步删除相关用户和角色的 ResourcePermission 授权记录
        from app.models.permission import ResourcePermission
        from sqlalchemy import delete
        await db.execute(
            delete(ResourcePermission).where(
                ResourcePermission.resource_type == "dataset",
                ResourcePermission.resource_id.in_(payload.ids)
            )
        )
        if affected_user_ids:
            await PermissionService(db).invalidate_cached_permissions_for_users(affected_user_ids)

        await metadata_service.mark_deleted(payload.ids, user_name=user.get("user_name"))
        await record_knowledge_audit(request, user, "datasets:delete", 200, {
            "action": "delete_dataset",
            "dataset_ids": payload.ids,
        }, started_at=started)
        return {"code": 0, "message": "删除成功"}
    except Exception as e:
        await record_knowledge_audit(request, user, "datasets:delete", 500, {
            "action": "delete_dataset",
            "dataset_ids": payload.ids,
        }, error_message=str(e), started_at=started)
        raise


@router.get("/datasets/{dataset_id}/documents")
async def list_dataset_documents(
    dataset_id: str,
    page: int = 1,
    page_size: int = 100,
    name: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await require_dataset_access(user, db, [dataset_id])
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    try:
        docs = await client.list_documents(dataset_id, name=name, page=page, page_size=page_size)
        return {"code": 0, "data": docs, "total": len(docs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/datasets/{dataset_id}/documents/{document_id}/chunks")
async def list_document_chunks(
    dataset_id: str,
    document_id: str,
    page: int = 1,
    page_size: int = 30,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    await require_dataset_write_access(user, db, [dataset_id])
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    try:
        data = await client.list_document_chunks(dataset_id, document_id, page=page, page_size=page_size)
        return {"code": 0, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/datasets/{dataset_id}/documents/upload")
async def upload_dataset_document(
    dataset_id: str,
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    started = time.time()
    await require_element_permission(user, db, "element:knowledge:upload_document")
    await require_dataset_write_access(user, db, [dataset_id])
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    try:
        blob = await file.read()
        uploaded = await client.upload_document(
            dataset_id,
            file.filename or "upload.bin",
            blob,
            file.content_type or "application/octet-stream",
        )
        await record_knowledge_audit(request, user, "documents:upload", 200, {
            "action": "upload_document",
            "dataset_id": dataset_id,
            "file_name": file.filename,
            "content_type": file.content_type,
            "size": len(blob),
        }, started_at=started)
        return {"code": 0, "data": uploaded}
    except Exception as e:
        await record_knowledge_audit(request, user, "documents:upload", 500, {
            "action": "upload_document",
            "dataset_id": dataset_id,
            "file_name": file.filename,
        }, error_message=str(e), started_at=started)
        raise


@router.delete("/datasets/{dataset_id}/documents")
async def delete_dataset_documents(
    dataset_id: str,
    payload: DeleteDocumentsRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    started = time.time()
    await require_element_permission(user, db, "element:knowledge:delete_document")
    await require_dataset_write_access(user, db, [dataset_id])
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No document ids provided")
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    try:
        await client.delete_documents(dataset_id, payload.ids)
        await record_knowledge_audit(request, user, "documents:delete", 200, {
            "action": "delete_document",
            "dataset_id": dataset_id,
            "document_ids": payload.ids,
        }, started_at=started)
        return {"code": 0, "message": "删除成功"}
    except Exception as e:
        await record_knowledge_audit(request, user, "documents:delete", 500, {
            "action": "delete_document",
            "dataset_id": dataset_id,
            "document_ids": payload.ids,
        }, error_message=str(e), started_at=started)
        raise


@router.post("/datasets/{dataset_id}/documents/parse")
async def parse_dataset_documents(
    dataset_id: str,
    payload: ParseDocumentsRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    started = time.time()
    await require_element_permission(user, db, "element:knowledge:parse_document")
    await require_dataset_write_access(user, db, [dataset_id])
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No document ids provided")
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    try:
        await client.parse_documents(dataset_id, payload.ids)
        await record_knowledge_audit(request, user, "documents:parse", 200, {
            "action": "parse_document",
            "dataset_id": dataset_id,
            "document_ids": payload.ids,
        }, started_at=started)
        return {"code": 0, "message": "解析任务已触发"}
    except Exception as e:
        await record_knowledge_audit(request, user, "documents:parse", 500, {
            "action": "parse_document",
            "dataset_id": dataset_id,
            "document_ids": payload.ids,
        }, error_message=str(e), started_at=started)
        raise


@router.post("/retrieval-test")
async def retrieval_test(
    payload: RetrievalTestRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    started = time.time()
    await require_element_permission(user, db, "element:knowledge:test_retrieval")
    await require_dataset_access(user, db, payload.dataset_ids)
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    try:
        chunks = await client.retrieve(
            payload.query,
            payload.dataset_ids,
            top_k=payload.top_k,
            similarity_threshold=payload.similarity_threshold,
            vector_similarity_weight=payload.vector_similarity_weight,
        )
        await record_knowledge_audit(request, user, "retrieval-test", 200, {
            "action": "retrieval_test",
            "dataset_ids": payload.dataset_ids,
            "query_summary": payload.query[:200],
            "top_k": payload.top_k,
            "similarity_threshold": payload.similarity_threshold,
            "vector_similarity_weight": payload.vector_similarity_weight,
            "hit_count": len(chunks),
        }, started_at=started)
        return {"code": 0, "data": chunks, "total": len(chunks)}
    except Exception as e:
        await record_knowledge_audit(request, user, "retrieval-test", 500, {
            "action": "retrieval_test",
            "dataset_ids": payload.dataset_ids,
            "query_summary": payload.query[:200],
        }, error_message=str(e), started_at=started)
        raise


@router.get("/datasets/{dataset_id}/permissions")
async def get_dataset_permissions(
    dataset_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    获取指定知识库授权的所有用户和角色列表
    """
    from app.models.permission import ResourcePermission, Role
    from app.models.user import User
    from sqlalchemy import select

    await require_dataset_write_access(user, db, [dataset_id])

    stmt = select(ResourcePermission).where(
        ResourcePermission.resource_type == "dataset",
        ResourcePermission.resource_id == dataset_id,
        ResourcePermission.enabled == True
    )
    result = await db.execute(stmt)
    perms = result.scalars().all()

    user_ids = [p.user_id for p in perms if p.user_id is not None]
    role_ids = [p.role_id for p in perms if p.role_id is not None]

    granted_users = []
    granted_roles = []

    if user_ids:
        user_stmt = select(User.id, User.user_name, User.real_name).where(User.id.in_(user_ids), User.status == 1)
        user_res = await db.execute(user_stmt)
        granted_users = [{"id": u.id, "user_name": u.user_name, "real_name": u.real_name} for u in user_res.all()]

    if role_ids:
        role_stmt = select(Role.id, Role.code, Role.name).where(Role.id.in_(role_ids))
        role_res = await db.execute(role_stmt)
        granted_roles = [{"id": r.id, "code": r.code, "name": r.name} for r in role_res.all()]

    return {
        "code": 0,
        "data": {
            "users": granted_users,
            "roles": granted_roles
        }
    }


@router.get("/metrics/summary")
async def get_ragflow_metrics_summary(
    start_date: str,
    end_date: str,
    user: dict = Depends(get_current_user)
):
    """
    获取指定日期范围内知识库与文档的统计指标汇总。
    查询前先触发一次 Redis → DB 归并同步，确保当日实时数据已落库；
    同步操作由分钟级 Redis 锁保护，重复调用不会造成数据重复写入。
    """
    from app.services.knowledge_metrics_service import KnowledgeMetricsService
    await KnowledgeMetricsService.sync_redis_metrics_to_db()
    data = await KnowledgeMetricsService.get_metrics_summary(start_date, end_date)
    return {"code": 0, "data": data}


async def _build_ragflow_document_file_response(document_id: str):
    from fastapi.responses import Response
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    try:
        content, filename, content_type = await client.download_document(document_id)
        
        # 强力纠正 Content-Type 防止未知流触发下载
        import mimetypes
        guess_type, _ = mimetypes.guess_type(filename)
        if guess_type:
            content_type = guess_type
        if filename.lower().endswith(".pdf"):
            content_type = "application/pdf"
            
        import urllib.parse
        encoded_filename = urllib.parse.quote(filename)
        headers = {
            "Content-Disposition": f'inline; filename="{encoded_filename}"; filename*=UTF-8\'\'{encoded_filename}'
        }
        return Response(content=content, media_type=content_type, headers=headers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch document file: {str(e)}")


@router.get("/datasets/{dataset_id}/documents/{document_id}/file")
async def get_ragflow_dataset_document_file(
    dataset_id: str,
    document_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    获取文档文件二进制流以供前端 iframe 原地预览；必须具备对应知识库读取权限。
    """
    await require_dataset_access(user, db, [dataset_id])
    return await _build_ragflow_document_file_response(document_id)


@router.get("/documents/{document_id}/file", dependencies=[Depends(require_admin)])
async def get_ragflow_document_file(
    document_id: str,
    user: dict = Depends(get_current_user),
):
    """
    旧版无 dataset 上下文的下载入口仅保留给管理员，避免普通用户绕过知识库授权。
    """
    return await _build_ragflow_document_file_response(document_id)


@router.get("/datasets/{dataset_id}/portal")
async def get_dataset_portal_recommendations(
    dataset_id: str,
    exclude: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    获取指定知识库数据集的侧边栏推荐提问与扩展概要，支持换一批
    """
    await require_dataset_access(user, db, [dataset_id])
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    try:
        # 1. 尝试获取数据集基本信息与文档列表（获取较多文件以支持换一批抽样）
        docs = await client.list_documents(dataset_id, page_size=100)
        doc_names = [d.get("name", "") for d in docs if d.get("name")]
        
        # 2. 默认快捷兜底问题
        default_questions = [
            {"label": "总结该知识库核心内容", "query": "请帮我梳理并总结一下当前选中的知识库里的核心要点 and 背景信息。"},
            {"label": "列出包含的所有文件", "query": "这个知识库里一共有哪些文档？分别介绍下它们的主题是什么。"},
            {"label": "检索使用说明", "query": "我想知道当前选中的这些文档里有哪些值得注意的关键条款或常见问题？"}
        ]
        
        # 3. 如果包含文档，尝试调用大模型动态生成更有针对性的问题
        if doc_names:
            try:
                from app.core.llm.client import get_llm_async
                from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
                from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage
                import json
                import re
                import random
                
                # 随机抽取前 10 个文件名进行推断，使得“换一批”有不同的提示内容
                sampled_names = list(doc_names)
                random.shuffle(sampled_names)
                sampled_names = sampled_names[:10]
                
                llm = await get_llm_async(streaming=False, temperature=0.7)
                if llm:
                    chat_client = chat_client_from_handle(llm)
                    doc_names_str = ", ".join(sampled_names)
                    exclude_instructions = ""
                    if exclude:
                        exclude_list = [q.strip() for q in exclude.split(",") if q.strip()]
                        if exclude_list:
                            exclude_instructions = f"\n请不要生成与以下已存在问题相近或重复的问题：{json.dumps(exclude_list, ensure_ascii=False)}。"
                            
                    prompt = (
                        f"你是一个专业的知识库导航助手。当前知识库包含以下文档列表：[{doc_names_str}]。\n"
                        f"请根据这些文件的名字所反映的业务内容，生成 3 个用户最有可能向 AI 提问的高频、专业且具体的问题。\n"
                        f"要求：\n"
                        f"1. 每个问题必须是一句完整、具体的提问，不要宽泛（如“介绍下XX项目的设计架构”而不是“关于架构的设计”）；\n"
                        f"2. 输出格式必须是一个合法的 JSON 数组，如：[\"问题一内容\", \"问题二内容\", \"问题三内容\"]\n"
                        f"3. 只输出 JSON 数组本身，严禁任何 Markdown 标记包围或多余解释。{exclude_instructions}"
                    )
                    messages = [
                        RuntimeMessage(
                            role="system",
                            content=[RuntimeContentBlock(type="text", text="你是一个严谨 of JSON 问答生成器。")]
                        ),
                        RuntimeMessage(
                            role="user",
                            content=[RuntimeContentBlock(type="text", text=prompt)]
                        )
                    ]
                    raw_text = await chat_client.generate_text(messages)
                    raw_text = str(raw_text or "").strip()
                    match = re.search(r"\[[\s\S]*\]", raw_text)
                    if match:
                        parsed_questions = json.loads(match.group())
                        if isinstance(parsed_questions, list) and len(parsed_questions) >= 3:
                            # 转换为前端要求的 label + query 格式
                            dynamic_questions = []
                            for q in parsed_questions[:3]:
                                q_str = str(q).strip()
                                # label 适当截短用于按钮展示
                                label = q_str[:15] + "..." if len(q_str) > 16 else q_str
                                dynamic_questions.append({
                                    "label": label,
                                    "query": q_str
                                })
                            return {"code": 0, "data": {"questions": dynamic_questions}}
            except Exception as llm_err:
                # LLM 生成失败时，安静退避到默认问题，不影响 API 的正常可用
                import logging
                logging.exception("Failed to generate dynamic questions via LLM in knowledge portal")
                pass
                
        return {"code": 0, "data": {"questions": default_questions}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate dataset portal info: {str(e)}")


@router.get("/datasets/{dataset_id}/documents/{document_id}/portal")
async def get_document_portal_recommendations(
    dataset_id: str,
    document_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    获取指定单个文档的专属推荐提问，支持 Redis 缓存（7天）与首尾切片大纲推断
    """
    await require_dataset_access(user, db, [dataset_id])
    
    # 1. 尝试从 Redis 缓存中获取
    from app.core.redis import get_redis
    redis_client = await get_redis()
    cache_key = f"ragflow:doc_recommendations:{document_id}"
    
    try:
        cached_data = None
        if redis_client:
            cached_data = await redis_client.get(cache_key)
            if cached_data:
                import json
                return {"code": 0, "data": json.loads(cached_data)}
    except Exception as redis_err:
        import logging
        logging.warning(f"Failed to read Redis cache for document portal: {str(redis_err)}")
        
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    
    # 2. 默认兜底提问
    default_questions = [
        {"label": "总结文档核心要点", "query": "根据选中的文档，请帮我梳理并详细总结一下它的核心内容与背景信息。"},
        {"label": "列出核心概念", "query": "这篇文档里有哪些值得注意的关键术语或核心概念？分别代表什么含义？"},
        {"label": "常见问题解答", "query": "我想知道这篇文档中可能包含的常见问题以及它们对应的解答。"}
    ]
    
    try:
        # 3. 异步获取文件前 3 个分块内容以作为大纲提取
        chunks_res = await client.list_document_chunks(dataset_id, document_id, page=1, page_size=3)
        chunks = chunks_res.get("chunks") or []
        doc_info = chunks_res.get("doc") or {}
        filename = doc_info.get("name") or "当前文档"
        
        # 4. 如果有分块文本，则结合大模型生成专属问题
        if chunks:
            text_snippets = []
            for c in chunks:
                content = c.get("content_with_weight") or c.get("content") or ""
                if content.strip():
                    text_snippets.append(content.strip()[:400]) # 限制每个片段字数，防止过大
                    
            if text_snippets:
                try:
                    from app.core.llm.client import get_llm_async
                    from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
                    from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage
                    import json
                    import re
                    
                    llm = await get_llm_async(streaming=False, temperature=0.7)
                    if llm:
                        chat_client = chat_client_from_handle(llm)
                        snippets_str = "\n---\n".join(text_snippets)
                        prompt = (
                            f"你是一个专业的文档分析助手。当前文件名为《{filename}》，它的核心大纲及前言片段如下：\n"
                            f"\"\"\"\n{snippets_str}\n\"\"\"\n\n"
                            f"请根据以上文件片段及文件名，策划生成 3 个针对该文件的、高频、专业且极为具体的提问。\n"
                            f"要求：\n"
                            f"1. 每个问题必须是一句完整、具体的提问，不要宽泛（如“如何配置ERP组织架构”而不是“关于配置问题”）；\n"
                            f"2. 输出格式必须是一个合法的 JSON 数组，如：[\"问题一\", \"问题二\", \"问题三\"]\n"
                            f"3. 只输出 JSON 数组本身，严禁任何 Markdown 标记包围或多余解释。"
                        )
                        messages = [
                            RuntimeMessage(
                                role="system",
                                content=[RuntimeContentBlock(type="text", text="你是一个严谨的 JSON 提问生成器。")]
                            ),
                            RuntimeMessage(
                                role="user",
                                content=[RuntimeContentBlock(type="text", text=prompt)]
                            )
                        ]
                        raw_text = await chat_client.generate_text(messages)
                        raw_text = str(raw_text or "").strip()
                        match = re.search(r"\[[\s\S]*\]", raw_text)
                        if match:
                            parsed_questions = json.loads(match.group())
                            if isinstance(parsed_questions, list) and len(parsed_questions) >= 3:
                                dynamic_questions = []
                                for q in parsed_questions[:3]:
                                    q_str = str(q).strip()
                                    label = q_str[:30] + "..." if len(q_str) > 31 else q_str
                                    dynamic_questions.append({
                                        "label": label,
                                        "query": q_str
                                    })
                                res_payload = {"questions": dynamic_questions}
                                
                                # 写入 Redis 缓存（有效期 7 天：604800 秒）
                                try:
                                    await redis_client.setex(cache_key, 604800, json.dumps(res_payload, ensure_ascii=False))
                                except Exception as cache_err:
                                    import logging
                                    logging.warning(f"Failed to write Redis cache for document portal: {str(cache_err)}")
                                    
                                return {"code": 0, "data": res_payload}
                except Exception as llm_err:
                    import logging
                    logging.exception("Failed to generate dynamic questions for document via LLM")
                    
        # 兜底返回
        return {"code": 0, "data": {"questions": default_questions}}
    except Exception as e:
        import logging
        logging.exception(f"Failed to fetch document recommendations: {str(e)}")
        return {"code": 0, "data": {"questions": default_questions}}


@router.post("/datasets/{dataset_id}/ai-analyze")
async def analyze_dataset_metadata(
    dataset_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    基于知识库里的文档文件名，调用大模型生成描述说明、标签和备注信息。
    """
    await require_dataset_write_access(user, db, [dataset_id])

    client = RagFlowClient(config_prefix="knowledge_ragflow")
    try:
        docs = await client.list_documents(dataset_id, page=1, page_size=100)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch documents from RAGFlow: {str(e)}"
        )

    doc_names = [doc.get("name") for doc in docs if doc.get("name")]
    if not doc_names:
        raise HTTPException(
            status_code=400,
            detail="该知识库暂无文档，无法进行 AI 分析。"
        )

    from app.core.llm.client import get_llm_async
    from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
    from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage
    import json
    import re

    llm = await get_llm_async(streaming=False, temperature=0.7)
    if not llm:
        raise HTTPException(
            status_code=500,
            detail="无法初始化大模型，请检查 LLM 相关系统设置。"
        )

    chat_client = chat_client_from_handle(llm)
    doc_names_str = ", ".join(doc_names[:50])

    prompt = (
        f"你是一个专业的知识库分析助手。当前知识库包含了以下文档列表：\n"
        f"[{doc_names_str}]\n\n"
        f"请根据这些文件的名字所反映的业务内容、行业特征及知识领域，为该知识库推断并生成以下元数据信息：\n"
        f"1. 描述说明（description）：一句简明扼要地介绍该知识库包含哪些内容以及其主要用途的话，中文，不超过 100 字。\n"
        f"2. 标签（tags）：3 到 5 个能够体现知识库核心主题的关键词或标签，可以是中文或英文，多个标签之间请用英文逗号 (,) 分隔。例如：\"ERP,系统说明,使用指南\"。\n"
        f"3. 备注信息（notes）：对该知识库使用场景、目标读者或注意事项的简要补充说明，中文，不超过 100 字。\n\n"
        f"请严格输出为以下格式的 JSON 对象，不要包含任何 Markdown 代码块标记（如 ```json）或多余的解释：\n"
        f"{{\n"
        f"  \"description\": \"...\",\n"
        f"  \"tags\": \"...\",\n"
        f"  \"notes\": \"...\"\n"
        f"}}"
    )

    messages = [
        RuntimeMessage(
            role="system",
            content=[RuntimeContentBlock(type="text", text="你是一个严谨的 JSON 格式输出器，只输出合法的 JSON，没有任何其他解释文本。")]
        ),
        RuntimeMessage(
            role="user",
            content=[RuntimeContentBlock(type="text", text=prompt)]
        )
    ]

    try:
        raw_text = await chat_client.generate_text(messages)
        raw_text = str(raw_text or "").strip()
        match = re.search(r"\{[\s\S]*\}", raw_text)
        if match:
            parsed_data = json.loads(match.group())
            return {
                "code": 0,
                "data": {
                    "description": parsed_data.get("description", "").strip(),
                    "tags": parsed_data.get("tags", "").strip(),
                    "notes": parsed_data.get("notes", "").strip()
                }
            }
        else:
            raise ValueError(f"No JSON block found in LLM response: {raw_text}")
    except Exception as llm_err:
        import logging
        logging.exception("Failed to run AI metadata analysis via LLM")
        raise HTTPException(
            status_code=500,
            detail=f"AI 分析失败: {str(llm_err)}"
        )

