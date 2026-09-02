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
    """写入不含凭证的安全事件；审计失败不阻断主流程。"""
    try:
        db.add(
            McpOAuthSecurityAuditLog(
                id=str(uuid.uuid4()),
                event_type=event_type[:64],
                request_id=request_id,
                client_id=client_id,
                user_id=user_id,
                actor_user_id=actor_user_id,
                result_status=result_status[:32],
                error_code=error_code[:128] if error_code else None,
                details=details,
            )
        )
        await db.flush()
    except Exception as exc:
        logger.warning("MCP OAuth security audit write failed: %s", exc)
