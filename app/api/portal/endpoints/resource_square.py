"""资源广场：知识库与数据集的目录查询，以及授权后的只读概览。

与「知识库管理 → 发现」的区别：发现只列**你还不能访问**的库，广场列的是
**全部可申请的目录**（含已授权的），因为这里要回答的是「我有什么、还能申请什么」。

可见范围：知识库只出现 public / team，个人私有库既不展示也不接受申请；
数据集出现全部启用项——数据集本来没有可见范围概念，缺的是「申请入口」。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.models.knowledge import KnowledgeAccessRequest, KnowledgeBaseMetadata
from app.models.metadata import MetaColumn, MetaDataset, MetaMetric, MetaRelationship, MetaTable
from app.models.permission import ResourcePermission, UserRoleRelation
from app.schemas.response import StandardResponse
from app.services.knowledge_dataset_filters import is_internal_dataset_name
from app.services.permission_service import PermissionService

logger = logging.getLogger(__name__)
router = APIRouter()

_ACCESS_STATES = ("all", "apply", "pending", "has")


def _safe_int(value: Any) -> Optional[int]:
    """引擎侧的计数可能是字符串、空串或缺失；拿不到就返回 None，别显示成 0。"""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _show_datasets_enabled() -> bool:
    try:
        from app.services.config_service import (
            RESOURCE_SQUARE_SHOW_DATASETS_KEY,
            ConfigService,
        )

        raw = await ConfigService.get(RESOURCE_SQUARE_SHOW_DATASETS_KEY, "true")
    except Exception as exc:  # noqa: BLE001 - 配置读不到就按默认开，别把广场整页打掉
        logger.warning("[ResourceSquare] 读取数据集目录开关失败，按开启处理: %s", exc)
        return True
    return str(raw).strip().lower() not in {"false", "0", "no", "off"}


async def _my_request_map(
    db: AsyncSession, user_id: int, resource_type: str, resource_ids: List[str]
) -> Dict[str, KnowledgeAccessRequest]:
    """我对这批资源最近一条申请（同资源多条时取最新的状态）。"""
    if not resource_ids:
        return {}
    rows = (
        await db.execute(
            select(KnowledgeAccessRequest)
            .where(
                KnowledgeAccessRequest.applicant_user_id == int(user_id),
                KnowledgeAccessRequest.resource_type == resource_type,
                KnowledgeAccessRequest.dataset_id.in_(resource_ids),
            )
            .order_by(
                KnowledgeAccessRequest.created_at.desc(), KnowledgeAccessRequest.id.desc()
            )
        )
    ).scalars().all()
    latest: Dict[str, KnowledgeAccessRequest] = {}
    for row in rows:
        latest.setdefault(row.dataset_id, row)
    return latest


async def _knowledge_engine_stats(dataset_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """文档数与切片数来自 RAGFlow，取不到就整体降级（返回空 dict）。"""
    if not dataset_ids:
        return {}
    try:
        from app.services.ai.ragflow_client import RagFlowClient

        items = await RagFlowClient(config_prefix="knowledge_ragflow").list_all_datasets_cached()
        wanted = set(dataset_ids)
        stats: Dict[str, Dict[str, Any]] = {}
        for item in items or []:
            key = str(item.get("id") or "")
            if key not in wanted:
                continue
            stats[key] = {
                "doc_count": _safe_int(item.get("document_count")),
                "chunk_count": _safe_int(item.get("chunk_count")),
                "engine_update_time": item.get("update_time"),
            }
        return stats
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ResourceSquare] 读取知识库引擎统计失败: %s", exc)
        return {}


def _metadata_permission_condition(user_id: int, role_ids: List[int]):
    condition = ResourcePermission.user_id == int(user_id)
    if role_ids:
        condition = condition | ResourcePermission.role_id.in_(role_ids)
    return condition


async def _permitted_metadata_ids(db: AsyncSession, user_id: int) -> set[str]:
    role_ids = (
        await db.execute(
            select(UserRoleRelation.role_id).where(UserRoleRelation.user_id == int(user_id))
        )
    ).scalars().all()
    stmt = select(ResourcePermission.resource_id).where(
        ResourcePermission.resource_type == "metadata",
        ResourcePermission.enabled.is_(True),
    ).where(_metadata_permission_condition(int(user_id), list(role_ids)))
    return {str(value) for value in (await db.execute(stmt)).scalars().all()}


def _state_matches(
    state: str, *, has_access: bool, request_status: Optional[str]
) -> bool:
    if state == "apply":
        return not has_access and request_status != "pending"
    if state == "pending":
        return request_status == "pending"
    if state == "has":
        return has_access
    return True


@router.get("/knowledge", response_model=StandardResponse[Dict[str, Any]], summary="资源广场·知识库")
async def list_square_knowledge(
    keyword: Optional[str] = None,
    visibility: str = Query(default="all", pattern="^(all|public|team)$"),
    status: str = Query(default="all", pattern="^(all|apply|pending|has)$"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=12, ge=1, le=50),
    user: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = int(user["user_id"])
    is_admin = str(user.get("role") or "") == "admin"
    access = await PermissionService(db).get_knowledge_base_access(user_id, user.get("user_name"))
    accessible = set(access.get("accessible_ids") or set())

    # 候选集默认只含 public/team：私有库的存在性不该泄露给别人。
    # 但「已授权」筛选要对齐工作台「我的知识库」的数字，而那个数字是
    # len(accessible_ids)，含自己创建的私有库与管理员的全部库。
    # 只按 public/team 过滤会让两处对不上（工作台 3、广场 2），所以在这种情况下
    # 直接按「我有权限的库」取，范围仍然只有本人可见的那些。
    if status == "has":
        # 管理员对所有库都有权限，工作台按「库里还剩多少个」算，含私有库
        if is_admin:
            stmt = select(KnowledgeBaseMetadata)
        elif accessible:
            stmt = select(KnowledgeBaseMetadata).where(
                KnowledgeBaseMetadata.ragflow_dataset_id.in_(list(accessible))
            )
        else:
            stmt = select(KnowledgeBaseMetadata).where(False)
    else:
        # 「全部 / 可申请 / 审批中」只看公共级与部门级：私有库的存在性不对外泄露
        stmt = select(KnowledgeBaseMetadata).where(
            KnowledgeBaseMetadata.visibility.in_(["public", "team"])
        )

    rows = (
        await db.execute(
            stmt.where(KnowledgeBaseMetadata.status != "deleted").order_by(
                KnowledgeBaseMetadata.id.desc()
            )
        )
    ).scalars().all()

    candidates = []
    for row in rows:
        if is_internal_dataset_name(row.name):
            continue
        if visibility != "all" and (row.visibility or "private") != visibility:
            continue
        candidates.append(row)

    request_map = await _my_request_map(
        db, user_id, "dataset", [row.ragflow_dataset_id for row in candidates]
    )
    engine_stats = await _knowledge_engine_stats(
        [row.ragflow_dataset_id for row in candidates]
    )

    keyword_text = (keyword or "").strip().lower()
    enriched: List[Dict[str, Any]] = []
    for row in candidates:
        if keyword_text and not (
            keyword_text in (row.name or "").lower()
            or keyword_text in (row.description or "").lower()
            or keyword_text in (row.owner or "").lower()
            or keyword_text in " ".join(str(tag) for tag in (row.tags or [])).lower()
            # 与知识库管理页一致：Dataset ID 也参与搜索，方便按复制来的 ID 定位
            or keyword_text in (row.ragflow_dataset_id or "").lower()
        ):
            continue

        latest = request_map.get(row.ragflow_dataset_id)
        request_status = latest.status if latest else None
        has_access = is_admin or row.ragflow_dataset_id in accessible
        if not _state_matches(status, has_access=has_access, request_status=request_status):
            continue

        stats = engine_stats.get(row.ragflow_dataset_id) or {}
        enriched.append(
            {
                "dataset_id": row.ragflow_dataset_id,
                "name": row.name,
                "description": row.description,
                "visibility": row.visibility,
                "owner": row.owner,
                "owner_dept_id": row.owner_dept_id,
                "tags": list(row.tags or [])[:6],
                "doc_count": stats.get("doc_count"),
                "chunk_count": stats.get("chunk_count"),
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "has_access": has_access,
                "request_status": request_status,
                "request_id": latest.id if latest else None,
            }
        )

    total = len(enriched)
    start = (page - 1) * size
    return StandardResponse(
        data={
            "items": enriched[start : start + size],
            "total": total,
            "page": page,
            "size": size,
            # 引擎读不到时前端提示「规模可能不是最新」，而不是显示一堆 0
            "stale": not engine_stats and bool(candidates),
            "summary": {
                "apply": sum(
                    1 for item in enriched if not item["has_access"] and item["request_status"] != "pending"
                ),
                "pending": sum(1 for item in enriched if item["request_status"] == "pending"),
                "has": sum(1 for item in enriched if item["has_access"]),
            },
        }
    )


@router.get("/datasets", response_model=StandardResponse[Dict[str, Any]], summary="资源广场·数据集")
async def list_square_datasets(
    keyword: Optional[str] = None,
    status: str = Query(default="all", pattern="^(all|apply|pending|has)$"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=12, ge=1, le=50),
    user: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = int(user["user_id"])
    is_admin = str(user.get("role") or "") == "admin"

    if not await _show_datasets_enabled():
        return StandardResponse(
            data={
                "items": [],
                "total": 0,
                "page": page,
                "size": size,
                "enabled": False,
                "summary": {"apply": 0, "pending": 0, "has": 0},
            }
        )

    rows = list(
        (
            await db.execute(
                select(MetaDataset)
                .where(MetaDataset.status == 1)
                .order_by(MetaDataset.id.asc())
            )
        ).scalars().all()
    )
    dataset_ids = [int(row.id) for row in rows]

    permitted_ids = set() if is_admin else await _permitted_metadata_ids(db, user_id)
    request_map = await _my_request_map(
        db, user_id, "metadata", [str(row.id) for row in rows]
    )

    table_counts: Dict[int, int] = {}
    metric_counts: Dict[int, int] = {}
    if dataset_ids:
        table_counts = dict(
            (
                await db.execute(
                    select(MetaTable.dataset_id, func.count())
                    .where(MetaTable.dataset_id.in_(dataset_ids))
                    .group_by(MetaTable.dataset_id)
                )
            ).all()
        )
        metric_counts = dict(
            (
                await db.execute(
                    select(MetaMetric.dataset_id, func.count())
                    .where(MetaMetric.dataset_id.in_(dataset_ids))
                    .group_by(MetaMetric.dataset_id)
                )
            ).all()
        )

    keyword_text = (keyword or "").strip().lower()
    enriched: List[Dict[str, Any]] = []
    for row in rows:
        if is_internal_dataset_name(row.name):
            continue
        has_access = is_admin or str(row.id) in permitted_ids
        latest = request_map.get(str(row.id))
        request_status = latest.status if latest else None

        if keyword_text and not (
            keyword_text in (row.name or "").lower()
            or keyword_text in (row.display_name or "").lower()
            or keyword_text in (row.description or "").lower()
            or keyword_text in " ".join(str(tag) for tag in (row.tags or [])).lower()
        ):
            continue
        if not _state_matches(status, has_access=has_access, request_status=request_status):
            continue

        enriched.append(
            {
                "id": row.id,
                "name": row.name,
                "display_name": row.display_name or row.name,
                "description": row.description,
                "data_source": row.data_source,
                "tags": list(row.tags or [])[:6],
                "quality_score": row.quality_score,
                "table_count": int(table_counts.get(row.id, 0)),
                "metric_count": int(metric_counts.get(row.id, 0)),
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "has_access": has_access,
                "request_status": request_status,
                "request_id": latest.id if latest else None,
            }
        )

    total = len(enriched)
    start = (page - 1) * size
    return StandardResponse(
        data={
            "items": enriched[start : start + size],
            "total": total,
            "page": page,
            "size": size,
            "enabled": True,
            "summary": {
                "apply": sum(
                    1 for item in enriched if not item["has_access"] and item["request_status"] != "pending"
                ),
                "pending": sum(1 for item in enriched if item["request_status"] == "pending"),
                "has": sum(1 for item in enriched if item["has_access"]),
            },
        }
    )


async def _require_metadata_access(
    db: AsyncSession, user: Dict[str, Any], dataset_id: int
) -> MetaDataset:
    dataset = await db.get(MetaDataset, int(dataset_id))
    if not dataset:
        raise HTTPException(status_code=404, detail="数据集不存在")
    if str(user.get("role") or "") == "admin":
        return dataset

    role_ids = (
        await db.execute(
            select(UserRoleRelation.role_id).where(
                UserRoleRelation.user_id == int(user["user_id"])
            )
        )
    ).scalars().all()
    stmt = select(ResourcePermission).where(
        ResourcePermission.resource_type == "metadata",
        ResourcePermission.resource_id == str(dataset_id),
        ResourcePermission.enabled.is_(True),
    ).where(_metadata_permission_condition(int(user["user_id"]), list(role_ids)))
    if not (await db.execute(stmt.limit(1))).scalars().first():
        raise HTTPException(status_code=403, detail="你还没有这个数据集的访问权限")
    return dataset


@router.get(
    "/datasets/{dataset_id}",
    response_model=StandardResponse[Dict[str, Any]],
    summary="数据集只读概览",
)
async def get_dataset_readonly(
    dataset_id: int,
    user: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """授权用户可看的范围：表清单、指标口径、质量分。

    刻意不给：字段明细、样例值、YAML、行级过滤配置、数据源连接信息。
    只读权限的意思是「能看懂这个数据集是什么、怎么用」，不是「能把它导走」。
    """
    dataset = await _require_metadata_access(db, user, dataset_id)

    tables = (
        await db.execute(
            select(MetaTable)
            .where(MetaTable.dataset_id == dataset_id)
            .order_by(MetaTable.id.asc())
        )
    ).scalars().all()
    table_ids = [int(table.id) for table in tables]

    field_counts: Dict[int, int] = {}
    if table_ids:
        field_counts = dict(
            (
                await db.execute(
                    select(MetaColumn.table_id, func.count())
                    .where(MetaColumn.table_id.in_(table_ids))
                    .group_by(MetaColumn.table_id)
                )
            ).all()
        )

    metrics = (
        await db.execute(
            select(MetaMetric)
            .where(MetaMetric.dataset_id == dataset_id)
            .order_by(MetaMetric.id.asc())
        )
    ).scalars().all()

    relationship_count = 0
    if table_ids:
        relationship_count = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(MetaRelationship)
                    .where(
                        or_(
                            MetaRelationship.source_table_id.in_(table_ids),
                            MetaRelationship.target_table_id.in_(table_ids),
                        )
                    )
                )
            ).scalar()
            or 0
        )

    return StandardResponse(
        data={
            "id": dataset.id,
            "name": dataset.name,
            "display_name": dataset.display_name or dataset.name,
            "description": dataset.description,
            "data_source": dataset.data_source,
            "tags": list(dataset.tags or []),
            "quality_score": dataset.quality_score,
            "updated_at": dataset.updated_at.isoformat() if dataset.updated_at else None,
            "relationship_count": relationship_count,
            "tables": [
                {
                    "physical_name": table.physical_name,
                    "term": table.term,
                    "field_count": int(field_counts.get(table.id, 0)),
                    "description": table.description,
                }
                for table in tables
            ],
            "metrics": [
                {
                    "name": metric.name,
                    "display_name": metric.display_name,
                    "description": metric.description,
                    "unit": metric.unit,
                }
                for metric in metrics
            ],
        }
    )


@router.get(
    "/knowledge/{dataset_id}",
    response_model=StandardResponse[Dict[str, Any]],
    summary="知识库只读概览",
)
async def get_knowledge_readonly(
    dataset_id: str,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=100, ge=1, le=200),
    user: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """授权用户可看的范围：库信息 + 文档目录。

    刻意不给：切片正文、原文下载/预览地址。目录足够回答「这个库里有什么」，
    真要读内容就走对话检索——那里有检索日志。
    """
    meta = (
        await db.execute(
            select(KnowledgeBaseMetadata)
            .where(KnowledgeBaseMetadata.ragflow_dataset_id == dataset_id)
            .limit(1)
        )
    ).scalars().first()
    if not meta or (meta.status or "active") == "deleted":
        raise HTTPException(status_code=404, detail="知识库不存在")

    is_admin = str(user.get("role") or "") == "admin"
    access = await PermissionService(db).get_knowledge_base_access(
        int(user["user_id"]), user.get("user_name")
    )
    if not is_admin and dataset_id not in (access.get("accessible_ids") or set()):
        raise HTTPException(status_code=403, detail="你还没有这个知识库的访问权限")

    documents: List[Dict[str, Any]] = []
    # 文档列表要现连 RAGFlow，默认超时 20s；引擎一慢，前端就是一个长时间空白页。
    # 概览页的核心价值是「这个库里有什么」，库信息本身不该被文档列表拖住，
    # 因此这里独立限时，超时就把 documents_error 交给前端说明。
    documents_error = False
    try:
        import asyncio

        from app.services.ai.ragflow_client import RagFlowClient

        rows = await asyncio.wait_for(
            RagFlowClient(config_prefix="knowledge_ragflow").list_documents(
                dataset_id, page=page, page_size=size
            ),
            timeout=8,
        )
        for doc in rows or []:
            documents.append(
                {
                    "id": doc.get("id"),
                    "name": doc.get("name"),
                    "file_type": doc.get("type") or doc.get("file_type"),
                    "size": _safe_int(doc.get("size")),
                    "chunk_count": _safe_int(doc.get("chunk_count")),
                    "run_status": doc.get("run") or doc.get("status"),
                    "updated_at": doc.get("update_time"),
                }
            )
    except Exception as exc:  # noqa: BLE001 - 引擎失联不该让概览整页 500
        documents_error = True
        logger.warning("[ResourceSquare] 读取知识库文档列表失败: %s", exc)

    return StandardResponse(
        data={
            "dataset_id": dataset_id,
            "name": meta.name,
            "description": meta.description,
            "visibility": meta.visibility,
            "owner": meta.owner,
            "tags": list(meta.tags or []),
            "updated_at": meta.updated_at.isoformat() if meta.updated_at else None,
            "documents": documents,
            # 引擎读不到 ≠ 这个库没有文档。前端据此提示，而不是摆一个骗人的 0
            "documents_error": documents_error,
            "page": page,
            "size": size,
        }
    )
