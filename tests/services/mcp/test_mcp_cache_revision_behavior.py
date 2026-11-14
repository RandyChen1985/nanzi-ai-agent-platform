"""两组进程内缓存共享一条 Redis 修订信号，不访问 Redis 服务。"""

from types import SimpleNamespace

import pytest

from app.api.portal.endpoints import mcp
from app.models.credential_type import McpCredentialProfile
from app.models.mcp import McpToolCache
from app.services.ai.tools import registry


@pytest.fixture
def workers(monkeypatch):
    class Worker2(registry.ToolRegistry):
        _db_tool_cache = {}
        _db_tool_source_cache = {}
        _db_tool_ids_fetched_at = {}
        _mcp_tool_cache_revisions = {}

    for name in ("_db_tool_cache", "_db_tool_source_cache", "_db_tool_ids_fetched_at", "_mcp_tool_cache_revisions"):
        monkeypatch.setattr(registry.ToolRegistry, name, {})
    row = McpToolCache(
        id="fixture", server_id="server", tool_name="fixture:find",
        parameter_schema='{"type":"object","properties":{}}', tool_description="original",
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, _stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: row)

        async def get(self, model, _id):
            assert model is McpCredentialProfile
            return None

    class SharedRedis:
        value = None
        unavailable = False

        async def get(self, _key):
            if self.unavailable:
                raise ConnectionError("fixture")
            return self.value

        async def set(self, _key, value):
            self.value = value

    redis = SharedRedis()

    async def get_redis():
        return redis

    from app.services.mcp import tool_cache_revision
    monkeypatch.setattr(tool_cache_revision.settings, "REDIS_ENABLE", True)
    monkeypatch.setattr(tool_cache_revision, "get_redis", get_redis)
    monkeypatch.setattr(registry, "AsyncSessionLocal", Session)
    return registry.ToolRegistry, Worker2, row, redis


async def test_mcp_edit_reloads_another_workers_cached_tool(workers):
    local, other, row, _ = workers
    initial = await other.get_tool(row.tool_name)
    assert initial is await other.get_tool(row.tool_name)
    row.tool_description = "updated"
    await mcp._clear_runtime_tool_cache()
    changed = await other.get_tool(row.tool_name)
    assert changed is not initial
    assert changed is await other.get_tool(row.tool_name)


async def test_redis_failure_does_not_keep_mcp_metadata_stale(workers):
    _, worker, row, redis = workers
    initial = await worker.get_tool(row.tool_name)
    redis.unavailable = True
    row.tool_description = "updated"
    assert await worker.get_tool(row.tool_name) is not initial
