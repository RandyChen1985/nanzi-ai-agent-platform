from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeBaseMetadata
from app.services.knowledge_dataset_filters import is_internal_dataset_name

# 区分"未提供该字段"与"显式清空"（传 None/0）。
# 元数据更新是 PATCH 语义，owner 与 owner_dept_id 都需要支持被清空：
#   - 未提供 → 保持原值
#   - 提供了 None/0 → 置空
_UNSET: Any = object()


# 可见范围三档：判定侧（permission_service）有同名归一化，这里独立实现是为了
# 避免 service 之间互相 import。空值/未知值按 public 兜底，与判定口径一致。
_VISIBILITY_ALLOWED = {"public", "team", "private"}


def _normalize_visibility(value: object, *, fallback: str) -> str:
    """把外部传入的可见范围归一成三档之一。

    ``fallback`` 区分两条路径：新建默认 ``private``（用户没说就是自己的草稿），
    更新时缺省按 ``public``（与判定侧"未知即可授权"的兜底一致）。
    """
    token = str(value or "").strip().lower()
    if token in _VISIBILITY_ALLOWED:
        return token
    return fallback


class KnowledgeBaseMetadataService:
    """Manage local metadata for RAGFlow knowledge bases."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_metadata(self, include_deleted: bool = False) -> List[KnowledgeBaseMetadata]:
        stmt = select(KnowledgeBaseMetadata)
        if not include_deleted:
            stmt = stmt.where(KnowledgeBaseMetadata.status != "deleted")
        rows = (await self.db.execute(stmt)).scalars().all()
        return list(rows)

    async def get_by_dataset_id(self, dataset_id: str) -> Optional[KnowledgeBaseMetadata]:
        stmt = select(KnowledgeBaseMetadata).where(KnowledgeBaseMetadata.ragflow_dataset_id == dataset_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def find_active_by_name(self, name: str) -> Optional[KnowledgeBaseMetadata]:
        """按名称查未删除的知识库元数据（建库重名校验用）。

        RAGFlow 侧允许同名 dataset，平台侧必须自己兜住：一旦重名，
        「建库返回缺 id 时按名字反查」就会取到任意一个，可能绑到别人的库上，
        随后还会把 owner_user_id 写成当前用户（越权）。宁可在建库前直接拒绝。
        """
        token = (name or "").strip()
        if not token:
            return None
        stmt = (
            select(KnowledgeBaseMetadata)
            .where(
                KnowledgeBaseMetadata.name == token,
                KnowledgeBaseMetadata.status != "deleted",
            )
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def upsert_metadata(
        self,
        *,
        ragflow_dataset_id: str,
        name: str,
        description: Optional[str] = None,
        owner: Optional[str] = None,
        owner_dept_id: Optional[int] = None,
        owner_user_id: Optional[int] = None,
        visibility: Optional[str] = None,
        tags: Optional[List[str]] = None,
        notes: Optional[str] = None,
        extra_config: Optional[Dict[str, Any]] = None,
        status: str = "active",
        user_name: Optional[str] = None,
    ) -> KnowledgeBaseMetadata:
        record = await self.get_by_dataset_id(ragflow_dataset_id)
        is_new = record is None
        if is_new:
            record = KnowledgeBaseMetadata(
                ragflow_dataset_id=ragflow_dataset_id,
                name=name,
                created_by=user_name,
            )
            self.db.add(record)

        record.name = name
        record.description = description
        record.owner = owner
        record.owner_dept_id = int(owner_dept_id) if owner_dept_id else None
        # 归属人只在显式传值时改写：同步/批量更新不能把已转移的归属重置成"同步者"。
        if owner_user_id is not None:
            record.owner_user_id = int(owner_user_id)
        # 只在显式传值时改写可见范围：更新路径漏传参数不能把库悄悄重置成 private
        # （默认值曾经是 "private"，任何一次不带 visibility 的 upsert 都会静默降级）
        if visibility is not None:
            record.visibility = _normalize_visibility(visibility, fallback="private")
        elif is_new:
            record.visibility = _normalize_visibility("private", fallback="private")
        record.tags = tags or []
        record.notes = notes
        record.extra_config = extra_config or {}
        record.status = status
        record.updated_by = user_name
        record.updated_at = datetime.now()
        await self.db.flush()
        return record

    async def update_metadata(
        self,
        dataset_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        owner: Any = _UNSET,
        owner_dept_id: Any = _UNSET,
        owner_user_id: Any = _UNSET,
        visibility: Optional[str] = None,
        tags: Optional[List[str]] = None,
        notes: Optional[str] = None,
        extra_config: Optional[Dict[str, Any]] = None,
        user_name: Optional[str] = None,
    ) -> Optional[KnowledgeBaseMetadata]:
        record = await self.get_by_dataset_id(dataset_id)
        if not record:
            record = KnowledgeBaseMetadata(
                ragflow_dataset_id=dataset_id,
                name=name if name is not None else "未命名知识库",
                created_by=user_name,
                status="active",
            )
            self.db.add(record)

        if name is not None:
            record.name = name
        if description is not None:
            record.description = description
        if owner is not _UNSET:
            record.owner = owner or None
        if owner_dept_id is not _UNSET:
            record.owner_dept_id = int(owner_dept_id) if owner_dept_id else None
        if owner_user_id is not _UNSET:
            # 传 0/None = 清空归属人（此后判定回落 created_by 文本）
            record.owner_user_id = int(owner_user_id) if owner_user_id else None
        if visibility is not None:
            record.visibility = _normalize_visibility(visibility, fallback="public")
        if tags is not None:
            record.tags = tags
        if notes is not None:
            record.notes = notes
        if extra_config is not None:
            record.extra_config = extra_config
        record.updated_by = user_name
        record.updated_at = datetime.now()
        await self.db.flush()
        return record

    async def update_extra_config_key(
        self,
        dataset_id: str,
        *,
        key: str,
        value: Any,
        user_name: Optional[str] = None,
    ) -> Optional[KnowledgeBaseMetadata]:
        """只改 extra_config 的一个子键，其余键原样保留。

        虚拟文件夹原先走整份 ``PUT /metadata``：移动一个文档会把 name/visibility
        等前端缓存值一起回写，并发操作就是 lost update。这里把「改目录结构」
        收敛成一个明确的局部写。
        """
        record = await self.get_by_dataset_id(dataset_id)
        if not record or record.status == "deleted":
            return None
        # 复制一份再赋值：SQLAlchemy 的 JSON 列靠引用变化检测变更，原地改不会落库
        config = dict(record.extra_config or {})
        if value in (None, {}, []):
            config.pop(key, None)
        else:
            config[key] = value
        record.extra_config = config
        record.updated_by = user_name
        record.updated_at = datetime.now()
        if self.db is not None:
            await self.db.flush()
        return record

    async def transfer_owner(
        self,
        dataset_id: str,
        *,
        target_user_id: int,
        user_name: Optional[str] = None,
    ) -> Optional[tuple[KnowledgeBaseMetadata, Optional[int]]]:
        """把知识库归属人转给另一个用户，返回 ``(记录, 原归属人 ID)``。

        只改 ``owner_user_id``，**刻意不改** ``created_by``：那是真实的创建记录，
        转移时覆盖掉就再没人能说出这个库最初是谁建的。归属人本就是独立的业务概念。

        元数据不存在或已软删时返回 ``None``（调用方按 404 处理）；目标用户的合法性
        由调用方校验，这里只负责落库。
        """
        record = await self.get_by_dataset_id(dataset_id)
        if not record or record.status == "deleted":
            return None
        previous_owner_user_id = record.owner_user_id
        record.owner_user_id = int(target_user_id)
        record.updated_by = user_name
        record.updated_at = datetime.now()
        await self.db.flush()
        return record, previous_owner_user_id

    async def mark_deleted(self, dataset_ids: Iterable[str], user_name: Optional[str] = None) -> None:
        for dataset_id in dataset_ids:
            record = await self.get_by_dataset_id(dataset_id)
            if record:
                record.status = "deleted"
                record.updated_by = user_name
                record.updated_at = datetime.now()
        await self.db.flush()

    async def sync_from_ragflow_datasets(
        self,
        ragflow_datasets: List[Dict[str, Any]],
        user_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        # dataset_ids 记录「本次真正被创建/更新的库」，供审计按库落条；
        # skipped（meta-* 等）不算受影响，避免审计里出现噪音。
        result: Dict[str, Any] = {
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "dataset_ids": [],
        }
        for ds in ragflow_datasets:
            dataset_id = str(ds.get("id") or "").strip()
            name = str(ds.get("name") or "").strip()
            if not dataset_id or is_internal_dataset_name(name):
                result["skipped"] += 1
                continue

            description = ds.get("description")
            record = await self.get_by_dataset_id(dataset_id)
            if record:
                record.name = name or record.name
                record.description = description
                record.status = "active"
                record.updated_by = user_name
                record.updated_at = datetime.now()
                result["updated"] += 1
                result["dataset_ids"].append(dataset_id)
                continue

            await self.upsert_metadata(
                ragflow_dataset_id=dataset_id,
                name=name or dataset_id,
                description=description,
                # 同步进来的库不是"某人新建的私有资料"，而是平台已存在的共享资源：
                # 沿用迁移后的统一口径 public（需授权），否则一次同步就会把它们
                # 全部变成执行同步者的个人私有库，其他人连访问入口都没有。
                visibility="public",
                status="active",
                user_name=user_name,
            )
            result["created"] += 1
            result["dataset_ids"].append(dataset_id)

        if self.db is not None:
            await self.db.flush()
        return result

    async def merge_with_ragflow(
        self,
        ragflow_datasets: List[Dict[str, Any]],
        include_missing: bool = True,
        missing_allow_ids: Optional[set[str]] = None,
    ) -> List[Dict[str, Any]]:
        metadata_rows = await self.list_metadata(include_deleted=False)
        metadata_by_id = {m.ragflow_dataset_id: m for m in metadata_rows}
        ragflow_by_id = {str(ds.get("id")): ds for ds in ragflow_datasets if ds.get("id")}

        merged: List[Dict[str, Any]] = []
        for ds in ragflow_datasets:
            dataset_id = str(ds.get("id", ""))
            local = metadata_by_id.get(dataset_id)
            item = dict(ds)
            item["ragflow_dataset_id"] = dataset_id
            item["local_metadata"] = self._serialize_metadata(local) if local else None
            item["platform_name"] = local.name if local else ds.get("name")
            item["platform_description"] = local.description if local else ds.get("description")
            item["owner"] = local.owner if local else None
            item["owner_dept_id"] = local.owner_dept_id if local else None
            item["owner_user_id"] = local.owner_user_id if local else None
            item["tags"] = local.tags if local else []
            item["notes"] = local.notes if local else None
            # 元数据缺失（RAGFlow 已建、本地尚未同步）时按 public 兜底：
            # 与权限判定的兜底口径一致（未知可见范围按"需要授权"处理），避免前端
            # 显示"个人私有"、隐藏授权入口，实际却又按 public 判定。
            item["visibility"] = local.visibility if local else "public"
            item["status"] = local.status if local else "active"
            item["created_by"] = local.created_by if local else None
            item["is_missing_in_ragflow"] = False
            merged.append(item)

        if include_missing:
            for dataset_id, local in metadata_by_id.items():
                if dataset_id in ragflow_by_id:
                    continue
                # 过滤掉特殊的案例库和元数据管理库的失联记录
                if is_internal_dataset_name(local.name):
                    continue
                # 非管理员只看到自己范围内的失联库：否则「失联」会变成
                # 看穿全平台残留元数据的后门（库名、归属人、部门全暴露）。
                if missing_allow_ids is not None and dataset_id not in missing_allow_ids:
                    continue
                merged.append({
                    "id": dataset_id,
                    "ragflow_dataset_id": dataset_id,
                    "name": local.name,
                    "description": local.description,
                    "doc_count": 0,
                    "chunk_count": 0,
                    "update_time": None,
                    "local_metadata": self._serialize_metadata(local),
                    "platform_name": local.name,
                    "platform_description": local.description,
                    "owner": local.owner,
                    "owner_dept_id": local.owner_dept_id,
                    "owner_user_id": local.owner_user_id,
                    "created_by": local.created_by,
                    "tags": local.tags or [],
                    "notes": local.notes,
                    "visibility": local.visibility,
                    "status": "missing",
                    "is_missing_in_ragflow": True,
                })

        return merged

    def _serialize_metadata(self, record: Optional[KnowledgeBaseMetadata]) -> Optional[Dict[str, Any]]:
        if not record:
            return None
        return {
            "id": record.id,
            "ragflow_dataset_id": record.ragflow_dataset_id,
            "name": record.name,
            "description": record.description,
            "owner": record.owner,
            "owner_dept_id": record.owner_dept_id,
            "owner_user_id": record.owner_user_id,
            "visibility": record.visibility,
            "tags": record.tags or [],
            "notes": record.notes,
            "extra_config": record.extra_config or {},
            "status": record.status,
            "created_by": record.created_by,
            "updated_by": record.updated_by,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }
