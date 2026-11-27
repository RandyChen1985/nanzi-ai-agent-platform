"""MCP OAuth 安全事件审计写入。"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform_mcp import McpOAuthSecurityAuditLog


logger = logging.getLogger(__name__)


async def write_security_audit(
    db: AsyncSession,
    *,
    event_type: str,
    request_id: str | None = None,
    client_id: str | None = None,
    user_id: str | None = None,
    actor_user_id: str | None = None,
    result_status: str = "completed",
    error_code: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """写入不含凭证的安全事件；审计失败不阻断主流程。

    使用 SAVEPOINT（``begin_nested()``）隔离 flush，避免 PostgreSQL 下审计写入报错
    将外层业务事务置为 aborted 导致后续主流程 commit 抛 ``PendingRollbackError``。
    """
    entry = McpOAuthSecurityAuditLog(
        id=str(uuid.uuid4()),
        event_type=str(event_type or "")[:64],
        request_id=str(request_id)[:64] if request_id is not None else None,
        client_id=str(client_id)[:64] if client_id is not None else None,
        user_id=str(user_id)[:64] if user_id is not None else None,
        actor_user_id=str(actor_user_id)[:64] if actor_user_id is not None else None,
        result_status=str(result_status or "")[:32],
        error_code=str(error_code)[:128] if error_code else None,
        details=details,
    )
    begin_nested = getattr(db, "begin_nested", None)
    try:
        if callable(begin_nested):
            async with begin_nested():
                db.add(entry)
                await db.flush()
        else:
            db.add(entry)
            await db.flush()
    except Exception as exc:
        logger.warning("MCP OAuth security audit write failed: %s", exc)
