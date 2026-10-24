"""``ai_agents.system_status`` / ``system_pending_request_id`` 的列探测与读写。

为什么不在 ORM 里声明这两列
---------------------------
代码可能**先于迁移**上线（V185 / V85）。一旦在 ``AIAgent`` 上声明，SQLAlchemy 会把它们
带进 ``SELECT ai_agents.*`` 与每次 ``INSERT``：迁移没执行时智能体列表、对话、委派全部
报 1054 变成 5xx——用一个新功能把核心功能打挂，和 ``ResourcePermission.agent_id`` 那次
事故是同一类问题。因此和那张表一样：**ORM 不映射，走原生 SQL + 列探测**。

未迁移时的降级
--------------
探测到列缺失时 ``supports_system_status()`` 返回 False，此时：

* 列表/对话/委派完全不受影响（它们根本不读这两列）；
* 申请与发布接口返回 400「系统级发布功能尚未就绪：请先执行数据库迁移」，
  而不是静默成功或抛 500。

探测方式
--------
直接试引用该列（``SELECT system_status FROM ai_agents LIMIT 1``），**不查**
``information_schema``——后者在 MySQL 里覆盖整台实例上该用户可见的所有 schema，
同实例别的库迁移过就会被误判成本库已迁移。探测放进 SAVEPOINT，失败只回滚这一小段。
"""
from __future__ import annotations

import logging
import time
from typing import Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

SYSTEM_STATUS_NONE = "NONE"
SYSTEM_STATUS_PENDING = "PENDING_REVIEW"

MIGRATION_HINT = "请先执行数据库迁移 V185（MySQL）/ V85（PostgreSQL）"

# 数据库明确回答「没有这一列」的错误码：MySQL 1054 / PostgreSQL 42703
_UNKNOWN_COLUMN_MARKERS = ("1054", "42703", "Unknown column", "does not exist")

# None=未探测；True=已迁移；False=未迁移
_COLUMN_STATE: Optional[bool] = None
# 确认缺失的时间点：留过期窗口，迁移刚执行完时不必重启进程
_ABSENT_AT: float = 0.0
# 探测结论不确定（连接抖动等）的时间点：只节流，不写死
_PROBE_FAILED_AT: float = 0.0

_ABSENT_RETRY_SECONDS = 60.0
_PROBE_FAILED_RETRY_SECONDS = 30.0


def reset_probe_state_for_tests(state: Optional[bool] = None) -> None:
    """测试用：直接注入探测结论（``_ABSENT_AT=0`` 表示这是注入而非探测得来的）。"""
    global _COLUMN_STATE, _ABSENT_AT, _PROBE_FAILED_AT
    _COLUMN_STATE = state
    _ABSENT_AT = 0.0
    _PROBE_FAILED_AT = 0.0


async def supports_system_status(session: AsyncSession) -> bool:
    """这两个列是否已由迁移加入（进程内缓存，缺失时有重试窗口）。"""
    global _COLUMN_STATE, _ABSENT_AT, _PROBE_FAILED_AT
    now = time.monotonic()

    if _COLUMN_STATE is True:
        return True
    if _COLUMN_STATE is False:
        if _ABSENT_AT == 0.0:
            return False
        if now - _ABSENT_AT < _ABSENT_RETRY_SECONDS:
            return False
        # 过了窗口，可能是迁移刚执行完，重新探测
    if _PROBE_FAILED_AT and now - _PROBE_FAILED_AT < _PROBE_FAILED_RETRY_SECONDS:
        return False

    try:
        async with session.begin_nested():
            await session.execute(
                text("SELECT system_status FROM ai_agents LIMIT 1")
            )
    except Exception as ex:  # noqa: BLE001
        message = str(ex)
        if any(marker in message for marker in _UNKNOWN_COLUMN_MARKERS):
            _COLUMN_STATE = False
            _ABSENT_AT = now
            logger.warning(
                "[agent-system] system_status column is absent; publish/apply disabled "
                "until migration is applied. %s: %s",
                MIGRATION_HINT,
                ex,
            )
        else:
            # 结论不确定：不写死 False，节流后重试
            _PROBE_FAILED_AT = now
            logger.warning(
                "[agent-system] Probe for system_status was inconclusive; retry in %.0fs: %s",
                _PROBE_FAILED_RETRY_SECONDS,
                ex,
            )
    else:
        _COLUMN_STATE = True
        _ABSENT_AT = 0.0
        _PROBE_FAILED_AT = 0.0
        logger.info("[agent-system] system_status column detected; publication enabled")

    return _COLUMN_STATE is True


async def supports_request_type(session: AsyncSession) -> bool:
    """``ai_agent_system_requests.request_type`` 是否存在。

    该列与申请表同属 V185，但**脚本早期版本**已经建过表、没有这一列；
    这类环境必须重跑一次幂等脚本补列，否则 ORM 查申请表会 1054。
    """
    try:
        async with session.begin_nested():
            await session.execute(
                text("SELECT request_type FROM ai_agent_system_requests LIMIT 1")
            )
        return True
    except Exception as exc:  # noqa: BLE001 - 任何失败都当作「不可用」，不影响主流程
        message = str(exc)
        if any(marker in message for marker in _UNKNOWN_COLUMN_MARKERS):
            logger.warning("[agent-system] request_type 列缺失，需重跑迁移脚本补列")
            return False
        # 表不存在等其它情况同样视为未就绪
        return False


async def assert_migrated(session: AsyncSession) -> None:
    """需要写流程状态前调用：未迁移时给出可执行的提示而不是 500。"""
    if not await supports_system_status(session):
        raise HTTPException(
            status_code=400,
            detail=f"系统级发布功能尚未就绪：{MIGRATION_HINT}",
        )
    if not await supports_request_type(session):
        raise HTTPException(
            status_code=400,
            detail=(
                "系统级发布申请单缺少 request_type 列："
                "请重跑一次迁移脚本 V185（MySQL）/ V85（PostgreSQL）补列"
            ),
        )


async def read_system_status(
    session: AsyncSession, agent_id: str
) -> Tuple[str, Optional[str]]:
    """返回 ``(system_status, system_pending_request_id)``；未迁移时视为无流程状态。"""
    if not await supports_system_status(session):
        return SYSTEM_STATUS_NONE, None

    row = await session.execute(
        text(
            "SELECT system_status, system_pending_request_id "
            "FROM ai_agents WHERE id = :agent_id"
        ).bindparams(agent_id=str(agent_id))
    )
    record = row.first()
    if record is None:
        return SYSTEM_STATUS_NONE, None
    status = str(record[0] or SYSTEM_STATUS_NONE)
    pending = record[1]
    return status, (str(pending) if pending else None)


async def write_system_status(
    session: AsyncSession,
    agent_id: str,
    *,
    status: str,
    pending_request_id: Optional[str],
) -> None:
    """写流程状态。调用方必须已经过 ``assert_migrated``。"""
    await session.execute(
        text(
            "UPDATE ai_agents SET system_status = :status, "
            "system_pending_request_id = :pending WHERE id = :agent_id"
        ).bindparams(
            status=str(status),
            pending=str(pending_request_id) if pending_request_id else None,
            agent_id=str(agent_id),
        )
    )
    await session.flush()


async def attach_system_status(session: AsyncSession, agents) -> None:
    """把两个流程列批量挂到 ORM 对象上，供 ``AIAgentResponse`` 序列化。

    ORM 不声明这两列（见模块顶部说明），但接口响应需要它们来判断「审核中」，
    所以在返回前用**一条**原生 SQL 取出来 ``setattr`` 到对象上。这是临时属性：
    SQLAlchemy 不认它是列，flush 时不会带上它，也就不会在未迁移的库上报错。

    未迁移时直接跳过，``system_status`` 由 schema 默认成 ``NONE``。
    """
    agent_list = [agent for agent in (agents or []) if agent is not None]
    if not agent_list:
        return
    if not await supports_system_status(session):
        return

    ids = [str(agent.id) for agent in agent_list]
    try:
        # 顺带取申请单的提交时间：卡片冻结条要显示「什么时候提交的」，
        # 否则用户只看到一个「审核中」，不知道等了多久。
        rows = await session.execute(
            text(
                "SELECT a.id, a.system_status, a.system_pending_request_id, r.requested_at "
                "FROM ai_agents a "
                "LEFT JOIN ai_agent_system_requests r ON r.id = a.system_pending_request_id "
                "WHERE a.id IN :ids"
            ).bindparams(bindparam("ids", expanding=True), ids=ids)
        )
        mapping = {
            str(row[0]): (str(row[1] or SYSTEM_STATUS_NONE), row[2], row[3])
            for row in rows
        }
    except Exception as exc:  # noqa: BLE001
        # 状态只是列表上的一个徽标，查不到就按「无流程状态」展示，不能连累主列表
        logger.debug("读取系统级流程状态失败，按默认值展示: %s", exc)
        mapping = {}

    for agent in agent_list:
        status, pending, requested_at = mapping.get(str(agent.id), (SYSTEM_STATUS_NONE, None, None))
        setattr(agent, "system_status", status)
        setattr(agent, "system_pending_request_id", str(pending) if pending else None)
        setattr(agent, "system_requested_at", requested_at)
