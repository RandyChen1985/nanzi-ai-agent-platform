import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from types import SimpleNamespace
from app.services.pool_manager import DataSourcePoolManager

# 构造一个模拟的 DB 连接配置
def make_mock_config(source_id=1, db_type="mysql", host="localhost", port=3306, user="root", pwd="pwd", db_name="test_db"):
    return SimpleNamespace(
        id=source_id,
        name=f"config_{source_id}",
        db_type=db_type,
        host=host,
        port=port,
        db_user=user,
        password=pwd,
        database_name=db_name
    )

@pytest.mark.asyncio
async def test_pool_manager_fingerprint_and_invalidation():
    # 重置 PoolManager 的缓存
    DataSourcePoolManager._pools.clear()
    
    config1 = make_mock_config(source_id=1, db_type="mysql", host="127.0.0.1", port=3306)
    config2 = make_mock_config(source_id=1, db_type="mysql", host="127.0.0.1", port=3306, pwd="changed_pwd") # 密码变了

    # Mock 连接池创建函数，避免真正去连数据库
    mock_pool_1 = MagicMock()
    mock_pool_1.close = AsyncMock()
    mock_pool_1.wait_closed = AsyncMock()
    
    mock_pool_2 = MagicMock()
    mock_pool_2.close = AsyncMock()
    mock_pool_2.wait_closed = AsyncMock()
    
    # 第一次获取 pool，期望触发创建
    with patch("app.services.db_connection_service.DbConnectionService.get_config", new_callable=AsyncMock) as mock_get_config, \
         patch.object(DataSourcePoolManager, "_create_mysql_pool", new_callable=AsyncMock) as mock_create_mysql:
         
        mock_get_config.return_value = config1
        mock_create_mysql.return_value = mock_pool_1
        
        pool = await DataSourcePoolManager.get_pool(source_id=1)
        assert pool == mock_pool_1
        assert 1 in DataSourcePoolManager._pools
        
        # 第二次获取 pool (配置未变)，期望直接复用缓存
        pool_cached = await DataSourcePoolManager.get_pool(source_id=1)
        assert pool_cached == mock_pool_1
        assert mock_create_mysql.call_count == 1 # 应该只调用了一次 create

    # 配置变了，再次获取 pool
    with patch("app.services.db_connection_service.DbConnectionService.get_config", new_callable=AsyncMock) as mock_get_config, \
         patch.object(DataSourcePoolManager, "_create_mysql_pool", new_callable=AsyncMock) as mock_create_mysql:
         
        mock_get_config.return_value = config2 # 返回修改密码后的配置
        mock_create_mysql.return_value = mock_pool_2
        
        # 期望自动识别指纹不匹配，先 close 旧池，再创建新池
        pool_new = await DataSourcePoolManager.get_pool(source_id=1)
        assert pool_new == mock_pool_2
        assert mock_create_mysql.call_count == 1
        mock_pool_1.close.assert_called_once() # 验证旧的连接池被关闭释放了

@pytest.mark.asyncio
async def test_pool_manager_invalidate_pool():
    DataSourcePoolManager._pools.clear()
    
    config = make_mock_config(source_id=2, db_type="clickhouse")
    mock_pool = MagicMock()
    mock_pool.close = AsyncMock()
    
    with patch("app.services.db_connection_service.DbConnectionService.get_config", new_callable=AsyncMock, return_value=config), \
         patch.object(DataSourcePoolManager, "_create_clickhouse_pool", new_callable=AsyncMock, return_value=mock_pool):
         
        await DataSourcePoolManager.get_pool(source_id=2)
        assert 2 in DataSourcePoolManager._pools
        
        # 主动失效 pool
        await DataSourcePoolManager.invalidate_pool(source_id=2)
        assert 2 not in DataSourcePoolManager._pools
        mock_pool.close.assert_called_once()

@pytest.mark.asyncio
async def test_pool_manager_close_all_pools():
    DataSourcePoolManager._pools.clear()
    
    pool1 = MagicMock()
    pool1.close = AsyncMock()
    pool2 = MagicMock()
    pool2.close = AsyncMock()
    
    # 手动放入缓存
    DataSourcePoolManager._pools[10] = (pool1, "fp1")
    DataSourcePoolManager._pools[20] = (pool2, "fp2")
    
    await DataSourcePoolManager.close_all_pools()
    
    assert len(DataSourcePoolManager._pools) == 0
    pool1.close.assert_called_once()
    pool2.close.assert_called_once()

@pytest.mark.asyncio
async def test_pool_manager_oracle_mode_dsn():
    """测试 Oracle Thick/Thin Mode 连接池创建时构建的 DSN"""
    DataSourcePoolManager._pools.clear()
    
    # 1. 默认 SID 模式
    config_sid = make_mock_config(source_id=3, db_type="oracle", host="oracle.host", port=1521, db_name="ORCL")
    
    # 2. Service Name 模式 (database_name 以 / 开头)
    config_sn = make_mock_config(source_id=4, db_type="oracle", host="oracle.host", port=1521, db_name="/ORCLPDB")
    
    mock_oracledb = MagicMock()
    mock_oracledb.create_pool_async = AsyncMock()
    mock_oracledb.makedsn = lambda host, port, sid: f"(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST={host})(PORT={port}))(CONNECT_DATA=(SID={sid})))"
    
    # Mock `import oracledb` 并调用 _create_oracle_pool 
    with patch.dict("sys.modules", {"oracledb": mock_oracledb}), \
         patch.dict("os.environ", {"USE_ORACLE_THICK_MODE": "0"}): # Thin mode
         
        # SID 模式测试
        await DataSourcePoolManager._create_oracle_pool(config_sid)
        mock_oracledb.create_pool_async.assert_called_once()
        dsn_called = mock_oracledb.create_pool_async.call_args[1]["dsn"]
        assert "SID=ORCL" in dsn_called
        
        # Service Name 模式测试
        mock_oracledb.create_pool_async.reset_mock()
        await DataSourcePoolManager._create_oracle_pool(config_sn)
        mock_oracledb.create_pool_async.assert_called_once()
        dsn_called_sn = mock_oracledb.create_pool_async.call_args[1]["dsn"]
        assert dsn_called_sn == "oracle.host:1521/ORCLPDB"

@pytest.mark.asyncio
async def test_pool_manager_sqlserver_type_routing():
    DataSourcePoolManager._pools.clear()

    config = make_mock_config(source_id=5, db_type="sqlserver", host="mssql.host", port=1433, db_name="erp")
    mock_pool = MagicMock()
    mock_pool.close = AsyncMock()

    with patch("app.services.db_connection_service.DbConnectionService.get_config", new_callable=AsyncMock, return_value=config), \
         patch.object(DataSourcePoolManager, "_create_sqlserver_pool", new_callable=AsyncMock, return_value=mock_pool) as mock_create_sqlserver:

        pool = await DataSourcePoolManager.get_pool(source_id=5)
        assert pool is mock_pool
        mock_create_sqlserver.assert_awaited_once_with(config)
