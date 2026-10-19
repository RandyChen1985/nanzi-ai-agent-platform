"""工作台「最近会话」装载。

与数据门户首页「最近分析」刻意分开：门户只关心 ChatBI 数据分析会话，
工作台要覆盖当前用户与**全部智能体**的普通对话。

查询语义与智能助手历史列表保持一致，复用 ``history_query`` 的纯函数：

* 会话键 ``coalesce(conversation_id, trace_id)``（见 ``conversation_key``）
* ``scope='chat'`` 排除 ``task_conv_`` 前缀的定时任务会话（见 ``scope_condition``）

只保留 ``conversation_id`` 非空的会话：工作台卡片的「继续对话」依赖它跳转，
没有会话 ID 的历史既无法恢复上下文，也无法去重。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AIAgent
from app.models.audit import AgentExecutionHistory as History
from app.services.ai.history_query import conversation_key, scope_condition

logger = logging.getLogger(__name__)

# 与门户首页保持同样的候选窗口：先取最近 N 个会话，再由 payload 层裁剪为 4 条
RECENT_CONVERSATION_QUERY_LIMIT = 30
TITLE_LIMIT = 60
UNKNOWN_AGENT_LABEL = "未知智能体"


def _iso(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value or "")


def conversation_title(raw: Any) -> str:
    """会话标题：压平换行/连续空白，避免卡片被多行 query 撑开。"""
    text = re.sub(r"\s+", " ", str(raw or "").replace("\r", "\n")).strip()
    if not text:
        return "未命名会话"
    if len(text) <= TITLE_LIMIT:
        return text
    return text[: TITLE_LIMIT - 1].rstrip() + "…"


def _agent_label(identity: Optional[Sequence[Any]]) -> str:
    """智能体展示名：``build_agent_identity_map`` 的顺序是 (name, display_name, avatar)，
    因此必须显式先取 display_name，物理 name 只作兜底。"""
    if not identity:
        return UNKNOWN_AGENT_LABEL
    for index in (1, 0):
        if index >= len(identity):
            continue
        label = str(identity[index] or "").strip()
        if label:
            return label
    return UNKNOWN_AGENT_LABEL


def conversation_items_from_rows(
    rows: Sequence[Any],
    identity_map: Mapping[str, Sequence[Any]],
) -> List[Dict[str, Any]]:
    """把会话代表行组装成工作台卡片，按会话去重并保持时间倒序。

    副标题只放智能体展示名：成功/失败状态由 ``WorkbenchItemMeta`` 的徽标承载，
    这里再拼一次状态会和徽标重复。
    """
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        conversation_id = str(getattr(row, "conversation_id", "") or "").strip()
        if not conversation_id or conversation_id in seen:
            continue
        seen.add(conversation_id)

        agent_id = str(getattr(row, "agent_id", "") or "")
        target: Dict[str, Any] = {"conversation_id": conversation_id}
        if agent_id:
            target["agent_id"] = agent_id

        items.append(
            {
                "id": f"conversation:{getattr(row, 'id', '')}",
                "business_key": f"conversation:{conversation_id}",
                "type": "conversation",
                "title": conversation_title(getattr(row, "query", None)),
                "subtitle": _agent_label(identity_map.get(agent_id)),
                "occurred_at": _iso(getattr(row, "created_at", None)),
                "status": str(getattr(row, "status", "") or "success"),
                "action": "open_conversation",
                "target": target,
            }
        )
    return items


async def _load_agent_identity(
    db: AsyncSession,
    agent_ids: Sequence[str],
) -> Dict[str, Tuple[Any, ...]]:
    """按会话里出现的 agent_id 回填展示名。

    刻意不走 ``list_allowed_agents``：停用/已下架智能体的历史会话仍要显示名字，
    否则用户看到的是「未知智能体」。
    """
    from app.services.ai.agent_manager import build_agent_identity_map

    unique_ids = sorted({str(value) for value in agent_ids if value})
    if not unique_ids:
        return {}
    result = await db.execute(select(AIAgent).where(AIAgent.id.in_(unique_ids)))
    return build_agent_identity_map(result.scalars().all())


async def list_recent_conversations(
    db: AsyncSession,
    *,
    user_id: int,
    limit: int = RECENT_CONVERSATION_QUERY_LIMIT,
) -> List[Dict[str, Any]]:
    """当前用户与全部智能体的最近会话（排除定时任务会话）。"""
    filters = [
        History.user_id == str(user_id),
        History.conversation_id.isnot(None),
        # 空串/纯空白不是有效会话：它们会在分组后占一个 limit 名额，
        # 再在 Python 里被 strip 掉，等于白丢一条最近会话。
        func.trim(History.conversation_id) != "",
    ]
    source_scope = scope_condition("chat")
    if source_scope is not None:
        filters.append(source_scope)

    # 每个会话取最新一轮作为代表行：工作台展示的是「会话」而不是「对话轮次」。
    latest_turn = (
        select(func.max(History.id).label("max_id"))
        .where(*filters)
        .group_by(conversation_key())
        .subquery()
    )
    result = await db.execute(
        select(History)
        .join(latest_turn, History.id == latest_turn.c.max_id)
        .order_by(desc(History.created_at), desc(History.id))
        .limit(int(limit))
    )
    rows = list(result.scalars().all())
    identity_map = await _load_agent_identity(
        db, [str(row.agent_id) for row in rows if getattr(row, "agent_id", None)]
    )
    return conversation_items_from_rows(rows, identity_map)
