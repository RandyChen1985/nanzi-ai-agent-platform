"""数据集业务审计写入。

与 ``KnowledgeAuditService`` 同一套做法，理由也一样：

- 全站访问日志是**每个请求一条**的流水，默认只查 90 天且通常定期清理，
  而「这个数据集是谁授权给谁的」必须长期可查；
- 访问日志里的 resource_id 埋在 ``request_params`` 的 JSON 文本里，
  按数据集查询只能全表 LIKE；
- ``ResourcePermission`` 只有当前状态，撤销即消失，没有历史。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Optional

from app.core.orm import AsyncSessionLocal
from app.models.metadata import MetadataAuditLog

logger = logging.getLogger(__name__)


def _normalize_dataset_ids(dataset_ids: Iterable[Any]) -> list[int]:
    """去重保序地归一化数据集 ID，丢掉空值与非整数。"""
    seen: set[int] = set()
    result: list[int] = []
    for raw in dataset_ids or []:
        try:
            token = int(raw)
        except (TypeError, ValueError):
            continue
        if token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


class MetadataAuditService:
    """写入数据集业务审计。"""

    @staticmethod
    async def log(
        *,
        metadata_dataset_ids: Iterable[Any],
        action: str,
        user: Optional[Dict[str, Any]] = None,
        status_code: int = 200,
        detail: Optional[Dict[str, Any]] = None,
        client_ip: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """写一条（或按数据集拆成多条）审计记录。

        三个刻意的设计，与知识库审计一致：

        1. **独立 session**：业务事务回滚时审计要能留下，审计自身出错也不能
           把正常操作打成 5xx；
        2. **按数据集拆条**：批量操作给每个数据集各写一条，查任意一个数据集
           都能看到它自己经历过什么；
        3. **失败只记日志不抛**：审计是旁路，不能反过来影响业务可用性。
        """
        ids = _normalize_dataset_ids(metadata_dataset_ids)
        if not ids:
            return False

        actor = user or {}
        try:
            user_id = int(actor["user_id"]) if actor.get("user_id") is not None else None
        except (TypeError, ValueError):
            user_id = None

        try:
            async with AsyncSessionLocal() as session:
                session.add_all(
                    [
                        MetadataAuditLog(
                            metadata_dataset_id=dataset_id,
                            action=action,
                            user_id=user_id,
                            user_name=actor.get("user_name"),
                            status_code=status_code,
                            detail=detail or None,
                            client_ip=client_ip,
                            error_message=error_message,
                        )
                        for dataset_id in ids
                    ]
                )
                await session.commit()
            return True
        except Exception as exc:  # noqa: BLE001 - 审计失败不能影响主流程
            logger.error(
                "写入数据集审计失败（已忽略）: action=%s ids=%s err=%s",
                action,
                ids,
                exc,
            )
            return False
