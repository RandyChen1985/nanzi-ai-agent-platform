"""知识库配额：防止单个库被传成垃圾场，也防止一个人无限建库。

默认值取「够用但不至于失控」，管理员可通过配置项调整，不需要迁移。
超限直接拒绝并说明「现在多少 / 上限多少 / 该怎么办」——只说「超出配额」
等于让用户自己猜。
"""

from typing import Optional

from sqlalchemy import func, select

from app.models.knowledge import KnowledgeBaseMetadata
from app.services.config_service import ConfigService

DATASET_LIMIT_KEY = "knowledge_quota_max_datasets_per_user"
DOCUMENT_LIMIT_KEY = "knowledge_quota_max_documents_per_dataset"

DEFAULT_DATASET_LIMIT = 50
DEFAULT_DOCUMENT_LIMIT = 2000


class QuotaExceeded(Exception):
    """配额不足。端点上转成 400 + 可读文案。"""


async def _positive_int_config(key: str, default: int) -> int:
    raw = await ConfigService.get(key)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    # <=0 视为「不限」，避免管理员填 0 时把功能锁死
    return value


class KnowledgeQuotaService:
    @classmethod
    async def limits(cls) -> dict:
        return {
            "max_datasets_per_user": await _positive_int_config(
                DATASET_LIMIT_KEY, DEFAULT_DATASET_LIMIT
            ),
            "max_documents_per_dataset": await _positive_int_config(
                DOCUMENT_LIMIT_KEY, DEFAULT_DOCUMENT_LIMIT
            ),
        }

    @classmethod
    async def owned_dataset_count(cls, db, user_id: int) -> int:
        """只数「自己拥有」的库：被授权进来的库不该占他的额度。"""
        total = (
            await db.execute(
                select(func.count())
                .select_from(KnowledgeBaseMetadata)
                .where(
                    KnowledgeBaseMetadata.owner_user_id == user_id,
                    KnowledgeBaseMetadata.status != "deleted",
                )
            )
        ).scalar()
        return int(total or 0)

    @classmethod
    async def ensure_can_create_dataset(cls, db, user_id: int) -> None:
        limit = await _positive_int_config(DATASET_LIMIT_KEY, DEFAULT_DATASET_LIMIT)
        if limit <= 0:
            return
        used = await cls.owned_dataset_count(db, user_id)
        if used >= limit:
            raise QuotaExceeded(
                f"你已拥有 {used} 个知识库，达到上限 {limit} 个。"
                "请先删除不再使用的库，或联系管理员调整配额。"
            )

    @classmethod
    async def ensure_can_upload(cls, client, dataset_id: str, incoming: int = 1) -> None:
        limit = await _positive_int_config(DOCUMENT_LIMIT_KEY, DEFAULT_DOCUMENT_LIMIT)
        if limit <= 0 or incoming <= 0:
            return
        try:
            used: Optional[int] = await client.count_documents(dataset_id)
        except Exception:
            # 统计失败（引擎抖动 / 权限异常）不该变成"完全不能上传"：
            # 配额是防滥用，不是可用性前提
            return
        if used is None:
            # 拿不到总数时不拦：宁可放过一次，也不要因为引擎不回 total 就完全不能上传
            return
        if used + incoming > limit:
            raise QuotaExceeded(
                f"该知识库已有 {used} 个文档，上限 {limit} 个；本次还要上传 {incoming} 个。"
                "请先清理不再需要的文档，或联系管理员调整配额。"
            )

    @classmethod
    async def snapshot(cls, client, dataset_id: str, db=None, user_id: Optional[int] = None) -> dict:
        limits = await cls.limits()
        used_docs = await client.count_documents(dataset_id)
        payload = {
            "documents": {
                "used": used_docs,
                "limit": limits["max_documents_per_dataset"],
            },
            "max_datasets_per_user": limits["max_datasets_per_user"],
        }
        if db is not None and user_id:
            payload["owned_datasets"] = {
                "used": await cls.owned_dataset_count(db, int(user_id)),
                "limit": limits["max_datasets_per_user"],
            }
        return payload
