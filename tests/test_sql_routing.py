import pytest
import json
from unittest.mock import MagicMock, AsyncMock, patch
from app.services.ai.tools.data_api import call_external_sql_api
from app.services.data_adapter.sqlserver import SQLServerAdapter

pytestmark = pytest.mark.no_infrastructure

@pytest.mark.asyncio
async def test_sql_routing_forced_env_local():
    """验证当环境变量 SQL_EXECUTION_MODE=local 时，强制分流到本地模式，不发起 HTTP 请求"""
    sql = "SELECT id FROM users"
    
    mock_adapter = MagicMock()
    mock_adapter.execute_sql = AsyncMock(return_value={"columns": [{"name": "id", "type": "int"}], "items": [[1]]})
    
    with patch.dict("os.environ", {"SQL_EXECUTION_MODE": "local"}), \
         patch("app.services.data_adapter.factory.get_adapter", return_value=mock_adapter) as mock_get_adapter, \
         patch("app.core.redis.get_redis", return_value=None), \
         patch("app.core.http_client.GlobalHttpClient.get_client") as mock_get_http_client:
         
        res = await call_external_sql_api(sql, data_source="mysql_test")
        
        # 验证是否返回了正确格式的 JSON
        res_dict = json.loads(res)
        assert res_dict["items"] == [[1]]
        
        # 验证是否调用了本地适配器
        mock_get_adapter.assert_called_once_with("mysql_test")
        mock_adapter.execute_sql.assert_called_once()
        
        # 验证在此模式下根本没有获取 HTTP 客户端或发起请求
        mock_get_http_client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_sql_routing_local_sqlserver_uses_top_limit_without_subquery_wrapper():
    """SQL Server 本地执行不能套 MySQL LIMIT 子查询，否则 ORDER BY 会触发 1033。"""
    sql = (
        "SELECT bm AS department, SUM(amount) AS total_amount "
        "FROM t_cw_clg "
        "WHERE status = N'已报销' "
        "GROUP BY bm "
        "ORDER BY total_amount DESC"
    )
    adapter = SQLServerAdapter(source_id=9)
    adapter.execute_sql = AsyncMock(return_value={"columns": [], "items": []})

    with patch.dict("os.environ", {"SQL_EXECUTION_MODE": "local"}), \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config, \
         patch("app.services.data_adapter.factory.get_adapter", return_value=adapter), \
         patch("app.core.redis.get_redis", return_value=None):
        mock_config.side_effect = lambda key, **kw: "60.0" if key == "data_api_timeout_seconds" else kw.get("default")
        res = await call_external_sql_api(sql, data_source="sqlserver_erp")

    assert json.loads(res)["items"] == []
    executed_sql = adapter.execute_sql.await_args.args[0]
    assert "TOP 1000" in executed_sql.upper()
    assert "N'已报销'" in executed_sql
    assert "ORDER BY TOTAL_AMOUNT DESC" in executed_sql.upper()
    assert " LIMIT " not in f" {executed_sql.upper()} "
    assert "FROM (" not in executed_sql.upper()


@pytest.mark.asyncio
async def test_sql_routing_forced_env_remote():
    """验证当环境变量 SQL_EXECUTION_MODE=remote 时，即使数据库配置为 local，也强制走远程 HTTP 接口"""
    sql = "SELECT id FROM users"
    
    mock_http_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.is_error = False
    mock_response.json.return_value = {"code": 200, "data": {"columns": [{"name": "id", "type": "int"}], "items": [[2]]}}
    mock_http_client.post.return_value = mock_response
    
    # 即使 ConfigService.get('sql_execution_mode') 返回 local
    with patch.dict("os.environ", {"SQL_EXECUTION_MODE": "remote"}), \
         patch("app.services.config_service.ConfigService.get", side_effect=lambda key, **kw: "local" if key == "sql_execution_mode" else ("60.0" if key == "data_api_timeout_seconds" else "http://remote/api")), \
         patch("app.services.data_adapter.factory.get_adapter") as mock_get_adapter, \
         patch("app.core.redis.get_redis", return_value=None), \
         patch("app.core.http_client.GlobalHttpClient.get_client", return_value=mock_http_client):
         
        res = await call_external_sql_api(sql, data_source="mysql_test")
        
        res_dict = json.loads(res)
        assert res_dict["items"] == [[2]]
        
        # 验证没有调用本地适配器
        mock_get_adapter.assert_not_called()
        # 验证发起了 HTTP 请求
        mock_http_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_sql_routing_fallback_to_db_local():
    """验证当环境变量为空或 auto 时，会读取数据库配置；若数据库为 local，则执行本地模式"""
    sql = "SELECT id FROM users"
    
    mock_adapter = MagicMock()
    mock_adapter.execute_sql = AsyncMock(return_value={"columns": [{"name": "id", "type": "int"}], "items": [[3]]})
    
    # 模拟环境变量为 auto，数据库配置为 local
    with patch.dict("os.environ", {"SQL_EXECUTION_MODE": "auto"}), \
         patch("app.services.config_service.ConfigService.get", side_effect=lambda key, **kw: "local" if key == "sql_execution_mode" else ("60.0" if key == "data_api_timeout_seconds" else "http://remote/api")), \
         patch("app.services.data_adapter.factory.get_adapter", return_value=mock_adapter) as mock_get_adapter, \
         patch("app.core.redis.get_redis", return_value=None), \
         patch("app.core.http_client.GlobalHttpClient.get_client") as mock_get_http_client:
         
        res = await call_external_sql_api(sql, data_source="mysql_test")
        
        res_dict = json.loads(res)
        assert res_dict["items"] == [[3]]
        
        mock_get_adapter.assert_called_once_with("mysql_test")
        mock_get_http_client.assert_not_called()


@pytest.mark.asyncio
async def test_sql_routing_invalid_db_config_fallback_remote():
    """验证当数据库配置非法时（例如 'invalid_mode'），自动降级为远程 HTTP 模式"""
    sql = "SELECT id FROM users"
    
    mock_http_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.is_error = False
    mock_response.json.return_value = {"code": 200, "data": {"columns": [{"name": "id", "type": "int"}], "items": [[4]]}}
    mock_http_client.post.return_value = mock_response
    
    # 数据库配置非法值，回退为 remote
    with patch.dict("os.environ", {}), \
         patch("app.services.config_service.ConfigService.get", side_effect=lambda key, **kw: "invalid_mode" if key == "sql_execution_mode" else ("60.0" if key == "data_api_timeout_seconds" else "http://remote/api")), \
         patch("app.services.data_adapter.factory.get_adapter") as mock_get_adapter, \
         patch("app.core.redis.get_redis", return_value=None), \
         patch("app.core.http_client.GlobalHttpClient.get_client", return_value=mock_http_client):
         
        res = await call_external_sql_api(sql, data_source="mysql_test")
        
        res_dict = json.loads(res)
        assert res_dict["items"] == [[4]]
        
        mock_get_adapter.assert_not_called()
        mock_http_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_sql_routing_local_source_not_found():
    """验证当本地模式下 data_source 在本地配置表中不存在时，返回归一化 TOOL_ERROR 错误"""
    sql = "SELECT id FROM users"
    
    # get_adapter 抛出 ValueError 模拟未配置
    with patch.dict("os.environ", {"SQL_EXECUTION_MODE": "local"}), \
         patch("app.services.data_adapter.factory.get_adapter", side_effect=ValueError("未找到对应的数据源配置")), \
         patch("app.core.redis.get_redis", return_value=None):
         
        res = await call_external_sql_api(sql, data_source="nonexistent_source")
        
        assert "[TOOL_ERROR]" in res
        assert any(keyword in res for keyword in ("非法的", "不存在", "未找到"))


@pytest.mark.asyncio
async def test_sql_routing_cache_key_includes_execution_mode():
    """验证 local/remote 使用不同缓存 key，避免模式切换后拿到另一种模式的结果"""
    sql = "SELECT id FROM users"
    cache_store = {}

    class FakeRedis:
        async def get(self, key):
            return cache_store.get(key)

        async def set(self, key, value, ex=None):
            cache_store[key] = value

    local_adapter = MagicMock()
    local_adapter.execute_sql = AsyncMock(return_value={"columns": [], "items": [["local"]]})

    remote_client = AsyncMock()
    remote_response = MagicMock()
    remote_response.is_error = False
    remote_response.json.return_value = {"code": 200, "data": {"columns": [], "items": [["remote"]]}}
    remote_client.post.return_value = remote_response

    with patch("app.core.redis.get_redis", return_value=FakeRedis()), \
         patch("app.services.config_service.ConfigService.get", side_effect=lambda key, **kw: "60.0" if key == "data_api_timeout_seconds" else "http://remote/api"), \
         patch("app.services.data_adapter.factory.get_adapter", return_value=local_adapter), \
         patch("app.core.http_client.GlobalHttpClient.get_client", return_value=remote_client):
        with patch.dict("os.environ", {"SQL_EXECUTION_MODE": "remote"}):
            remote_res = json.loads(await call_external_sql_api(sql, data_source="mysql_test"))
        with patch.dict("os.environ", {"SQL_EXECUTION_MODE": "local"}):
            local_res = json.loads(await call_external_sql_api(sql, data_source="mysql_test"))

    assert remote_res["items"] == [["remote"]]
    assert local_res["items"] == [["local"]]
    assert len(cache_store) == 2
