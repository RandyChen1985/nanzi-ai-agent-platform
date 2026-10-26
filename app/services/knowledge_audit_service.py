"""知识库业务审计写入。

单独一张 `ai_agent_knowledge_audit_logs`，不复用 `ai_agent_access_logs`：

- 访问日志是**每个请求一条**的全站流水，默认只查 90 天且通常定期清理；
  业务审计要回答「这个库是谁转交出去的」，必须长期可查；
- `dataset_id` 在访问日志里是埋在 `request_params` JSON 文本中的，
  按库查询只能全表 LIKE，库一多就查不动。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Optional

from app.core.orm import AsyncSessionLocal
from app.models.knowledge import KnowledgeAuditLog

logger = logging.getLogger(__name__)

#: 没有单一库归属的全局操作（如「从 RAGFlow 同步」）。按库查询不会命中它，
#: 只出现在全局审计视图里，避免与真实 dataset_id 混淆。
GLOBAL_AUDIT_DATASET_ID = "__global__"


def _normalize_dataset_ids(dataset_ids: Iterable[Any]) -> list[str]:
    """去重保序地归一化数据集 ID，丢掉空值。"""
    seen: set[str] = set()
    result: list[str] = []
    for raw in dataset_ids or []:
        token = str(raw or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


class KnowledgeAuditService:
    """写入知识库业务审计。"""

    @staticmethod
    async def log(
        *,
        dataset_ids: Iterable[Any],
        action: str,
        user: Optional[Dict[str, Any]] = None,
        status_code: int = 200,
        detail: Optional[Dict[str, Any]] = None,
        client_ip: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """写一条（或按库拆成多条）审计记录。

        三个刻意的设计：

        1. **独立 session**：业务事务回滚时审计要能留下（失败了也要知道谁试过），
           同时审计自身出错绝不能把正常操作打成 5xx；
        2. **按库拆条**：批量删库、跨库检索给每个库各写一条，查任意一个库
           都能看到它自己经历过什么；
        3. **失败只记日志不抛**：审计是旁路，不能反过来影响业务可用性。

        返回是否写入成功，便于调用方在测试中断言。
        """
        ids = _normalize_dataset_ids(dataset_ids)
        if not ids:
            return False

        actor = user or {}
        try:
            user_id = int(actor["user_id"]) if actor.get("user_id") is not None else None
        except (TypeError, ValueError):
            user_id = None
        user_name = actor.get("user_name")

        try:
            async with AsyncSessionLocal() as session:
                session.add_all(
                    [
                        KnowledgeAuditLog(
                            dataset_id=dataset_id,
                            action=action,
                            user_id=user_id,
                            user_name=user_name,
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
                "写入知识库审计失败（已忽略）: action=%s dataset_ids=%s err=%s",
                action,
                ids,
                exc,
            )
            return False
