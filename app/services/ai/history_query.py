"""会话历史查询构建。

把多维度筛选条件收敛为可独立单测的纯函数，避免在 endpoint 内联拼装查询时
遗漏语义分层：会话级属性（来源 / 智能体 / 时间）过滤分组代表行，轮次级属性
（关键词 / 状态）需判定「会话内是否存在满足条件的轮次」。
"""

from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy import Select, func, not_, or_, select
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.models.audit import AgentExecutionHistory as History


TASK_CONVERSATION_PREFIX = "task_conv_"
VALID_SCOPES = ("all", "task", "chat")


def normalize_scope(scope: Optional[str]) -> str:
    """把空值与非法取值统一降级为 all，避免前端灰度期出现硬失败。"""
    value = str(scope or "").strip().lower()
    return value if value in VALID_SCOPES else "all"


def conversation_key(model=History) -> ColumnElement:
    """会话分组键。

    必须与分组使用的键完全一致；``trace_id`` 为 NOT NULL，因此该表达式
    永不为 NULL，可安全用于 ``IN`` 子查询而不受 SQL 三值逻辑影响。
    """
    return func.coalesce(model.conversation_id, model.trace_id)


def scope_condition(scope: Optional[str]) -> Optional[ColumnElement]:
    """会话来源过滤，属于会话级属性，可安全用于分组代表行。"""
    normalized = normalize_scope(scope)
    if normalized == "task":
        return History.conversation_id.like(f"{TASK_CONVERSATION_PREFIX}%")
    if normalized == "chat":
        # NULL 必须显式包含：SQL 中 NULL NOT LIKE 'x' 的结果是 NULL（非真），
        # 若不处理会导致 conversation_id 为空的历史在「普通对话」下消失。
        return or_(
            History.conversation_id.is_(None),
            not_(History.conversation_id.like(f"{TASK_CONVERSATION_PREFIX}%")),
        )
    return None


def turn_level_conversation_keys(
    *,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    keyword: Optional[str] = None,
    status: Optional[str] = None,
) -> Select:
    """返回「存在满足条件轮次」的会话键集合。

    关键词与状态被合并进同一个子查询，因此语义是「同一会话内存在同时满足
    两者的轮次」，而不是「某轮命中关键词」且「另一轮失败」。

    用户约束基于子查询自身的表别名重建：子查询与外层引用同一张表，直接复用
    外层实体表达式会产生同表引用歧义。
    """
    turn = aliased(History)
    conditions = []
    if user_id is not None:
        conditions.append(turn.user_id == user_id)
    elif username:
        conditions.append(turn.username == username)
    if keyword:
        pattern = f"%{keyword}%"
        conditions.append(or_(turn.query.like(pattern), turn.summary.like(pattern)))
    if status:
        conditions.append(turn.status == status)
    return select(conversation_key(turn)).where(*conditions).distinct()


def build_history_query(
    *,
    page: int = 1,
    page_size: int = 20,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    agent_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    keyword: Optional[str] = None,
    status: Optional[str] = None,
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
    scope: Optional[str] = None,
    group_by_conversation: bool = False,
) -> Tuple[Select, Select]:
    """构建历史列表查询与总数查询。

    返回 ``(rows_query, count_query)``；``count_query`` 在分页之前构建，
    因此总数统计不受 offset / limit 影响。
    """
    session_filters = []
    if user_id is not None:
        session_filters.append(History.user_id == user_id)
    elif username:
        session_filters.append(History.username == username)

    source = scope_condition(scope)
    if source is not None:
        session_filters.append(source)

    if group_by_conversation:
        grouped = (
            select(
                func.max(History.id).label("max_id"),
                func.count(History.id).label("turn_count"),
            )
            .where(*session_filters)
            .group_by(conversation_key())
            .subquery()
        )
        query = select(History, grouped.c.turn_count).join(
            grouped, History.id == grouped.c.max_id
        )
    else:
        query = select(History)

    if session_filters:
        query = query.where(*session_filters)

    if agent_id:
        query = query.where(History.agent_id == agent_id)
    if conversation_id:
        query = query.where(History.conversation_id == conversation_id)
    if start_dt:
        query = query.where(History.created_at >= start_dt)
    if end_dt:
        query = query.where(History.created_at <= end_dt)

    if group_by_conversation:
        # 轮次级条件：代表行的会话键命中即等价于该会话存在匹配轮次，
        # 因为同一会话的所有行共享同一会话键。
        if keyword or status:
            query = query.where(
                conversation_key().in_(
                    turn_level_conversation_keys(
                        user_id=user_id,
                        username=username,
                        keyword=keyword,
                        status=status,
                    )
                )
            )
    else:
        # 非分组模式每行即一轮，直接过滤当前行，不套用会话级存在性判定。
        if keyword:
            pattern = f"%{keyword}%"
            query = query.where(
                or_(History.query.like(pattern), History.summary.like(pattern))
            )
        if status:
            query = query.where(History.status == status)

    count_query = select(func.count()).select_from(query.subquery())
    query = (
        query.order_by(History.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return query, count_query
