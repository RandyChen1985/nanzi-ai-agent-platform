"""知识库 → 智能体的反向引用查询。

绑定关系存在 ``ai_agents.engine_config.dataset_ids``（JSON）。数量级是
「智能体几百个 × 每个几个库」，实时扫描内存聚合比维护一张双写映射表更可靠：
不存在「保存智能体时忘了写映射」的漂移，也不需要回填历史数据。

用途只有一个：在删除知识库、收窄可见范围之前回答「会影响哪些智能体」。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AIAgent
from app.services.ai.knowledge_utils import merge_dataset_id_sources


def engine_dataset_ids(engine_config: Any) -> List[str]:
    """从 engine_config 取 dataset_ids，兼容 JSON 字符串、字符串、列表。

    ``engine_config`` 在库里是 JSON 列，但历史数据与手工 SQL 写入过字符串，
    因此不能假设它一定是 dict。解析失败一律当作「没有绑定」，不让脏数据
    把整个列表接口打挂。
    """
    config = engine_config
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except (TypeError, ValueError):
            return []
    if not isinstance(config, dict):
        return []
    return merge_dataset_id_sources(config.get("dataset_ids"))


class KnowledgeReferenceService:
    """查询知识库被哪些智能体引用。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def agents_by_dataset(
        self, dataset_ids: Iterable[str]
    ) -> Dict[str, List[Dict[str, Any]]]:
        wanted = {str(item).strip() for item in dataset_ids if str(item).strip()}
        if not wanted:
            return {}

        rows = (
            await self.db.execute(
                select(
                    AIAgent.id,
                    AIAgent.name,
                    AIAgent.display_name,
                    AIAgent.is_enabled,
                    AIAgent.engine_config,
                )
            )
        ).all()

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for agent_id, name, display_name, is_enabled, engine_config in rows:
            for dataset_id in engine_dataset_ids(engine_config):
                if dataset_id not in wanted:
                    continue
                grouped.setdefault(dataset_id, []).append(
                    {
                        "id": agent_id,
                        "name": name,
                        "display_name": display_name or name,
                        "is_enabled": bool(is_enabled),
                    }
                )

        # 启用的排前面，其次按显示名，保证列表稳定
        for items in grouped.values():
            items.sort(key=lambda item: (not item["is_enabled"], item["display_name"]))
        return grouped
