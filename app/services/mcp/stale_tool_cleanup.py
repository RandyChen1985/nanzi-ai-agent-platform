"""清理 MCP 工具缓存中「远端已删除」的僵尸行。

远端把工具改名或下架后，``sys_mcp_tool_cache`` 会保留 ``is_available = False``
的旧行——这些行对用户只有噪音价值（不可发布、不可执行、不被智能体调用），
却会让服务卡片一直显示「N 个远端已删除」。

清理只做两件事，都不影响服务本身的配置与调用审计：

1. 删除该服务下 ``is_available = False`` 的缓存行；
2. 从智能体版本配置里剔除指向这些工具名的死引用。

第 2 步是必要的：版本配置的 ``tools`` 在前端是整体加载、整体保存的，UI 里
看不到已失效工具，用户也就无法手动取消勾选，不剔除就会永久残留。
剔除不改变实际能力——这些工具本来就被 ``is_available`` 过滤掉。

刻意**不**删的东西：``sys_mcp_servers``（服务配置）、
``sys_mcp_outbound_audit_logs``（调用审计）、``mcp_server_shares``（分享记录）。
删服务会因外键 ``ondelete=CASCADE`` 连带清空审计日志，代价远大于清缓存。
"""
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AIAgentVersion
from app.models.mcp import McpToolCache


def _tool_reference_name(item: Any) -> Optional[str]:
    """版本配置里的工具项可能是纯名称，也可能是带配置的对象。"""
    if isinstance(item, str):
        return item.strip() or None
    if isinstance(item, dict):
        name = item.get("name")
        if name is None:
            return None
        text = str(name).strip()
        return text or None
    return None


def prune_dead_tool_refs(tools_config: Any, dead_names: Set[str]) -> Optional[List[Any]]:
    """返回剔除死引用后的工具列表；无需改动（或无有效结构）时返回 ``None``。

    返回空列表是合法结果：某个版本可能只挂了这一个已失效工具，此时写回
    ``[]`` 而不是 ``None``，前端是按数组遍历的。
    """
    if not isinstance(tools_config, list):
        return None

    remaining: List[Any] = []
    changed = False
    for item in tools_config:
        name = _tool_reference_name(item)
        if name is not None and name in dead_names:
            changed = True
            continue
        remaining.append(item)
    return remaining if changed else None


async def prune_agent_versions_by_names(db: AsyncSession, dead_names: Set[str]) -> int:
    """从所有智能体版本配置里剔除指向 ``dead_names`` 的引用，返回受影响的版本数。

    刻意不 commit：删除 MCP 服务需要与「删缓存 + 删服务」在同一个事务里落库，
    否则中途失败会留下「服务没了、引用还在」的半成品状态。
    """
    if not dead_names:
        return 0

    affected_versions = 0
    # tools 的 JSON 包含查询在 MySQL / PostgreSQL 语法不同，而清理是低频管理操作，
    # 全表取 (id, tools) 在 Python 侧过滤最省心，也避免方言分支。
    versions_result = await db.execute(select(AIAgentVersion.id, AIAgentVersion.tools))
    for version_id, tools_config in versions_result.all():
        remaining = prune_dead_tool_refs(tools_config, dead_names)
        if remaining is None:
            continue
        await db.execute(
            update(AIAgentVersion).where(AIAgentVersion.id == version_id).values(tools=remaining)
        )
        affected_versions += 1
    return affected_versions


async def cleanup_stale_tools(db: AsyncSession, server_id: str) -> Dict[str, Any]:
    """删除 ``server_id`` 下所有远端已不存在的工具缓存，并清理版本死引用。"""
    stale_result = await db.execute(
        select(McpToolCache).where(
            McpToolCache.server_id == server_id,
            McpToolCache.is_available == False,  # noqa: E712 — 显式比较，避免 NULL 语义歧义
        )
    )
    stale_tools = stale_result.scalars().all()
    if not stale_tools:
        return {"removed_count": 0, "affected_agent_versions": 0, "removed_tool_names": []}

    dead_names: Set[str] = {tool.tool_name for tool in stale_tools}
    stale_ids = [tool.id for tool in stale_tools]
    await db.execute(delete(McpToolCache).where(McpToolCache.id.in_(stale_ids)))

    affected_versions = await prune_agent_versions_by_names(db, dead_names)
    await db.commit()
    return {
        "removed_count": len(stale_ids),
        "affected_agent_versions": affected_versions,
        "removed_tool_names": sorted(dead_names),
    }
