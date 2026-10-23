"""Tests for federated subquery pre-execute gates."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.ai.executors.federated_subquery_gates import (
    FAILED_FEDERATED_SQL_REPEAT_PREFIX,
    federated_failed_sql_repeat_message,
    validate_federated_subquery_before_execute,
)


@pytest.mark.asyncio
async def test_validate_federated_subquery_blocks_static_risk_sql():
    runner = MagicMock()
    runner._resolve_sql_schema_preflight_error = AsyncMock(return_value="")
    dataset = MagicMock()
    dataset.name = "demo_ds"
    dataset.data_source = "oracle_demo"

    block = await validate_federated_subquery_before_execute(
        runner,
        session=MagicMock(),
        sub_sql="SELECT a.*, b.* FROM t1 a, t2 b",
        dataset=dataset,
        schema_output="dataset: demo_ds",
        sql_query_binding=None,
        user_question="查关联",
    )
    assert block is not None
    assert "SQL_STATIC_GATE" in block


def test_federated_failed_sql_repeat_message_has_prefix():
    message = federated_failed_sql_repeat_message(summary="ORA-00904")
    assert FAILED_FEDERATED_SQL_REPEAT_PREFIX in message
    assert "ORA-00904" in message


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_federated_preflight_receives_project_dataset_scope():
    runner = MagicMock()
    runner.execution_options = {
        "resource_scope": {
            "datasets": [{"id": "7", "dataset_name": "mounted_ds"}],
        }
    }
    runner._resolve_sql_schema_preflight_error = AsyncMock(return_value="")
    dataset = MagicMock()
    dataset.name = "mounted_ds"
    dataset.data_source = "oracle"

    block = await validate_federated_subquery_before_execute(
        runner,
        session=MagicMock(),
        sub_sql="SELECT id FROM orders",
        dataset=dataset,
        schema_output="dataset: mounted_ds",
        sql_query_binding=None,
        user_question="查订单",
    )

    assert block is None
    assert runner._resolve_sql_schema_preflight_error.await_args.kwargs["allowed_dataset_names"] == {"mounted_ds"}
