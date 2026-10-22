"""MCP 外部出站工具调用审计日志服务"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional
from datetime import datetime

from app.core.orm import AsyncSessionLocal
from app.models.mcp import McpOutboundAuditLog, McpServer
from app.services.ai.audit_payload import bound_audit_payload
from app.services.ai.user_extra_data import sanitize_extra_data
from sqlalchemy import select

logger = logging.getLogger(__name__)


async def record_outbound_audit_log(
    *,
    server_id: str,
    tool_name: str,
    arguments: Dict[str, Any] | None = None,
    user_info: Optional[Dict[str, Any]] = None,
    agent_info: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
    status: str = "success",
    latency_ms: Optional[int] = None,
    error_message: Optional[str] = None,
    tool_output: Any = None,
) -> None:
    """异步记录出站 MCP 工具调用流水；内部静默捕获异常，绝不阻塞或中断主链路。"""
    try:
        async with AsyncSessionLocal() as session:
            server_stmt = select(McpServer.server_name).where(McpServer.id == server_id)
            server_name = (await session.execute(server_stmt)).scalar_one_or_none()

            user_id = str((user_info or {}).get("user_id") or "").strip() or None
            user_name = str(
                (user_info or {}).get("user_name")
                or (user_info or {}).get("real_name")
                or ""
            ).strip() or None

            agent_id = str((agent_info or {}).get("agent_id") or "").strip() or None
            agent_name = str((agent_info or {}).get("agent_name") or "").strip() or None

            # 工具入参/出参可能带密码、Token；审计只保留打码后的结构。
            bounded_input = bound_audit_payload(
                sanitize_extra_data(arguments or {})
            )
            bounded_output = (
                bound_audit_payload(sanitize_extra_data(tool_output))
                if tool_output is not None
                else None
            )

            log_entry = McpOutboundAuditLog(
                id=str(uuid.uuid4()),
                server_id=server_id,
                server_name=server_name,
                tool_name=tool_name,
                agent_id=agent_id,
                agent_name=agent_name,
                user_id=user_id,
                user_name=user_name,
                trace_id=request_id,
                status=status[:32],
                latency_ms=latency_ms,
                error_message=str(error_message)[:2000] if error_message else None,
                tool_input=bounded_input,
                tool_output=bounded_output,
                created_at=datetime.utcnow(),
            )
            session.add(log_entry)
            await session.commit()
    except Exception as exc:
        logger.warning(
            "MCP outbound audit log write failed for server=%s tool=%s: %s",
            server_id,
            tool_name,
            exc,
        )


async def prune_mcp_outbound_audit_logs(retention_days: int) -> int:
    """按保留天数清理出站审计；时间字段是 UTC，阈值也用 UTC。

    复用平台 ``audit_log_retention_days`` 配置，由每日审计维护任务调用。
    """
    if retention_days <= 0:
        return 0

    from datetime import timedelta

    from sqlalchemy import text

    from app.core.config import settings

    threshold = datetime.utcnow() - timedelta(days=retention_days)
    table = "sys_mcp_outbound_audit_logs"
    batch_size = 5000
    deleted_total = 0
    try:
        async with AsyncSessionLocal() as session:
            while True:
                if settings.normalized_database_type == "postgresql":
                    sql = text(
                        f"""
                        DELETE FROM "{table}"
                        WHERE ctid IN (
                            SELECT ctid FROM "{table}"
                            WHERE created_at < :threshold
                            LIMIT :batch_size
                        )
                        """
                    )
                else:
                    sql = text(
                        f"DELETE FROM `{table}` WHERE created_at < :threshold LIMIT :batch_size"
                    )
                result = await session.execute(
                    sql, {"threshold": threshold, "batch_size": batch_size}
                )
                await session.commit()
                deleted = int(result.rowcount or 0)
                deleted_total += deleted
                if deleted < batch_size:
                    break
    except Exception as exc:  # noqa: BLE001 - 维护任务失败不影响主流程
        logger.warning("MCP outbound audit retention failed: %s", exc)
        return deleted_total
    if deleted_total:
        logger.info(
            "Pruned %s MCP outbound audit rows older than %s days",
            deleted_total,
            retention_days,
        )
    return deleted_total
