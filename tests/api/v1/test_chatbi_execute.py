import pytest
import json
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import HTTPException
from httpx import AsyncClient
from pydantic import ValidationError
from types import SimpleNamespace

from app.api.v1.endpoints import chatbi
from app.api.v1.endpoints.chatbi import ChatBiSqlExecuteRequest
from app.services.ai.chatbi_sql_query_binding import TableBinding

# 构造 Mock Dataset
mock_dataset = SimpleNamespace(
    id=1,
    name="test_dataset",
    enable_data_perm=False,
    row_filter_config=None
)

@pytest.fixture
def mock_infrastructure():
    """Mock metadata and permissions checks to focus on SQL execution routing"""
    with patch("app.services.metadata_service.MetadataService.get_dataset_by_name", return_value=mock_dataset), \
         patch("app.services.sql_query_execution_service.enforce_physical_table_permissions_for_select", return_value=None):
        yield


@pytest.mark.asyncio
async def test_chatbi_execute_request_requires_sessionid():
    """执行 SQL 请求模型必须提供 OpenClaw 会话 ID。"""
    with pytest.raises(ValidationError) as exc_info:
        ChatBiSqlExecuteRequest.model_validate({
            "sql": "SELECT 1",
            "data_source": "default_clickhouse",
        })

    assert "sessionid" in str(exc_info.value)


@pytest.mark.no_infrastructure
def test_chatbi_execute_request_accepts_optional_dataset_name():
    body = ChatBiSqlExecuteRequest.model_validate({
        "sql": "SELECT 1",
        "data_source": "default_clickhouse",
        "dataset_name": "sales_dataset",
        "sessionid": "openclaw-session-1",
    })

    assert body.dataset_name == "sales_dataset"


@pytest.mark.no_infrastructure
def test_openclaw_openai_sessionid_extracts_username():
    username = chatbi._openclaw_openai_username_from_sessionid(
        "agent:chatbi_bot:openai-user:chenxiaolong-6c03b966-9d89-413d-8138-01aa395e6ea2"
    )

    assert username == "chenxiaolong"


@pytest.mark.no_infrastructure
def test_openclaw_openai_sessionid_ignores_other_formats():
    assert chatbi._openclaw_openai_username_from_sessionid("openclaw-session-1") is None
    assert chatbi._openclaw_openai_username_from_sessionid(
        "agent:chatbi_bot:web-user:chenxiaolong-6c03b966-9d89-413d-8138-01aa395e6ea2"
    ) is None


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_openclaw_session_auth_uses_session_username(monkeypatch):
    body = ChatBiSqlExecuteRequest.model_validate({
        "sql": "SELECT 1",
        "data_source": "default_clickhouse",
        "dataset_name": "sales_dataset",
        "sessionid": "agent:chatbi_bot:openai-user:chenxiaolong-6c03b966-9d89-413d-8138-01aa395e6ea2",
    })
    captured = {}

    async def fake_resolve_user_by_username(username, db):
        captured["username"] = username
        return {
            "user_id": "42",
            "user_name": username,
            "real_name": username,
            "role": "user",
            "dept_code": "",
            "org_path": "",
            "extra_data": "",
        }

    async def fake_execute_sql_query_core(*args, **kwargs):
        captured["auth_kwargs"] = kwargs
        return json.dumps({"allowed": True})

    monkeypatch.setattr(chatbi.AuthService, "resolve_user_by_username", fake_resolve_user_by_username)
    monkeypatch.setattr(chatbi, "execute_sql_query_core", fake_execute_sql_query_core)

    await chatbi._enforce_openclaw_session_sql_auth(AsyncMock(), body)

    assert captured["username"] == "chenxiaolong"
    assert captured["auth_kwargs"]["user_id"] == 42
    assert captured["auth_kwargs"]["dataset_name"] == "sales_dataset"
    assert captured["auth_kwargs"]["auth_check_only"] is True
    assert captured["auth_kwargs"]["dry_run"] is False


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_direct_sql_dataset_name_infers_unique_dataset(monkeypatch):
    async def fake_resolve_table_bindings_from_db(db, physical_names):
        assert physical_names == ["orders", "customers"]
        return {
            "orders": TableBinding(physical_name="orders", dataset_name="sales_dataset"),
            "customers": TableBinding(physical_name="customers", dataset_name="sales_dataset"),
        }

    monkeypatch.setattr(
        chatbi,
        "extract_physical_table_refs_from_select_sql",
        lambda sql, dialect: (None, {"orders": "orders", "customers": "customers"}),
    )
    monkeypatch.setattr(chatbi, "resolve_table_bindings_from_db", fake_resolve_table_bindings_from_db)

    dataset_name = await chatbi._resolve_direct_sql_dataset_name(
        AsyncMock(),
        sql="SELECT * FROM orders JOIN customers ON orders.customer_id = customers.id",
        data_source="mysql_test",
        explicit_dataset_name=None,
    )

    assert dataset_name == "sales_dataset"


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_direct_sql_dataset_name_rejects_cross_dataset(monkeypatch):
    async def fake_resolve_table_bindings_from_db(db, physical_names):
        return {
            "orders": TableBinding(physical_name="orders", dataset_name="sales_dataset"),
            "tickets": TableBinding(physical_name="tickets", dataset_name="support_dataset"),
        }

    monkeypatch.setattr(
        chatbi,
        "extract_physical_table_refs_from_select_sql",
        lambda sql, dialect: (None, {"orders": "orders", "tickets": "tickets"}),
    )
    monkeypatch.setattr(chatbi, "resolve_table_bindings_from_db", fake_resolve_table_bindings_from_db)

    with pytest.raises(HTTPException) as exc_info:
        await chatbi._resolve_direct_sql_dataset_name(
            AsyncMock(),
            sql="SELECT * FROM orders JOIN tickets ON orders.id = tickets.order_id",
            data_source="mysql_test",
            explicit_dataset_name=None,
        )

    assert exc_info.value.status_code == 400
    assert "跨数据集" in str(exc_info.value.detail)


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_direct_sql_dataset_inference_rejects_when_row_filter_required(monkeypatch):
    async def fake_resolve_table_bindings_from_db(db, physical_names):
        return {
            "orders": TableBinding(physical_name="orders", dataset_name="sales_dataset"),
        }

    async def fake_get_dataset_by_name(_db, name):
        return SimpleNamespace(enable_data_perm=True) if name == "sales_dataset" else None

    monkeypatch.setattr(
        chatbi,
        "extract_physical_table_refs_from_select_sql",
        lambda sql, dialect: (None, {"orders": "orders", "customers": "customers"}),
    )
    monkeypatch.setattr(chatbi, "resolve_table_bindings_from_db", fake_resolve_table_bindings_from_db)
    monkeypatch.setattr(chatbi.MetadataService, "get_dataset_by_name", fake_get_dataset_by_name)

    with pytest.raises(HTTPException) as exc_info:
        await chatbi._resolve_direct_sql_dataset_name(
            AsyncMock(),
            sql="SELECT * FROM orders JOIN customers ON orders.customer_id = customers.id",
            data_source="mysql_test",
            explicit_dataset_name=None,
        )

    assert exc_info.value.status_code == 400
    assert "行级数据权限" in str(exc_info.value.detail)


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_chatbi_execute_passes_explicit_dataset_name(monkeypatch):
    body = ChatBiSqlExecuteRequest.model_validate({
        "sql": "SELECT COUNT(*) FROM orders",
        "data_source": "mysql_test",
        "dataset_name": "sales_dataset",
        "sessionid": "openclaw-session-1",
    })
    user_info = {
        "user_id": "7",
        "user_name": "alice",
        "real_name": "Alice",
        "role": "user",
        "dept_code": "D001",
        "org_path": "/D001",
        "extra_data": "",
    }
    captured = {}

    async def fake_execute_sql_query_core(*args, **kwargs):
        captured.update(kwargs)
        return json.dumps({"columns": [], "items": []})

    monkeypatch.setattr(chatbi, "execute_sql_query_core", fake_execute_sql_query_core)

    with patch.dict("os.environ", {"SQL_EXECUTION_MODE": "local"}):
        await chatbi.chatbi_sql_execute(body, user_info=user_info, db=AsyncMock())

    assert captured["dataset_name"] == "sales_dataset"
    assert captured["user_dimensions"]["dept_code"] == "D001"


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_chatbi_execute_returns_permission_notice_when_row_filter_applied(monkeypatch):
    body = ChatBiSqlExecuteRequest.model_validate({
        "sql": "SELECT COUNT(*) FROM orders",
        "data_source": "mysql_test",
        "dataset_name": "sales_dataset",
        "sessionid": "openclaw-session-1",
    })
    user_info = {
        "user_id": "7",
        "user_name": "alice",
        "real_name": "Alice",
        "role": "user",
        "dept_code": "D001",
        "org_path": "/D001",
        "extra_data": "",
    }

    async def fake_execute_sql_query_core(*args, **kwargs):
        kwargs["permission_notice"]["row_filter_applied"] = True
        kwargs["permission_notice"]["dataset_name"] = "sales_dataset"
        kwargs["permission_notice"]["rule_count"] = 2
        kwargs["permission_notice"]["message"] = "已按你的数据权限自动过滤结果"
        return json.dumps({"columns": [], "items": []})

    monkeypatch.setattr(chatbi, "execute_sql_query_core", fake_execute_sql_query_core)

    with patch.dict("os.environ", {"SQL_EXECUTION_MODE": "local"}):
        response = await chatbi.chatbi_sql_execute(body, user_info=user_info, db=AsyncMock())

    assert response.data.permission_notice == {
        "row_filter_applied": True,
        "dataset_name": "sales_dataset",
        "rule_count": 2,
        "message": "已按你的数据权限自动过滤结果",
    }


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_chatbi_checkauth_passes_explicit_dataset_name(monkeypatch):
    body = chatbi.ChatBiSqlCheckAuthRequest.model_validate({
        "username": "alice",
        "sql": "SELECT COUNT(*) FROM orders",
        "data_source": "mysql_test",
        "dataset_name": "sales_dataset",
    })
    captured = {}

    async def fake_resolve_user_by_username(username, db):
        return {
            "user_id": "7",
            "user_name": username,
            "real_name": "Alice",
            "role": "user",
            "dept_code": "D001",
            "org_path": "/D001",
            "extra_data": "",
        }

    async def fake_execute_sql_query_core(*args, **kwargs):
        captured.update(kwargs)
        return json.dumps({"allowed": True})

    monkeypatch.setattr(chatbi.AuthService, "resolve_user_by_username", fake_resolve_user_by_username)
    monkeypatch.setattr(chatbi, "execute_sql_query_core", fake_execute_sql_query_core)

    with patch.dict("os.environ", {"SQL_EXECUTION_MODE": "local"}):
        await chatbi.chatbi_sql_checkauth(body, db=AsyncMock())

    assert captured["dataset_name"] == "sales_dataset"
    assert captured["auth_check_only"] is True


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_chatbi_execute_uses_session_user_for_openclaw_sql_execution(monkeypatch):
    body = ChatBiSqlExecuteRequest.model_validate({
        "sql": "SELECT COUNT(*) FROM view_ai_cabinet_sales",
        "data_source": "mysql_zhifu",
        "sessionid": "agent:chatbi_bot:openai-user:chenxiaolong-5f6d6e95-5415-4d89-bedb-3a0a5ab87cf3",
    })
    api_key_user = {
        "user_id": "898",
        "user_name": "openclaw",
        "real_name": "openclaw",
        "role": "user",
        "dept_code": "",
        "org_path": "",
        "extra_data": "",
    }
    calls = []

    async def fake_resolve_user_by_username(username, db):
        assert username == "chenxiaolong"
        return {
            "user_id": "4",
            "user_name": "chenxiaolong",
            "real_name": "陈小龙",
            "role": "user",
            "dept_code": "",
            "org_path": "",
            "extra_data": "",
        }

    async def fake_execute_sql_query_core(*args, **kwargs):
        calls.append(kwargs)
        if kwargs.get("auth_check_only"):
            return json.dumps({"allowed": True})
        return json.dumps({"columns": [], "items": []})

    monkeypatch.setattr(chatbi.AuthService, "resolve_user_by_username", fake_resolve_user_by_username)
    monkeypatch.setattr(chatbi, "execute_sql_query_core", fake_execute_sql_query_core)
    monkeypatch.setattr(chatbi, "resolve_table_bindings_from_db", AsyncMock(return_value={}))

    with patch.dict("os.environ", {"SQL_EXECUTION_MODE": "local"}):
        await chatbi.chatbi_sql_execute(body, user_info=api_key_user, db=AsyncMock())

    assert calls[0]["user_id"] == 4
    assert calls[0]["auth_check_only"] is True
    assert calls[1]["user_id"] == 4
    assert calls[1]["user_dimensions"]["user_name"] == "chenxiaolong"
    assert calls[1].get("bypass_table_auth") is False


@pytest.mark.asyncio
async def test_chatbi_execute_local_success(client: AsyncClient, admin_api_key: str, mock_infrastructure):
    """测试本地模式下执行只读 SQL 成功返回数据"""
    mock_adapter = AsyncMock()
    mock_adapter.execute_sql.return_value = {
        "columns": [{"name": "id", "type": "int"}, {"name": "name", "type": "str"}],
        "items": [[1, "Alice"]]
    }
    
    headers = {"X-API-Key": admin_api_key}
    
    with patch.dict("os.environ", {"SQL_EXECUTION_MODE": "local"}), \
         patch("app.services.data_adapter.factory.get_adapter", return_value=mock_adapter) as mock_get_adapter, \
         patch("app.core.redis.get_redis", return_value=None):
         
        response = await client.post(
            "/api/v1/chatbi/sql/execute",
            headers=headers,
            json={
                "sql": "SELECT id, name FROM users",
                "data_source": "mysql_test",
                "sessionid": "openclaw-session-1"
            }
        )
        
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["code"] == 200
        assert body["data"]["items"] == [[1, "Alice"]]
        assert "SELECT * FROM (SELECT id, name FROM users) AS _sub LIMIT 1000" in mock_adapter.execute_sql.call_args[0][0]


@pytest.mark.asyncio
async def test_chatbi_execute_local_safety_block(client: AsyncClient, admin_api_key: str, mock_infrastructure):
    """测试本地模式下执行危险 SQL 被拦截，返回 403 权限拒绝"""
    headers = {"X-API-Key": admin_api_key}
    
    # 模拟 SQLSafetyError 拦截
    with patch.dict("os.environ", {"SQL_EXECUTION_MODE": "local"}), \
         patch("app.core.redis.get_redis", return_value=None):
         
        response = await client.post(
            "/api/v1/chatbi/sql/execute",
            headers=headers,
            json={
                "sql": "DROP TABLE users",
                "data_source": "mysql_test",
                "sessionid": "openclaw-session-1"
            }
        )
        
        # 被 validate_sql (AST 解析) 拦截返回 400 Bad Request
        assert response.status_code == 400
        assert "Only read-only queries (SELECT, EXPLAIN, SHOW, DESCRIBE) are allowed" in response.json()["message"]


@pytest.mark.asyncio
async def test_chatbi_execute_local_timeout(client: AsyncClient, admin_api_key: str, mock_infrastructure):
    """测试本地模式下 SQL 执行超时，返回 502 Bad Gateway"""
    mock_adapter = AsyncMock()
    # 模拟超时异常
    mock_adapter.execute_sql.side_effect = asyncio.TimeoutError()
    
    headers = {"X-API-Key": admin_api_key}
    
    with patch.dict("os.environ", {"SQL_EXECUTION_MODE": "local"}), \
         patch("app.services.data_adapter.factory.get_adapter", return_value=mock_adapter), \
         patch("app.services.config_service.ConfigService.get", return_value="0.01"), \
         patch("app.core.redis.get_redis", return_value=None):
         
        response = await client.post(
            "/api/v1/chatbi/sql/execute",
            headers=headers,
            json={
                "sql": "SELECT * FROM giant_table",
                "data_source": "mysql_test",
                "sessionid": "openclaw-session-1"
            }
        )
        
        assert response.status_code == 502
        assert "[TOOL_ERROR]" in response.json()["message"]
        assert "超时" in response.json()["message"]


@pytest.mark.asyncio
async def test_chatbi_execute_local_source_not_found(client: AsyncClient, admin_api_key: str, mock_infrastructure):
    """测试数据源不存在时，本地模式返回 502 错误并且包含 TOOL_ERROR"""
    headers = {"X-API-Key": admin_api_key}
    
    with patch.dict("os.environ", {"SQL_EXECUTION_MODE": "local"}), \
         patch("app.services.data_adapter.factory.get_adapter", side_effect=ValueError("数据源不存在")), \
         patch("app.core.redis.get_redis", return_value=None):
         
        response = await client.post(
            "/api/v1/chatbi/sql/execute",
            headers=headers,
            json={
                "sql": "SELECT 1",
                "data_source": "non_existent_db",
                "sessionid": "openclaw-session-1"
            }
        )
        
        assert response.status_code == 502
        assert "[TOOL_ERROR]" in response.json()["message"]
        assert "数据源" in response.json()["message"]


@pytest.mark.asyncio
async def test_chatbi_execute_response_contains_execution_mode(client: AsyncClient, admin_api_key: str, mock_infrastructure):
    """测试无论成功还是失败，响应 JSON 的 trace_id 后面都会带上 execution_mode 字段"""
    headers = {"X-API-Key": admin_api_key}
    
    mock_adapter = AsyncMock()
    mock_adapter.execute_sql.return_value = {
        "columns": [{"name": "id", "type": "int"}],
        "items": [[1]]
    }
    
    with patch.dict("os.environ", {"SQL_EXECUTION_MODE": "local"}), \
         patch("app.services.data_adapter.factory.get_adapter", return_value=mock_adapter), \
         patch("app.core.redis.get_redis", return_value=None):
         
        # 1. 测试成功响应带上 execution_mode
        response = await client.post(
            "/api/v1/chatbi/sql/execute",
            headers=headers,
            json={
                "sql": "SELECT id FROM users",
                "data_source": "mysql_test",
                "sessionid": "openclaw-session-1"
            }
        )
        assert response.status_code == 200
        body = response.json()
        assert "execution_mode" in body
        assert body["execution_mode"] == "local"
        
        # 2. 测试失败响应 (如校验失败) 带上 execution_mode
        response_fail = await client.post(
            "/api/v1/chatbi/sql/execute",
            headers=headers,
            json={
                "sql": "DROP TABLE users",
                "data_source": "mysql_test",
                "sessionid": "openclaw-session-1"
            }
        )
        assert response_fail.status_code == 400
        body_fail = response_fail.json()
        assert "execution_mode" in body_fail
        assert body_fail["execution_mode"] == "local"


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_chatbi_execute_switches_is_admin_to_session_user(monkeypatch):
    """OpenClaw 用管理员 API Key 携带普通用户会话时，执行身份必须整体切换。

    只切 user_id 不切 is_admin，管理员身份会继续作用于普通用户会话，
    跳过字段级裁剪与行级 ``custom`` 过滤——SELECT * 就能拿到全部受限列。
    """
    body = ChatBiSqlExecuteRequest.model_validate({
        "sql": "SELECT * FROM ai_agent_users",
        "data_source": "mysql_test",
        "dataset_name": "sales_dataset",
        "sessionid": "openclaw-session-1",
    })
    caller_admin = {
        "user_id": "1",
        "user_name": "openclaw",
        "real_name": "OpenClaw",
        "role": "admin",
        "dept_code": "D000",
        "org_path": "/D000",
        "extra_data": "",
    }
    session_user = {
        "user_id": 178,
        "user_name": "bob",
        "real_name": "Bob",
        "role": "user",
        "dept_code": "D001",
        "org_path": "/D001",
        "extra_data": "",
    }
    captured = {}

    async def fake_session_auth(_db, _body):
        return session_user

    async def fake_execute_sql_query_core(*args, **kwargs):
        captured.update(kwargs)
        return json.dumps({"columns": [], "items": []})

    monkeypatch.setattr(chatbi, "_enforce_openclaw_session_sql_auth", fake_session_auth)
    monkeypatch.setattr(chatbi, "execute_sql_query_core", fake_execute_sql_query_core)
    monkeypatch.setattr(
        chatbi, "_resolve_direct_sql_dataset_name", AsyncMock(return_value="sales_dataset")
    )
    monkeypatch.setattr(
        chatbi, "build_sql_user_dimensions", AsyncMock(return_value={"dept_code": "D001"})
    )

    with patch.dict("os.environ", {"SQL_EXECUTION_MODE": "local"}):
        await chatbi.chatbi_sql_execute(body, user_info=caller_admin, db=AsyncMock())

    assert captured["user_id"] == 178
    assert captured["is_admin"] is False
