"""Unit tests for federated join empty-filter retry edge cases."""

from __future__ import annotations

import sys
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# federated_executor imports duckdb at module load time
if "duckdb" not in sys.modules:
    duckdb_stub = ModuleType("duckdb")
    duckdb_stub.connect = MagicMock()
    sys.modules["duckdb"] = duckdb_stub

from app.services.ai.empty_result_filter_diagnostic import AutoFilterRetryResult
from app.services.ai.executors.federated_executor import FederatedQueryExecutor

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_maybe_retry_federated_join_handles_invalid_empty_retry_json():
    runner = MagicMock()
    executor = FederatedQueryExecutor(runner, "", ["crm_ds"])

    bad_retry = AutoFilterRetryResult(
        attempted=True,
        corrected_sql="SELECT contract_code FROM VIEW_AI_CONTRACT WHERE region = '华东'",
        raw_output="<html>gateway timeout</html>",
        parsed_output=None,
        has_rows=True,
        summary="探查完成但响应非 JSON",
    )
    mock_ds = SimpleNamespace(name="crm_ds", data_source="oracle_crm")

    with patch(
        "app.services.metadata_service.MetadataService.get_dataset_by_name",
        AsyncMock(return_value=mock_ds),
    ), patch.object(
        executor,
        "_try_empty_filter_auto_repair",
        AsyncMock(return_value=bad_retry),
    ):
        result = await executor._maybe_retry_federated_join_after_empty_filter(
            MagicMock(),
            duckdb_conn=MagicMock(),
            join_sql="SELECT contract_code FROM t_crm",
            sub_queries=[
                {
                    "dataset_name": "crm_ds",
                    "temp_table": "t_crm",
                    "sql": "SELECT contract_code FROM VIEW_AI_CONTRACT WHERE region = '华东'",
                }
            ],
            temp_table_schemas={},
            user_id=1,
            is_admin=False,
            user_dimensions=None,
            agent_context=None,
            _platform_auto_sql_attempts=[0],
        )

    assert result is None or "join_rows" not in result
    assert result is None or result.get("attempt") == 1
