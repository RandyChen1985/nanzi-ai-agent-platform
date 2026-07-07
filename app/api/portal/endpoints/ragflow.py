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
async def list_ragflow_agents(page: int = 1, page_size: int = 100):
    """
    Proxy to list RAGFlow agents (assistants).
    """
    base_url, api_key = await get_ragflow_client()
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
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Proxy to list RAGFlow datasets (knowledge bases) with permission filtering.
    """
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    metadata_service = KnowledgeBaseMetadataService(db)

    # 1. Check Permissions
    service = PermissionService(db)
    access = await service.get_knowledge_base_access(
        int(user["user_id"]),
        user.get("user_name"),
    )
    is_admin = access["is_admin"]

    try:
        items = await client.list_datasets(page=page, page_size=page_size)
        items = [item for item in items if not item.get("name", "").startswith("meta-")]

        if not is_admin:
            allowed = access["accessible_ids"] or set()
            items = [item for item in items if item.get("id") in allowed]

        merged = await metadata_service.merge_with_ragflow(items, include_missing=is_admin)
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
