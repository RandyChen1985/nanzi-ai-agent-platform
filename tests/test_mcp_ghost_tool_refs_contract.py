"""删除 MCP 服务必须顺手清理智能体版本引用（幽灵配置的源头）。

纯源码契约测试：这三处一旦被改回去，用户就会重新遇到「服务删了、引用还在、
编辑器里看不到也解不掉」的问题，而这个问题不会在其它测试里冒出来。
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

MCP_ENDPOINT = Path("app/api/portal/endpoints/mcp.py").read_text()
AGENT_ENDPOINT = Path("app/api/portal/endpoints/agents.py").read_text()
CLEANUP = Path("app/services/mcp/stale_tool_cleanup.py").read_text()


def test_delete_server_collects_tool_names_before_deleting_cache():
    delete_body = MCP_ENDPOINT.split("async def delete_mcp_server", 1)[1].split(
        "@router.delete", 1
    )[0]
    assert "select(McpToolCache.tool_name)" in delete_body
    assert "prune_agent_versions_by_names" in delete_body
    # 名字必须在删缓存前取，否则只剩空集合，清理会变成空操作
    assert delete_body.index("select(McpToolCache.tool_name)") < delete_body.index(
        "delete(McpToolCache)"
    )


def test_delete_server_reports_affected_versions():
    delete_body = MCP_ENDPOINT.split("async def delete_mcp_server", 1)[1].split(
        "@router.delete", 1
    )[0]
    assert '"affected_agent_versions"' in delete_body
    assert "removed_tool_names" in delete_body


def test_version_tool_health_endpoint_exists_and_is_owner_scoped():
    assert "@router.get(\"/{agent_id}/versions/{version_id}/tool-health\")" in AGENT_ENDPOINT
    health_body = AGENT_ENDPOINT.split("async def get_agent_version_tool_health", 1)[1].split(
        "@router.", 1
    )[0]
    assert "find_stale_tool_refs" in health_body
    assert "stale_tools" in health_body
    assert "_resolve_actor" in health_body
    assert "created_by" in health_body


def test_stale_cleanup_reuses_shared_prune_helper():
    assert "async def prune_agent_versions_by_names" in CLEANUP
    # 清理远端已删除工具与删除服务必须走同一个实现，避免两套语义漂移
    cleanup_body = CLEANUP.split("async def cleanup_stale_tools", 1)[1]
    assert "prune_agent_versions_by_names(db, dead_names)" in cleanup_body
    # 共用 helper 不自己 commit，由调用方控制事务边界
    helper_body = CLEANUP.split("async def prune_agent_versions_by_names", 1)[1].split(
        "async def cleanup_stale_tools", 1
    )[0]
    assert "await db.commit()" not in helper_body


def test_static_tools_are_never_treated_as_stale():
    """静态工具不依赖数据库配置，诊断必须把它们排除，否则会误报一大批。"""
    registry_source = Path("app/services/ai/tools/registry.py").read_text()
    health_source = Path("app/services/mcp/tool_ref_health.py").read_text()
    assert "def known_static_tool_names" in registry_source
    assert "ToolRegistry.known_static_tool_names()" in health_source
    assert "AGENTSCOPE_BUILTIN_TOOL_ALIASES" in registry_source


def test_tool_health_has_stateless_variant_for_unsaved_versions():
    """新建/克隆版本没有 id：必须有一个接收 tools 的只读诊断入口。"""
    source = Path("app/api/portal/endpoints/agents.py").read_text()

    assert "class AgentToolsHealthRequest" in source
    assert '@router.post("/{agent_id}/tool-health")' in source
    # 与按版本查询共用同一套判定，权限也一致（owner 或 admin）
    post_body = source.split("async def check_agent_tools_health", 1)[1].split(
        '@router.post("/{agent_id}/versions"', 1
    )[0]
    assert "find_stale_tool_refs(session, payload.tools, user=user)" in post_body
    assert "只能校验自己智能体的工具配置" in post_body
    assert '"version_id": None' in post_body
