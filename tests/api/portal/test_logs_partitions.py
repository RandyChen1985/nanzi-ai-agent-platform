import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timedelta
from fastapi import HTTPException
from sqlalchemy.dialects import mysql
from app.services.partition_service import PARTITION_TABLES, PartitionService
from app.api.portal.endpoints.system import (
    get_logs_config,
    update_logs_config,
    get_logs_partitions,
    manual_cleanup_logs,
    LogConfigUpdateRequest
)

@pytest.mark.asyncio
async def test_partition_service_detects_partitioned_status():
    """验证 PartitionService 能够正确根据 information_schema 返回的数据量判断表是否已分区。"""
    mock_db = AsyncMock()
    
    # 模拟已分区：返回的分区数 > 1
    mock_result_partitioned = MagicMock()
    mock_result_partitioned.scalar.return_value = 4
    mock_db.execute.return_value = mock_result_partitioned
    
    is_part = await PartitionService.is_table_partitioned(mock_db, "ai_agent_access_logs")
    assert is_part is True
    
    # 模拟未分区：返回的分区数 <= 1
    mock_result_single = MagicMock()
    mock_result_single.scalar.return_value = 1
    mock_db.execute.return_value = mock_result_single
    
    is_part_single = await PartitionService.is_table_partitioned(mock_db, "ai_agent_access_logs")
    assert is_part_single is False


@pytest.mark.asyncio
async def test_partition_service_get_partition_status():
    """验证 PartitionService 正确提取并格式化物理分区表的信息。"""
    mock_db = AsyncMock()
    mock_result = MagicMock()
    
    # 模拟查出来的分区信息元组列表
    mock_result.fetchall.return_value = [
        ("ai_agent_access_logs", "p202605", "'2026-06-01 00:00:00'", 12000),
        ("ai_agent_access_logs", "pmax", "MAXVALUE", 0)
    ]
    mock_db.execute.return_value = mock_result
    
    status = await PartitionService.get_partition_status(mock_db)
    assert len(status) == 2
    assert status[0]["table_name"] == "ai_agent_access_logs"
    assert status[0]["partition_name"] == "p202605"
    assert "2026-06-01" in status[0]["less_than"]
    assert status[0]["table_rows"] == 12000
    assert status[1]["data_range"] == "MAXVALUE (兜底)"


@pytest.mark.asyncio
async def test_partition_status_expands_table_list_in_clause():
    """``IN :tables`` 必须按 expanding 绑定展开，否则驱动会拿到整只 tuple 当单个参数并直接报错。"""
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    mock_db.execute.return_value = mock_result

    with patch(
        "app.services.partition_service.settings",
        SimpleNamespace(normalized_database_type="mysql"),
    ):
        assert await PartitionService.get_partition_status(mock_db) == []

    statement, params = mock_db.execute.await_args.args
    assert params == {"tables": list(PARTITION_TABLES)}

    rendered = statement.params(**params).compile(
        dialect=mysql.dialect(), compile_kwargs={"render_postcompile": True}
    ).string
    # 展开后每个表名各占一个占位符；只渲染出一个 %s 就说明 tuple 被当成标量传给了驱动
    assert rendered.count("%s") == len(PARTITION_TABLES)


@pytest.mark.asyncio
async def test_partition_service_prune_expired_logs_partitioned():
    """验证当表处于分区状态时，prune_expired_logs 正确识别并 Drop 过期分区。"""
    mock_db = AsyncMock()
    
    # 1. 模拟表是已分区的
    mock_partition_check = MagicMock()
    mock_partition_check.scalar.return_value = 3
    
    # 2. 模拟分区边界列表（p202604 过期，p202605 未过期）
    # 假设当前是 2026-06-03，保留 30 天，临界过期点是 2026-05-04。
    # p202604 边界是 2026-05-01 (完全过期，应该被 Drop)
    # p202605 边界是 2026-06-01 (未完全过期，必须保留)
    mock_partitions_list = MagicMock()
    mock_partitions_list.fetchall.return_value = [
        ("p202604", "'2026-05-01 00:00:00'"),
        ("p202605", "'2026-06-01 00:00:00'")
    ]
    
    # 按顺序模拟 db.execute 执行的返回值
    mock_db.execute.side_effect = [
        mock_partition_check,  # check table 1 partitioned
        mock_partitions_list,  # fetch table 1 partitions
        AsyncMock(),          # execute drop table 1 partition
        mock_partition_check,  # check table 2 partitioned
        mock_partitions_list,  # fetch table 2 partitions
        AsyncMock()           # execute drop table 2 partition
    ]
    
    # 执行 30 天日志保留期清理
    res = await PartitionService.prune_expired_logs(mock_db, retention_days=30)
    
    assert res["status"] == "success"
    # 两张表都应当成功识别并 Drop 掉 p202604 分区
    assert "p202604" in res["details"]["ai_agent_access_logs"]["dropped"]
    assert "p202605" not in res["details"]["ai_agent_access_logs"]["dropped"]
    
    # 验证是否执行了 DDL DDrop Partition
    executed_sqls = [call.args[0].text for call in mock_db.execute.call_args_list if hasattr(call.args[0], "text")]
    assert any("DROP PARTITION p202604" in sql for sql in executed_sqls)
    assert not any("DROP PARTITION p202605" in sql for sql in executed_sqls)


@pytest.mark.asyncio
async def test_partition_service_prune_expired_logs_unpartitioned():
    """验证当表未做物理分区时，能平滑降级为微批量 DELETE 进行日志清理。"""
    mock_db = AsyncMock()
    
    # 模拟未分区
    mock_partition_check = MagicMock()
    mock_partition_check.scalar.return_value = 1
    
    # 模拟第一批 delete 影响了 5000 行，第二批 delete 影响了 1200 行（小于 batch_size 退出循环）
    mock_del_res_1 = MagicMock()
    mock_del_res_1.rowcount = 5000
    mock_del_res_2 = MagicMock()
    mock_del_res_2.rowcount = 1200
    
    mock_db.execute.side_effect = [
        mock_partition_check, # check table 1 partitioned -> False
        mock_del_res_1,       # delete batch 1
        mock_del_res_2,       # delete batch 2
        mock_partition_check, # check table 2 partitioned -> False
        mock_del_res_2        # delete batch 1 (done)
    ]
    
    res = await PartitionService.prune_expired_logs(mock_db, retention_days=30)
    assert res["status"] == "success"
    assert res["details"]["ai_agent_access_logs"]["type"] == "delete"
    assert res["details"]["ai_agent_access_logs"]["deleted_rows"] == 6200
    assert res["details"]["ai_agent_execution_traces"]["deleted_rows"] == 1200


# --- Controller Endpoints Unit Tests ---

@pytest.mark.asyncio
async def test_get_logs_config_endpoint():
    """验证获取配置接口正确获取并转换参数格式。"""
    with patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="45")):
        res = await get_logs_config(user={"role": "admin"})
        assert res["audit_log_retention_days"] == 45


@pytest.mark.asyncio
async def test_update_logs_config_endpoint():
    """验证修改配置接口校验逻辑。"""
    mock_user = {"user_name": "super_admin", "role": "admin"}
    
    # 1. 正常修改
    with patch("app.services.config_service.ConfigService.update_config_value", AsyncMock(return_value=True)):
        req = LogConfigUpdateRequest(audit_log_retention_days=120)
        res = await update_logs_config(req, user=mock_user)
        assert res["status"] == "success"
        
    # 2. 校验错误边界 (天数不可为 0 或超限)
    with pytest.raises(HTTPException) as exc:
        await update_logs_config(LogConfigUpdateRequest(audit_log_retention_days=0), user=mock_user)
    assert exc.value.status_code == 400
