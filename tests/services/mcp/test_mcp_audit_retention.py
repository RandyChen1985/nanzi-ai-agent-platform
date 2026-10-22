"""MCP 出站审计：入参脱敏、保留期清理、后台任务强引用。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.mcp import outbound_audit

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_prune_mcp_outbound_audit_logs_uses_utc_threshold_and_batches():
    result = AsyncMock()
    result.rowcount = 2
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    context = AsyncMock()
    context.__aenter__.return_value = session
    context.__aexit__.return_value = None

    with patch(
        "app.services.mcp.outbound_audit.AsyncSessionLocal", return_value=context
    ):
        deleted = await outbound_audit.prune_mcp_outbound_audit_logs(90)

    assert deleted == 2
    statement = session.execute.await_args.args[0]
    params = session.execute.await_args.args[1]
    assert "sys_mcp_outbound_audit_logs" in str(statement)
    assert params["batch_size"] == 5000
    assert params["threshold"].tzinfo is None  # UTC naive，与 created_at 列一致
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_prune_skips_when_retention_disabled():
    with patch("app.services.mcp.outbound_audit.AsyncSessionLocal") as factory:
        assert await outbound_audit.prune_mcp_outbound_audit_logs(0) == 0
    factory.assert_not_called()


def test_audit_payload_is_redacted_and_maintenance_job_prunes_it():
    import inspect

    from app.services.ai import scheduler_service

    source = inspect.getsource(outbound_audit.record_outbound_audit_log)
    assert "sanitize_extra_data(arguments" in source
    assert "sanitize_extra_data(tool_output)" in source

    scheduler_source = inspect.getsource(
        scheduler_service._system_audit_log_maintenance_job
    )
    assert "prune_mcp_outbound_audit_logs(retention_days)" in scheduler_source
