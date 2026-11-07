import asyncio
import contextlib
import json
import logging
import os
import time
import uuid
import datetime
import httpx
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.orm import get_db_session
from app.services.config_service import ConfigService
from app.core.dependencies import require_admin, get_current_user, require_permission
from app.services.permission_service import (
    ALLOWED_EDIT_POLICY_MODES,
    EDIT_POLICY_SELECTED,
    VISIBILITY_PRIVATE,
    PermissionService,
    normalize_edit_policy_mode,
    normalize_knowledge_visibility,
    resolve_edit_policy_from_config,
)
from app.services.ai.ragflow_client import RagFlowClient, clear_dataset_list_cache
from app.services.knowledge_base_service import KnowledgeBaseMetadataService
from app.services.knowledge_access_request_service import (
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_PENDING,
    STATUS_REJECTED,
    AccessRequestError,
    KnowledgeAccessRequestService,
)
from app.services.knowledge_dataset_filters import is_internal_dataset_name
from app.services.knowledge_health_service import KnowledgeHealthService
from app.services.portal_notification_service import PortalNotificationService
from app.services.knowledge_quota_service import KnowledgeQuotaService, QuotaExceeded
from app.services import knowledge_image_service as image_service
from app.services.ai.multimodal_support import resolve_default_multimodal_model_name
from app.services.knowledge_recent_visit_service import KnowledgeRecentVisitService
from app.services.knowledge_retrieval_log_service import (
    SOURCE_TEST,
    KnowledgeRetrievalLogService,
)
from app.services.knowledge_reference_service import KnowledgeReferenceService
from app.services.audit_service import AuditService
from app.services.knowledge_audit_service import (
    GLOBAL_AUDIT_DATASET_ID,
    KnowledgeAuditService,
)
from app.models.knowledge import KnowledgeBaseFavorite
from app.models.permission import ResourcePermission

router = APIRouter()
logger = logging.getLogger(__name__)

#: 平台侧单文件上限 256MB：提前拦住，避免整个文件流到上游才报错。
#: 超过这个量级的文档解析质量通常也不好，宁可让用户拆分。
KNOWLEDGE_UPLOAD_MAX_BYTES = 256 * 1024 * 1024
KNOWLEDGE_UPLOAD_MAX_MB = KNOWLEDGE_UPLOAD_MAX_BYTES // (1024 * 1024)

#: 与 RAGFlow 解析器支持的格式保持一致；不在白名单的直接拒绝，不做静默透传。
#:
#: 刻意不收图片（png/jpg/gif/bmp/tif）：RAGFlow 的图片解析需要视觉模型配合，
#: 当前部署没有可用链路，传上去只会停在「解析失败」。与其让用户传完再发现，
#: 不如在入口就挡掉——需要图片内容时先转成 PDF 再上传。
KNOWLEDGE_UPLOAD_ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".txt", ".md", ".csv",
    ".xls", ".xlsx", ".ppt", ".pptx", ".html", ".htm", ".epub",
}


def _validate_knowledge_upload(file_name: str, size: int) -> None:
    """类型 + 大小前置校验。

    顺序是先类型后大小：类型不符时没必要为一个几 GB 的文件先读一遍。
    """
    ext = os.path.splitext(file_name or "")[1].lower()
    if ext not in KNOWLEDGE_UPLOAD_ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=(
                f"不支持的文件类型「{ext or '无扩展名'}」，"
                "支持 PDF/Word/Excel/PPT/Markdown/TXT/HTML/EPUB"
            ),
        )
    if size > KNOWLEDGE_UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过 {KNOWLEDGE_UPLOAD_MAX_MB}MB 上限，请拆分后再上传",
        )


async def _read_upload_with_limit(
    file: UploadFile,
    limit_bytes: Optional[int] = None,
    limit_label: str = "",
) -> bytes:
    """分块读取并卡上限，避免 ``await file.read()`` 把整个文件读进内存。

    不能只信 ``file.size``：部分客户端不传 Content-Length，边读边卡才可靠。

    图片走同一条读取逻辑，只是上限低一个数量级（多模态模型吃不下 256MB 的图）。
    """
    # 上限在这里取而不是当默认参数：默认值在定义时就绑定了，
    # 于是 monkeypatch KNOWLEDGE_UPLOAD_MAX_BYTES 对已导入的函数不生效。
    cap = KNOWLEDGE_UPLOAD_MAX_BYTES if limit_bytes is None else limit_bytes
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            label = limit_label or f"{KNOWLEDGE_UPLOAD_MAX_MB}MB"
            raise HTTPException(
                status_code=413,
                detail=f"文件超过 {label} 上限，请压缩或拆分后再上传",
            )
        chunks.append(chunk)
    return b"".join(chunks)


class CreateDatasetRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    chunk_method: str = "naive"
    # 业务归属：owner_dept_id 是唯一真源，owner 仅为兼容旧调用方的文本快照。
    owner: Optional[str] = None
    owner_dept_id: Optional[int] = None
    visibility: str = "private"
    tags: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    extra_config: Dict[str, Any] = Field(default_factory=dict)


class UpdateFolderStructureRequest(BaseModel):
    #: 虚拟文件夹 → 文档 ID 列表。前端把整份映射发上来（覆盖语义），
    #: 服务端只替换 extra_config.folder_structure 这一个子键。
    folder_structure: Dict[str, List[str]] = Field(default_factory=dict)


class UpdateDatasetMetadataRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    # 业务归属：字段不出现 = 保持原值；owner_dept_id 传 0/None = 清空部门归属。
    owner: Optional[str] = None
    owner_dept_id: Optional[int] = None
    visibility: Optional[str] = None
    tags: Optional[List[str]] = None
    notes: Optional[str] = None
    #: 整份覆盖 ``extra_config``。**保留仅为兼容旧调用方**——不要在新代码里用它：
    #: 调用方手里往往只有打开页面时的快照，整份回写会把并发方改过的
    #: ``folder_structure`` 悄悄冲掉（同一个库两个人操作 = lost update）。
    #: 需要改快捷提问请用下面的 ``custom_questions``。
    extra_config: Optional[Dict[str, Any]] = None
    #: 只更新 ``extra_config.custom_questions``，其余键保持数据库里的现值。
    custom_questions: Optional[List[Dict[str, Any]]] = None


class UpdateEditPolicyRequest(BaseModel):
    """知识库编辑权限范围。

    ``mode`` 的合法值取决于库的可见范围（见 permission_service 的
    ALLOWED_EDIT_POLICY_MODES）：public 支持只读/被授权者可编辑/所有可见者/指定成员，
    team 支持只读/部门成员/指定成员。``editors`` 只在 selected 时生效，且只认具体用户。
    """

    mode: str
    editors: List[Dict[str, Any]] = Field(default_factory=list)


class DeleteDatasetsRequest(BaseModel):
    ids: List[str] = Field(default_factory=list)


class DeleteDocumentsRequest(BaseModel):
    ids: List[str] = Field(default_factory=list)


class ParseDocumentsRequest(BaseModel):
    ids: List[str] = Field(default_factory=list)


class RetrievalTestRequest(BaseModel):
    query: str = Field(..., min_length=1)
    dataset_ids: List[str] = Field(..., min_length=1)
    #: 语义是「返回条数」，映射到 RAGFlow 的 page_size（不是 top_k）
    top_n: int = Field(default=5, ge=1, le=50)
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
    """可写：管理员 / 归属人（仅被授权或被分配的非归属人只读）"""
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


async def require_dataset_content_write_access(
    user: dict,
    db: AsyncSession,
    dataset_ids: List[str],
):
    """内容级可写：管理员 / 创建人 / team 库的归属部门（含下级部门）成员。

    与 ``require_dataset_write_access``（管理级）的区别只在「能不能改库的设置」：

    - 内容级：上传文档、删除文档、触发解析、查看切片明细；
    - 管理级：改元数据、改可见范围、改归属部门、删库、分配/撤销授权、看授权名单。

    部门成员能维护本部门的资料，但不能把这个库改成别人才看得到、也不能删库。
    授权成员（被显式分配的人）恒为只读，两级都不含。
    """
    service = PermissionService(db)
    access = await service.get_knowledge_base_access(
        int(user["user_id"]),
        user.get("user_name"),
    )
    if access["is_admin"]:
        return
    writable = access.get("content_writable_ids") or set()
    denied = [dataset_id for dataset_id in dataset_ids if dataset_id not in writable]
    if denied:
        raise HTTPException(
            status_code=403,
            detail=f"Read-only or no permission for dataset: {', '.join(denied)}",
        )


async def _resolve_owner_department(
    db: AsyncSession,
    owner_dept_id: Optional[int],
) -> tuple[Optional[str], Optional[int]]:
    """校验业务归属部门并回填文本快照。

    owner_dept_id 是唯一真源，owner 文本只在写库时按部门当前名称派生，
    这样部门改名后展示以部门表为准（前端按 owner_dept_id 映射），这里的快照
    仅用于兼容旧数据与直接读库的场景。

    注意：该字段并非纯展示字段。public/private 下它只影响浏览分组与搜索，
    但 team（TEAM 级）下它是**自动只读权限的判定依据**：归属部门（含下级部门）
    的成员据此自动获得只读权。改归属部门等于改可见人群。
    """
    if not owner_dept_id:
        return None, None
    from app.services.department_service import DepartmentService

    dept = await DepartmentService(db).get_by_ref(str(int(owner_dept_id)))
    if not dept:
        raise HTTPException(status_code=400, detail="业务归属部门不存在或已被删除")
    return dept.name, int(dept.id)


async def require_dataset_shareable(db: AsyncSession, dataset_id: str) -> None:
    """个人私有（private）知识库不允许分配授权。

    private 的语义就是「仅创建人可访问」，因此授权端点必须拒绝写授权记录，
    否则会出现"页面上加了人、对方却依然看不到"的假操作。元数据缺失时按
    public 处理，与 `PermissionService` 的兜底口径保持一致。
    """
    from app.models.knowledge import KnowledgeBaseMetadata
    from sqlalchemy import select

    visibility = (
        await db.execute(
            select(KnowledgeBaseMetadata.visibility).where(
                KnowledgeBaseMetadata.ragflow_dataset_id == dataset_id,
                KnowledgeBaseMetadata.status != "deleted",
            )
        )
    ).scalar_one_or_none()
    if visibility is not None and str(visibility).strip().lower() == "private":
        raise HTTPException(
            status_code=400,
            detail="个人私有知识库不支持分配授权，请先把「可见范围」改为授权可见或 TEAM 级",
        )


def _apply_knowledge_permission_flags(
    items: List[Dict[str, Any]],
    access: Dict[str, Any],
) -> None:
    for item in items:
        ds_id = str(item.get("ragflow_dataset_id") or item.get("id") or "")
        # 编辑策略随列表一起返回：前端不必为每个库单独请求一次，也能直接显示
        # 「这个库谁能改文档」。缺失/非法值由 resolve_edit_policy_from_config 兜底。
        #
        # 注意取值路径：merge_with_ragflow 把本地元数据整体挂在 local_metadata 下
        # （不平铺 extra_config）。早先这里直接读 item["extra_config"] 永远是 None，
        # 于是列表永远显示默认策略 —— 表现为「保存成功了，刷新又还原」。
        local_meta = item.get("local_metadata")
        extra_config = item.get("extra_config")
        if extra_config is None and isinstance(local_meta, dict):
            extra_config = local_meta.get("extra_config")
        item["edit_policy"] = resolve_edit_policy_from_config(
            extra_config, item.get("visibility")
        )
        if access.get("is_admin"):
            item["can_write"] = True
            item["can_manage"] = True
            item["can_view_chunks"] = True
            item["is_read_only"] = False
            continue
        writable = access.get("writable_ids") or set()
        content_writable = access.get("content_writable_ids") or set()
        accessible = access.get("accessible_ids") or set()
        # can_write  = 内容级（上传/删文档/解析/看切片）：创建人 + team 部门成员
        # can_manage = 管理级（改元数据/可见范围/删库/授权）：仅创建人
        item["can_write"] = ds_id in content_writable
        item["can_manage"] = ds_id in writable
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
    # 按库拆条：批量删库 / 跨库检索要落到每一个 dataset_id 上，
    # 否则查其中一个库时会看不到「它自己被删了」。
    raw_ids: list = []
    single = safe_payload.pop("dataset_id", None)
    if single:
        raw_ids.append(single)
    batch = safe_payload.pop("dataset_ids", None)
    if batch:
        raw_ids.extend(batch if isinstance(batch, (list, tuple, set)) else [batch])

    # 只写业务审计表：HTTP 访问日志已由中间件统一记录，
    # 同一个操作再往访问日志写一条纯属重复。
    await KnowledgeAuditService.log(
        dataset_ids=raw_ids,
        action=action,
        user=user,
        status_code=status_code,
        detail=safe_payload,
        client_ip=request.client.host if request.client else None,
        error_message=error_message,
    )


#: 断连探测间隔：太密会空转 receive，太疏则上游白跑的时间变长。
DISCONNECT_POLL_INTERVAL_SECONDS = 0.25


async def _watch_client_disconnect(request: Request) -> bool:
    """轮询客户端连接状态，断开即返回 True。

    检索是高延迟的同步等待，用户点「取消检索」后浏览器到本服务的连接立刻断开，
    但后端到 RAGFlow 的请求不会自己停：不主动取消就要等到超时（默认 30s），
    期间上游的向量检索/重排算力照样被占用。
    """
    while True:
        if await request.is_disconnected():
            return True
        await asyncio.sleep(DISCONNECT_POLL_INTERVAL_SECONDS)


def _audit_status_code(error: Exception) -> int:
    """审计里的状态码要尽量还原真实原因，不能一律 500。

    RAGFlow 的 4xx / 5xx 会被包装成 ``RagFlowServiceError``（status_code=502）
    或 ``RagFlowUnavailableError``（503）；如果不还原，事后只能看到「500」，
    既分不清是平台 bug 还是上游拒绝，也做不到按错误类型统计。
    """
    if isinstance(error, HTTPException):
        return int(error.status_code)
    return int(getattr(error, "status_code", 500) or 500)


async def _attach_owner_user_names(db: AsyncSession, items: List[Dict[str, Any]]) -> None:
    """给列表项补归属人显示名。

    前端只拿到 ``owner_user_id`` 的话只能显示一串数字；这里一次查完所有涉及的
    用户，避免逐库回查（列表是几十到几百条量级）。
    已离职/已删除的用户查不到，``owner_user_name`` 保持缺失，前端回落显示创建人。
    """
    from app.models.user import User
    from sqlalchemy import select

    owner_ids = {
        int(item["owner_user_id"]) for item in items if item.get("owner_user_id")
    }
    if not owner_ids:
        return
    rows = (
        await db.execute(
            select(User.id, User.user_name, User.real_name).where(User.id.in_(owner_ids))
        )
    ).all()
    name_by_id = {int(uid): (real_name or user_name) for uid, user_name, real_name in rows}
    for item in items:
        owner_user_id = item.get("owner_user_id")
        if owner_user_id:
            item["owner_user_name"] = name_by_id.get(int(owner_user_id))


async def _attach_agent_refs(db: AsyncSession, items: List[Dict[str, Any]]) -> None:
    """给列表项补「被几个智能体引用」，只回前 5 个，避免响应膨胀。

    引用关系是删除/收窄可见范围前的影响面依据。列表里给数量与少量样本，
    完整名单走 ``GET /datasets/{id}/agents``。
    """
    dataset_ids = [
        str(item.get("ragflow_dataset_id") or item.get("id") or "")
        for item in items
    ]
    grouped = await KnowledgeReferenceService(db).agents_by_dataset(dataset_ids)
    for item in items:
        dataset_id = str(item.get("ragflow_dataset_id") or item.get("id") or "")
        agents = grouped.get(dataset_id, [])
        item["agent_ref_count"] = len(agents)
        item["agent_refs"] = agents[:5]


async def _attach_grant_counts(db: AsyncSession, items: List[Dict[str, Any]]) -> None:
    """给列表项补「授权给了多少用户 / 多少角色」。

    列表只回数量，完整名单仍走 ``GET /datasets/{id}/permissions``：数量用来判断
    「收窄可见范围会影响到多少人」，点进去才需要看具体是谁。
    """
    from app.models.permission import ResourcePermission
    from sqlalchemy import func, select

    dataset_ids = [
        str(item.get("ragflow_dataset_id") or item.get("id") or "") for item in items
    ]
    dataset_ids = [dataset_id for dataset_id in dataset_ids if dataset_id]
    if not dataset_ids:
        for item in items:
            item["grant_user_count"] = 0
            item["grant_role_count"] = 0
        return

    # 一次 group by 算全量，避免每个库各查一遍
    rows = (
        await db.execute(
            select(
                ResourcePermission.resource_id,
                func.count(func.distinct(ResourcePermission.user_id)),
                func.count(func.distinct(ResourcePermission.role_id)),
            )
            .where(
                ResourcePermission.resource_type == "dataset",
                ResourcePermission.resource_id.in_(dataset_ids),
                ResourcePermission.enabled == True,  # noqa: E712
            )
            .group_by(ResourcePermission.resource_id)
        )
    ).all()
    counts = {str(resource_id): (int(users or 0), int(roles or 0)) for resource_id, users, roles in rows}

    for item in items:
        dataset_id = str(item.get("ragflow_dataset_id") or item.get("id") or "")
        users, roles = counts.get(dataset_id, (0, 0))
        item["grant_user_count"] = users
        item["grant_role_count"] = roles


async def _attach_favorites(db: AsyncSession, items: List[Dict[str, Any]], user_id: int) -> None:
    """标注当前用户是否收藏过这些库。

    「我的收藏」不额外查一套列表接口：收藏是个人视图，列表项上带布尔位，
    前端按它过滤即可，也避免两个接口的可见范围打架。
    """
    dataset_ids = [
        str(item.get("ragflow_dataset_id") or item.get("id") or "")
        for item in items
    ]
    from sqlalchemy import select

    dataset_ids = [item for item in dataset_ids if item]
    favorited: set = set()
    if dataset_ids:
        rows = await db.execute(
            select(KnowledgeBaseFavorite.dataset_id).where(
                KnowledgeBaseFavorite.user_id == user_id,
                KnowledgeBaseFavorite.dataset_id.in_(dataset_ids),
            )
        )
        favorited = {str(row) for row in rows.scalars().all()}
    for item in items:
        dataset_id = str(item.get("ragflow_dataset_id") or item.get("id") or "")
        item["is_favorited"] = dataset_id in favorited


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
    from app.services.ai.knowledge_utils import (
        is_knowledge_base_enabled,
        resolve_rag_retrieval_params,
    )

    base_url = await ConfigService.get("knowledge_ragflow_api_url")
    api_key = await ConfigService.get("knowledge_ragflow_api_key")
    # 默认值必须与全库其余 8 处（schema/examples/metadata_service/…）一致为 local：
    # 这里漏改时，没显式配置的项目会被前端判成 RAGFlow 模式，进而误报连通性故障。
    metadata_provider = await ConfigService.get("metadata_provider", default="local")
    knowledge_enabled = await is_knowledge_base_enabled()
    api_url = base_url.rstrip("/") + "/" if base_url else ""
    # 检索测试页的参数默认值与智能体链路同源：配置改一次两边一起变，
    # 避免「页面上写死 0.3、线上读配置 0.5」两套默认值打架。
    threshold, vector_weight, top_k = resolve_rag_retrieval_params(
        system_threshold=await ConfigService.get("knowledge_ragflow_similarity_threshold"),
        system_weight=await ConfigService.get("knowledge_ragflow_vector_weight"),
        system_top_k=await ConfigService.get("knowledge_ragflow_metadata_top_k"),
    )
    return {
        "code": 0,
        "data": {
            "api_url": api_url,
            "api_key_configured": bool(api_key),
            "configured": bool(api_url and api_key),
            "metadata_provider": metadata_provider,
            "knowledge_base_enabled": knowledge_enabled,
            "retrieval_defaults": {
                "top_n": top_k,
                "similarity_threshold": threshold,
                "vector_similarity_weight": vector_weight,
            },
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
            if resp.status_code in (401, 403):
                # 上游鉴权失败绝不能原样透传 401：门户前端把任何 401 都当作「登录态
                # 失效」而直接跳登录页，管理员只是打开智能体列表就会莫名被登出。
                # 与模型供应商探活一致（见 models.py），按网关侧 502 上报。
                raise HTTPException(
                    status_code=502,
                    detail="RAGFlow 鉴权失败，请检查知识库 RAGFlow API Key 配置。",
                )
            raise HTTPException(status_code=resp.status_code, detail=f"RAGFlow Error: {resp.text}")
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to RAGFlow: {str(e)}")

#: ``/datasets`` 允许的可见范围取值。非法值/空串一律当「没传」——直接把拼错的值
#: 当过滤条件会返回空列表，前端看到的却是「我的知识库全没了」。
_KNOWLEDGE_VISIBILITY_VALUES = {"public", "team", "private"}


def _dataset_id_of(item: Dict[str, Any]) -> str:
    return str(item.get("ragflow_dataset_id") or item.get("id") or "")


def _to_epoch_seconds(value: Any) -> Optional[float]:
    """把 RAGFlow / 本地元数据里的时间归一成秒级时间戳。

    同一个列表里可能出现三种形态：RAGFlow 的毫秒时间戳（字符串或数字）、
    本地元数据的 ISO 字符串（``updated_at``）。归一不了就返回 None，
    由调用方按「没有时间」处理（排最后），不能当成 0 参与排序。
    """
    if value is None or value == "" or isinstance(value, bool):
        return None
    number: Optional[float] = None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            try:
                parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
            return parsed.timestamp()
    # RAGFlow 用毫秒，本地元数据用秒；1e11 秒已经是公元 5000 年了，够区分
    return number / 1000.0 if number > 1e11 else number


def _dataset_updated_at(item: Dict[str, Any]) -> Optional[float]:
    for key in ("update_time", "updated_at"):
        ts = _to_epoch_seconds(item.get(key))
        if ts is not None:
            return ts
    return None


def _numeric_field(item: Dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = item.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _dataset_keyword_blob(item: Dict[str, Any]) -> str:
    """keyword 的匹配面：库名 / 引擎名 / ID / 负责人 / 描述 / 标签。

    ``owner`` 是本地元数据里的归属人字符串，``owner_user_name`` 是本次请求
    补出来的显示名——两者都要能搜，否则搜「张三」搜不到他名下的库。
    """
    parts: List[Any] = [
        item.get("platform_name"),
        item.get("name"),
        item.get("ragflow_dataset_id") or item.get("id"),
        item.get("owner"),
        item.get("owner_user_name"),
        item.get("platform_description") or item.get("description"),
    ]
    tags = item.get("tags")
    if isinstance(tags, (list, tuple, set)):
        parts.extend(tags)
    elif tags:
        parts.append(tags)
    return "\n".join(str(part) for part in parts if part).lower()


def _sort_knowledge_datasets(
    items: List[Dict[str, Any]], sort: Optional[str]
) -> List[Dict[str, Any]]:
    """updated（默认）/ name / docs 三种排序，同值一律按 id 兜底保持稳定。"""
    mode = (sort or "updated").strip().lower()
    # 先按 id 排一遍做稳定基准：同值时顺序不随输入顺序抖动
    ordered = sorted(items, key=_dataset_id_of)
    if mode == "name":
        ordered.sort(
            key=lambda item: str(item.get("platform_name") or item.get("name") or "")
        )
        return ordered
    if mode == "docs":
        ordered.sort(
            key=lambda item: _numeric_field(item, "doc_count", "document_count"),
            reverse=True,
        )
        return ordered

    def _updated_key(item: Dict[str, Any]) -> float:
        ts = _dataset_updated_at(item)
        # 没有时间的排最后（reverse=True 下 -inf 自然沉底）
        return float("-inf") if ts is None else ts

    ordered.sort(key=_updated_key, reverse=True)
    return ordered


@router.get("/datasets")
async def list_ragflow_datasets(
    page: int = 1, 
    # 默认一次拉全量：这个接口的语义是「列出我能看的库」，而 RAGFlow 的
    # /datasets 里混着成百上千个 meta-* 元数据数据集。传 100 只拿到第一页，
    # 过滤 meta-* 后可能为空，于是本地知识库全被判成「已失联」。
    # 现在由 client 按 total 翻完所有页（不再有 1000 条上限），这里只是返回窗口。
    page_size: int = 5000,
    # 1 = 绕过 60s 列表缓存强制回源（同步按钮 / 手动刷新用）
    refresh: int = 0,
    override_url: Optional[str] = None,
    override_key: Optional[str] = None,
    include_missing: bool = True,
    # --- 列表过滤 / 排序（全部在 total 之前生效）---
    keyword: Optional[str] = None,
    visibility: Optional[str] = None,
    dept_id: Optional[int] = None,
    missing: Optional[int] = None,
    sort: Optional[str] = None,
    favorite: int = 0,
    ids: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """Proxy to list RAGFlow datasets (knowledge bases) with permission filtering.

    过滤顺序不能乱：``keyword`` 要能搜到负责人显示名、``favorite`` 依赖
    ``is_favorited``，所以先补齐各类附加字段（权限位 / 负责人 / 引用 / 授权数 /
    收藏），再过滤，再排序，最后才是分页切片；``total`` 是**过滤后**的条数。
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
        stale = False
        try:
            items = await client.list_all_datasets_cached(refresh=bool(refresh))
        except Exception as cache_err:
            # 引擎抖了但本地有上次快照：给一份旧数据 + stale 标记，
            # 总比整页报错、看起来像「所有库都没了」要好
            snapshot = RagFlowClient.cached_datasets_snapshot(client.base_url)
            if snapshot is None:
                raise
            items = snapshot
            stale = True
            logger.warning("[Knowledge] RAGFlow 列表拉取失败，降级为缓存快照: %s", cache_err)

        items = [
            item for item in items if not is_internal_dataset_name(item.get("name"))
        ]

        if not is_admin:
            allowed = access["accessible_ids"] or set()
            items = [item for item in items if item.get("id") in allowed]

        # 结合参数决定是否包含失联知识库（如果临时覆写了 RAGFlow 地址则不包含）
        real_include_missing = include_missing
        if override_url:
            real_include_missing = False
        # 归属人得能看见「自己的库在引擎侧失联了」，否则它只是从列表里消失，
        # 看起来像被别人删了；但非管理员只能看到自己范围内的失联记录。
        missing_allow_ids = None if is_admin else (access["accessible_ids"] or set())

        merged = await metadata_service.merge_with_ragflow(
            items,
            include_missing=real_include_missing,
            missing_allow_ids=missing_allow_ids,
        )
        _apply_knowledge_permission_flags(merged, access)
        await _attach_owner_user_names(db, merged)
        await _attach_agent_refs(db, merged)
        await _attach_grant_counts(db, merged)
        await _attach_favorites(db, merged, int(user["user_id"]))

        # --- 过滤 ---
        keyword_text = (keyword or "").strip().lower()
        if keyword_text:
            merged = [
                item for item in merged if keyword_text in _dataset_keyword_blob(item)
            ]

        visibility_value = (visibility or "").strip().lower() or None
        if visibility_value in _KNOWLEDGE_VISIBILITY_VALUES:
            merged = [
                item
                for item in merged
                if str(item.get("visibility") or "").strip().lower() == visibility_value
            ]
        # visibility 非法（拼错 / 空串）时忽略，不当作「筛出空列表」

        if dept_id is not None:
            try:
                dept_value = int(dept_id)
            except (TypeError, ValueError):
                dept_value = None
            if dept_value == -1:
                # -1 = 未关联部门：本地元数据缺失的库 owner_dept_id 也是 None，
                # 与前端「未分配」分组的口径保持一致
                merged = [item for item in merged if item.get("owner_dept_id") in (None, "")]
            elif dept_value is not None:
                merged = [
                    item
                    for item in merged
                    if str(item.get("owner_dept_id")) == str(dept_value)
                ]

        if missing is not None:
            try:
                missing_value = int(missing)
            except (TypeError, ValueError):
                missing_value = None
            if missing_value == 1:
                merged = [item for item in merged if item.get("is_missing_in_ragflow") is True]

        if favorite is not None:
            try:
                favorite_value = int(favorite)
            except (TypeError, ValueError):
                favorite_value = 0
            if favorite_value == 1:
                merged = [item for item in merged if item.get("is_favorited") is True]

        # --- 排序 ---
        merged = _sort_knowledge_datasets(merged, sort)

        # --- 「最近访问」：按传入顺序返回，不在本次可见范围内的直接忽略 ---
        if ids:
            wanted: List[str] = []
            for segment in str(ids).split(","):
                dataset_id = segment.strip()
                if dataset_id and dataset_id not in wanted:
                    wanted.append(dataset_id)
            if wanted:
                by_id = {_dataset_id_of(item): item for item in merged}
                merged = [by_id[dataset_id] for dataset_id in wanted if dataset_id in by_id]

        total = len(merged)
        if page_size and page_size > 0:
            current_page = page if page and page > 0 else 1
            start = max(0, (current_page - 1) * page_size)
            merged = merged[start:start + page_size]
        return {"code": 0, "data": merged, "total": total, "stale": stale}
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to connect to RAGFlow: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class AccessRequestBody(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=2000)


@router.get("/datasets/discover")
async def discover_datasets(
    keyword: Optional[str] = None,
    page: int = 1,
    size: int = 20,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """「发现」：我能看见存在、但还没拿到权限的公共/部门库，可直接申请。

    private 库不出现——它连存在这件事都不该暴露给无关的人。
    """
    from app.models.knowledge import KnowledgeAccessRequest

    service = PermissionService(db)
    access = await service.get_knowledge_base_access(
        int(user["user_id"]), user.get("user_name")
    )
    if access.get("is_admin"):
        # 管理员的 accessible_ids 是 None（"不受限"），按集合算会得出"一个库都访问不了"，
        # 于是整站的库全被当成可申请对象。管理员本来就对所有库有权限，「发现」对他没有意义。
        return {"code": 0, "data": {"items": [], "total": 0, "is_admin": True}}

    accessible = access["accessible_ids"] or set()

    metadata_service = KnowledgeBaseMetadataService(db)
    rows = await metadata_service.list_metadata()

    # 失联库（引擎侧已不存在）不该出现在「发现」里：申请了也打不开内容，
    # 审批人点了通过才发现库是空的。引擎不可用时不过滤——宁可多列几条，
    # 也不要因为一次抖动让整个发现列表凭空消失。
    try:
        engine_items = await RagFlowClient(
            config_prefix="knowledge_ragflow"
        ).list_all_datasets_cached()
        engine_map = {
            str(item.get("id")): item for item in engine_items if item.get("id")
        }
        engine_ids = set(engine_map)
    except Exception as engine_exc:
        logger.warning("[Knowledge] 发现列表无法读取引擎侧库列表，跳过失联过滤: %s", engine_exc)
        engine_ids = None
        engine_map = {}

    candidates = [
        row
        for row in rows
        if row.ragflow_dataset_id not in accessible
        and (row.visibility or "private") in ("public", "team")
        and (engine_ids is None or row.ragflow_dataset_id in engine_ids)
        # 平台内置库（chatbi-example-meta 等）不是用户的库，申请了也用不上
        and not is_internal_dataset_name(row.name)
    ]

    kw = (keyword or "").strip().lower()
    if kw:
        candidates = [
            row
            for row in candidates
            if kw in (row.name or "").lower()
            or kw in (row.description or "").lower()
            or kw in (row.owner or "").lower()
            # 支持按 Dataset ID 搜索：用户常从详情页复制完整 ID 过来确认库是否存在
            or kw in (row.ragflow_dataset_id or "").lower()
        ]

    # 我在这批库上的申请状态（取最近一条）
    my_requests = {}
    if candidates:
        from sqlalchemy import select

        result = await db.execute(
            select(KnowledgeAccessRequest)
            .where(
                KnowledgeAccessRequest.applicant_user_id == int(user["user_id"]),
                KnowledgeAccessRequest.dataset_id.in_([row.ragflow_dataset_id for row in candidates]),
            )
            .order_by(KnowledgeAccessRequest.created_at.desc(), KnowledgeAccessRequest.id.desc())
        )
        for item in result.scalars().all():
            my_requests.setdefault(item.dataset_id, item)

    page = max(1, int(page or 1))
    size = min(100, max(1, int(size or 20)))
    total = len(candidates)
    window = candidates[(page - 1) * size: (page - 1) * size + size]

    return {
        "code": 0,
        "data": {
            "total": total,
            "page": page,
            "size": size,
            "items": [
                {
                    "dataset_id": row.ragflow_dataset_id,
                    "name": row.name,
                    "description": row.description,
                    "owner": row.owner,
                    "visibility": row.visibility,
                    "tags": list(getattr(row, "tags", None) or [])[:6],
                    # 规模与新鲜度是「值不值得申请」的主要依据：
                    # 只有名字和描述的话，用户只能凭感觉点申请。
                    "doc_count": _safe_int(
                        (engine_map.get(row.ragflow_dataset_id) or {}).get("document_count")
                    ),
                    "chunk_count": _safe_int(
                        (engine_map.get(row.ragflow_dataset_id) or {}).get("chunk_count")
                    ),
                    "engine_update_time": (
                        engine_map.get(row.ragflow_dataset_id) or {}
                    ).get("update_time"),
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                    "request_status": (
                        my_requests[row.ragflow_dataset_id].status
                        if row.ragflow_dataset_id in my_requests
                        else None
                    ),
                }
                for row in window
            ],
        },
    }


def _safe_int(value) -> Optional[int]:
    """引擎侧的计数可能是字符串、空串或缺失；拿不到就返回 None，别显示成 0。"""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@router.post("/datasets/{dataset_id}/access-requests")
async def create_access_request(
    dataset_id: str,
    payload: AccessRequestBody,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """提交访问申请。已有权限就别申请了，直接告诉用户。

    这是「发现」弹窗用的旧入口，保留是为了不打断已有前端；申请本身走统一服务，
    与资源广场、数据集申请是同一条链路、同一个审批队列。
    """
    metadata_service = KnowledgeBaseMetadataService(db)
    meta = await metadata_service.get_by_dataset_id(dataset_id)
    if not meta or meta.status == "deleted":
        raise HTTPException(status_code=404, detail="知识库不存在")
    if (meta.visibility or "private") == "private":
        raise HTTPException(status_code=400, detail="个人私有知识库不接受访问申请")

    service = PermissionService(db)
    access = await service.get_knowledge_base_access(
        int(user["user_id"]), user.get("user_name")
    )
    if access.get("is_admin"):
        raise HTTPException(status_code=400, detail="管理员对所有知识库都有访问权，无需申请")
    if dataset_id in (access["accessible_ids"] or set()):
        raise HTTPException(status_code=400, detail="你已经可以访问这个知识库")

    try:
        row = await KnowledgeAccessRequestService(db).create(
            dataset_id=dataset_id,
            dataset_name=meta.name,
            user=user,
            reason=payload.reason,
            dept_id=meta.owner_dept_id,
        )
    except AccessRequestError as err:
        raise HTTPException(status_code=400, detail=str(err))

    # 通知管理员与写审计都在统一服务里做了：这里再写一遍，管理员会收到两条同样的
    # 提醒、审计里也会出现两条 request_access。
    return {"code": 0, "data": {"id": row.id, "status": row.status}, "message": "申请已提交，等待审批"}


@router.get("/access-requests/mine")
async def list_my_access_requests(
    status: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """我的申请（个人中心）。

    数据来自统一申请服务：知识库与数据集共用一条链路，这里只做「知识库视角」的
    过滤与字段映射，保证旧前端不改也能继续用。
    """
    items = await KnowledgeAccessRequestService(db).list_mine(
        int(user["user_id"]), resource_type="dataset"
    )
    target = (status or "").strip().lower()
    if target:
        items = [item for item in items if item["status"] == target]
    return {
        "code": 0,
        "data": {"items": [_legacy_access_request_item(item) for item in items]},
    }


@router.get("/access-requests/pending")
async def list_pending_access_requests(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """待审批。审批人统一为管理员——归属人不再有审批权，理由见 approve 端点。"""
    _require_admin_reviewer(user)
    items, _total = await KnowledgeAccessRequestService(db).list_pending(
        resource_type="dataset", page=1, size=500
    )
    return {
        "code": 0,
        "data": {
            "items": [_legacy_access_request_item(item) for item in items],
            "total": len(items),
        },
    }


@router.get("/access-requests/reviews")
async def list_reviewed_access_requests(
    status: Optional[str] = None,
    size: int = 50,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """审批记录。审批通过后记录会从「待审批」消失，没有这个视图就只看得见待办。"""
    _require_admin_reviewer(user)
    items, _total = await KnowledgeAccessRequestService(db).list_reviews(
        resource_type="dataset", page=1, size=min(200, max(1, int(size or 50)))
    )
    target = (status or "").strip().lower()
    if target in (STATUS_APPROVED, STATUS_REJECTED):
        items = [item for item in items if item["status"] == target]
    return {
        "code": 0,
        "data": {
            "items": [_legacy_access_request_item(item) for item in items],
            "total": len(items),
        },
    }


class AccessReviewBody(BaseModel):
    comment: Optional[str] = Field(default=None, max_length=2000)


def _legacy_access_request_item(item: dict) -> dict:
    """统一服务结构 → 旧前端结构。

    这是兼容层：新前端走 ``/api/portal/access-requests/*``，旧前端（用户管理页、
    个人中心）还在读这里的字段。已授权库的规模统计不再实时查引擎（那会给列表
    接口加一次 RAGFlow 调用），旧前端缺失时显示「—」。
    """
    return {
        "id": item["id"],
        "dataset_id": item["resource_id"],
        "dataset_name": item["resource_name"],
        "applicant_user_id": item.get("applicant_user_id"),
        "applicant_user_name": item.get("applicant_user_name"),
        "reason": item.get("reason"),
        "status": item.get("status"),
        "reviewer_user_name": item.get("reviewer_user_name"),
        "review_comment": item.get("review_comment"),
        "created_at": item.get("created_at"),
        "reviewed_at": item.get("reviewed_at"),
        "visibility": item.get("visibility"),
        "owner": item.get("owner"),
        "doc_count": item.get("doc_count"),
        "chunk_count": item.get("chunk_count"),
        "dataset_exists": item.get("resource_exists"),
        # 数据集侧字段一并带上：旧端点目前只转发知识库申请，
        # 但统一服务返回的结构里两类资源都在，少映射一半会让调试时看不出差异
        "resource_type": item.get("resource_type"),
        "data_source": item.get("data_source"),
        "table_count": item.get("table_count"),
        "metric_count": item.get("metric_count"),
        "quality_score": item.get("quality_score"),
    }


def _require_admin_reviewer(user: dict) -> None:
    """审批人统一为管理员。

    知识库原本允许「库归属人」审批，但归属人未必有用户管理菜单权限，把入口放在
    用户管理页会让人被路由守卫挡在外面；两类资源各有一个审批人集合也会让「统一
    审核」名不副实。这里收口，旧接口也不再给归属人留后门。
    """
    if str(user.get("role") or "") != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以审批资源访问申请")


@router.post("/access-requests/{request_id}/approve")
async def approve_access_request(
    request_id: int,
    payload: AccessReviewBody,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """通过申请。

    清缓存、发通知、写审计都在 ``ResourceAccessRequestService.decide`` 里做——
    这里再写一遍会让申请人收到两条通知、审计里出现两条 grant。
    """
    _require_admin_reviewer(user)
    try:
        decided = await KnowledgeAccessRequestService(db).decide(
            request_id=request_id, reviewer=user, approve=True, comment=payload.comment
        )
    except AccessRequestError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return {
        "code": 0,
        "data": {"id": decided.id, "status": decided.status},
        "message": "已通过，对方现在可以访问",
    }


@router.post("/access-requests/{request_id}/reject")
async def reject_access_request(
    request_id: int,
    payload: AccessReviewBody,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    _require_admin_reviewer(user)
    try:
        decided = await KnowledgeAccessRequestService(db).decide(
            request_id=request_id, reviewer=user, approve=False, comment=payload.comment
        )
    except AccessRequestError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return {"code": 0, "data": {"id": decided.id, "status": decided.status}, "message": "已驳回"}


@router.post("/access-requests/{request_id}/cancel")
async def cancel_access_request(
    request_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        row = await KnowledgeAccessRequestService(db).cancel(request_id, user)
    except AccessRequestError as err:
        raise HTTPException(status_code=400, detail=str(err))
    return {"code": 0, "data": {"id": row.id, "status": row.status}, "message": "已撤回申请"}


async def _require_knowledge_admin(user: dict, db: AsyncSession) -> None:
    access = await PermissionService(db).get_knowledge_base_access(
        int(user["user_id"]), user.get("user_name")
    )
    if not access.get("is_admin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")


@router.post("/health/scan")
async def scan_knowledge_health(
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """跑一次巡检并把结果落成当天快照（管理员）。

    重复执行是覆盖当天快照，不会堆重复行——巡检本来就可能手动补跑。
    """
    started = time.time()
    await _require_knowledge_admin(user, db)
    data = await KnowledgeHealthService.scan(db)
    await record_knowledge_audit(request, user, "datasets:health_scan", 200, {
        "action": "scan_health",
        "summary": {key: data.get(key) for key in ("total", "missing", "empty", "failing", "stale")},
    }, started_at=started)
    return {"code": 0, "data": data, "message": "巡检完成"}


@router.get("/health")
async def get_knowledge_health(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """最近一次巡检结果（治理视图）。"""
    await _require_knowledge_admin(user, db)
    return {"code": 0, "data": await KnowledgeHealthService.latest(db)}


@router.get("/retrieval-stats")
async def get_retrieval_stats(
    days: int = 7,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """检索效果指标（管理员）：量、零结果率、P95 延迟、搜不到的 query。

    零结果率是判断「知识库覆盖到哪」最直接的信号——只命中数看不出问题，
    因为命中 1 条和零结果在平均值里差别不大。
    """
    service = PermissionService(db)
    access = await service.get_knowledge_base_access(
        int(user["user_id"]), user.get("user_name")
    )
    if not access.get("is_admin"):
        raise HTTPException(status_code=403, detail="需要管理员权限才能查看检索指标")

    if start_date or end_date:
        # 与 /metrics/summary 共用同一个上限，避免页面上两块卡片能查的范围不一致
        from app.services.knowledge_metrics_service import MAX_RANGE_DAYS

        if not (start_date and end_date):
            raise HTTPException(status_code=400, detail="start_date 与 end_date 必须同时提供")
        try:
            start_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
            end_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="start_date/end_date 必须为 YYYY-MM-DD 格式")
        if start_dt > end_dt:
            raise HTTPException(status_code=400, detail="start_date 不能晚于 end_date")
        if (end_dt - start_dt).days + 1 > MAX_RANGE_DAYS:
            raise HTTPException(
                status_code=400,
                detail=f"查询区间不能超过 {MAX_RANGE_DAYS} 天",
            )
        data = await KnowledgeRetrievalLogService.stats(
            db, start_date=start_date, end_date=end_date, source=source
        )
    else:
        data = await KnowledgeRetrievalLogService.stats(db, days=days, source=source)
    return {"code": 0, "data": data}


@router.get("/datasets/quota/me")
async def get_my_dataset_quota(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """我的知识库配额（与具体某个库无关）。

    左栏配额条和「新建知识库」的额度提示都只需要 owned_datasets：
    借某个库去查的话，那个库一旦失联或被删就拿不到数，所以单开一个入口。
    上限取自配置 knowledge_quota_max_datasets_per_user。
    """
    limits = await KnowledgeQuotaService.limits()
    used = await KnowledgeQuotaService.owned_dataset_count(db, int(user["user_id"]))
    return {
        "code": 0,
        "data": {
            "owned_datasets": {
                "used": used,
                "limit": limits["max_datasets_per_user"],
            },
            "documents": {
                "limit": limits["max_documents_per_dataset"],
            },
        },
    }


@router.post("/datasets/{dataset_id}/visit")
async def record_dataset_visit(
    dataset_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """记一次「最近访问」。

    由前端进入某个库时上报一次；Redis 里按人留 30 天，
    不做去抖是因为 ZSET 的 zadd 本身就是覆盖写，同一库重复访问只更新时间戳。
    """
    await require_dataset_access(user, db, [dataset_id])
    await KnowledgeRecentVisitService.record(user["user_id"], dataset_id)
    return {"code": 0, "data": {"visited": True}}


@router.get("/datasets/recent")
async def list_recent_datasets(
    limit: int = 20,
    user: dict = Depends(get_current_user),
):
    """我最近访问过的库（按时间倒序，只回 id）。

    列表项本身仍由 ``GET /datasets`` 提供：这里只负责顺序，
    避免两套接口各算一次可见范围，出现「最近里有、列表里没有」的库。
    """
    payload = await KnowledgeRecentVisitService.snapshot(user["user_id"], limit=limit)
    return {"code": 0, "data": payload}


@router.post("/datasets/{dataset_id}/favorite")
async def favorite_dataset(
    dataset_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """收藏知识库。

    只要求「可读」：收藏是个人视图，不额外授权，也不改变别人的可见范围。
    重复收藏幂等（唯一键 uix_kbf_user_dataset 兜底）。
    """
    from sqlalchemy import select

    await require_dataset_access(user, db, [dataset_id])
    user_id = int(user["user_id"])
    exists = (
        await db.execute(
            select(KnowledgeBaseFavorite.id).where(
                KnowledgeBaseFavorite.user_id == user_id,
                KnowledgeBaseFavorite.dataset_id == dataset_id,
            )
        )
    ).scalar()
    if not exists:
        db.add(KnowledgeBaseFavorite(user_id=user_id, dataset_id=dataset_id))
        await db.commit()
    return {"code": 0, "data": {"favorited": True}}


@router.delete("/datasets/{dataset_id}/favorite")
async def unfavorite_dataset(
    dataset_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """取消收藏。没收藏过也返回成功，前端只看结果状态。"""
    from sqlalchemy import delete

    await require_dataset_access(user, db, [dataset_id])
    await db.execute(
        delete(KnowledgeBaseFavorite).where(
            KnowledgeBaseFavorite.user_id == int(user["user_id"]),
            KnowledgeBaseFavorite.dataset_id == dataset_id,
        )
    )
    await db.commit()
    return {"code": 0, "data": {"favorited": False}}


@router.get("/datasets/{dataset_id}/quota")
async def get_dataset_quota(
    dataset_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """当前用量与上限（详情页显示「文档 312 / 2000」）。

    与上传同口径：只要求可读，成员也应该知道这个库还剩多少空间。
    """
    await require_dataset_access(user, db, [dataset_id])
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    try:
        data = await KnowledgeQuotaService.snapshot(
            client, dataset_id, db=db, user_id=int(user["user_id"])
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"读取配额失败：{e}")
    return {"code": 0, "data": data}


@router.get("/datasets/{dataset_id}/agents")
async def list_dataset_agents(
    dataset_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """该知识库当前被哪些智能体引用（删除/收窄可见范围前的影响面提示）。

    只要求「可读」：被授权进来的成员也应该知道自己的库喂给了哪些智能体，
    这不是敏感信息，不需要管理级权限。
    """
    await require_dataset_access(user, db, [dataset_id])
    grouped = await KnowledgeReferenceService(db).agents_by_dataset([dataset_id])
    agents = grouped.get(dataset_id, [])
    return {
        "code": 0,
        "data": {
            "total": len(agents),
            "enabled_total": sum(1 for agent in agents if agent["is_enabled"]),
            "items": agents,
        },
    }


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
        # 必须取全集：只拉前 500 个时，排在后面的库永远同步不到元数据表
        from app.services.ai.knowledge_utils import load_all_ragflow_datasets

        datasets = await load_all_ragflow_datasets(client)
        result = await metadata_service.sync_from_ragflow_datasets(
            datasets,
            user_name=user.get("user_name"),
        )
        touched_ids = result.get("dataset_ids") or []
        audit_detail = {
            "action": "sync_datasets_from_ragflow",
            "created": result["created"],
            "updated": result["updated"],
            "skipped": result["skipped"],
        }
        clear_dataset_list_cache()
        # 按受影响库拆条；一次都没同步到也留一条全局痕 —— 否则「点过同步、
        # 什么都没发生」与「根本没点」在事后无法区分。
        await record_knowledge_audit(
            request=request,
            user=user,
            action="datasets:sync",
            status_code=200,
            payload={
                "action": "sync_datasets_from_ragflow",
                "sync_result": audit_detail,
                "dataset_ids": touched_ids or [GLOBAL_AUDIT_DATASET_ID],
            },
            started_at=started,
        )
        return {"code": 0, "data": result, "message": "同步完成"}
    except Exception as e:
        await record_knowledge_audit(
            request=request,
            user=user,
            action="datasets:sync",
            status_code=_audit_status_code(e),
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
    try:
        await KnowledgeQuotaService.ensure_can_create_dataset(db, int(user["user_id"]))
    except QuotaExceeded as quota_err:
        raise HTTPException(status_code=400, detail=str(quota_err))
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    metadata_service = KnowledgeBaseMetadataService(db)

    # 平台侧先做唯一性校验：RAGFlow 允许多个同名 dataset，而本函数在「建库返回缺 id」
    # 时会按名字反查列表 —— 重名等于可能绑到别人的库，并把 owner_user_id 写成当前用户
    # （越权）。宁可在调用上游之前直接拒绝。
    exists = await metadata_service.find_active_by_name(payload.name)
    if exists:
        raise HTTPException(
            status_code=409,
            detail=f"知识库名称「{payload.name}」已存在，请换一个名称",
        )

    try:
        created = await client.create_dataset(
            name=payload.name,
            description=payload.description,
            chunk_method=payload.chunk_method,
        )
        dataset_id = created.get("id") or created.get("dataset_id")
        if not dataset_id:
            matched = await client.list_datasets(name=payload.name)
            matched_ids = [str(item.get("id")) for item in matched if item.get("id")]
            if len(matched_ids) == 1:
                dataset_id = matched_ids[0]
            else:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "RAGFlow 创建成功但未能确定 Dataset ID"
                        f"（同名候选 {len(matched_ids)} 个）。"
                        "请到 RAGFlow 核对后手动同步，避免元数据绑错库。"
                    ),
                )

        owner_text, owner_dept_id = await _resolve_owner_department(db, payload.owner_dept_id)
        record = await metadata_service.upsert_metadata(
            ragflow_dataset_id=dataset_id,
            name=payload.name,
            description=payload.description,
            # 选了部门就以部门名为准，未选部门才沿用调用方传入的文本（兼容旧前端）
            owner=owner_text if owner_dept_id else payload.owner,
            owner_dept_id=owner_dept_id,
            # 建库人就是归属人：此后交接走「转移归属」，不再依赖 created_by 文本
            owner_user_id=int(user["user_id"]),
            visibility=payload.visibility,
            tags=payload.tags,
            notes=payload.notes,
            extra_config=payload.extra_config,
            user_name=user.get("user_name"),
        )
        # 个人私有库不落创建人授权行：判定侧本来就"创建人自动可读"，而私有库里
        # 出现一条授权记录会让详情页显示"授权用户：创建人"，与"不支持分配授权"矛盾，
        # 还会让编辑弹窗误报"保存后 N 个授权对象将失去访问权"。
        if str(payload.visibility or "").strip().lower() != VISIBILITY_PRIVATE:
            await _grant_dataset_to_creator(db, user, dataset_id)
        clear_dataset_list_cache()
        await record_knowledge_audit(request, user, "datasets:create", 200, {
            "action": "create_dataset",
            "dataset_id": dataset_id,
            "name": payload.name,
        }, started_at=started)
        return {"code": 0, "data": {"ragflow": created, "metadata_id": record.id, "id": dataset_id}}
    except Exception as e:
        await record_knowledge_audit(
            request=request,
            user=user,
            action="datasets:create",
            status_code=_audit_status_code(e),
            payload={"action": "create_dataset", "name": payload.name},
            error_message=str(e),
            started_at=started,
        )
        raise


@router.put("/datasets/{dataset_id}/metadata")
async def update_dataset_metadata(
    dataset_id: str,
    payload: UpdateDatasetMetadataRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    started = time.time()
    await require_element_permission(user, db, "element:knowledge:edit")
    await require_dataset_write_access(user, db, [dataset_id])
    metadata_service = KnowledgeBaseMetadataService(db)

    # owner/owner_dept_id 是 PATCH 语义，必须区分「未提供」与「显式清空」：
    # 只有请求体里真的出现该字段才进入 kwargs，否则保持库里原值。
    owner_kwargs: Dict[str, Any] = {}
    if "owner_dept_id" in payload.model_fields_set:
        owner_text, owner_dept_id = await _resolve_owner_department(db, payload.owner_dept_id)
        owner_kwargs["owner_dept_id"] = owner_dept_id
        # 选了部门 → 文本快照按部门名回填；清空部门但没显式给 owner 文本时，
        # 保留历史自由文本（它在左侧树里归入「未关联部门」）。
        if owner_dept_id:
            owner_kwargs["owner"] = owner_text
        elif "owner" in payload.model_fields_set:
            owner_kwargs["owner"] = payload.owner
    elif "owner" in payload.model_fields_set:
        owner_kwargs["owner"] = payload.owner

    record = await metadata_service.update_metadata(
        dataset_id,
        name=payload.name,
        description=payload.description,
        visibility=payload.visibility,
        tags=payload.tags,
        notes=payload.notes,
        extra_config=payload.extra_config,
        user_name=user.get("user_name"),
        **owner_kwargs,
    )
    if not record:
        raise HTTPException(status_code=404, detail="Knowledge base metadata not found")

    # 快捷提问走键级写：``extra_config`` 里还躺着 folder_structure，而保存元数据的人
    # 手里只有打开弹窗时的快照——整份回写会把别人刚做的目录调整一并回滚。
    # 判断依据是「请求体里真的出现了这个字段」，不是「值非空」：清空快捷提问也是合法操作。
    if "custom_questions" in payload.model_fields_set:
        updated = await metadata_service.update_extra_config_key(
            dataset_id,
            key="custom_questions",
            value=payload.custom_questions or [],
            user_name=user.get("user_name"),
        )
        if updated:
            record = updated
    # 改可见范围会直接改变「谁能访问」：从 public 改 private 是撤权，反向是扩权，
    # 都必须留痕，否则事后无法解释"某个库怎么突然多/少了一批访问者"。
    clear_dataset_list_cache()
    await record_knowledge_audit(request, user, "datasets:update_metadata", 200, {
        "action": "update_metadata",
        "dataset_id": dataset_id,
        "visibility": record.visibility,
        "owner_dept_id": record.owner_dept_id,
    }, started_at=started)
    return {"code": 0, "data": metadata_service._serialize_metadata(record)}



@router.get("/datasets/{dataset_id}/edit-policy/candidates")
async def get_edit_policy_candidates(
    dataset_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """「指定成员可编辑」的候选用户。

    不能复用 ``/api/portal/metadata/candidates``：那个接口只认
    ``element:metadata:edit`` 或 ``element:user:edit``，而能配置编辑策略的人只需要
    ``element:knowledge:edit`` + 库的管理权。复用会让这类管理员打开弹窗时看到
    「没有可添加的用户」，看起来像功能坏了。
    """
    await require_element_permission(user, db, "element:knowledge:edit")
    await require_dataset_write_access(user, db, [dataset_id])

    from app.models.department import DEPARTMENT_STATUS_ENABLED, Department, UserDepartment
    from app.models.user import User
    from sqlalchemy import select

    user_rows = (
        await db.execute(
            select(User.id, User.user_name, User.real_name).where(User.status == 1)
        )
    ).all()
    dept_rows = (
        await db.execute(
            select(UserDepartment.user_id, Department.id, Department.name)
            .join(Department, Department.id == UserDepartment.dept_id)
            .where(Department.status == DEPARTMENT_STATUS_ENABLED)
            .order_by(Department.path)
        )
    ).all()
    depts_by_user: Dict[int, List[Dict[str, Any]]] = {}
    for uid, dept_id, dept_name in dept_rows:
        depts_by_user.setdefault(int(uid), []).append(
            {"id": int(dept_id), "name": str(dept_name or "")}
        )
    return {
        "code": 0,
        "data": {
            "users": [
                {
                    "id": row.id,
                    "user_name": row.user_name,
                    "real_name": row.real_name,
                    "departments": depts_by_user.get(int(row.id), []),
                }
                for row in user_rows
            ]
        },
    }


@router.put("/datasets/{dataset_id}/edit-policy")
async def update_dataset_edit_policy(
    dataset_id: str,
    payload: UpdateEditPolicyRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """设置「能打开这个库的人是否也能改文档」。

    权限：全局 ``element:knowledge:edit`` + 管理级写权限（管理员或归属人）。
    编辑权 ≠ 管理权：拿到编辑权的人改不了可见范围、授权名单，也删不了库。

    策略写进 ``extra_config.edit_policy``（只替换这一个子键），并由判定函数
    ``resolve_knowledge_dataset_access`` 在每次请求时现算，所以收窄策略立即生效。
    """
    started = time.time()
    await require_element_permission(user, db, "element:knowledge:edit")
    await require_dataset_write_access(user, db, [dataset_id])

    metadata_service = KnowledgeBaseMetadataService(db)
    record = await metadata_service.get_by_dataset_id(dataset_id)
    if not record or record.status == "deleted":
        raise HTTPException(status_code=404, detail="知识库不存在")

    visibility = normalize_knowledge_visibility(record.visibility)
    if visibility == VISIBILITY_PRIVATE:
        raise HTTPException(
            status_code=400,
            detail="个人私有库只有归属人可见，不支持设置编辑权限范围",
        )

    requested_mode = str(payload.mode or "").strip().lower()
    allowed = ALLOWED_EDIT_POLICY_MODES.get(visibility, ())
    if requested_mode not in allowed:
        raise HTTPException(
            status_code=422,
            detail="该可见范围不支持的编辑策略: %s（可选: %s）"
            % (requested_mode or "空", " / ".join(allowed)),
        )

    editors: List[Dict[str, Any]] = []
    if requested_mode == EDIT_POLICY_SELECTED:
        # 只认具体用户：类型不是 user 或缺少 ID 的一律拒掉，别静默丢弃 ——
        # 前端以为存上了「可编辑成员」，实际一个都没存是最糟的结果。
        seen: set[int] = set()
        for item in payload.editors:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "user").strip().lower() != "user":
                raise HTTPException(status_code=422, detail="编辑成员只支持具体用户，不支持角色")
            try:
                user_id = int(item.get("user_id"))
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="编辑成员缺少有效的 user_id")
            if user_id in seen:
                continue
            seen.add(user_id)
            editors.append(
                {
                    "type": "user",
                    "user_id": user_id,
                    "user_name": str(item.get("user_name") or ""),
                    "real_name": str(item.get("real_name") or ""),
                }
            )
        if not editors:
            raise HTTPException(status_code=422, detail="指定成员可编辑时至少要选一个成员")

    policy = {
        "mode": requested_mode,
        "editors": editors,
        "updated_by": str(user.get("user_name") or ""),
        "updated_at": datetime.datetime.now().isoformat(),
    }
    await metadata_service.update_extra_config_key(
        dataset_id,
        key="edit_policy",
        value=policy,
        user_name=user.get("user_name"),
    )
    clear_dataset_list_cache()

    await record_knowledge_audit(
        request,
        user,
        "datasets:update",
        200,
        {
            "action": "update_edit_policy",
            "dataset_id": dataset_id,
            "mode": requested_mode,
            "editors": [item["user_id"] for item in editors],
        },
        started_at=started,
    )
    return {"code": 0, "data": {"edit_policy": resolve_edit_policy_from_config({"edit_policy": policy}, visibility)}}

@router.put("/datasets/{dataset_id}/folder-structure")
async def update_folder_structure(
    dataset_id: str,
    payload: UpdateFolderStructureRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """只更新虚拟文件夹映射，不触碰名称/可见范围等治理字段。

    以前移动一个文档要走整份 ``PUT /metadata``：前端把缓存的 name/visibility
    一起回写，两个人同时操作就是 lost update，最坏情况是把公开库改成私有。
    """
    started = time.time()
    # 归类是「内容层面」的事，与上传/删除文档同级：部门成员能整理本部门的资料，
    # 却因为管理级门禁连一个文件夹都拖不进去，这条限制比它要保护的东西更碍事。
    # 元素权限仍要 edit——可见范围、归属这些治理字段还是管理级才动得了。
    await require_element_permission(user, db, "element:knowledge:edit")
    await require_dataset_content_write_access(user, db, [dataset_id])

    record = await KnowledgeBaseMetadataService(db).update_extra_config_key(
        dataset_id,
        key="folder_structure",
        value=payload.folder_structure,
        user_name=user.get("user_name"),
    )
    if not record:
        raise HTTPException(status_code=404, detail="知识库元数据不存在或已删除")

    await record_knowledge_audit(
        request=request,
        user=user,
        action="datasets:update_layout",
        status_code=200,
        payload={
            "action": "update_folder_structure",
            "dataset_id": dataset_id,
            "folder_count": len(payload.folder_structure),
            "document_count": sum(len(ids) for ids in payload.folder_structure.values()),
        },
        started_at=started,
    )
    return {"code": 0, "data": {"extra_config": record.extra_config or {}}}


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
            # 必须拿全集：漏掉的库会被判成「RAGFlow 侧已不存在」而跳过删除，
            # 于是只删了本地元数据、RAGFlow 里留下不再受管的孤儿库。
            # 以前这里写的是 list_datasets(page_size=1000)——注释要全集，代码却只要 10 页，
            # 数据集超过 1000 个之后排在后面的库就再也删不掉了。
            # refresh=True 是刻意的：刚建完就删的库不能在 60s 缓存里缺席。
            ragflow_datasets = await client.list_all_datasets_cached(refresh=True)
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

        # 收藏与「待审批」申请都挂在 dataset_id 上，库没了不清理就是脏数据：
        # 收藏表留下无效 id；待审批列表则永远挂着一条记录——管理员点「通过」会被
        # _ensure_resource_available 拒掉（库已不存在），只能手动驳回。
        from app.models.knowledge import KnowledgeAccessRequest, KnowledgeBaseFavorite
        from sqlalchemy import update as sa_update

        await db.execute(
            delete(KnowledgeBaseFavorite).where(KnowledgeBaseFavorite.dataset_id.in_(payload.ids))
        )
        await db.execute(
            sa_update(KnowledgeAccessRequest)
            .where(
                KnowledgeAccessRequest.dataset_id.in_(payload.ids),
                KnowledgeAccessRequest.status == STATUS_PENDING,
            )
            .values(
                status=STATUS_CANCELLED,
                review_comment="知识库已删除，申请自动作废",
                reviewed_at=datetime.datetime.now(),
            )
        )

        await metadata_service.mark_deleted(payload.ids, user_name=user.get("user_name"))
        clear_dataset_list_cache()
        # 原图与 md 快照是本地文件，RAGFlow 删库不会带它们走。
        # 放在 mark_deleted 之后：前面的步骤抛异常就不清，免得库还在、图先没了。
        for dataset_id in payload.ids:
            image_service.delete_dataset_images(dataset_id)
        await record_knowledge_audit(request, user, "datasets:delete", 200, {
            "action": "delete_dataset",
            "dataset_ids": payload.ids,
        }, started_at=started)
        return {"code": 0, "message": "删除成功"}
    except Exception as e:
        await record_knowledge_audit(request, user, "datasets:delete", _audit_status_code(e), {
            "action": "delete_dataset",
            "dataset_ids": payload.ids,
        }, error_message=str(e), started_at=started)
        raise


# --- 文档工作区：状态判定 / 虚拟文件夹 / 排序 ---

#: status_counts 的固定键集合（前端 chips 顺序依赖它，不能随数据多退少补）
DOC_STATUS_KEYS = ("parsed", "parsing", "unparsed", "failed")
#: 未归入任何虚拟文件夹的「伪文件夹」路径
UNCLASSIFIED_FOLDER = "__unclassified__"


def _as_text(value: Any) -> str:
    """把大模型返回的字段收敛成字符串。

    prompt 里要求 tags 是逗号分隔的字符串，但模型偶尔会输出 JSON 数组
    （``"tags": ["ERP", "手册"]``）——直接 ``.strip()`` 会 AttributeError，
    用户看到的是 500「AI 分析失败: 'list' object has no attribute 'strip'」。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple, set)):
        parts = [str(item).strip() for item in value]
        return ",".join(part for part in parts if part)
    return str(value).strip()


def _doc_status_of(doc: Any) -> str:
    """文档解析状态的唯一后端口径。

    必须与前端 ``frontend/src/utils/knowledgeDocStatus.ts`` 的 ``docStatusOf``
    完全一致，否则同一个文件在列表 chip 上「解析失败」、按 chip 过滤后却消失。
    RAGFlow 把状态放在 ``run``：0/UNSTART 未解析、1/DONE 完成、2/RUNNING 解析中、
    3/FAIL 失败；只有 ``run`` 完全缺失时才回落 ``status``——``status`` 里的
    0/1 表示启用/停用，当解析状态用会把「停用的文档」误判成「未解析」。
    """
    if not isinstance(doc, dict):
        return "unparsed"

    run = str(doc.get("run") if doc.get("run") is not None else "").upper()
    if run in ("RUNNING", "2"):
        return "parsing"
    if run in ("DONE", "1"):
        return "parsed"
    if run in ("FAIL", "3"):
        return "failed"
    if run in ("UNSTART", "0"):
        return "unparsed"

    if doc.get("run") is None:
        status = str(doc.get("status") if doc.get("status") is not None else "").upper()
        if status in ("COMPLETED", "PARSED"):
            return "parsed"
        if status in ("RUNNING", "PARSING"):
            return "parsing"
        if status == "FAILED":
            return "failed"
    return "unparsed"


def _doc_id_of(doc: Any) -> str:
    if not isinstance(doc, dict):
        return ""
    return str(doc.get("id") or doc.get("doc_id") or "")


def _split_folder_path(path: Any) -> List[str]:
    """`` a / b `` → ``["a", "b"]``，与前端 splitFolderPath 一致。"""
    return [segment.strip() for segment in str(path or "").split("/") if segment.strip()]


def _normalize_folder_path(path: Any) -> str:
    return "/".join(_split_folder_path(path))


async def _prune_folder_structure(
    db: AsyncSession,
    dataset_id: str,
    removed_ids: List[str],
    user_name: Optional[str] = None,
) -> None:
    """把已删除的文档从虚拟文件夹里摘掉。

    不清理的话 ``extra_config.folder_structure`` 只会增不会减：列表查询有
    「按真实 docId 取交集」的保护，界面上看不出来，但这份 JSON 会一直膨胀，
    前端每次读写的都是几十个失效 id。
    """
    if not removed_ids:
        return
    try:
        service = KnowledgeBaseMetadataService(db)
        record = await service.get_by_dataset_id(dataset_id)
        extra_config = getattr(record, "extra_config", None) if record else None
        structure = extra_config.get("folder_structure") if isinstance(extra_config, dict) else None
        if not isinstance(structure, dict) or not structure:
            return

        wanted = {str(item) for item in removed_ids}
        pruned: Dict[str, List[str]] = {}
        changed = False
        for path, doc_ids in structure.items():
            kept = [str(doc_id) for doc_id in (doc_ids or []) if str(doc_id) not in wanted]
            if len(kept) != len(doc_ids or []):
                changed = True
            pruned[path] = kept
        if not changed:
            return

        await service.update_extra_config_key(
            dataset_id,
            key="folder_structure",
            value=pruned,
            user_name=user_name,
        )
    except Exception as exc:  # noqa: BLE001 - 清理是旁路，不能挡住删除本身
        logger.warning("[Knowledge] 删除文档后清理文件夹映射失败: %s", exc)


def _build_folder_nodes(structure: Any) -> Dict[str, List[str]]:
    """把扁平的 ``folder_structure`` 展开成节点表（含按需补出的祖先路径）。

    与前端 ``buildFolderIndex`` 一致：key 里出现 ``A/B`` 时即使没有 ``A`` 这个
    key，也要补出 ``A`` 节点，否则二层文件夹会挂在一个不存在的父节点上。
    节点里的 docId 可能是已删除的残留，由调用方按实际文档过滤。
    """
    nodes: Dict[str, List[str]] = {}
    if not isinstance(structure, dict):
        return nodes

    for raw_path in structure:
        cursor = ""
        for segment in _split_folder_path(raw_path):
            cursor = f"{cursor}/{segment}" if cursor else segment
            nodes.setdefault(cursor, [])

    for raw_path, doc_ids in structure.items():
        path = _normalize_folder_path(raw_path)
        if not path:
            continue
        bucket = nodes.setdefault(path, [])
        if not isinstance(doc_ids, (list, tuple, set)):
            continue
        for doc_id in doc_ids:
            if doc_id:
                bucket.append(str(doc_id))
    return nodes


def _folder_scope_doc_ids(nodes: Dict[str, List[str]], path: str) -> set:
    """folder 的语义：路径等于它，或以 ``path + '/'`` 为前缀。

    所以选中 ``A`` 会命中放在 ``A/B`` 里的文档，这正是前端树形结构的含义。
    """
    prefix = f"{path}/"
    scoped: set = set()
    for node_path, doc_ids in nodes.items():
        if node_path == path or node_path.startswith(prefix):
            scoped.update(doc_ids)
    return scoped


def _all_folder_doc_ids(nodes: Dict[str, List[str]]) -> set:
    assigned: set = set()
    for doc_ids in nodes.values():
        assigned.update(doc_ids)
    return assigned


def _folder_counts(nodes: Dict[str, List[str]], existing_ids: set) -> Dict[str, int]:
    """每个目录（含子目录）下真实存在的文档数 + 未分类数量。"""
    counts: Dict[str, int] = {}
    for path in nodes:
        scoped = _folder_scope_doc_ids(nodes, path)
        counts[path] = len(scoped & existing_ids)
    counts[UNCLASSIFIED_FOLDER] = len(existing_ids - _all_folder_doc_ids(nodes))
    return counts


async def _load_folder_structure(db: AsyncSession, dataset_id: str) -> Dict[str, Any]:
    """读本地元数据里的 ``extra_config.folder_structure``。

    文件夹只是平台侧的组织视图（RAGFlow 不认识它），读失败就按「没有文件夹」
    处理——不能因为一个装饰性字段让整个文档列表 500。
    """
    try:
        record = await KnowledgeBaseMetadataService(db).get_by_dataset_id(dataset_id)
    except Exception as exc:  # noqa: BLE001 - 仅用于降级，记录后继续
        logger.warning("[Knowledge] 读取文件夹结构失败，按无文件夹处理: %s", exc)
        return {}
    extra_config = getattr(record, "extra_config", None) if record else None
    if not isinstance(extra_config, dict):
        return {}
    structure = extra_config.get("folder_structure")
    return structure if isinstance(structure, dict) else {}


def _doc_updated_at(doc: Dict[str, Any]) -> Optional[float]:
    for key in ("update_time", "create_time", "created_at"):
        ts = _to_epoch_seconds(doc.get(key))
        if ts is not None:
            return ts
    return None


def _sort_documents(
    docs: List[Dict[str, Any]], sort: Optional[str]
) -> List[Dict[str, Any]]:
    """updated（默认）/ name / size / chunks 排序。

    先按 id 排一遍做稳定基准：同值时顺序固定，否则跨页会出现「第 2 页又看到
    第 1 页的文档、还有几条永远翻不到」。
    """
    mode = (sort or "updated").strip().lower()
    ordered = sorted(docs, key=_doc_id_of)
    if mode == "name":
        ordered.sort(key=lambda doc: str(doc.get("name") or ""))
        return ordered
    if mode == "size":
        ordered.sort(key=lambda doc: _numeric_field(doc, "size"), reverse=True)
        return ordered
    if mode == "chunks":
        ordered.sort(
            key=lambda doc: _numeric_field(doc, "chunk_count", "chunks"), reverse=True
        )
        return ordered

    def _updated_key(doc: Dict[str, Any]) -> float:
        ts = _doc_updated_at(doc)
        # 缺时间的排最后：-inf 在 reverse=True 下自然沉底
        return float("-inf") if ts is None else ts

    ordered.sort(key=_updated_key, reverse=True)
    return ordered


@router.get("/datasets/{dataset_id}/documents")
async def list_dataset_documents(
    dataset_id: str,
    page: int = 1,
    page_size: int = 100,
    name: Optional[str] = None,
    status: Optional[str] = None,
    sort: Optional[str] = None,
    folder: Optional[str] = None,
    refresh: int = 0,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """文档列表：一次全量（60s 缓存）+ 内存过滤 / 排序 / 分页。

    RAGFlow 只支持 page/page_size 分页，过滤与排序都得由平台做；原来按页透传时
    ``total`` 恒等于「当前页条数」，跨页排序也不可能正确。现在改成全量拉取后
    在内存里处理，``total`` 是**过滤后**的真实总数，另带 ``all_total`` /
    ``status_counts`` / ``folders`` 供前端渲染 chips 与分页器。
    """
    await require_dataset_access(user, db, [dataset_id])
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    current_page = page if page and page > 0 else 1
    try:
        docs = await client.list_all_documents_cached(dataset_id, refresh=bool(refresh))
        all_total = len(docs)

        # folder_structure 只在平台侧：RAGFlow 不认识虚拟文件夹
        folder_structure = await _load_folder_structure(db, dataset_id)
        folder_nodes = _build_folder_nodes(folder_structure)

        working = list(docs)

        # 1) folder（路径前缀语义 / __unclassified__）
        normalized_folder = _normalize_folder_path(folder)
        if normalized_folder == UNCLASSIFIED_FOLDER:
            assigned_ids = _all_folder_doc_ids(folder_nodes)
            working = [doc for doc in working if _doc_id_of(doc) not in assigned_ids]
        elif normalized_folder:
            wanted_ids = _folder_scope_doc_ids(folder_nodes, normalized_folder)
            working = [doc for doc in working if _doc_id_of(doc) in wanted_ids]

        # 2) name
        name_text = (name or "").strip().lower()
        if name_text:
            working = [
                doc for doc in working if name_text in str(doc.get("name") or "").lower()
            ]

        # 3) status_counts：在 folder + name 之后、status 之前统计，
        #    否则选了「解析失败」后其它状态 chip 的计数会自己缩水到 0
        status_counts = {key: 0 for key in DOC_STATUS_KEYS}
        for doc in working:
            doc_status = _doc_status_of(doc)
            status_counts[doc_status] = status_counts.get(doc_status, 0) + 1

        # 4) status（非法值忽略）
        status_value = (status or "").strip().lower() or None
        if status_value in status_counts:
            working = [doc for doc in working if _doc_status_of(doc) == status_value]

        filtered = _sort_documents(working, sort)

        # folders：只统计当前全量里真实存在的 docId，folder_structure 为空时返回 {}
        folder_counts: Dict[str, int] = {}
        if folder_nodes:
            existing_ids = {_doc_id_of(doc) for doc in docs if _doc_id_of(doc)}
            folder_counts = _folder_counts(folder_nodes, existing_ids)

        total = len(filtered)
        if page_size and page_size > 0:
            start = (current_page - 1) * page_size
            page_docs = filtered[start:start + page_size]
        else:
            page_docs = filtered

        return {
            "code": 0,
            "data": page_docs,
            "total": total,
            "all_total": all_total,
            "status_counts": status_counts,
            "page": current_page,
            "page_size": page_size,
            "folders": folder_counts,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CreateChunkRequest(BaseModel):
    content: str
    important_keywords: Optional[List[str]] = None


class UpdateChunkRequest(BaseModel):
    content: Optional[str] = None
    available: Optional[bool] = None
    important_keywords: Optional[List[str]] = None


def _chunk_content_preview(content: Optional[str]) -> str:
    """审计只留前 120 字：切片正文动辄上千字，整段进日志表会很快撑爆。"""
    return (content or "")[:120]


@router.post("/datasets/{dataset_id}/documents/{document_id}/chunks")
async def create_document_chunk(
    dataset_id: str,
    document_id: str,
    payload: CreateChunkRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """手工新增一条切片（把 QA、笔记之类补进知识库）。"""
    started = time.time()
    await require_element_permission(user, db, "element:knowledge:parse_document")
    await require_dataset_content_write_access(user, db, [dataset_id])

    content = (payload.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="切片内容不能为空")

    client = RagFlowClient(config_prefix="knowledge_ragflow")
    try:
        created = await client.create_chunk(
            dataset_id,
            document_id,
            content,
            important_keywords=payload.important_keywords,
        )
        await record_knowledge_audit(request, user, "chunks:create", 200, {
            "action": "create_chunk",
            "dataset_id": dataset_id,
            "document_id": document_id,
            "chunk_id": str((created or {}).get("id") or ""),
            "content_preview": _chunk_content_preview(content),
        }, started_at=started)
        return {"code": 0, "message": "已新增切片，向量重建需数秒", "data": created}
    except Exception as e:
        await record_knowledge_audit(request, user, "chunks:create", _audit_status_code(e), {
            "action": "create_chunk",
            "dataset_id": dataset_id,
            "document_id": document_id,
            "content_preview": _chunk_content_preview(content),
        }, error_message=str(e), started_at=started)
        raise


@router.put("/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}")
async def update_document_chunk(
    dataset_id: str,
    document_id: str,
    chunk_id: str,
    payload: UpdateChunkRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """改切片内容或启停。停用后切片保留，但不参与召回。"""
    started = time.time()
    await require_element_permission(user, db, "element:knowledge:parse_document")
    await require_dataset_content_write_access(user, db, [dataset_id])

    content = payload.content.strip() if payload.content is not None else None
    if content is not None and not content:
        raise HTTPException(status_code=400, detail="切片内容不能为空")
    if content is None and payload.available is None and payload.important_keywords is None:
        raise HTTPException(
            status_code=400,
            detail="没有要更新的字段（content / available / important_keywords 至少给一个）",
        )

    audit_detail = {
        "action": "toggle_chunk" if content is None and payload.available is not None else "update_chunk",
        "dataset_id": dataset_id,
        "document_id": document_id,
        "chunk_id": chunk_id,
        "available": payload.available,
        "content_preview": _chunk_content_preview(content),
    }
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    try:
        updated = await client.update_chunk(
            dataset_id,
            document_id,
            chunk_id,
            content=content,
            available=payload.available,
            important_keywords=payload.important_keywords,
        )
        await record_knowledge_audit(
            request, user, "chunks:update", 200, dict(audit_detail), started_at=started
        )
        return {"code": 0, "message": "已更新，向量重建需数秒", "data": updated}
    except Exception as e:
        await record_knowledge_audit(
            request,
            user,
            "chunks:update",
            _audit_status_code(e),
            dict(audit_detail),
            error_message=str(e),
            started_at=started,
        )
        raise


@router.delete("/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}")
async def delete_document_chunk(
    dataset_id: str,
    document_id: str,
    chunk_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    started = time.time()
    await require_element_permission(user, db, "element:knowledge:parse_document")
    await require_dataset_content_write_access(user, db, [dataset_id])

    client = RagFlowClient(config_prefix="knowledge_ragflow")
    try:
        await client.delete_chunk(dataset_id, document_id, chunk_id)
        await record_knowledge_audit(request, user, "chunks:delete", 200, {
            "action": "delete_chunk",
            "dataset_id": dataset_id,
            "document_id": document_id,
            "chunk_id": chunk_id,
        }, started_at=started)
        return {"code": 0, "message": "已删除切片"}
    except Exception as e:
        await record_knowledge_audit(request, user, "chunks:delete", _audit_status_code(e), {
            "action": "delete_chunk",
            "dataset_id": dataset_id,
            "document_id": document_id,
            "chunk_id": chunk_id,
        }, error_message=str(e), started_at=started)
        raise


@router.get("/datasets/{dataset_id}/documents/{document_id}/chunks")
async def list_document_chunks(
    dataset_id: str,
    document_id: str,
    page: int = 1,
    page_size: int = 30,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    # 内容级：部门成员也能看切片明细（原先要求管理级，等于逼他们靠全文猜测）
    await require_dataset_content_write_access(user, db, [dataset_id])
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
    await require_dataset_content_write_access(user, db, [dataset_id])
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    file_name = file.filename or "upload.bin"
    try:
        _validate_knowledge_upload(file_name, int(getattr(file, "size", 0) or 0))
        await KnowledgeQuotaService.ensure_can_upload(client, dataset_id, incoming=1)
        blob = await _read_upload_with_limit(file)
        uploaded = await client.upload_document(
            dataset_id,
            file_name,
            blob,
            file.content_type or "application/octet-stream",
        )
        await record_knowledge_audit(request, user, "documents:upload", 200, {
            "action": "upload_document",
            "dataset_id": dataset_id,
            "file_name": file_name,
            "content_type": file.content_type,
            "size": len(blob),
        }, started_at=started)
        return {"code": 0, "data": uploaded}
    except QuotaExceeded as quota_err:
        # 配额不足是用户可自行处理的问题（清文档 / 找管理员），不该混进 500
        await record_knowledge_audit(request, user, "documents:upload", 400, {
            "action": "upload_document",
            "dataset_id": dataset_id,
            "file_name": file_name,
        }, error_message=str(quota_err), started_at=started)
        raise HTTPException(status_code=400, detail=str(quota_err))
    except Exception as e:
        await record_knowledge_audit(request, user, "documents:upload", _audit_status_code(e), {
            "action": "upload_document",
            "dataset_id": dataset_id,
            "file_name": file.filename,
        }, error_message=str(e), started_at=started)
        raise


@router.post("/datasets/{dataset_id}/documents/replace")
async def replace_dataset_document(
    dataset_id: str,
    request: Request,
    file: UploadFile = File(...),
    old_document_id: str = Form(...),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """同名上传的「替换」：先上传新文件，改名回旧名，再删旧文档，最后触发解析。

    反向做（先删后传）一旦上传失败，用户就直接失去文档；而删旧失败必须回滚
    掉新文件，否则库里同时躺着两个同名文档。
    """
    started = time.time()
    await require_element_permission(user, db, "element:knowledge:upload_document")
    await require_element_permission(user, db, "element:knowledge:delete_document")
    await require_dataset_content_write_access(user, db, [dataset_id])

    file_name = file.filename or "upload.bin"
    audit_detail = {
        "action": "replace_document",
        "dataset_id": dataset_id,
        "document_id": old_document_id,
        "file_name": file_name,
    }
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    new_document_id: Optional[str] = None
    old_deleted = False
    try:
        _validate_knowledge_upload(file_name, int(getattr(file, "size", 0) or 0))
        blob = await _read_upload_with_limit(file)

        # 旧文档必须属于本库：否则等于借「替换」删掉别的库（或已不存在）的文档
        existing = await client.list_documents(dataset_id, page_size=1000)
        old_doc = next(
            (doc for doc in existing if str(doc.get("id")) == old_document_id), None
        )
        if old_doc is None:
            raise HTTPException(status_code=404, detail="要替换的文档不存在或已被删除")

        uploaded = await client.upload_document(
            dataset_id, file_name, blob, file.content_type or "application/octet-stream"
        )
        new_document_id = str(uploaded.get("id") or "") or None
        if not new_document_id:
            raise HTTPException(status_code=502, detail="RAGFlow 上传成功但未返回文档 ID")

        # 同名上传时 RAGFlow 可能把新文件存成 "xxx (1).pdf"，改回原名再删旧
        await client.update_document(
            dataset_id, new_document_id, name=old_doc.get("name") or file_name
        )

        await client.delete_documents(dataset_id, [old_document_id])
        old_deleted = True

        try:
            await client.parse_documents(dataset_id, [new_document_id])
        except Exception as parse_err:
            # 旧文档已删、新文档已就位：此时再删新的等于两个都没，只能留给用户重试
            logger.warning(
                "[Knowledge] 替换后触发解析失败 doc=%s: %s", new_document_id, parse_err
            )

        await record_knowledge_audit(
            request,
            user,
            "documents:replace",
            200,
            {
                **audit_detail,
                "old_document_id": old_document_id,
                "new_document_id": new_document_id,
            },
            started_at=started,
        )
        return {"code": 0, "message": "已替换", "data": {"id": new_document_id}}
    except Exception as e:
        # 只在旧文档还没删时回滚：否则会把用户仅存的文档也删掉
        if new_document_id and not old_deleted:
            try:
                await client.delete_documents(dataset_id, [new_document_id])
            except Exception as rollback_err:
                logger.error(
                    "[Knowledge] 替换回滚失败，残留文档 %s: %s", new_document_id, rollback_err
                )
        await record_knowledge_audit(
            request,
            user,
            "documents:replace",
            _audit_status_code(e),
            dict(audit_detail),
            error_message=str(e),
            started_at=started,
        )
        raise


# ── 图片 → Markdown ──────────────────────────────────────────────────────
# 图片不走 RAGFlow 的图片解析（当前部署没有可用的视觉链路），而是先用系统配置的
# 多模态模型读成 md，再把 md 当普通文档入库。原图只留平台附件，用于事后核对。

@router.get("/datasets/{dataset_id}/images/source-info/{md_filename}")
async def get_dataset_image_source_info(
    dataset_id: str,
    md_filename: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """文档详情里「查看原图」的探测。

    前端拿到 ``exists: true`` 才渲染入口，避免点开是裂图或 404。
    入参是 **md 文件名**（RAGFlow 里的文档名），原图名靠哈希尾巴反查。
    返回的 ``url`` 交给 ``<img src>``：同源请求会带 cookie，鉴权仍由下载端点负责。
    """
    await require_dataset_access(user, db, [dataset_id])
    path = image_service.source_path_for_markdown(dataset_id, md_filename)
    if not path:
        return {"code": 0, "data": {"exists": False}}
    name = os.path.basename(path)
    try:
        size = os.path.getsize(path)
    except OSError:
        return {"code": 0, "data": {"exists": False}}
    return {
        "code": 0,
        "data": {
            "exists": True,
            "name": name,
            "size": size,
            "content_type": image_service.mime_for_extension(name),
            "url": f"/api/portal/ragflow/datasets/{dataset_id}/images/source/{name}",
        },
    }


@router.get("/datasets/{dataset_id}/images/source/{file_name}")
async def get_dataset_image_source(
    dataset_id: str,
    file_name: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """按库读权限取原图。

    原图目录已经从 ``data/uploads`` 挪走，不再被 ``/static/uploads`` 无鉴权托管；
    要拿到图片只能经过这里的 ``require_dataset_access``。
    """
    await require_dataset_access(user, db, [dataset_id])
    path = image_service.absolute_source_path(dataset_id, file_name)
    if not path:
        raise HTTPException(status_code=404, detail="原图不存在")
    return FileResponse(
        path,
        media_type=image_service.mime_for_extension(file_name),
        headers={"Cache-Control": "private, max-age=3600"},
    )


class ImageCommitPayload(BaseModel):
    """确认入库的 md 正文。

    文件名与正文都由前端回传：解析和确认之间用户可能改过内容，服务端不做二次生成。
    """

    md_filename: str = Field(..., min_length=1, max_length=200)
    markdown: str = Field(..., min_length=1, max_length=2_000_000)


@router.get("/knowledge-images/vision-model")
async def get_vision_model_status(user: dict = Depends(get_current_user)):
    """前端据此决定「图片」tab 是否可用，以及置灰时给什么引导。"""
    model = await resolve_default_multimodal_model_name()
    return {"code": 0, "data": {"ready": bool(model), "model": model}}


@router.post("/datasets/{dataset_id}/images/parse")
async def parse_dataset_image(
    dataset_id: str,
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """单张图片 → Markdown。只返回结果，不入库、不占配额。"""
    started = time.time()
    await require_element_permission(user, db, "element:knowledge:upload_document")
    await require_dataset_content_write_access(user, db, [dataset_id])

    file_name = file.filename or "image.png"
    if not image_service.is_supported_image(file_name):
        raise HTTPException(
            status_code=415,
            detail=(
                f"不支持的图片格式「{image_service.image_extension(file_name) or '无扩展名'}」，"
                "支持 PNG/JPG/WEBP"
            ),
        )

    model = await resolve_default_multimodal_model_name()
    if not model:
        raise HTTPException(
            status_code=400,
            detail=(
                "系统还没有配置可用的默认多模态模型，图片无法解析。"
                "请管理员在「系统配置 · 模型配置」里指定 multimodal/vision 类型的模型。"
            ),
        )

    blob = await _read_upload_with_limit(
        file,
        limit_bytes=image_service.MAX_IMAGE_BYTES,
        limit_label=f"{image_service.MAX_IMAGE_MB}MB",
    )
    image_service.save_source_image(dataset_id, file_name, blob)

    try:
        result = await image_service.image_to_markdown(
            dataset_id=dataset_id, file_name=file_name, blob=blob, model=model
        )
    except Exception as e:
        await record_knowledge_audit(request, user, "documents:upload", 502, {
            "action": "parse_image",
            "dataset_id": dataset_id,
            "file_name": file_name,
        }, error_message=str(e), started_at=started)
        raise HTTPException(status_code=502, detail=f"图片解析失败：{e}")

    await record_knowledge_audit(request, user, "documents:upload", 200, {
        "action": "parse_image",
        "dataset_id": dataset_id,
        "file_name": file_name,
        "model": model,
        "char_count": result["char_count"],
    }, started_at=started)
    return {"code": 0, "data": result}


@router.post("/datasets/{dataset_id}/images/commit")
async def commit_dataset_image_markdown(
    dataset_id: str,
    payload: ImageCommitPayload,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """把用户确认后的 md 当普通文档上传，走与文档完全相同的入库链路。"""
    started = time.time()
    await require_element_permission(user, db, "element:knowledge:upload_document")
    await require_dataset_content_write_access(user, db, [dataset_id])

    md_filename = os.path.basename(payload.md_filename or "").strip()
    if not md_filename.lower().endswith(".md"):
        raise HTTPException(status_code=400, detail="文件名必须以 .md 结尾")

    client = RagFlowClient(config_prefix="knowledge_ragflow")
    try:
        await KnowledgeQuotaService.ensure_can_upload(client, dataset_id, incoming=1)
        uploaded = await client.upload_document(
            dataset_id,
            md_filename,
            payload.markdown.encode("utf-8"),
            "text/markdown",
        )
        await record_knowledge_audit(request, user, "documents:upload", 200, {
            "action": "upload_image_markdown",
            "dataset_id": dataset_id,
            "file_name": md_filename,
            "char_count": len(payload.markdown),
        }, started_at=started)
        return {"code": 0, "data": uploaded}
    except QuotaExceeded as quota_err:
        await record_knowledge_audit(request, user, "documents:upload", 400, {
            "action": "upload_image_markdown",
            "dataset_id": dataset_id,
            "file_name": md_filename,
        }, error_message=str(quota_err), started_at=started)
        raise HTTPException(status_code=400, detail=str(quota_err))
    except Exception as e:
        await record_knowledge_audit(request, user, "documents:upload", _audit_status_code(e), {
            "action": "upload_image_markdown",
            "dataset_id": dataset_id,
            "file_name": md_filename,
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
    await require_dataset_content_write_access(user, db, [dataset_id])
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No document ids provided")
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    # 删除接口只吃 document_id，返回的也没有文件名，所以先查一次名字：
    # 图片转来的文档靠「原图与 md 同名（仅后缀不同）」反查本地原图。
    # 查不到就跳过清理，不能让一次列表失败挡住删除本身。
    try:
        listed = await client.list_documents(dataset_id, page=1, page_size=1000)
        wanted = set(payload.ids)
        names_by_id = {
            str(doc.get("id")): doc.get("name")
            for doc in (listed or [])
            if str(doc.get("id")) in wanted and doc.get("name")
        }
    except Exception as exc:  # noqa: BLE001 - 清原图是旁路
        logger.warning("删除文档前取文件名失败（跳过原图清理）: %s", exc)
        names_by_id = {}
    try:
        await client.delete_documents(dataset_id, payload.ids)
        for name in names_by_id.values():
            image_service.delete_source_for_markdown(dataset_id, name)
        await _prune_folder_structure(db, dataset_id, payload.ids, user.get("user_name"))
        await record_knowledge_audit(request, user, "documents:delete", 200, {
            "action": "delete_document",
            "dataset_id": dataset_id,
            "document_ids": payload.ids,
        }, started_at=started)
        return {"code": 0, "message": "删除成功"}
    except Exception as e:
        await record_knowledge_audit(request, user, "documents:delete", _audit_status_code(e), {
            "action": "delete_document",
            "dataset_id": dataset_id,
            "document_ids": payload.ids,
        }, error_message=str(e), started_at=started)
        raise


class UpdateDocumentRequest(BaseModel):
    """只传要改的字段：None 表示"不动"，避免只停用文档时把名字一并重置。"""

    name: Optional[str] = None
    enabled: Optional[bool] = None


@router.put("/datasets/{dataset_id}/documents/{document_id}")
async def update_dataset_document(
    dataset_id: str,
    document_id: str,
    payload: UpdateDocumentRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """改名 / 启停文档。停用后文档仍保留切片，只是不再参与召回。"""
    started = time.time()
    await require_element_permission(user, db, "element:knowledge:parse_document")
    await require_dataset_content_write_access(user, db, [dataset_id])

    name = payload.name.strip() if payload.name is not None else None
    if name is not None and not name:
        raise HTTPException(status_code=400, detail="文档名称不能为空")
    if name is not None and len(name) > 255:
        raise HTTPException(status_code=400, detail="文档名称过长（最多 255 个字符）")
    if name is None and payload.enabled is None:
        raise HTTPException(
            status_code=400,
            detail="没有要更新的字段（name / enabled 至少给一个）",
        )

    audit_detail = {
        "action": "update_document" if payload.enabled is None else "toggle_document",
        "dataset_id": dataset_id,
        "document_id": document_id,
        "name": name,
        "enabled": payload.enabled,
    }
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    try:
        updated = await client.update_document(
            dataset_id, document_id, name=name, enabled=payload.enabled
        )
        await record_knowledge_audit(
            request, user, "documents:update", 200, dict(audit_detail), started_at=started
        )
        return {"code": 0, "message": "已更新", "data": updated}
    except Exception as e:
        await record_knowledge_audit(
            request,
            user,
            "documents:update",
            _audit_status_code(e),
            dict(audit_detail),
            error_message=str(e),
            started_at=started,
        )
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
    await require_dataset_content_write_access(user, db, [dataset_id])
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
        await record_knowledge_audit(request, user, "documents:parse", _audit_status_code(e), {
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
        # 「前 n 条」只映射到 page_size；向量候选池沿用 RAGFlow 官方默认 1024，
        # 与在控制台用同一组参数检索完全一致，页面调参才有迁移意义。
        retrieve_task = asyncio.create_task(
            client.retrieve(
                payload.query,
                payload.dataset_ids,
                top_n=payload.top_n,
                similarity_threshold=payload.similarity_threshold,
                vector_similarity_weight=payload.vector_similarity_weight,
            )
        )
        disconnect_task = asyncio.create_task(_watch_client_disconnect(request))
        try:
            done, _ = await asyncio.wait(
                {retrieve_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            disconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await disconnect_task
            # 上游请求还没回来就收尾（客户端断开、或本请求本身被取消）时，
            # 必须把它一起取消，否则 httpx 连接和协程会挂到超时才释放。
            if not retrieve_task.done():
                retrieve_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await retrieve_task

        if retrieve_task not in done:
            # 用户点了「取消检索」或关掉页面：浏览器到本服务的连接已断，
            # 也不写检索日志——用户没拿到结果，算进零结果率会污染效果指标。
            raise HTTPException(status_code=499, detail="客户端已断开，检索请求已取消")

        chunks = retrieve_task.result()
        try:
            await KnowledgeRetrievalLogService.log(
                query=payload.query,
                dataset_ids=payload.dataset_ids,
                hit_count=len(chunks),
                top_score=(
                    max(
                        (
                            float(chunk.get("similarity") or 0)
                            for chunk in chunks
                            if isinstance(chunk, dict)
                        ),
                        default=None,
                    )
                    if chunks
                    else None
                ),
                latency_ms=int((time.time() - started) * 1000),
                source=SOURCE_TEST,
                user=user,
            )
        except Exception as log_exc:  # pragma: no cover - 埋点失败不影响检索
            logger.debug("[Knowledge] 检索日志写入跳过: %s", log_exc)

        await record_knowledge_audit(request, user, "retrieval-test", 200, {
            "action": "retrieval_test",
            "dataset_ids": payload.dataset_ids,
            "query_summary": payload.query[:200],
            "top_n": payload.top_n,
            "similarity_threshold": payload.similarity_threshold,
            "vector_similarity_weight": payload.vector_similarity_weight,
            "hit_count": len(chunks),
        }, started_at=started)
        return {"code": 0, "data": chunks, "total": len(chunks)}
    except Exception as e:
        await record_knowledge_audit(request, user, "retrieval-test", _audit_status_code(e), {
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

    record = await KnowledgeBaseMetadataService(db).get_by_dataset_id(dataset_id)
    return {
        "code": 0,
        "data": {
            "users": granted_users,
            "roles": granted_roles,
            # 前端据此标记「归属人」并禁用取消按钮：不加这个字段，
            # 名单里就分不清哪一条是撤销不掉的。
            "owner_user_id": getattr(record, "owner_user_id", None) if record else None,
        }
    }


class TransferDatasetOwnerRequest(BaseModel):
    user_id: int = Field(..., ge=1)


@router.get("/owner-candidates")
async def list_owner_candidates(
    keyword: str = "",
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """转移归属时的候选用户（只返回启用账号）。

    刻意不复用 ``/api/portal/metadata/candidates``：那个端点要求
    ``element:metadata:edit``（数据源编辑权），而知识库负责人通常只有
    ``element:knowledge:edit``，会拿到 403 后看到空列表 —— 按钮点开一片空白。
    """
    await require_element_permission(user, db, "element:knowledge:edit")
    from app.models.user import User
    from sqlalchemy import or_, select

    stmt = select(User.id, User.user_name, User.real_name).where(User.status == 1)
    token = keyword.strip()
    if token:
        like = f"%{token}%"
        stmt = stmt.where(or_(User.user_name.like(like), User.real_name.like(like)))
    rows = (await db.execute(stmt.order_by(User.user_name).limit(200))).all()

    # 打「是否有知识库编辑权限」标记：转移只校验账号存在且启用，但改元数据/再转移/
    # 分配授权还有一道 element:knowledge:edit 闸门。转给没有该权限点的人，他会
    # 成为归属人却打不开任何管理入口 —— 库变成「有主但管不了」。这里只做提示，
    # 不拦截（交接时角色往往还没配好，硬拦会卡住流程）。
    manageable = await PermissionService(db).get_users_with_element_permission(
        [int(row.id) for row in rows], "element:knowledge:edit"
    )

    return {
        "code": 0,
        "data": [
            {
                "id": row.id,
                "user_name": row.user_name,
                "real_name": row.real_name,
                "can_manage": int(row.id) in manageable,
            }
            for row in rows
        ],
    }


@router.post("/datasets/{dataset_id}/owner")
async def transfer_dataset_owner(
    dataset_id: str,
    payload: TransferDatasetOwnerRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """把知识库归属人转给另一个用户（离职 / 调岗交接）。

    权限：管理级写权限（现归属人或管理员）+ ``element:knowledge:edit``。

    影响面（必须在 UI 上说清，否则会被当成 bug 报上来）：

    - ``private`` 库：原归属人**立刻失去访问**，这是转移的本意；
    - ``public`` / ``team`` 库：授权名单与部门可见范围都不变，只是管理权换人
      —— ``team`` 的可见范围由 ``owner_dept_id`` 决定，与归属人无关。

    刻意只改 ``owner_user_id``：``created_by`` 是真实创建记录，覆盖掉就再也
    没人能说出这个库最初是谁建的。
    """
    started = time.time()
    await require_element_permission(user, db, "element:knowledge:edit")
    await require_dataset_write_access(user, db, [dataset_id])

    from app.models.user import User

    target = await db.get(User, payload.user_id)
    if not target:
        raise HTTPException(status_code=400, detail="目标用户不存在")
    if int(target.status or 0) != 1:
        raise HTTPException(status_code=400, detail="目标用户已停用，不能作为归属人")

    metadata_service = KnowledgeBaseMetadataService(db)
    result = await metadata_service.transfer_owner(
        dataset_id,
        target_user_id=int(target.id),
        user_name=user.get("user_name"),
    )
    if not result:
        raise HTTPException(status_code=404, detail="知识库元数据不存在或已删除")
    record, previous_owner_user_id = result

    # 旧归属人失去管理权、新归属人获得管理权，两人的权限缓存都必须失效；
    # 少失效一侧会出现「页面还能改、接口已 403」或反过来的错位。
    affected_user_ids = {
        int(uid) for uid in (previous_owner_user_id, target.id) if uid
    }
    await PermissionService(db).invalidate_cached_permissions_for_users(affected_user_ids)

    clear_dataset_list_cache()
    await record_knowledge_audit(request, user, "datasets:transfer_owner", 200, {
        "action": "transfer_owner",
        "dataset_id": dataset_id,
        "from_owner_user_id": previous_owner_user_id,
        "to_owner_user_id": int(target.id),
        "to_user_name": target.user_name,
    }, started_at=started)

    return {
        "code": 0,
        "data": {
            "ragflow_dataset_id": record.ragflow_dataset_id,
            "owner_user_id": record.owner_user_id,
            "owner_user_name": target.real_name or target.user_name,
            "previous_owner_user_id": previous_owner_user_id,
        },
        "message": "归属人已转移",
    }


@router.get(
    "/metrics/summary",
    dependencies=[Depends(require_permission("menu", "menu:knowledge_metrics"))],
)
async def get_ragflow_metrics_summary(
    start_date: str,
    end_date: str,
    order_by: str = Query("citation", pattern="^(citation|search)$"),
):
    """
    获取指定日期范围内知识库与文档的统计指标汇总。

    安全：运营分析属于平台级运营视图，须持有「运营分析」菜单权限
    （`menu:knowledge_metrics`）才可读取。该口径必须与前端的菜单显示与路由守卫
    逐字一致：三者若不一致，用户会看到一个能进、但数据必然加载失败的页面
    （曾出现后端 `require_admin`、前端却按菜单权限放行的 403「Admin access required」）。
    2026-10-20 菜单入库时把原先与「知识库管理」共用的码拆成独立码，迁移脚本已把旧授权
    复制到 `menu:knowledge_metrics`，因此老角色不会因为这次拆码失去访问权。
    管理员由 `require_permission` 内部短路放行，无需在此另判角色。
    注意此前只校验登录即可读取全平台知识库名、文件名与调用量，收紧口径时三处需同改。
    时效：查询前**先归并、再查询**，保证本次响应即包含最新数据。归并必须发生在查询之前：
    若改为 BackgroundTasks（响应发出后才执行），本次响应会读到归并前的旧值，用户需要
    连刷两次才能看到最新数据。归并本身由分钟级 Redis 锁串行化，重复调用不会重复累加。
    """
    from app.services.knowledge_metrics_service import (
        KnowledgeMetricsService,
        MAX_RANGE_DAYS,
    )

    try:
        start_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="start_date/end_date 必须为 YYYY-MM-DD 格式")

    if start_dt > end_dt:
        raise HTTPException(status_code=400, detail="start_date 不能晚于 end_date")
    if (end_dt - start_dt).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(status_code=400, detail=f"查询区间不能超过 {MAX_RANGE_DAYS} 天")

    await KnowledgeMetricsService.sync_redis_metrics_to_db()

    data = await KnowledgeMetricsService.get_metrics_summary(
        start_date,
        end_date,
        order_by=order_by,
    )
    return {"code": 0, "data": data}


async def _build_ragflow_document_file_response(document_id: str, *, dataset_id: str | None = None):
    from fastapi.responses import StreamingResponse
    client = RagFlowClient(config_prefix="knowledge_ragflow")
    try:
        # 流式转发：预览大文件时不再把整份字节读进内存再吐出去
        chunks, filename, content_type = await client.open_document_stream(
            document_id,
            dataset_id=dataset_id,
        )
        
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
        return StreamingResponse(chunks, media_type=content_type, headers=headers)
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
    return await _build_ragflow_document_file_response(document_id, dataset_id=dataset_id)


@router.get("/documents/{document_id}/file", dependencies=[Depends(require_admin)])
async def get_ragflow_document_file(
    document_id: str,
    dataset_id: Optional[str] = Query(None, description="RAGFlow dataset id (recommended)"),
    user: dict = Depends(get_current_user),
):
    """
    旧版无 dataset 上下文的下载入口仅保留给管理员，避免普通用户绕过知识库授权。
    建议传 dataset_id，走 /datasets/{dataset_id}/documents/{document_id} 下载。
    """
    return await _build_ragflow_document_file_response(document_id, dataset_id=dataset_id)


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
    
    # 0. 尝试加载本地自定义问题配置
    from app.models.knowledge import KnowledgeBaseMetadata
    from sqlalchemy import select
    
    custom_questions = []
    try:
        stmt = select(KnowledgeBaseMetadata).where(
            KnowledgeBaseMetadata.ragflow_dataset_id == dataset_id,
            KnowledgeBaseMetadata.status != "deleted"
        )
        res = await db.execute(stmt)
        local_kb = res.scalar_one_or_none()
        if local_kb and local_kb.extra_config:
            raw_cust = local_kb.extra_config.get("custom_questions")
            if isinstance(raw_cust, list):
                for item in raw_cust:
                    if isinstance(item, dict) and item.get("label") and item.get("query"):
                        custom_questions.append({
                            "label": str(item["label"]).strip(),
                            "query": str(item["query"]).strip()
                        })
    except Exception:
        # 本地没有找到或加载失败时，退避到空，不影响大模型生成
        pass

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
                    
                    # 合并外部排除与内置自定义排除问题列表
                    exclude_queries = []
                    if exclude:
                        exclude_queries.extend([q.strip() for q in exclude.split(",") if q.strip()])
                    if custom_questions:
                        exclude_queries.extend([q["query"] for q in custom_questions])

                    exclude_instructions = ""
                    if exclude_queries:
                        exclude_instructions = f"\n请不要生成与以下已存在问题相近或重复的问题：{json.dumps(exclude_queries, ensure_ascii=False)}。"
                            
                    prompt = (
                        f"你是一个专业的知识库导航助手。当前知识库包含以下文档列表：[{doc_names_str}]。\n"
                        f"请根据这些文件的名字所反映的业务内容，生成 3 个用户最有可能向 AI 提问的高频、专业且具体的问题。\n"
                        f"要求：\n"
                        f"1. 每个问题必须是一句完整、具体的提问，不要宽泛（如“介绍下XX项目的设计架构”而不是“关于架构的设计”）；\n"
                        f"2. 输出格式必须是一个合法的 JSON 数组，如：[\"问题内容1\", \"问题内容2\", \"问题内容3\"]\n"
                        f"3. 只输出 JSON 数组本身，严禁任何 Markdown 标记包围或多余解释。{exclude_instructions}"
                    )
                    messages = [
                        RuntimeMessage(
                            role="system",
                            content=[RuntimeContentBlock(type="text", text="你是一个严谨的 JSON 问答生成器。")]
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
                            return {"code": 0, "data": {
                                "custom_questions": custom_questions,
                                "questions": dynamic_questions
                            }}
            except Exception as llm_err:
                # LLM 生成失败时，安静退避到默认问题，不影响 API 的正常可用
                import logging
                logging.exception("Failed to generate dynamic questions via LLM in knowledge portal")
                pass
                
        return {"code": 0, "data": {
            "custom_questions": custom_questions,
            "questions": default_questions
        }}
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
                                
                                # 写入 Redis 缓存（有效期 30 天：2592000 秒）
                                try:
                                    await redis_client.setex(cache_key, 2592000, json.dumps(res_payload, ensure_ascii=False))
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
    # 与其它编辑类端点保持一致：能写这个库，还要有「编辑知识库」的元素权限。
    # 只有数据级门禁时，一个没有编辑权限的角色照样能调这个接口拿 AI 生成的治理建议。
    await require_element_permission(user, db, "element:knowledge:edit")
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
                    "description": _as_text(parsed_data.get("description")),
                    "tags": _as_text(parsed_data.get("tags")),
                    "notes": _as_text(parsed_data.get("notes"))
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


class AddDatasetPermissionsRequest(BaseModel):
    target_type: str  # "user" 或 "role"
    target_ids: List[int]

class DeleteDatasetPermissionRequest(BaseModel):
    target_type: str  # "user" 或 "role"
    target_id: int


# 权限码用 element:user:edit（「编辑用户信息」）而不是 menu:system:users：
# 菜单码只负责侧栏是否可见，拿它当功能校验等于「看得见菜单 = 有权改授权」。
# 而且用户页/角色页的整份保存用的就是 element:* 系列，两处口径必须一致，
# 否则同一个人会在一个入口能授权、另一个入口被拒。
# 文档内容类操作：对该库**可读**的人都能看这些流水（谁传了什么、删了什么）。
# 授权变更、转移归属、改可见范围、删库这些只对管理级（创建人 / 管理员）可见 ——
# 被授权进来的外部成员不该看到本库的授权名单变更历史。
KNOWLEDGE_CONTENT_AUDIT_ACTIONS = (
    "documents:upload",
    "documents:delete",
    "documents:parse",
    "retrieval-test",
)


@router.get("/datasets/{dataset_id}/audit-logs")
async def list_dataset_audit_logs(
    dataset_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    action: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """知识库操作日志（详情页「操作日志」tab）。

    可见范围分两级：

    - 管理级（创建人 / 管理员）：全部操作，含授权变更、归属转移、改可见范围、删库；
    - 其余可读成员（部门成员、被授权成员）：仅文档内容类操作，
      但含失败记录 —— 「谁试过删除失败」同样是有用信息。
    """
    from app.models.knowledge import KnowledgeAuditLog
    from sqlalchemy import func, select

    await require_dataset_access(user, db, [dataset_id])

    service = PermissionService(db)
    access = await service.get_knowledge_base_access(
        int(user["user_id"]), user.get("user_name")
    )
    can_manage = bool(access.get("is_admin")) or dataset_id in (
        access.get("writable_ids") or set()
    )

    conditions = [KnowledgeAuditLog.dataset_id == dataset_id]
    if not can_manage:
        conditions.append(KnowledgeAuditLog.action.in_(KNOWLEDGE_CONTENT_AUDIT_ACTIONS))
    if action:
        if can_manage or action in KNOWLEDGE_CONTENT_AUDIT_ACTIONS:
            conditions.append(KnowledgeAuditLog.action == action)
        else:
            # 请求了对自己不可见的 action（如部门成员想看「授权变更」）：
            # 返回空列表，既不泄露"确实存在这类记录"，也避免拼出矛盾条件。
            return {
                "code": 0,
                "data": {
                    "total": 0,
                    "page": page,
                    "size": size,
                    "can_manage": can_manage,
                    "items": [],
                },
            }

    total = (
        await db.execute(
            select(func.count()).select_from(KnowledgeAuditLog).where(*conditions)
        )
    ).scalar() or 0

    rows = (
        (
            await db.execute(
                select(KnowledgeAuditLog)
                .where(*conditions)
                .order_by(KnowledgeAuditLog.created_at.desc(), KnowledgeAuditLog.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        )
        .scalars()
        .all()
    )

    return {
        "code": 0,
        "data": {
            "total": int(total),
            "page": page,
            "size": size,
            "can_manage": can_manage,
            "items": [
                {
                    "id": row.id,
                    "action": row.action,
                    "user_id": row.user_id,
                    "user_name": row.user_name,
                    "status_code": row.status_code,
                    "detail": row.detail,
                    "client_ip": row.client_ip,
                    "error_message": row.error_message,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ],
        },
    }


def _parse_audit_time(value: Optional[str], *, end_of_day: bool = False) -> Optional[datetime.datetime]:
    """接受 `2026-10-01` 与 `2026-10-01T12:00:00`；按天给 end 时补到 23:59:59。"""
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        if len(raw) == 10:
            parsed = datetime.datetime.strptime(raw, "%Y-%m-%d")
            return parsed.replace(hour=23, minute=59, second=59) if end_of_day else parsed
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="时间格式不合法，请用 2026-10-01 或 2026-10-01T12:00:00",
        )


def _audit_log_csv(rows) -> StreamingResponse:
    """导出 CSV。带 UTF-8 BOM，否则 Excel 打开中文全是乱码。"""
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["时间", "操作", "知识库", "操作人", "状态码", "详情", "错误信息", "来源 IP"])
    for row in rows:
        writer.writerow([
            row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
            row.action or "",
            row.dataset_id or "",
            f"{row.user_name or ''} ({row.user_id or ''})".strip(),
            row.status_code if row.status_code is not None else "",
            json.dumps(row.detail, ensure_ascii=False) if row.detail else "",
            row.error_message or "",
            row.client_ip or "",
        ])
    body = "\ufeff" + buffer.getvalue()
    filename = f"knowledge-audit-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.csv"
    return StreamingResponse(
        iter([body.encode("utf-8")]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/audit-logs")
async def list_global_audit_logs(
    page: int = 1,
    size: int = 50,
    action: Optional[str] = None,
    dataset_id: Optional[str] = None,
    user_id: Optional[int] = None,
    keyword: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    export: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """跨库审计日志（管理员）。

    逐库的「操作日志」tab 只解决"这个库发生了什么"；这里解决"某个人今天动了
    哪些库"。`__global__` 记录（同步、巡检这类没落到具体库的动作）同样列出——
    否则「点过同步但什么都没发生」这种事查不到。
    """
    from app.models.knowledge import KnowledgeAuditLog
    from sqlalchemy import func, or_, select

    service = PermissionService(db)
    access = await service.get_knowledge_base_access(
        int(user["user_id"]), user.get("user_name")
    )
    if not access.get("is_admin"):
        raise HTTPException(status_code=403, detail="需要管理员权限才能查看全平台审计")

    page = max(1, int(page or 1))
    size = min(200, max(1, int(size or 50)))

    start_dt = _parse_audit_time(start)
    end_dt = _parse_audit_time(end, end_of_day=True)

    conditions = []
    if action:
        conditions.append(KnowledgeAuditLog.action == action)
    if dataset_id:
        conditions.append(KnowledgeAuditLog.dataset_id == dataset_id)
    if user_id:
        conditions.append(KnowledgeAuditLog.user_id == user_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                KnowledgeAuditLog.user_name.like(like),
                KnowledgeAuditLog.error_message.like(like),
            )
        )
    if start_dt:
        conditions.append(KnowledgeAuditLog.created_at >= start_dt)
    if end_dt:
        conditions.append(KnowledgeAuditLog.created_at <= end_dt)

    if export == "csv":
        # 导出上限：再大就该走离线任务，别让一次点击把内存打满
        export_rows = (
            (
                await db.execute(
                    select(KnowledgeAuditLog)
                    .where(*conditions)
                    .order_by(KnowledgeAuditLog.created_at.desc(), KnowledgeAuditLog.id.desc())
                    .limit(5000)
                )
            )
            .scalars()
            .all()
        )
        return _audit_log_csv(export_rows)

    total = (
        await db.execute(
            select(func.count()).select_from(KnowledgeAuditLog).where(*conditions)
        )
    ).scalar() or 0

    rows = (
        (
            await db.execute(
                select(KnowledgeAuditLog)
                .where(*conditions)
                .order_by(KnowledgeAuditLog.created_at.desc(), KnowledgeAuditLog.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        )
        .scalars()
        .all()
    )

    return {
        "code": 0,
        "data": {
            "total": int(total),
            "page": page,
            "size": size,
            "items": [
                {
                    "id": row.id,
                    "dataset_id": row.dataset_id,
                    "action": row.action,
                    "user_id": row.user_id,
                    "user_name": row.user_name,
                    "status_code": row.status_code,
                    "detail": row.detail,
                    "client_ip": row.client_ip,
                    "error_message": row.error_message,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ],
        },
    }


@router.post("/datasets/{dataset_id}/permissions", dependencies=[Depends(require_permission("element", "element:user:edit"))])
async def add_dataset_permissions(
    dataset_id: str,
    payload: AddDatasetPermissionsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user)
):
    """
    添加知识库的反向授权角色或用户 (仅有用户管理权限的用户可用)
    """
    started = time.time()
    from app.models.permission import ResourcePermission, UserRoleRelation
    from sqlalchemy import select

    await require_dataset_write_access(user, db, [dataset_id])
    await require_dataset_shareable(db, dataset_id)
    
    perm_service = PermissionService(db)
    affected_user_ids = set()

    for tid in payload.target_ids:
        # 查询是否已经有对应记录了
        stmt = select(ResourcePermission).where(
            ResourcePermission.resource_type == "dataset",
            ResourcePermission.resource_id == dataset_id
        )
        if payload.target_type == "user":
            stmt = stmt.where(ResourcePermission.user_id == tid)
            affected_user_ids.add(tid)
        else:
            stmt = stmt.where(ResourcePermission.role_id == tid)
            # 获取该角色下的所有关联用户
            r_users_stmt = select(UserRoleRelation.user_id).where(UserRoleRelation.role_id == tid)
            r_users_res = await db.execute(r_users_stmt)
            for uid in r_users_res.scalars().all():
                affected_user_ids.add(uid)

        result = await db.execute(stmt)
        record = result.scalar_one_or_none()

        if record:
            record.enabled = True
        else:
            new_perm = ResourcePermission(
                resource_type="dataset",
                resource_id=dataset_id,
                enabled=True
            )
            if payload.target_type == "user":
                new_perm.user_id = tid
            else:
                new_perm.role_id = tid
            db.add(new_perm)

    await db.flush()

    # 清理受影响用户的缓存，使其变更即刻生效
    if affected_user_ids:
        await perm_service.invalidate_cached_permissions_for_users(affected_user_ids)

    # 授权变更是敏感操作：谁在什么时候把哪个库授给了谁，必须留痕。
    # 两个入口（知识库页反向授权 / 用户页整份保存）都会改这张表，没有审计就查不出是谁改的。
    await record_knowledge_audit(request, user, "datasets:grant", 200, {
        "action": "add_dataset_permissions",
        "dataset_id": dataset_id,
        "target_type": payload.target_type,
        "target_ids": payload.target_ids,
        "affected_user_count": len(affected_user_ids),
    }, started_at=started)

    return {"code": 0, "message": "权限配置已成功添加"}


@router.delete("/datasets/{dataset_id}/permissions", dependencies=[Depends(require_permission("element", "element:user:edit"))])
async def delete_dataset_permission(
    dataset_id: str,
    payload: DeleteDatasetPermissionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user)
):
    """
    移除知识库关联的某个角色或用户授权 (仅有用户管理权限的用户可用)
    """
    started = time.time()
    from app.models.permission import ResourcePermission, UserRoleRelation
    from sqlalchemy import select, delete

    await require_dataset_write_access(user, db, [dataset_id])
    # 这里**不**校验 require_dataset_shareable：改成个人私有之前写下的历史授权，
    # 需要能逐条清理（新增授权仍被上面的 add 端点拒绝）。否则会出现
    # "看得见这条授权记录、点删除却 400"的死角。

    # 但归属人的那条授权是例外：他的可读可写来自 owner_user_id，删掉记录
    # 只是让名单看起来「撤销成功」，实际仍能访问。要变更请走转移归属。
    record = await KnowledgeBaseMetadataService(db).get_by_dataset_id(dataset_id)
    if (
        payload.target_type == "user"
        and record is not None
        and getattr(record, "owner_user_id", None) == payload.target_id
    ):
        raise HTTPException(
            status_code=400,
            detail="归属人的访问权不可撤销，如需变更请先把知识库转移给其他同事",
        )

    perm_service = PermissionService(db)
    affected_user_ids = set()

    # 查出受影响的用户列表以作缓存清理
    if payload.target_type == "user":
        affected_user_ids.add(payload.target_id)
    else:
        r_users_stmt = select(UserRoleRelation.user_id).where(UserRoleRelation.role_id == payload.target_id)
        r_users_res = await db.execute(r_users_stmt)
        for uid in r_users_res.scalars().all():
            affected_user_ids.add(uid)

    # 物理清除
    stmt = delete(ResourcePermission).where(
        ResourcePermission.resource_type == "dataset",
        ResourcePermission.resource_id == dataset_id
    )
    if payload.target_type == "user":
        stmt = stmt.where(ResourcePermission.user_id == payload.target_id)
    else:
        stmt = stmt.where(ResourcePermission.role_id == payload.target_id)

    await db.execute(stmt)
    await db.flush()

    # 清理 Redis 缓存
    if affected_user_ids:
        await perm_service.invalidate_cached_permissions_for_users(affected_user_ids)

    await record_knowledge_audit(request, user, "datasets:revoke", 200, {
        "action": "delete_dataset_permission",
        "dataset_id": dataset_id,
        "target_type": payload.target_type,
        "target_id": payload.target_id,
        "affected_user_count": len(affected_user_ids),
    }, started_at=started)

    return {"code": 0, "message": "授权已成功取消"}

