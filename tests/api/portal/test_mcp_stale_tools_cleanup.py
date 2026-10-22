"""「远端已删除」工具清理：服务层行为 + 端点契约。

背景：远端把工具改名/下架后，``sys_mcp_tool_cache`` 会留下一批
``is_available = False`` 的僵尸行，服务卡片上显示「N 个远端已删除」。
唯一自动消失的路径是远端恢复同名全名工具；其余情况此前只能连库 DELETE。
本模块锁定新增的一键清理路径：只删失效缓存、只从智能体版本配置里剔除
死引用，不碰服务配置与审计日志。
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.sql.dml import Delete, Update

from app.api.portal.endpoints import mcp as mcp_endpoints
from app.services.mcp.stale_tool_cleanup import cleanup_stale_tools

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
ENDPOINT_SOURCE = (ROOT / "app/api/portal/endpoints/mcp.py").read_text(encoding="utf-8")


def _fake_db(execute_results):
    """按 SELECT 的调用顺序返回结果；DELETE / UPDATE 自动返回空结果。

    清理流程里的 SELECT 固定是「失效工具行 → 版本配置」，DML 语句不产生结果集，
    这样测试不必为 DELETE/UPDATE 也塞占位返回，只关心读取到的数据。
    """
    db = MagicMock()
    pending = list(execute_results)

    async def _execute(statement, *_args, **_kwargs):
        if isinstance(statement, (Delete, Update)):
            return MagicMock()
        return pending.pop(0) if pending else MagicMock()

    db.execute = AsyncMock(side_effect=_execute)
    db.commit = AsyncMock()
    return db


def _scalars_result(items):
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _rows_result(rows):
    result = MagicMock()
    result.all.return_value = list(rows)
    return result


def _stale_tool(tool_id: str, name: str):
    return SimpleNamespace(
        id=tool_id,
        server_id="server-1",
        tool_name=name,
        is_published=False,
        is_available=False,
    )


async def test_cleanup_without_stale_tools_is_a_noop():
    """没有失效工具时不发删除/更新语句，避免空 IN () 报错。"""
    db = _fake_db([_scalars_result([])])

    result = await cleanup_stale_tools(db, "server-1")

    assert result == {
        "removed_count": 0,
        "affected_agent_versions": 0,
        "removed_tool_names": [],
    }
    assert db.execute.await_count == 1
    db.commit.assert_not_awaited()


async def test_cleanup_deletes_only_stale_rows_and_prunes_agent_version_refs():
    """删缓存只按 is_available=False 的行；版本里同名的死引用一并剔除。"""
    stale = [
        _stale_tool("tool-1", "wtoe:old_query"),
        _stale_tool("tool-2", "wtoe:old_report"),
    ]
    versions = [
        ("version-hit", ["keep_tool", "wtoe:old_query", "wtoe:old_report"]),
        ("version-dicts", [{"name": "wtoe:old_query", "config": 1}, "keep_other"]),
        ("version-clean", ["keep_tool"]),
    ]
    db = _fake_db([_scalars_result(stale), _rows_result(versions)])

    result = await cleanup_stale_tools(db, "server-1")

    assert result["removed_count"] == 2
    assert result["affected_agent_versions"] == 2
    assert result["removed_tool_names"] == ["wtoe:old_query", "wtoe:old_report"]

    # 1 次 select 失效工具 + 1 次 DELETE + 1 次 select 版本 + 2 次 UPDATE 命中版本
    assert db.execute.await_count == 5
    db.commit.assert_awaited_once()

    # 第一个 update 是剔除引用，不应命中无死引用的 version-clean
    update_statements = [c.args[0] for c in db.execute.await_args_list]
    compiled = [str(stmt) for stmt in update_statements]
    assert any("sys_mcp_tool_cache" in sql and "DELETE" in sql.upper() for sql in compiled)
    assert sum("UPDATE AI_AGENT_VERSIONS" in sql.upper() for sql in compiled) == 2


async def test_cleanup_keeps_empty_list_when_all_refs_are_pruned():
    """版本里只剩死引用时必须写回 []，不能写 NULL（前端按数组遍历）。"""
    stale = [_stale_tool("tool-1", "wtoe:old_query")]
    db = _fake_db([
        _scalars_result(stale),
        _rows_result([("version-only-stale", ["wtoe:old_query"])]),
    ])

    await cleanup_stale_tools(db, "server-1")

    # 0 = select 失效行，1 = DELETE，2 = select 版本，3 = 唯一一次 UPDATE
    update_stmt = db.execute.await_args_list[3].args[0]
    assert update_stmt.compile().params["tools"] == []


async def test_cleanup_tolerates_legacy_non_list_tools_config():
    """历史数据里 tools 可能是 None / 字符串 / 非法 JSON，不能因此 500。"""
    stale = [_stale_tool("tool-1", "wtoe:old_query")]
    versions = [
        ("v-none", None),
        ("v-string", "keep_tool"),
        ("v-empty", []),
    ]
    db = _fake_db([_scalars_result(stale), _rows_result(versions)])

    result = await cleanup_stale_tools(db, "server-1")

    assert result["removed_count"] == 1
    assert result["affected_agent_versions"] == 0
    # select 失效 + DELETE + select 版本，没有 UPDATE
    assert db.execute.await_count == 3


def test_cleanup_route_is_registered_with_delete_permission():
    """路由与权限点：清理是删除类动作，必须复用 element:mcp_management:delete。"""
    routes = set()
    for route in mcp_endpoints.router.routes:
        for method in getattr(route, "methods", set()) or set():
            routes.add((method.upper(), route.path))
    assert ("DELETE", "/servers/{server_id}/stale-tools") in routes

    assert 'element:mcp_management:delete' in ENDPOINT_SOURCE
    block = ENDPOINT_SOURCE.split("async def cleanup_mcp_server_stale_tools(", 1)[1]
    block = block.split("@router.", 1)[0]
    # 平台公共 MCP 只有管理员能动，个人 MCP 只有属主能动
    assert "_ensure_server_control_access(server, user)" in block
    assert '"Server not found"' in block
