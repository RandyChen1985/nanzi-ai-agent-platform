"""身份缺失时的 fail-closed 契约。

覆盖三处口径：
1. 真实执行 SQL 必须拿到可信 user_id，否则直接拒绝（不再静默以「无身份」执行）；
2. 缓存作用域缺失时不读也不写缓存（早期退化成共享 anon 桶，会跨调用命中彼此结果）；
3. 行级策略在身份缺失时显式拒绝，不依赖 default_policy 是否被配成空列表。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import sql_query_execution_service as sql_service
from app.services.ai.executors.base import BaseExecutor
from app.services.ai.rewriters.sql_rewriter import SQLRewriter
from app.services.ai.tools import data_api


def _config_getter(mapping: dict):
    async def _get(key, default=None):
        return mapping.get(key, default)

    return _get


# ---------------------------------------------------------------------------
# 1. execute_sql_query_core 身份门禁
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_execute_sql_rejects_missing_identity(monkeypatch):
    called = False

    async def fake_call(*args, **kwargs):
        nonlocal called
        called = True
        return "{}"

    monkeypatch.setattr(sql_service, "call_external_sql_api", fake_call)

    result = await sql_service.execute_sql_query_core(
        AsyncMock(),
        sql="SELECT 1",
        data_source="mysql_test",
        user_id=None,
        api_key=None,
        dry_run=False,
        is_admin=False,
    )

    assert result.startswith("[Permission Denied]")
    assert "缺少可信用户身份" in result
    assert called is False


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_dry_run_without_identity_keeps_previous_behaviour():
    """dry_run 不产生真实数据访问，保持原有可用性（仍受表权限约束）。"""

    result = await sql_service.execute_sql_query_core(
        AsyncMock(),
        sql="SELECT 1",
        data_source="mysql_test",
        user_id=None,
        dry_run=True,
        is_admin=False,
    )

    assert "[DRY_RUN]" in result


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_admin_without_identity_is_not_blocked_by_identity_gate(monkeypatch):
    """管理员跳过表级权限，不该被身份门禁误伤（避免把 admin 后台任务打断）。"""
    from app.services.ai.sql_sandbox_gate import SQLSandboxGate, SQLSandboxResult

    async def fake_verify(session, *, sql, data_source, dialect):
        return SQLSandboxResult(allowed=True, optimized_sql=sql)

    monkeypatch.setattr(SQLSandboxGate, "verify_and_optimize", fake_verify)

    async def fake_call(*args, **kwargs):
        return '{"columns": [], "items": []}'

    monkeypatch.setattr(sql_service, "call_external_sql_api", fake_call)

    result = await sql_service.execute_sql_query_core(
        AsyncMock(),
        sql="SELECT 1",
        data_source="mysql_test",
        user_id=None,
        dry_run=False,
        is_admin=True,
    )

    assert "[Permission Denied]" not in result


# ---------------------------------------------------------------------------
# 2. call_external_sql_api 缓存作用域
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_external_sql_api_skips_cache_without_scope():
    adapter = MagicMock()
    adapter.execute_sql = AsyncMock(
        return_value={"columns": [{"name": "id"}], "items": [[1]]}
    )
    redis_client = AsyncMock()

    with patch.dict("os.environ", {"SQL_EXECUTION_MODE": "local"}), \
         patch("app.services.data_adapter.factory.get_adapter", return_value=adapter), \
         patch("app.services.config_service.ConfigService.get", side_effect=_config_getter({"data_api_timeout_seconds": "60"})), \
         patch("app.core.redis.get_redis", new_callable=AsyncMock, return_value=redis_client):
        await data_api.call_external_sql_api("SELECT * FROM t", data_source="mysql_test")
        await data_api.call_external_sql_api("SELECT * FROM t", data_source="mysql_test")

    redis_client.get.assert_not_called()
    redis_client.set.assert_not_called()
    assert adapter.execute_sql.await_count == 2


@pytest.mark.asyncio
async def test_external_sql_api_still_caches_with_scope():
    adapter = MagicMock()
    adapter.execute_sql = AsyncMock(
        return_value={"columns": [{"name": "id"}], "items": [[1]]}
    )
    redis_client = AsyncMock()
    redis_client.get.return_value = None

    with patch.dict("os.environ", {"SQL_EXECUTION_MODE": "local"}), \
         patch("app.services.data_adapter.factory.get_adapter", return_value=adapter), \
         patch("app.services.config_service.ConfigService.get", side_effect=_config_getter({"data_api_timeout_seconds": "60"})), \
         patch("app.core.redis.get_redis", new_callable=AsyncMock, return_value=redis_client):
        await data_api.call_external_sql_api(
            "SELECT * FROM t", data_source="mysql_test", cache_scope="7"
        )

    redis_client.get.assert_awaited_once()
    redis_client.set.assert_awaited_once()
    cache_key = redis_client.set.await_args.args[0]
    assert ":7:" in cache_key
    assert ":anon:" not in cache_key


# ---------------------------------------------------------------------------
# 3. 行级策略解析
# ---------------------------------------------------------------------------


def test_resolve_strategy_denies_missing_identity_even_with_empty_default_policy():
    config = {"default_policy": [], "role_policies": {}, "user_overrides": {}}

    assert SQLRewriter.resolve_strategy(None, [], config) == [{"condition": "1=0"}]


def test_resolve_strategy_keeps_default_policy_for_real_user():
    config = {"default_policy": []}

    assert SQLRewriter.resolve_strategy(7, [], config) == []


def test_resolve_strategy_without_config_stays_passthrough():
    """数据集没配行级策略时不应被身份门禁误伤成「全拒」。"""

    assert SQLRewriter.resolve_strategy(None, [], {}) == []


# ---------------------------------------------------------------------------
# 4. 上下文身份注入
# ---------------------------------------------------------------------------


def _executor_stub(**kwargs):
    base = dict(
        trace_id="trace-1",
        conversation_id="conv-1",
        user_info={"api_key": "sk-test", "role": "user"},
        execution_options={},
        permission_options={},
        trace_buffer=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_apply_identity_clears_stale_user_but_keeps_api_key():
    executor = _executor_stub()
    ctx = SimpleNamespace(user_id=99, is_admin=True, user_dimensions={"id": 99}, api_key=None)

    BaseExecutor._apply_user_identity_to_context(
        executor,
        ctx,
        {"u_id_val": None, "is_admin_val": False, "user_dims": {}},
    )

    assert ctx.user_id is None
    assert ctx.is_admin is False
    assert ctx.user_dimensions == {}
    # api_key 必须在提前返回之前写入，供下游反查身份
    assert ctx.api_key == "sk-test"


def test_apply_identity_without_user_info_keeps_inherited_identity():
    executor = _executor_stub(user_info=None)
    ctx = SimpleNamespace(user_id=5, is_admin=False, user_dimensions={}, api_key=None)

    BaseExecutor._apply_user_identity_to_context(
        executor,
        ctx,
        {"u_id_val": None, "is_admin_val": False, "user_dims": {}},
    )

    assert ctx.user_id == 5
