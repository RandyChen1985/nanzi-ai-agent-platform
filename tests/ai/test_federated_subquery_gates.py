"""Tests for federated subquery pre-execute gates."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.executors.federated_subquery_gates import (
    FAILED_FEDERATED_SQL_REPEAT_PREFIX,
    federated_failed_sql_repeat_message,
    validate_federated_subquery_before_execute,
)


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_validate_federated_subquery_blocks_static_risk_sql():
    runner = MagicMock()
    dataset = MagicMock()
    dataset.name = "demo_ds"
    dataset.data_source = "oracle_demo"

    block = await validate_federated_subquery_before_execute(
        runner,
        session=MagicMock(),
        sub_sql="SELECT a.*, b.* FROM t1 a JOIN t2 b",
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
async def test_federated_subquery_forwards_session_dataset_scope():
    """联邦子查询必须与单查询共用数据集 scope 门禁。"""
    runner = SimpleNamespace(
        user_info={"id": 7, "role": "user"},
        debug_options={
            "resource_scope": {
                "datasets": [{"id": "1", "dataset_name": "dataset_a"}],
            }
        },
    )
    resolver = AsyncMock(return_value="")
    with patch(
        "app.services.ai.chatbi_sql_query_binding.resolve_sql_schema_preflight_with_binding",
        resolver,
    ):
        error = await validate_federated_subquery_before_execute(
            runner,
            session=AsyncMock(),
            sub_sql="SELECT id FROM table_a LIMIT 10",
            dataset=SimpleNamespace(name="dataset_a", data_source="mysql"),
            schema_output="",
            sql_query_binding=None,
            user_question="查询数据",
        )

    assert error is None
    assert resolver.await_args.kwargs["allowed_dataset_names"] == {"dataset_a"}


@pytest.mark.asyncio
async def test_federated_subquery_rejects_unmounted_dataset_before_preflight():
    """模型将同一张表改走未挂载数据集时，必须在执行前拦截。"""
    runner = SimpleNamespace(
        user_info={"id": 7, "role": "user"},
        debug_options={
            "resource_scope": {
                "datasets": [{"id": "1", "dataset_name": "dataset_a"}],
            }
        },
    )
    error = await validate_federated_subquery_before_execute(
        runner,
        session=AsyncMock(),
        sub_sql="SELECT id FROM table_b LIMIT 10",
        dataset=SimpleNamespace(name="dataset_b", data_source="mysql"),
        schema_output="",
        sql_query_binding=None,
        user_question="查询数据",
    )

    assert error is not None
    assert "dataset_b" in error
