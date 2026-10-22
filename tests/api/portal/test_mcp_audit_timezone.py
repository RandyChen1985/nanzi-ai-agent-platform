"""MCP 出站审计时间：DB 存 UTC，接口必须带时区，前端才能按本地时间正确展示。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.api.portal.endpoints.mcp import _iso_utc

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
MCP_ENDPOINT = ROOT / "app/api/portal/endpoints/mcp.py"


def test_naive_audit_datetime_is_serialized_as_utc():
    assert _iso_utc(datetime(2026, 10, 22, 6, 0, 0)) == "2026-10-22T06:00:00+00:00"
    assert _iso_utc(None) is None


def test_both_outbound_log_endpoints_use_utc_serializer():
    source = MCP_ENDPOINT.read_text(encoding="utf-8")
    assert source.count('"created_at": _iso_utc(log.created_at)') == 2
    assert 'log.created_at.isoformat()' not in source
    assert '"last_called_at": _iso_utc(row.last_called_at)' in source
