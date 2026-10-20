"""菜单服务：导航树读取、按权限过滤、菜单与分组的增删改、排序与恢复默认。

设计要点（见 docs/superpowers/specs/2026-10-20-menu-management-design.md）：

1. **表可能还不存在**。正常上线顺序是"先执行迁移再部署代码"，但仓库里更常见的实际顺序
   是反的（`permission_service._supports_agent_scope` 就是为这个场景写的）。因此这里做表探测：
   表不存在时回退到 `menu_seed.BUILTIN_MENU_SEED`，侧栏表现与改造前完全一致，迁移执行后
   自动切换，不需要重启。
2. **导航由服务端过滤**。前端不再持有"全部菜单"，避免"菜单表与权限码"两套判断漂移。
3. **缓存带版本号**。写操作 `INCR sys:menu:version` 并删树缓存；前端带版本号请求，
   版本未变只回 `unchanged`，省流量也避免无谓重渲染。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select, text

from app.core.orm import AsyncSessionLocal
from app.models.menu import AgentMenu
from app.services.menu_seed import (
    ALLOWED_EXTERNAL_SCHEMES,
    ALLOWED_ICON_KEYS,
    ALLOWED_OPEN_MODES,
    BUILTIN_MENU_SEED,
    NODE_ELEMENT,
    NODE_EXTERNAL,
    NODE_GROUP,
    NODE_PAGE,
    OPEN_MODE_NEW_TAB,
)

logger = logging.getLogger(__name__)

MENU_NODES_CACHE_KEY = "sys:menu:nodes:v1"
MENU_VERSION_KEY = "sys:menu:version"
MENU_NODES_CACHE_TTL = 300

# 导航「渲染逻辑」版本，与数据版本（MENU_VERSION_KEY）合成下发版本号。
# 为什么需要它：前端把导航快照存在 localStorage 并带 `?version=` 做条件请求，
# 只认数据版本时，一次**纯代码**修复（例如过滤规则改对）不会让旧快照失效——
# 客户端继续拿到 `unchanged: true`，表现就是"改了代码但侧栏没变"。
# 改动过滤/序列化逻辑时 +1 即可，无需清用户浏览器缓存。
MENU_NAV_REVISION = 2
_MENU_VERSION_STRIDE = 1_000_000

# MySQL 1146 / PostgreSQL 42P01：表或关系不存在。只有这两个码才允许降级到内置兜底，
# 其它错误（权限不足、连接断开、列缺失）必须原样抛出，否则真实故障会被静默吞掉。
_TABLE_MISSING_CODES = ("1146", "42P01")

# 表探测结论的进程内缓存。False 必须带过期时间：迁移刚执行完、进程还没重启时，
# 永久 False 会让侧栏一直停在内置快照上，看起来像"改了菜单没生效"。
_MENU_TABLE_STATE: Optional[bool] = None
_MENU_TABLE_PROBED_AT: float = 0.0
_MENU_TABLE_RETRY_SECONDS = 60.0

MAX_TREE_DEPTH = 3


class MenuValidationError(ValueError):
    """菜单写操作的校验失败。端点层转成 400。"""


class MenuNotFoundError(LookupError):
    """目标菜单不存在。端点层转成 404。"""


def _extract_db_error_code(exc: Exception) -> Optional[str]:
    """从 SQLAlchemy 包装后的异常里取出数据库原生错误码。

    MySQL(pymysql) 的 `args[0]` 是整数码；PostgreSQL(psycopg) 用 `pgcode`。
    """
    orig = getattr(exc, "orig", None) or exc
    pgcode = getattr(orig, "pgcode", None)
    if pgcode:
        return str(pgcode)
    args = getattr(orig, "args", None)
    if args:
        first = args[0]
        if isinstance(first, int):
            return str(first)
        if isinstance(first, str) and first.isdigit():
            return first
    return None


def reset_table_probe_state() -> None:
    """测试用：清掉进程内探测结论，让下一次请求重新探测。"""
    global _MENU_TABLE_STATE, _MENU_TABLE_PROBED_AT
    _MENU_TABLE_STATE = None
    _MENU_TABLE_PROBED_AT = 0.0


def force_table_state(ready: bool) -> None:
    """测试用：直接注入探测结论（避免测试依赖真实数据库）。"""
    global _MENU_TABLE_STATE, _MENU_TABLE_PROBED_AT
    _MENU_TABLE_STATE = ready
    _MENU_TABLE_PROBED_AT = time.monotonic()


def _split_route_names(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def _builtin_nodes_payload() -> List[Dict[str, Any]]:
    """把内置定义渲染成与表一致的节点结构（表未建立时的兜底）。"""
    payload: List[Dict[str, Any]] = []
    for index, node in enumerate(BUILTIN_MENU_SEED):
        payload.append(
            {
                # 内置兜底没有数据库 id；前端只用 code，因此给一个负数占位，
                # 保证排序稳定且不会被误当成真实 id 提交回来。
                "id": -(index + 1),
                "code": node.code,
                "parent_code": node.parent_code,
                "node_type": node.node_type,
                "permission_code": node.permission_code,
                "label": node.label,
                "icon": node.icon,
                "path": node.path,
                "active_route_names": _split_route_names(node.active_route_names),
                "url": None,
                "open_mode": None,
                "sort_order": node.sort_order,
                "enabled": True,
                "desktop_only": node.desktop_only,
                "is_landing": node.is_landing,
                "builtin": True,
                "description": node.description,
            }
        )
    return payload


def _serialize_row(row: AgentMenu, code_by_id: Dict[int, str]) -> Dict[str, Any]:
    return {
        "id": row.id,
        "code": row.code,
        "parent_code": code_by_id.get(row.parent_id) if row.parent_id else None,
        "node_type": row.node_type,
        "permission_code": row.permission_code,
        "label": row.label,
        "icon": row.icon,
        "path": row.path,
        "active_route_names": _split_route_names(row.active_route_names),
        "url": row.url,
        "open_mode": row.open_mode,
        "sort_order": row.sort_order,
        "enabled": bool(row.enabled),
        "desktop_only": bool(row.desktop_only),
        "is_landing": bool(row.is_landing),
        "builtin": bool(row.builtin),
        "description": row.description,
    }


def build_tree(nodes: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 `parent_code` 组装嵌套结构，同级按 `sort_order` 升序。"""
    by_code: Dict[str, Dict[str, Any]] = {}
    for node in nodes:
        item = dict(node)
        item["children"] = []
        by_code[item["code"]] = item

    roots: List[Dict[str, Any]] = []
    for item in by_code.values():
        parent_code = item.get("parent_code")
        parent = by_code.get(parent_code) if parent_code else None
        if parent is None:
            roots.append(item)
        else:
            parent["children"].append(item)

    def sort_recursive(items: List[Dict[str, Any]]) -> None:
        items.sort(key=lambda n: (n.get("sort_order") or 0, n.get("code") or ""))
        for item in items:
            sort_recursive(item["children"])

    sort_recursive(roots)
    return roots


class MenuService:
    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------------ 表探测与缓存

    async def table_ready(self) -> bool:
        """菜单表是否可用。结论在进程内缓存；False 每 60s 重试一次。"""
        global _MENU_TABLE_STATE, _MENU_TABLE_PROBED_AT
        now = time.monotonic()
        if _MENU_TABLE_STATE is True:
            return True
        if _MENU_TABLE_STATE is False and now - _MENU_TABLE_PROBED_AT < _MENU_TABLE_RETRY_SECONDS:
            return False

        # 用独立 session 探测：失败的 SELECT 会让当前事务进入 invalid 状态，
        # 若共用请求 session，后续正常查询会连带报 "transaction is inactive"。
        try:
            async with AsyncSessionLocal() as probe_db:
                await probe_db.execute(text("SELECT 1 FROM ai_agent_menus WHERE 1 = 0"))
            _MENU_TABLE_STATE = True
            _MENU_TABLE_PROBED_AT = now
            return True
        except Exception as exc:  # noqa: BLE001 - 需要按错误码分流
            code = _extract_db_error_code(exc)
            if code in _TABLE_MISSING_CODES:
                if _MENU_TABLE_STATE is not False:
                    logger.warning(
                        "ai_agent_menus 表不存在，菜单降级为内置定义（执行 V176/V77 后自动切换）"
                    )
                _MENU_TABLE_STATE = False
                _MENU_TABLE_PROBED_AT = now
                return False
            raise

    async def _get_redis(self):
        from app.core.redis import get_redis

        return await get_redis()

    async def get_version(self) -> int:
        # 表未建立时走内置兜底：必须返回 0，调用方据此禁用 `unchanged`。
        # 否则客户端会拿着一个非 0 版本号一直停在旧快照上，永远看不到兜底内容。
        if not await self.table_ready():
            return 0
        redis = await self._get_redis()
        if not redis:
            return 0
        try:
            raw = await redis.get(MENU_VERSION_KEY)
            data_version = int(raw or 0)
            return MENU_NAV_REVISION * _MENU_VERSION_STRIDE + data_version
        except Exception:  # noqa: BLE001 - 缓存故障不应让侧栏不可用
            logger.warning("读取菜单版本号失败，按 0 处理", exc_info=True)
            return 0

    async def _read_nodes_cache(self) -> Optional[List[Dict[str, Any]]]:
        redis = await self._get_redis()
        if not redis:
            return None
        try:
            raw = await redis.get(MENU_NODES_CACHE_KEY)
        except Exception:  # noqa: BLE001
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return data if isinstance(data, list) else None

    async def _write_nodes_cache(self, nodes: List[Dict[str, Any]]) -> None:
        redis = await self._get_redis()
        if not redis:
            return
        try:
            await redis.set(MENU_NODES_CACHE_KEY, json.dumps(nodes, ensure_ascii=False), ex=MENU_NODES_CACHE_TTL)
        except Exception:  # noqa: BLE001
            logger.warning("写入菜单缓存失败", exc_info=True)

    async def invalidate(self) -> int:
        """写操作后调用：删树缓存并递增版本号，返回新版本号。"""
        redis = await self._get_redis()
        if not redis:
            return 0
        try:
            await redis.delete(MENU_NODES_CACHE_KEY)
            version = await redis.incr(MENU_VERSION_KEY)
            return int(version)
        except Exception:  # noqa: BLE001
            logger.warning("菜单缓存失效失败", exc_info=True)
            return 0

    # ------------------------------------------------------------------ 读取

    async def get_nodes(self) -> List[Dict[str, Any]]:
        """全量节点（含停用项），供导航过滤与管理页使用。"""
        if not await self.table_ready():
            return _builtin_nodes_payload()

        cached = await self._read_nodes_cache()
        if cached is not None:
            return cached

        rows = (
            await self.db.execute(select(AgentMenu).order_by(AgentMenu.sort_order.asc(), AgentMenu.id.asc()))
        ).scalars().all()
        code_by_id = {row.id: row.code for row in rows}
        nodes = [_serialize_row(row, code_by_id) for row in rows]
        await self._write_nodes_cache(nodes)
        return nodes

    async def get_admin_nodes(self) -> List[Dict[str, Any]]:
        """管理页用：全量节点 + 已解析父级 code（已含停用项）。"""
        return await self.get_nodes()

    async def get_nav(
        self,
        user: Dict[str, Any],
        client_version: Optional[int] = None,
        *,
        menus: Optional[Sequence[str]] = None,
        is_admin: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """当前用户的导航树。

        `menus` / `is_admin` 允许调用方注入（测试与内部复用），缺省时自行读取权限。
        """
        version = await self.get_version()
        # version == 0 表示 Redis 不可用或表未建立：此时不能回 unchanged，
        # 否则客户端会一直停在旧快照上。
        if client_version is not None and version > 0 and client_version == version:
            return {"version": version, "unchanged": True}

        nodes = await self.get_nodes()

        if is_admin is None:
            is_admin = (user or {}).get("role") == "admin"
        if menus is None and not is_admin:
            menus = await self._load_user_menus(user)

        visible = self.filter_visible(nodes, menus or [], bool(is_admin))
        landing = self.resolve_landing(visible)
        return {
            "version": version,
            "unchanged": False,
            "nodes": build_tree(visible),
            "landing_path": landing,
        }

    async def _load_user_menus(self, user: Dict[str, Any]) -> List[str]:
        # 认证依赖（require_api_key → verify_api_key）下发的用户字典只有 `user_id`，
        # 是字符串；`id` 只出现在少数接口自己拼的响应里。这里必须两者都认：
        # 只读 `id` 会让所有非 admin 用户的授权读成空集 —— 侧栏只剩「我的工作台」这类
        # 无权限码的公开菜单，看起来像"菜单丢了"。
        embedded = ((user or {}).get("permissions") or {}).get("menus")
        if isinstance(embedded, list) and embedded:
            return list(embedded)
        raw_id = (user or {}).get("user_id") or (user or {}).get("id")
        if raw_id in (None, ""):
            return []
        try:
            user_id = int(raw_id)
        except (TypeError, ValueError):
            return []
        from app.services.permission_service import PermissionService

        permissions = await PermissionService(self.db).get_user_permissions(user_id)
        return list(permissions.permissions.menus or [])

    @staticmethod
    def filter_visible(
        nodes: Sequence[Dict[str, Any]],
        menus: Sequence[str],
        is_admin: bool,
    ) -> List[Dict[str, Any]]:
        """按权限与启用状态裁剪节点。

        规则与改造前的侧栏一致：
        - admin 全放行；
        - `group` 自身不参与授权判断，只要有可见子项就显示（「系统管理」的 `menu:system`
          码只服务权限树与历史授权，不用于侧栏过滤）；
        - 无 `permission_code` 的菜单项（如「我的工作台」）对所有登录用户可见；
        - 任一级祖先被停用，整棵子树都不下发。
        """
        by_code = {node["code"]: node for node in nodes}
        allowed = set(menus or [])

        def enabled_with_ancestors(node: Dict[str, Any]) -> bool:
            cursor: Optional[Dict[str, Any]] = node
            guard = 0
            while cursor is not None and guard < 16:
                if not cursor.get("enabled", True):
                    return False
                parent_code = cursor.get("parent_code")
                cursor = by_code.get(parent_code) if parent_code else None
                guard += 1
            return True

        visible: Dict[str, bool] = {}
        for node in nodes:
            if node["node_type"] == NODE_ELEMENT:
                continue
            if is_admin:
                visible[node["code"]] = True
            elif node["node_type"] == NODE_GROUP:
                visible[node["code"]] = False
            else:
                code = node.get("permission_code")
                visible[node["code"]] = (not code) or (code in allowed)

        # 子项可见 → 祖先分组可见（分组标题不因自身无码而被藏掉）。
        for node in nodes:
            if node["node_type"] == NODE_GROUP:
                continue
            if not visible.get(node["code"]):
                continue
            parent_code = node.get("parent_code")
            guard = 0
            while parent_code and guard < 16:
                visible[parent_code] = True
                parent = by_code.get(parent_code)
                parent_code = parent.get("parent_code") if parent else None
                guard += 1

        return [
            node
            for node in nodes
            if node["node_type"] != NODE_ELEMENT
            and visible.get(node["code"], False)
            and enabled_with_ancestors(node)
        ]

    @staticmethod
    def resolve_landing(visible_nodes: Sequence[Dict[str, Any]]) -> Optional[str]:
        """默认落地页：`is_landing=1` 的可见页面，退化为第一个可见页面。"""
        pages = [n for n in visible_nodes if n["node_type"] == NODE_PAGE and n.get("path")]
        for node in pages:
            if node.get("is_landing"):
                return node["path"]
        pages.sort(key=lambda n: (n.get("sort_order") or 0, n.get("code") or ""))
        return pages[0]["path"] if pages else None

    async def get_permission_tree(self, include_external: bool = True) -> Dict[str, Any]:
        """角色管理用的权限树。

        - 有 `permission_code` 的 `page` / `external` 可勾选；
        - `group` 只作展示容器（「系统管理」带 `menu:system` 码，但前端按分组渲染）；
        - `element` 是只读叶子；
        - 无权限码的非分组节点（「我的工作台」）不出现在权限树里，与改造前一致。
        """
        nodes = await self.get_nodes()
        kept: List[Dict[str, Any]] = []
        for node in nodes:
            if node["node_type"] == NODE_EXTERNAL and not include_external:
                # 个人中心的「我的权限」对所有登录用户开放，不把内部系统名与地址下发给普通用户。
                continue
            if node["node_type"] == NODE_ELEMENT:
                kept.append(node)
                continue
            if node["node_type"] == NODE_GROUP:
                kept.append(node)
                continue
            if node.get("permission_code"):
                kept.append(node)
        return {"nodes": build_tree(kept)}

    # ------------------------------------------------------------------ 写操作

    def _next_sort_order(self, nodes: Sequence[Dict[str, Any]], parent_code: Optional[str]) -> int:
        siblings = [n.get("sort_order") or 0 for n in nodes if (n.get("parent_code") or None) == (parent_code or None)]
        return (max(siblings) + 10) if siblings else 10

    @staticmethod
    def _normalize_parent(nodes: Sequence[Dict[str, Any]], parent_code: Optional[str], node_type: str) -> Optional[str]:
        parent_code = (parent_code or "").strip() or None
        if node_type == NODE_GROUP:
            # 分组只允许一层：分组嵌套会让侧栏出现无法理解的层级，也没有业务需求。
            if parent_code:
                raise MenuValidationError("分组只能挂在最外层")
            return None
        if not parent_code:
            if node_type in (NODE_PAGE, NODE_ELEMENT):
                raise MenuValidationError("菜单项与功能点必须挂在分组下")
            return None
        parent = next((n for n in nodes if n["code"] == parent_code), None)
        if parent is None:
            raise MenuValidationError(f"父级不存在：{parent_code}")
        if parent["node_type"] != NODE_GROUP:
            raise MenuValidationError("父级必须是分组")
        return parent_code

    @staticmethod
    def _assert_not_cycle(nodes: Sequence[Dict[str, Any]], code: str, parent_code: Optional[str]) -> None:
        cursor = parent_code
        guard = 0
        while cursor and guard < 32:
            if cursor == code:
                raise MenuValidationError("父级不能指向自己或自己的后代")
            node = next((n for n in nodes if n["code"] == cursor), None)
            cursor = node.get("parent_code") if node else None
            guard += 1

    @staticmethod
    def _validate_icon(icon: Optional[str]) -> Optional[str]:
        if not icon:
            return None
        if icon not in ALLOWED_ICON_KEYS:
            raise MenuValidationError(f"不支持的图标：{icon}")
        return icon

    @staticmethod
    def _validate_external_url(url: Optional[str]) -> str:
        url = (url or "").strip()
        if not url:
            raise MenuValidationError("外链菜单必须填写地址")
        lowered = url.lower()
        if not any(lowered.startswith(scheme) for scheme in ALLOWED_EXTERNAL_SCHEMES):
            raise MenuValidationError("外链地址只允许 http:// 或 https://")
        if len(url) > 512:
            raise MenuValidationError("外链地址过长")
        return url

    async def create_node(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not await self.table_ready():
            raise MenuValidationError("菜单表尚未建立，请先执行 V176/V77 迁移")
        node_type = str(payload.get("node_type") or "").strip()
        if node_type not in (NODE_GROUP, NODE_EXTERNAL):
            raise MenuValidationError("只允许新增分组或外链菜单")

        label = str(payload.get("label") or "").strip()
        if not label:
            raise MenuValidationError("请填写菜单名称")
        if len(label) > 64:
            raise MenuValidationError("菜单名称过长")

        nodes = await self.get_nodes()
        parent_code = self._normalize_parent(nodes, payload.get("parent_code"), node_type)
        icon = self._validate_icon(payload.get("icon"))

        row = AgentMenu(
            parent_id=next((n["id"] for n in nodes if n["code"] == parent_code), None) if parent_code else None,
            node_type=node_type,
            code="",
            permission_code=None,
            label=label,
            icon=icon,
            sort_order=self._next_sort_order(nodes, parent_code),
            enabled=bool(payload.get("enabled", True)),
            desktop_only=bool(payload.get("desktop_only", False)),
            is_landing=False,
            builtin=False,
            description=(str(payload.get("description") or "").strip() or None),
        )

        if node_type == NODE_EXTERNAL:
            row.url = self._validate_external_url(payload.get("url"))
            open_mode = str(payload.get("open_mode") or OPEN_MODE_NEW_TAB).strip()
            if open_mode not in ALLOWED_OPEN_MODES:
                raise MenuValidationError("不支持的打开方式")
            row.open_mode = open_mode
            slug = self._normalize_slug(payload.get("slug"))
            row.code = f"ext:{slug}"
            row.permission_code = f"menu:ext:{slug}"
            if any(n["code"] == row.code for n in nodes):
                raise MenuValidationError("该标识已被占用")
            if any(n.get("permission_code") == row.permission_code for n in nodes):
                raise MenuValidationError("该权限码已被占用")
        else:
            slug = self._normalize_slug(payload.get("slug") or label)
            row.code = f"group:{slug}"
            if any(n["code"] == row.code for n in nodes):
                raise MenuValidationError("该标识已被占用")

        self.db.add(row)
        await self.db.flush()
        await self.invalidate()
        return {"id": row.id, "code": row.code}

    @staticmethod
    def _normalize_slug(raw: Any) -> str:
        import re

        text_value = str(raw or "").strip().lower()
        if not text_value:
            return uuid.uuid4().hex[:8]
        slug = re.sub(r"[^a-z0-9_-]+", "-", text_value).strip("-")
        if not slug:
            return uuid.uuid4().hex[:8]
        return slug[:40]

    async def _get_row(self, node_id: int) -> AgentMenu:
        row = (
            await self.db.execute(select(AgentMenu).where(AgentMenu.id == int(node_id)))
        ).scalars().first()
        if row is None:
            raise MenuNotFoundError("菜单不存在")
        return row

    async def update_node(self, node_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not await self.table_ready():
            raise MenuValidationError("菜单表尚未建立，请先执行 V176/V77 迁移")
        row = await self._get_row(node_id)
        nodes = await self.get_nodes()
        current = next((n for n in nodes if n["code"] == row.code), None) or {}

        if "label" in payload:
            label = str(payload.get("label") or "").strip()
            if not label:
                raise MenuValidationError("请填写菜单名称")
            row.label = label[:64]
        if "icon" in payload:
            row.icon = self._validate_icon(payload.get("icon"))
        if "description" in payload:
            row.description = (str(payload.get("description") or "").strip() or None)
        if "enabled" in payload:
            next_enabled = bool(payload.get("enabled"))
            # 停用两个"入口的入口"会让后台彻底进不来（除非手敲 URL）：
            # 「菜单管理」自身，以及它所在的分组。锁死自己不是可接受的误操作。
            if not next_enabled and row.code in ("menu:system:menus", "group:system"):
                raise MenuValidationError("「系统管理」与其下的「菜单管理」不能被停用")
            row.enabled = next_enabled
        if "desktop_only" in payload:
            row.desktop_only = bool(payload.get("desktop_only"))
        if "sort_order" in payload:
            try:
                row.sort_order = int(payload.get("sort_order"))
            except (TypeError, ValueError):
                raise MenuValidationError("排序值必须是整数") from None
        if "is_landing" in payload:
            if row.node_type != NODE_PAGE:
                raise MenuValidationError("只有页面菜单可以设为默认落地页")
            want_landing = bool(payload.get("is_landing"))
            if want_landing:
                await self.db.execute(
                    AgentMenu.__table__.update().where(AgentMenu.code != row.code).values(is_landing=False)
                )
            row.is_landing = want_landing

        if "parent_code" in payload:
            parent_code = self._normalize_parent(nodes, payload.get("parent_code"), row.node_type)
            self._assert_not_cycle(nodes, row.code, parent_code)
            row.parent_id = next((n["id"] for n in nodes if n["code"] == parent_code), None) if parent_code else None

        if row.node_type == NODE_EXTERNAL:
            if "url" in payload:
                row.url = self._validate_external_url(payload.get("url"))
            if "open_mode" in payload:
                open_mode = str(payload.get("open_mode") or "").strip()
                if open_mode not in ALLOWED_OPEN_MODES:
                    raise MenuValidationError("不支持的打开方式")
                row.open_mode = open_mode
            if "slug" in payload:
                new_slug = self._normalize_slug(payload.get("slug"))
                new_permission = f"menu:ext:{new_slug}"
                if new_permission != row.permission_code:
                    if any(n.get("permission_code") == new_permission for n in nodes):
                        raise MenuValidationError("该权限码已被占用")
                    await self._rename_permission_code(row.permission_code, new_permission)
                    row.code = f"ext:{new_slug}"
                    row.permission_code = new_permission

        # 内置页面与功能点的身份字段不可改：path 出错会白屏，code/permission_code
        # 变了会让角色上的历史授权全部失效。
        if row.builtin and "path" in payload:
            requested = str(payload.get("path") or "").strip()
            if requested and requested != (current.get("path") or ""):
                raise MenuValidationError("内置菜单的路由地址不可修改")

        await self.db.flush()
        await self.invalidate()
        return {"id": row.id, "code": row.code}

    async def _rename_permission_code(self, old_code: Optional[str], new_code: str) -> None:
        """外链改 slug 时同步迁移授权行，避免"改了名字所有人看不到"。"""
        if not old_code:
            return
        await self.db.execute(
            text(
                "UPDATE ai_agent_resource_permissions SET resource_id = :new_code "
                "WHERE resource_type = 'menu' AND resource_id = :old_code"
            ),
            {"new_code": new_code, "old_code": old_code},
        )
        # 授权行已随改名前移，但各用户 Redis 缓存里的旧权限码还在（TTL 1 小时），
        # 不清掉就会出现"管理员改了 slug，普通用户侧栏里该菜单消失一小时"。
        try:
            from app.services.permission_service import PermissionService

            await PermissionService.purge_all_permission_cache()
        except Exception as ex:  # 缓存故障不该让改名失败，最多晚 1 小时生效
            logger.warning("Failed to purge permission cache after menu code rename: %s", ex)

    async def delete_node(self, node_id: int) -> None:
        if not await self.table_ready():
            raise MenuValidationError("菜单表尚未建立，请先执行 V176/V77 迁移")
        row = await self._get_row(node_id)
        if row.builtin:
            raise MenuValidationError("内置菜单不可删除，请改为停用")
        if row.node_type == NODE_GROUP:
            # 自建分组（内置分组上面已拦下）常见于误建，一直留着只能改名复用。
            # 但只允许删空的：连带删子树等于一次误点清掉一批菜单与授权。
            children = await self.db.execute(
                select(AgentMenu.id).where(AgentMenu.parent_id == row.id).limit(1)
            )
            if children.first() is not None:
                raise MenuValidationError("该分组下还有菜单，请先移走或删除子项")
        elif row.node_type != NODE_EXTERNAL:
            raise MenuValidationError("只允许删除外链菜单与自建的空分组")
        if row.permission_code:
            await self.db.execute(
                text(
                    "DELETE FROM ai_agent_resource_permissions "
                    "WHERE resource_type = 'menu' AND resource_id = :code"
                ),
                {"code": row.permission_code},
            )
        await self.db.delete(row)
        await self.db.flush()
        await self.invalidate()

    async def reorder(self, items: Sequence[Dict[str, Any]]) -> int:
        if not await self.table_ready():
            raise MenuValidationError("菜单表尚未建立，请先执行 V176/V77 迁移")
        if not items:
            return 0
        nodes = await self.get_nodes()
        by_code = {n["code"]: n for n in nodes}
        updated = 0
        for item in items:
            code = str(item.get("code") or "").strip()
            node = by_code.get(code)
            if node is None:
                raise MenuValidationError(f"菜单不存在：{code}")
            parent_code = item.get("parent_code", node.get("parent_code"))
            parent_code = self._normalize_parent(nodes, parent_code, node["node_type"])
            self._assert_not_cycle(nodes, code, parent_code)
            try:
                sort_order = int(item.get("sort_order"))
            except (TypeError, ValueError):
                raise MenuValidationError("排序值必须是整数") from None
            await self.db.execute(
                AgentMenu.__table__.update()
                .where(AgentMenu.id == node["id"])
                .values(
                    sort_order=sort_order,
                    parent_id=next((n["id"] for n in nodes if n["code"] == parent_code), None) if parent_code else None,
                )
            )
            updated += 1
        await self.db.flush()
        await self.invalidate()
        return updated

    async def reset_builtin(self) -> int:
        """把内置项恢复成 seed 定义（名称/图标/排序/启停/移动端标记/路由），不动外链。"""
        if not await self.table_ready():
            raise MenuValidationError("菜单表尚未建立，请先执行 V176/V77 迁移")
        nodes = await self.get_nodes()
        restart = 0
        for seed_node in BUILTIN_MENU_SEED:
            current = next((n for n in nodes if n["code"] == seed_node.code), None)
            if current is None or not current.get("builtin"):
                continue
            parent_id = None
            if seed_node.parent_code:
                parent = next((n for n in nodes if n["code"] == seed_node.parent_code), None)
                parent_id = parent["id"] if parent else None
            await self.db.execute(
                AgentMenu.__table__.update()
                .where(AgentMenu.id == current["id"])
                .values(
                    parent_id=parent_id,
                    label=seed_node.label,
                    icon=seed_node.icon,
                    path=seed_node.path,
                    active_route_names=seed_node.active_route_names,
                    sort_order=seed_node.sort_order,
                    enabled=True,
                    desktop_only=seed_node.desktop_only,
                    is_landing=seed_node.is_landing,
                )
            )
            restart += 1
        await self.db.flush()
        await self.invalidate()
        return restart
