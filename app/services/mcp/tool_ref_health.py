"""识别智能体版本配置里「运行时已不可用」的 MCP 工具引用。

版本配置的 ``tools`` 在前端是整体加载、整体保存的，而工具选择器只渲染**当前可见且
已发布**的工具。MCP 服务被删除、被禁用，或工具被下线后，旧引用既不会自己消失，
也不会出现在 UI 里——用户没有任何取消勾选的入口，只能看到模型回答「这个工具不存在」。

本模块给出与运行时一致（``ToolRegistry._load_db_tool_with_source``）的可用性判定，
并把失效原因分类成可读文案，供编辑器展示与解绑。判定口径刻意与运行时对齐：

- 静态工具 / AgentScope 内置别名：永远可用（它们不依赖 MCP 配置）；
- ``sys_api_tools`` 中仍启用的业务 API 工具：可用；
- MCP 工具：缓存行 ``is_published`` 且 ``is_available``，服务 ``enabled_status == 1``，
  且 scope 对当前用户可见（公共或本人的个人服务）；
- 脚本工具（``sys_script_tools``）：行存在且 ``is_active``，或库里缺行时按内置定义兜底
  ——**漏了这一条会把有效配置判成失效，用户点「移除失效引用」就把工具删掉了**。

运行时不区分管理员：管理员也只能用公共 MCP 与自己的个人 MCP，这里保持一致。
"""
import json
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp import McpServer, McpToolCache
from app.models.script_tool import SysScriptTool
from app.models.tool import SysApiTool
from app.services.ai.tools.registry import ToolRegistry

# 失效原因 → 用户可读文案。新增原因必须同时补文案，否则前端会显示成 unknown。
REASON_LABELS: Dict[str, str] = {
    "server_deleted": "MCP 服务已删除",
    "server_forbidden": "无权访问该 MCP 服务",
    "server_disabled": "MCP 服务已禁用",
    "tool_missing": "工具已从服务中移除",
    "tool_removed_remote": "工具已被远端删除",
    "tool_unpublished": "工具已下线",
    "unknown": "工具已不存在",
}


def tool_reference_name(item: Any) -> Optional[str]:
    """版本配置里的工具项可能是纯名称，也可能是带运行配置的对象。"""
    if isinstance(item, str):
        return item.strip() or None
    if isinstance(item, dict):
        name = item.get("name")
        if name is None:
            return None
        text = str(name).strip()
        return text or None
    return None


def configured_tool_names(value: Any) -> Set[str]:
    """把 ``tools`` 字段（list / JSON 字符串 / 混合项）收敛成名称集合。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {value} if value.strip() else set()
    if not isinstance(value, list):
        return set()

    names: Set[str] = set()
    for item in value:
        name = tool_reference_name(item)
        if name:
            names.add(name)
    return names


def split_mcp_tool_name(name: str) -> Optional[Tuple[str, str]]:
    """拆分 ``server_name:tool_name``；不是 MCP 形态时返回 ``None``。

    服务名本身不允许包含冒号（见 MCP 创建接口的 ``validate_server_name``），所以按
    第一个冒号拆分与运行时的解析方式一致。
    """
    text = str(name or "").strip()
    if ":" not in text:
        return None
    server_name, _, tool_name = text.partition(":")
    server_name = server_name.strip()
    tool_name = tool_name.strip()
    if not server_name or not tool_name:
        return None
    return server_name, tool_name


def classify_stale_mcp_ref(
    *,
    server_exists: bool,
    server_visible: bool,
    server_enabled: bool,
    cache_exists: bool,
    cache_published: bool,
    cache_available: bool,
) -> str:
    """按「最接近可用」的视角归类失效原因。

    顺序即优先级：服务都没了就不必再追究工具状态；服务不可见时也不该泄露它的
    启用/发布状态（个人服务属于别人，只能告诉调用者「无权访问」）。
    """
    if not server_exists:
        return "server_deleted"
    if not server_visible:
        return "server_forbidden"
    if not server_enabled:
        return "server_disabled"
    if not cache_exists:
        return "tool_missing"
    if not cache_available:
        return "tool_removed_remote"
    if not cache_published:
        return "tool_unpublished"
    return "unknown"


def _mcp_visible_scope_condition(user_id: Optional[int]):
    """与运行时 ``_mcp_visible_scope_condition`` 同口径（含启用条件）。"""
    global_condition = and_(
        McpServer.enabled_status == 1,
        or_(McpServer.scope == "global", McpServer.scope.is_(None)),
    )
    if user_id is None:
        return global_condition
    return or_(
        global_condition,
        and_(
            McpServer.enabled_status == 1,
            McpServer.scope == "personal",
            McpServer.user_id == user_id,
        ),
    )


def _server_visible_to(server: McpServer, user_id: Optional[int]) -> bool:
    scope = (server.scope or "global").strip().lower()
    if scope == "global":
        return True
    return user_id is not None and server.user_id == user_id


def _is_static_tool_name(name: str) -> bool:
    return name in ToolRegistry.known_static_tool_names()


async def _visible_mcp_tool_names(
    db: AsyncSession, names: Set[str], user_id: Optional[int]
) -> Set[str]:
    if not names:
        return set()
    stmt = (
        select(McpToolCache.tool_name)
        .join(McpServer, McpServer.id == McpToolCache.server_id)
        .where(
            McpToolCache.tool_name.in_(names),
            McpToolCache.is_published.is_(True),
            McpToolCache.is_available.is_(True),
            _mcp_visible_scope_condition(user_id),
        )
    )
    rows = (await db.execute(stmt)).all()
    return {row[0] for row in rows if row[0]}


async def _active_api_tool_names(db: AsyncSession, names: Set[str]) -> Set[str]:
    if not names:
        return set()
    stmt = select(SysApiTool.name).where(
        SysApiTool.name.in_(names),
        SysApiTool.is_active.is_(True),
    )
    rows = (await db.execute(stmt)).all()
    return {row[0] for row in rows if row[0]}


async def _active_script_tool_names(db: AsyncSession, names: Set[str]) -> Set[str]:
    """可用的脚本工具名。

    两条来源：``sys_script_tools`` 里启用的行，以及「库里没有行」时的内置定义兜底
    （迁移漏跑时内置脚本仍然可用，与 ``merge_effective_script_tools`` 同口径）。
    表不存在不能抛：这个函数在权限/限制页面上也被调用，抛了会连累无关功能。
    """
    if not names:
        return set()

    from app.services.ai.tools.script_tool_builtins import BUILTIN_SCRIPT_TOOLS

    resolved = {
        name for name in names if any(item.name == name for item in BUILTIN_SCRIPT_TOOLS)
    }
    try:
        rows = (
            await db.execute(
                select(SysScriptTool.name, SysScriptTool.is_active).where(
                    SysScriptTool.name.in_(names)
                )
            )
        ).all()
    except Exception:  # noqa: BLE001 - 表不存在（迁移未执行）时只保留内置兜底
        # PostgreSQL 下查询失败会让事务进入 aborted 状态，不回滚的话同一请求里
        # 后续的 attach_system_status 等查询会全部报 InFailedSqlTransaction。
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001 - 回滚本身失败时保持原有兜底，不掩盖主异常
            pass
        return resolved

    for name, is_active in rows:
        if not name:
            continue
        if is_active:
            resolved.add(name)
        else:
            # 显式停用优先于内置兜底：用户关掉了它，引用确实不可用
            resolved.discard(name)
    return resolved


async def resolve_unresolved_tool_names(
    db: AsyncSession, names: Set[str], *, user_id: Optional[int]
) -> Set[str]:
    """返回运行时无法解析出实现的工具名（静态/API/MCP/脚本四条路径都落空）。"""
    if not names:
        return set()

    unresolved = {name for name in names if not _is_static_tool_name(name)}
    if not unresolved:
        return set()

    unresolved -= await _active_api_tool_names(db, unresolved)
    if not unresolved:
        return set()

    unresolved -= await _visible_mcp_tool_names(db, unresolved, user_id)
    if not unresolved:
        return set()

    unresolved -= await _active_script_tool_names(db, unresolved)
    return unresolved


def _resolve_user_id(user: Dict[str, Any]) -> Optional[int]:
    raw_user_id = user.get("user_id") if user.get("user_id") is not None else user.get("id")
    try:
        return int(raw_user_id) if raw_user_id is not None else None
    except (TypeError, ValueError):
        return None


def _classify_stale_items(
    names: Set[str],
    *,
    mcp_pairs: Dict[str, Tuple[str, str]],
    servers_by_name: Dict[str, List[McpServer]],
    caches_by_tool: Dict[str, List[McpToolCache]],
    user_id: Optional[int],
) -> List[Dict[str, Any]]:
    """把一批「解析不到实现」的名字归类成可展示的失效项。"""
    items: List[Dict[str, Any]] = []
    for name in sorted(names):
        pair = mcp_pairs.get(name)
        if not pair:
            items.append(
                {
                    "name": name,
                    "display_name": name,
                    "server_name": "",
                    "reason": "unknown",
                    "reason_label": REASON_LABELS["unknown"],
                }
            )
            continue

        server_name, tool_name = pair
        servers = servers_by_name.get(server_name, [])
        visible_servers = [server for server in servers if _server_visible_to(server, user_id)]
        # 同名服务可能因历史数据重复：只要有一个可见就按它解释原因。
        active_server = visible_servers[0] if visible_servers else None
        cache = None
        if active_server is not None:
            cache = next(
                (
                    item
                    for item in caches_by_tool.get(name, [])
                    if item.server_id == active_server.id
                ),
                None,
            )
        reason = classify_stale_mcp_ref(
            server_exists=bool(servers),
            server_visible=active_server is not None,
            server_enabled=bool(active_server and int(active_server.enabled_status or 0) == 1),
            cache_exists=cache is not None,
            cache_published=bool(cache and cache.is_published),
            cache_available=bool(cache and cache.is_available),
        )
        items.append(
            {
                "name": name,
                "display_name": tool_name,
                "server_name": server_name,
                "reason": reason,
                "reason_label": REASON_LABELS[reason],
            }
        )
    return items


async def find_stale_tool_refs_bulk(
    db: AsyncSession,
    tools_configs: Iterable[Any],
    *,
    user: Dict[str, Any],
) -> List[List[Dict[str, Any]]]:
    """批量判定多个版本的 ``tools`` 配置，返回与输入等长的明细列表。

    智能体列表页要逐个智能体判定；若每个都重新查 MCP 服务与工具缓存，
    智能体一多就是 2N 次往返。这里把所有名字取并集后查一遍，再按各自配置求交集。
    """
    configs = list(tools_configs)
    per_config_names = [configured_tool_names(config) for config in configs]
    all_names: Set[str] = set().union(*per_config_names) if per_config_names else set()
    if not all_names:
        return [[] for _ in configs]

    user_id = _resolve_user_id(user)
    unresolved = await resolve_unresolved_tool_names(db, all_names, user_id=user_id)
    if not unresolved:
        return [[] for _ in configs]

    mcp_pairs: Dict[str, Tuple[str, str]] = {}
    for name in sorted(unresolved):
        pair = split_mcp_tool_name(name)
        if pair:
            mcp_pairs[name] = pair

    server_names = {pair[0] for pair in mcp_pairs.values()}
    servers_by_name: Dict[str, List[McpServer]] = {}
    caches_by_tool: Dict[str, List[McpToolCache]] = {}

    if server_names:
        server_rows = (
            await db.execute(select(McpServer).where(McpServer.server_name.in_(server_names)))
        ).scalars().all()
        for server in server_rows:
            servers_by_name.setdefault(server.server_name, []).append(server)

        cache_rows = (
            await db.execute(
                select(McpToolCache)
                .join(McpServer, McpServer.id == McpToolCache.server_id)
                .where(
                    McpToolCache.tool_name.in_(set(mcp_pairs)),
                    McpServer.server_name.in_(server_names),
                )
            )
        ).scalars().all()
        for cache in cache_rows:
            caches_by_tool.setdefault(cache.tool_name, []).append(cache)

    return [
        _classify_stale_items(
            names & unresolved,
            mcp_pairs=mcp_pairs,
            servers_by_name=servers_by_name,
            caches_by_tool=caches_by_tool,
            user_id=user_id,
        )
        for names in per_config_names
    ]


async def find_stale_tool_refs(
    db: AsyncSession,
    tools_config: Any,
    *,
    user: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """列出 ``tools_config`` 里运行时已失效的引用，附带原因文案。

    只对 MCP 形态（``server:tool``）做服务级原因判定；其它无法解析的名字（例如被删除的
    业务 API 工具）归为 ``unknown``，同样允许移除——否则用户依然没有解绑入口。
    """
    results = await find_stale_tool_refs_bulk(db, [tools_config], user=user)
    return results[0] if results else []
