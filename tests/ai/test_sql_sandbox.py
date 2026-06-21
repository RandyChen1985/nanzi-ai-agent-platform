import pytest
import json
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.ai.sql_sandbox_gate import SQLSandboxGate, SQLSandboxResult

@pytest.mark.asyncio
async def test_sql_sandbox_join_cross_blocked():
    """
    测试用例 1: 验证无条件 JOIN (如 CROSS JOIN 或普通 JOIN 但缺失 ON/USING 条件) 会被沙箱拦截。
    """
    mock_session = AsyncMock(spec=AsyncSession)
    
    # 场景 A: 显式 CROSS JOIN
    sql_cross = "SELECT a.id, b.name FROM table_a a CROSS JOIN table_b b"
    res_cross = await SQLSandboxGate.verify_and_optimize(
        session=mock_session,
        sql=sql_cross,
        data_source="clickhouse_test",
        dialect="clickhouse"
    )
    assert res_cross.allowed is False
    assert "CROSS JOIN 笛卡尔积操作" in res_cross.message
    
    # 场景 B: 显式 JOIN 但无 ON/USING
    sql_no_cond = "SELECT * FROM table_a JOIN table_b"
    res_no_cond = await SQLSandboxGate.verify_and_optimize(
        session=mock_session,
        sql=sql_no_cond,
        data_source="clickhouse_test",
        dialect="clickhouse"
    )
    assert res_no_cond.allowed is False
    assert "包含没有关联条件的 JOIN 操作" in res_no_cond.message

    # 场景 C: 显式 LEFT JOIN 但带了 ON
    # 应该被正常放行，且不修改 SQL
    sql_valid_join = "SELECT * FROM table_a a LEFT JOIN table_b b ON a.id = b.id"
    with patch("app.services.ai.sql_sandbox_gate.call_external_sql_api", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = "[]"  # 模拟空 Explain 返回
        res_valid = await SQLSandboxGate.verify_and_optimize(
            session=mock_session,
            sql=sql_valid_join,
            data_source="clickhouse_test",
            dialect="clickhouse"
        )
        assert res_valid.allowed is True
        assert res_valid.optimized_sql == sql_valid_join

@pytest.mark.asyncio
async def test_sql_sandbox_explain_rows_blocked():
    """
    测试用例 2: 验证大表全表扫描 Explain 预估超限时的阻断熔断提示。
    """
    mock_session = AsyncMock(spec=AsyncSession)
    
    # 场景 A: 估算扫描行数 600 万行（超限 500 万行）
    sql_large = "SELECT * FROM huge_table"
    with patch("app.services.ai.sql_sandbox_gate.call_external_sql_api", new_callable=AsyncMock) as mock_api:
        # Mock ClickHouse Explain 返回，扫描 6,000,000 行
        mock_api.return_value = json.dumps([{"Explain": "Read 6000000 rows"}])
        
        res = await SQLSandboxGate.verify_and_optimize(
            session=mock_session,
            sql=sql_large,
            data_source="clickhouse_test",
            dialect="clickhouse"
        )
        assert res.allowed is False
        assert "物理执行计划预估扫描行数为 6,000,000 行" in res.message
        assert "已超过系统安全上限" in res.message

    # 场景 B: 估算扫描行数 400 万行（安全范围内，放行，且不修改 SQL）
    sql_safe = "SELECT * FROM huge_table"
    with patch("app.services.ai.sql_sandbox_gate.call_external_sql_api", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = json.dumps([{"Explain": "Read 4000000 rows"}])
        
        res = await SQLSandboxGate.verify_and_optimize(
            session=mock_session,
            sql=sql_safe,
            data_source="clickhouse_test",
            dialect="clickhouse"
        )
        assert res.allowed is True
        assert res.optimized_sql == sql_safe
