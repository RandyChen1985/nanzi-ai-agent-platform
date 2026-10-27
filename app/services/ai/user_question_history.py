"""把提问卡的终态写回历史轮次，并补一条「已取消」轮次。

「取消提问」不会产生一条新的历史轮次：取消回执在 ``ContextStep`` 就把
``execution_status`` 置为 ``cancelled`` 并返回，而 ``agent_config`` 要等到
``RouteStep`` 才解析；``PipelineRunner`` 对 ``cancelled`` 会跳过中间步骤直达
``FinalizeStep``，于是 ``finalize`` 里的 ``if agent_config:`` 为假，既不会写
``ai_agent_execution_history``，也不会留下回执消息。

前端历史回放本来就依赖「回执消息」把卡片从 pending 回填为 cancelled；取消轮不落库，
刷新后卡片就会重新变成「等待回答」并允许再次点击（后端再报「问题已取消」）。
这里做两件事：

1. 把终态写进**提问轮自己**的 timeline 快照（历史回放不再依赖取消轮）；
2. 补一条 ``status=cancelled`` 的历史轮次，让刷新后仍能看到那条「已取消」回复，
   并用回执消息作为双保险。

``trace_id`` 是唯一键：重复提交/重试会先查后插并吞掉唯一冲突，天然幂等。
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from app.core.orm import AsyncSessionLocal

logger = logging.getLogger(__name__)

QUESTION_LOG_CATEGORY = "user_question"
_RESOLVED_LOG_STATUS = "success"
CANCELLATION_MESSAGE = "已取消本次提问，本次任务已停止。"


def _iter_log_items(items: Iterable[Any] | None):
    """深度优先遍历 timeline 里的日志项（提问卡可能挂在 children 里）。"""
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        yield item
        children = item.get("children")
        if isinstance(children, list):
            yield from _iter_log_items(children)


def _matches_question(item: dict[str, Any], question_id: str) -> bool:
    if str(item.get("category") or "") != QUESTION_LOG_CATEGORY:
        return False
    if str(item.get("id") or "") == f"user_question_{question_id}":
        return True
    snapshot = item.get("user_question")
    return isinstance(snapshot, dict) and str(snapshot.get("question_id") or "") == question_id


def apply_question_status_to_timeline(
    items: Iterable[Any] | None,
    question_id: str,
    status: str,
) -> bool:
    """把匹配问题的日志项标为终态。

    - 日志项自身 ``status`` 置为 ``success``（与「等待类步骤有结果即 success」的惯例一致）；
    - ``user_question`` 快照追加 ``status``，供前端重建卡片时直接读到 cancelled/submitted。

    返回是否真的改动了内容；多个会话共用同一 question_id 的概率极低，但这里仍只改
    命中项、不动其它轮次。
    """
    normalized_question_id = str(question_id or "").strip()
    if not normalized_question_id:
        return False
    normalized_status = str(status or "").strip() or "submitted"
    changed = False
    for item in _iter_log_items(items):
        if not _matches_question(item, normalized_question_id):
            continue
        item["status"] = _RESOLVED_LOG_STATUS
        snapshot = item.get("user_question")
        if isinstance(snapshot, dict):
            snapshot["status"] = normalized_status
        changed = True
    return changed


async def persist_user_question_cancellation(
    *,
    conversation_id: str,
    question_id: str,
    trace_id: str,
    user_query: str,
    cancellation_message: str = CANCELLATION_MESSAGE,
    fallback_agent_id: str = "",
    fallback_user_id: str = "",
) -> bool:
    """取消提问后收尾：写回提问轮快照 + 补一条 cancelled 历史轮次。

    - 只扫最近 5 轮：提问卡与它的作答/取消必然相邻，扫整段历史既慢又可能命中同名旧问题；
    - ``agent_id`` 是非空约束：从该会话最近一轮继承，没有历史时用调用方传入的兜底值，
      仍为空则只更新快照、不插入历史（返回 False，由调用方记日志）；
    - ``trace_id`` 唯一冲突视为已写入（并发重试），不抛错、不影响取消流程。
    """
    if not conversation_id or not question_id or not trace_id:
        return False

    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.audit import AgentExecutionHistory

    async with AsyncSessionLocal() as session:
        existing = await session.execute(
            select(AgentExecutionHistory.id)
            .where(AgentExecutionHistory.trace_id == trace_id)
            .limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            return True

        recent = (
            (
                await session.execute(
                    select(AgentExecutionHistory)
                    .where(AgentExecutionHistory.conversation_id == conversation_id)
                    .order_by(AgentExecutionHistory.id.desc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )

        agent_id = str(fallback_agent_id or "").strip()
        user_id = str(fallback_user_id or "").strip() or None
        username: str | None = None
        for row in recent:
            if not agent_id and row.agent_id:
                agent_id = str(row.agent_id)
            if not user_id and row.user_id:
                user_id = str(row.user_id)
            if not username and row.username:
                username = str(row.username)
            if isinstance(row.process_timeline, list) and apply_question_status_to_timeline(
                row.process_timeline, question_id, "cancelled"
            ):
                flag_modified(row, "process_timeline")

        # 幂等兜底：用户连点「取消」会各自生成新的 trace_id，光靠 trace 去重挡不住。
        # 同一 question_id 的取消轮只允许存在一条（回执原文里带着 question_id）。
        already_logged = any(
            str(row.status or "") == "cancelled" and question_id in str(row.query or "")
            for row in recent
        )

        if agent_id and not already_logged:
            session.add(
                AgentExecutionHistory(
                    agent_id=agent_id,
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    username=username,
                    query=user_query,
                    summary=cancellation_message,
                    status="cancelled",
                )
            )
        else:
            if not agent_id:
                logger.warning(
                    "[UserQuestion] Skip cancellation history: no agent_id conversation=%s trace=%s",
                    conversation_id,
                    trace_id,
                )

        try:
            await session.commit()
        except IntegrityError:
            # 并发下的重复写入：另一请求已经补过这一轮，回执与快照以它为准。
            await session.rollback()
            return True
        return bool(agent_id)
