"""数据集级 / 表级 / 行级三层授权解析。

背景
----
历史实现里「数据集权限」就是「数据集内所有表的权限」：`ai_agent_resource_permissions`
只有一行 `resource_type='metadata'` + `resource_id=<MetaDataset.id>`，判定时按数据集整体放行。
本模块把授权扩展到三层，同时**保持历史行为完全兼容**：

- 数据集级：沿用原有行（有无该数据集授权 = 能不能进）；
- 表级：授权行新增 `scope` JSON（`{"mode":"all"|"list","tables":[MetaTable.id...]}`），
  NULL 视为 `all`，即历史数据「数据集内全部表可见」；
- 行级：授权行新增 `row_policy`（`inherit` / `none` / `custom`）+ `row_condition`，
  `inherit` 表示继续走 `meta_datasets.row_filter_config` 的用户/角色/默认三层策略。

主体
----
`subject_type` 取值 `user` / `role` / `all`，为 NULL 时按 `user_id` / `role_id` 推断：

- `all`（全员）是**公共基线**：任何进了该数据集的人至少能看基线范围内的表，
  个人/角色条目只能在其之上追加，**不能收窄**掉基线；
- 同一个人同时命中用户级与角色级条目时，**用户级覆盖角色级**（支持按人收窄）。

部署顺序
--------
本仓库的既有约定是「代码可以先于迁移上线」：`ResourcePermission` 的 ORM 映射里**不声明**
上述新列（否则旧库上 SELECT 会带出 Unknown column，权限系统整体 503）。因此这里用
真实引用一次这些列来探测（失败即回滚 SAVEPOINT），探测不到就整体降级为「无表级/行级扩展」，
行为与历史版本一致。

对外入口
--------
- :func:`resolve_user_dataset_scopes` 批量解析（schema 过滤、可访问物理表集合用）；
- :func:`resolve_dataset_scope` 单数据集解析；
- :func:`resolve_effective_row_policy` 行级策略（SQL 重写前调用）；
- :func:`enforce_table_scope` 表级门禁（执行 SQL 前调用）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import and_, bindparam, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metadata import MetaDataset, MetaTable
from app.models.permission import ResourcePermission, UserRoleRelation

logger = logging.getLogger(__name__)

MODE_ALL = "all"
MODE_LIST = "list"

ROW_POLICY_INHERIT = "inherit"
ROW_POLICY_NONE = "none"
ROW_POLICY_CUSTOM = "custom"

SUBJECT_USER = "user"
SUBJECT_ROLE = "role"
SUBJECT_ALL = "all"

_EXTENDED_COLUMNS = ("subject_type", "scope", "row_policy", "row_condition", "expire_at")
_column_cache: Optional[frozenset[str]] = None
_agent_column_cache: Optional[bool] = None


@dataclass(frozen=True)
class DatasetScope:
    """某个用户在一个数据集上的**生效**授权（已合并用户级/角色级/全员）。"""

    dataset_id: int
    mode: str
    table_ids: frozenset[int]
    row_policy: str = ROW_POLICY_INHERIT
    row_condition: Optional[str] = None
    sources: tuple[str, ...] = ()

    @property
    def allows_all_tables(self) -> bool:
        return self.mode == MODE_ALL

    def allows_table(self, table_id: int) -> bool:
        return self.mode == MODE_ALL or int(table_id) in self.table_ids


# ---------------------------------------------------------------------------
# 列探测与低层读取
# ---------------------------------------------------------------------------


async def _table_columns(session: AsyncSession) -> frozenset[str]:
    """探测扩展列是否已由迁移加入（进程内缓存一次）。

    判据是「能不能真的引用这些列」，**不查** ``information_schema``：后者在 MySQL 里覆盖
    整台实例上该用户可见的所有 schema，同实例别的库迁移过就会被误判成本库已迁移。

    探测包在 SAVEPOINT 里：失败只回滚这一小段，不会让 PostgreSQL 把外层事务标记为
    aborted，从而影响后续正常的授权查询/写入。
    """
    global _column_cache
    if _column_cache is not None:
        return _column_cache
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "SELECT subject_type, scope, row_policy, row_condition, expire_at "
                    "FROM ai_agent_resource_permissions LIMIT 1"
                )
            )
        _column_cache = frozenset(_EXTENDED_COLUMNS)
    except Exception as exc:  # noqa: BLE001 - 探测失败按「未迁移」降级为历史行为
        logger.info("[DatasetScope] 权限扩展列不可用，按历史行为降级: %s", exc)
        _column_cache = frozenset()
    return _column_cache


async def _supports_agent_scope_column(session: AsyncSession) -> bool:
    """``ai_agent_resource_permissions.agent_id`` 是否已由迁移加入（独立探测 + 缓存）。"""
    global _agent_column_cache
    if _agent_column_cache is not None:
        return _agent_column_cache
    try:
        async with session.begin_nested():
            await session.execute(
                text("SELECT agent_id FROM ai_agent_resource_permissions LIMIT 1")
            )
        _agent_column_cache = True
    except Exception:  # noqa: BLE001
        _agent_column_cache = False
    return _agent_column_cache


async def all_members_grants(
    session: AsyncSession, *, agent_id: Optional[str] = None
) -> list[tuple[str, str]]:
    """``subject_type='all'`` 的全员基线授权（resource_type, resource_id）。

    只认 ``resource_type='metadata'``：同一张表里还有 `menu:*` / `element:*` 权限行，
    它们同样可能是 ``user_id = role_id = NULL``，但语义是"默认可见的菜单/元素"，
    绝不能当成"每个用户都有的权限"合并进鉴权结果。

    - 未迁移时返回空列表（行为与历史一致）；
    - ``agent_id=None`` 只取全局行（``agent_id IS NULL``），与 ``PermissionService`` 的
      作用域口径保持一致。
    """
    if "subject_type" not in await _table_columns(session):
        return []

    flag = "enabled IS TRUE" if _dialect_name(session) == "postgresql" else "enabled = 1"
    clauses = ["subject_type = 'all'", "resource_type = 'metadata'", flag]
    params: dict[str, Any] = {}
    if await _supports_agent_scope_column(session):
        scope = str(agent_id).strip() if agent_id is not None else ""
        if scope:
            clauses.append("(agent_id IS NULL OR agent_id = :agent_scope)")
            params["agent_scope"] = scope
        else:
            clauses.append("agent_id IS NULL")

    sql = text(
        "SELECT resource_type, resource_id FROM ai_agent_resource_permissions WHERE "
        + " AND ".join(clauses)
    )
    try:
        rows = (await session.execute(sql, params)).all()
    except Exception as exc:  # noqa: BLE001 - 读不到就按无全员授权处理
        logger.warning("[DatasetScope] 读取全员授权失败: %s", exc)
        return []
    return [
        (str(resource_type), str(resource_id))
        for resource_type, resource_id in rows
        if resource_type is not None and resource_id is not None
    ]


async def supports_table_scope(session: AsyncSession) -> bool:
    """库结构是否已具备表级授权。供接口层做能力提示。"""
    return "scope" in await _table_columns(session)


def reset_scope_column_cache() -> None:
    """测试用：清空列探测缓存。"""
    global _column_cache, _agent_column_cache
    _column_cache = None
    _agent_column_cache = None


async def _load_extended_rows(
    session: AsyncSession, permission_ids: Sequence[int]
) -> dict[int, dict[str, Any]]:
    if not permission_ids:
        return {}
    columns = await _table_columns(session)
    if "scope" not in columns:
        return {}

    selectable = ["id"] + [name for name in _EXTENDED_COLUMNS if name in columns]
    sql = text(
        f"SELECT {', '.join(selectable)} "
        "FROM ai_agent_resource_permissions WHERE id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    try:
        rows = (await session.execute(sql, {"ids": [int(i) for i in permission_ids]})).mappings().all()
    except Exception as exc:  # noqa: BLE001 - 读不到扩展列时按历史行为处理
        logger.warning("[DatasetScope] 读取权限扩展属性失败，按历史行为处理: %s", exc)
        return {}
    return {int(row["id"]): dict(row) for row in rows}


# ---------------------------------------------------------------------------
# 解析辅助
# ---------------------------------------------------------------------------


def _parse_scope(raw: Any) -> tuple[str, frozenset[int]]:
    """解析 `scope` JSON；任何异常形态都回退到 ``all``（历史行为）。"""
    data: Any = raw
    if data is None:
        return MODE_ALL, frozenset()
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", "ignore")
    if isinstance(data, str):
        text_value = data.strip()
        if not text_value:
            return MODE_ALL, frozenset()
        try:
            data = json.loads(text_value)
        except (TypeError, ValueError):
            return MODE_ALL, frozenset()
    if not isinstance(data, dict):
        return MODE_ALL, frozenset()

    mode = str(data.get("mode") or MODE_ALL).strip().lower()
    if mode not in (MODE_ALL, MODE_LIST):
        mode = MODE_ALL

    table_ids: set[int] = set()
    for item in data.get("tables") or []:
        try:
            table_ids.add(int(item))
        except (TypeError, ValueError):
            continue
    if mode == MODE_ALL:
        # all 模式下列白名单没有意义，统一清空，避免脏数据影响后续并集判断。
        return MODE_ALL, frozenset()
    # list 模式且白名单为空 = 一张表都不允许（fail-closed），不回落成 all。
    return MODE_LIST, frozenset(table_ids)


def _parse_row_policy(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    return value if value in (ROW_POLICY_INHERIT, ROW_POLICY_NONE, ROW_POLICY_CUSTOM) else ROW_POLICY_INHERIT


def _subject_of(user_id: Any, role_id: Any, subject_type: Any) -> Optional[str]:
    """授权主体类型；无法判定时返回 ``None``（该行不参与判定）。

    **不能**把「``user_id`` / ``role_id`` 都为空」默认当成「全员」：
    `ai_agent_resource_permissions` 里还有 `menu:*` / `element:*` 这类权限行，
    历史上就有 `user_id = role_id = NULL` 的记录。若把它们当全员基线合并进
    `get_user_permissions`，等于给每个用户发 `element:metadata:edit`。
    全员必须由 ``subject_type = 'all'`` 显式声明。
    """
    declared = str(subject_type or "").strip().lower()
    if declared in (SUBJECT_USER, SUBJECT_ROLE, SUBJECT_ALL):
        return declared
    if user_id is not None:
        return SUBJECT_USER
    if role_id is not None:
        return SUBJECT_ROLE
    return None


def _is_expired(value: Any, now: Optional[datetime] = None) -> bool:
    if value is None or value == "":
        return False
    moment = value
    if isinstance(moment, str):
        raw = moment.strip().replace("Z", "").split("+")[0].replace("T", " ")
        try:
            moment = datetime.fromisoformat(raw)
        except ValueError:
            return False
    if not isinstance(moment, datetime):
        return False
    if moment.tzinfo is not None:
        moment = moment.replace(tzinfo=None)
    return moment < (now or datetime.now())


def _merge_scope(items: Iterable[dict[str, Any]]) -> tuple[str, frozenset[int]]:
    items = list(items)
    if any(item["mode"] == MODE_ALL for item in items):
        return MODE_ALL, frozenset()
    table_ids: set[int] = set()
    for item in items:
        table_ids.update(item["table_ids"])
    return MODE_LIST, frozenset(table_ids)


# ---------------------------------------------------------------------------
# 解析入口
# ---------------------------------------------------------------------------


def build_dataset_scope(dataset_id: int, items: Iterable[dict[str, Any]]) -> DatasetScope:
    """把同一数据集上的多条授权合并成一份生效授权（纯函数，便于单测）。

    规则（与原型一致）：
    - 用户级存在时**覆盖**角色级（支持按人收窄）；多个角色之间取并集；
    - 全员（``all``）是公共基线：永远与上面的结果**并集**，不能被任何人收窄；
    - 行级策略只取「用户级/角色级」的第一条：用户级存在时取用户级，
      否则取角色级；只有全员基线时回到 ``inherit``（走数据集自身策略）。
    """
    items = list(items)
    own = [item for item in items if item["subject"] == SUBJECT_USER]
    role = [item for item in items if item["subject"] == SUBJECT_ROLE]
    public = [item for item in items if item["subject"] == SUBJECT_ALL]

    base = own or role
    mode, table_ids = _merge_scope([*base, *public])

    if base:
        row_policy = base[0]["row_policy"]
        row_condition = base[0]["row_condition"]
    else:
        row_policy, row_condition = ROW_POLICY_INHERIT, None

    sources = tuple(dict.fromkeys(str(item["subject"]) for item in [*base, *public]))
    return DatasetScope(
        dataset_id=int(dataset_id),
        mode=mode,
        table_ids=table_ids,
        row_policy=row_policy,
        row_condition=row_condition,
        sources=sources,
    )


async def resolve_user_dataset_scopes(
    session: AsyncSession,
    user_id: int,
    *,
    dataset_ids: Optional[Sequence[int]] = None,
) -> dict[int, DatasetScope]:
    """批量解析用户在各数据集上的生效授权。

    ``dataset_ids`` 为 None 时解析该用户拥有的全部数据集授权（用于「可访问物理表集合」
    与 schema 过滤这类需要一次算完的场景）。
    """
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return {}

    role_ids = {
        int(role_id)
        for role_id in (
            await session.execute(
                select(UserRoleRelation.role_id).where(UserRoleRelation.user_id == uid)
            )
        )
        .scalars()
        .all()
        if role_id is not None
    }

    # 主体条件必须下推到 SQL：这张表存的是**所有用户/角色**的授权（历史 2000+ 行且随用户数增长），
    # 全量拉回内存再筛会让每次 ChatBI 查询都白读一遍全表。
    # 「全员」行的 user_id / role_id 都是 NULL，SQL 里先选出来，再由 _subject_of 判定
    # subject_type（扩展列不在 ORM 映射里，只能到 Python 侧看）。
    subject_conditions = [
        ResourcePermission.user_id == uid,
        and_(
            ResourcePermission.user_id.is_(None),
            ResourcePermission.role_id.is_(None),
        ),
    ]
    if role_ids:
        subject_conditions.append(
            ResourcePermission.role_id.in_(sorted(role_ids))
        )

    stmt = select(
        ResourcePermission.id,
        ResourcePermission.user_id,
        ResourcePermission.role_id,
        ResourcePermission.resource_id,
    ).where(
        ResourcePermission.resource_type == "metadata",
        ResourcePermission.enabled == True,  # noqa: E712 - 与既有查询口径保持一致
        or_(*subject_conditions),
    )
    # 智能体级授权（agent_id 非空）只在该智能体内生效，不能算成用户的平台级权限：
    # PermissionService.get_user_permissions 用 _agent_scope_filters 做了同样的区分，
    # 这里漏掉就会让"某智能体里的数据授权"升级成"整个平台都能访问该数据集"。
    if await _supports_agent_scope_column(session):
        stmt = stmt.where(text("agent_id IS NULL"))
    # 稳定排序：同一个人可能同时有用户级 + 全员行，行级策略取 `base[0]`，
    # 没有 ORDER BY 时"取哪一条"取决于数据库返回顺序，等于行为不确定。
    stmt = stmt.order_by(ResourcePermission.id.asc())
    if dataset_ids is not None:
        wanted = sorted({str(int(item)) for item in dataset_ids})
        if not wanted:
            return {}
        stmt = stmt.where(ResourcePermission.resource_id.in_(wanted))

    rows = (await session.execute(stmt)).all()
    if not rows:
        return {}

    extended = await _load_extended_rows(session, [int(row[0]) for row in rows])
    now = datetime.now()

    grouped: dict[int, list[dict[str, Any]]] = {}
    for permission_id, row_user_id, row_role_id, resource_id in rows:
        info = extended.get(int(permission_id), {})
        subject = _subject_of(row_user_id, row_role_id, info.get("subject_type"))
        if subject is None:
            continue

        if subject == SUBJECT_USER and row_user_id is None:
            continue
        if subject == SUBJECT_ROLE and row_role_id is None:
            continue
        if subject == SUBJECT_USER and int(row_user_id) != uid:
            continue
        if subject == SUBJECT_ROLE and int(row_role_id) not in role_ids:
            continue
        if _is_expired(info.get("expire_at"), now):
            continue

        try:
            dataset_id = int(str(resource_id))
        except (TypeError, ValueError):
            continue

        mode, table_ids = _parse_scope(info.get("scope"))
        grouped.setdefault(dataset_id, []).append(
            {
                "subject": subject,
                "mode": mode,
                "table_ids": table_ids,
                "row_policy": _parse_row_policy(info.get("row_policy")),
                "row_condition": str(info.get("row_condition") or "").strip() or None,
            }
        )

    return {
        dataset_id: build_dataset_scope(dataset_id, items)
        for dataset_id, items in grouped.items()
    }


async def resolve_dataset_scope(
    session: AsyncSession, user_id: int, dataset_id: int
) -> Optional[DatasetScope]:
    scopes = await resolve_user_dataset_scopes(
        session, user_id, dataset_ids=[int(dataset_id)]
    )
    return scopes.get(int(dataset_id))


async def resolve_effective_row_policy(
    session: AsyncSession, user_id: int, dataset_id: int
) -> tuple[str, Optional[str]]:
    """返回 (row_policy, row_condition)；无授权行时按 ``inherit``。"""
    scope = await resolve_dataset_scope(session, user_id, dataset_id)
    if scope is None:
        return ROW_POLICY_INHERIT, None
    if scope.row_policy == ROW_POLICY_CUSTOM and not scope.row_condition:
        # custom 但条件为空 = 配置损坏，按 inherit 处理，避免静默放行。
        return ROW_POLICY_INHERIT, None
    return scope.row_policy, scope.row_condition


# ---------------------------------------------------------------------------
# 表级门禁
# ---------------------------------------------------------------------------


async def enforce_table_scope_for_refs(
    session: AsyncSession,
    *,
    refs: dict[str, str],
    user_id: Optional[int],
    is_admin: bool = False,
    user_identity_label: Optional[str] = None,
    restrict_dataset_ids: Optional[set[int]] = None,
) -> Optional[str]:
    """表级门禁：SQL 引用的每一张已注册表都必须落在该用户的表授权范围内。

    与只校验「主数据集」的写法相比，这里按**每张表自己的归属数据集**判定——
    跨数据集（联邦 / 关联补全）查询里，附属数据集被收窄掉的表同样会被拦下，
    否则「主数据集放行 + 附表不在主数据集里 → 跳过校验」就是一条绕过路径。

    未注册的物理表不在这里拦：``enforce_physical_table_permissions`` 会给出
    「未在元数据中注册」的报错。
    """
    if is_admin or user_id is None or not refs:
        return None

    lower_names = sorted({str(name).lower() for name in refs})
    if not lower_names:
        return None

    rows = (
        await session.execute(
            select(
                MetaTable.id,
                MetaTable.physical_name,
                MetaTable.dataset_id,
                MetaDataset.name,
            )
            .join(MetaDataset, MetaDataset.id == MetaTable.dataset_id)
            .where(func.lower(MetaTable.physical_name).in_(lower_names))
        )
    ).all()
    if not rows:
        return None

    owners: dict[str, list[tuple[int, int, str]]] = {}
    for table_id, physical_name, dataset_id, dataset_name in rows:
        if physical_name is None:
            continue
        owners.setdefault(str(physical_name).lower(), []).append(
            (int(table_id), int(dataset_id), str(dataset_name or ""))
        )
    if not owners:
        return None

    scopes = await resolve_user_dataset_scopes(
        session,
        user_id,
        dataset_ids=[int(row[2]) for row in rows if row[2] is not None],
    )
    if not scopes:
        # 走到这里说明「物理表校验已放行、但实时解析不出任何范围」——通常是权限缓存
        # 与实时授权短暂不一致（角色刚被移除）。放行不会越权（前置校验已经通过），
        # 但必须留痕，否则线上出现"表级没生效"时无从定位。
        logger.warning(
            "[TableScope] 用户 %s 在数据集 %s 上解析不到授权范围，跳过表级门禁",
            user_id,
            sorted({int(row[2]) for row in rows if row[2] is not None}),
        )
        return None

    subject = f"{user_identity_label} " if user_identity_label else ""
    for lower_name, display in refs.items():
        candidates = owners.get(str(lower_name).lower()) or []
        if not candidates:
            continue
        # 本轮限定了数据集时，先看这张表有没有注册在限定范围内：
        #   * 注册了 → 只按限定数据集的授权判定。否则「在别的数据集里全开」会直接
        #     抹掉本次收窄（同一张表常同时注册在多个数据集，实测 ds1 只给 1 张表，
        #     却能通过 ds9/ds54 的 all 授权查到 ai_agent_users）；
        #   * 没注册 → 保持原规则，联邦查询里关联外部维表仍然可用。
        if restrict_dataset_ids is not None:
            restricted = [row for row in candidates if row[1] in restrict_dataset_ids]
            if restricted:
                candidates = restricted
            elif not restrict_dataset_ids:
                # 限定了范围，但挂载的数据集一个都解析不出来（多半刚被删除）：
                # 不放行，否则「限定」会静默退化成「不限」。
                return (
                    f"[Permission Denied] {subject}本次会话限定的数据集已不存在，"
                    "无法校验表级权限，请重新选择数据集后再试。"
                )
        # 同名物理表可能注册在多个数据集（不同数据源）：只要存在一条**已授权且允许**的
        # 路径就放行；没有授权的数据集不算路径（不能用「查不到 scope」来放行）。
        allowed_by_any = False
        for table_id, dataset_id, _ in candidates:
            scope = scopes.get(dataset_id)
            if scope is None:
                continue
            if scope.mode == MODE_ALL or scope.allows_table(table_id):
                allowed_by_any = True
                break
        if allowed_by_any:
            continue
        _, _, dataset_name = candidates[0]
        return (
            f"[Permission Denied] {subject}无权访问表 '{display}'："
            f"它不在你在数据集 '{dataset_name}' 的表授权范围内。"
            "请联系数据集管理员在「权限管理」里把该表加入你的授权范围。"
        )
    return None


async def allowed_table_names_by_dataset(
    session: AsyncSession, scopes: dict[int, DatasetScope]
) -> dict[int, Optional[set[str]]]:
    """把 ``list`` 模式的白名单翻译成物理表名（lower）；``None`` 表示不限表。"""
    result: dict[int, Optional[set[str]]] = {}
    list_dataset_ids = [dataset_id for dataset_id, scope in scopes.items() if scope.mode == MODE_LIST]
    for dataset_id, scope in scopes.items():
        if scope.mode == MODE_ALL:
            result[dataset_id] = None
        else:
            result[dataset_id] = set()

    if not list_dataset_ids:
        return result

    rows = (
        await session.execute(
            select(MetaTable.dataset_id, MetaTable.id, MetaTable.physical_name).where(
                MetaTable.dataset_id.in_(list_dataset_ids),
                MetaTable.status == 1,
            )
        )
    ).all()
    for dataset_id, table_id, physical_name in rows:
        scope = scopes.get(int(dataset_id))
        if scope is None or not physical_name:
            continue
        if scope.allows_table(int(table_id)):
            result.setdefault(int(dataset_id), set())
            name_set = result[int(dataset_id)]
            if name_set is not None:
                name_set.add(str(physical_name).lower())
    return result


async def table_names_by_dataset(
    session: AsyncSession, dataset_ids: Sequence[int]
) -> dict[int, set[str]]:
    """数据集 → 启用物理表名（lower），一次查询。"""
    wanted = sorted({int(item) for item in dataset_ids})
    if not wanted:
        return {}
    rows = (
        await session.execute(
            select(MetaTable.dataset_id, MetaTable.physical_name)
            .join(MetaDataset, MetaDataset.id == MetaTable.dataset_id)
            .where(
                MetaTable.dataset_id.in_(wanted),
                MetaTable.status == 1,
                MetaDataset.status == 1,
            )
        )
    ).all()
    result: dict[int, set[str]] = {dataset_id: set() for dataset_id in wanted}
    for dataset_id, physical_name in rows:
        if physical_name:
            result.setdefault(int(dataset_id), set()).add(str(physical_name).lower())
    return result


async def allowed_physical_names_for_user(session: AsyncSession, user_id: int) -> set[str]:
    """用户最终可见的物理表名（lower）集合：数据集权限 ∩ 表级白名单。

    这是 ``enforce_physical_table_permissions`` 的兜底口径——即使拿不到 SQL 与数据集的
    绑定关系，也不会把「数据集内被收窄掉的表」放行。一次查询完成，避免逐数据集 N+1。
    """
    scopes = await resolve_user_dataset_scopes(session, user_id)
    if not scopes:
        return set()

    rows = (
        await session.execute(
            select(MetaTable.dataset_id, MetaTable.id, MetaTable.physical_name)
            .join(MetaDataset, MetaDataset.id == MetaTable.dataset_id)
            .where(
                MetaTable.dataset_id.in_(sorted(scopes)),
                MetaTable.status == 1,
                MetaDataset.status == 1,
            )
        )
    ).all()
    allowed: set[str] = set()
    for dataset_id, table_id, physical_name in rows:
        scope = scopes.get(int(dataset_id))
        if scope is None or not physical_name:
            continue
        if scope.mode == MODE_ALL or scope.allows_table(int(table_id)):
            allowed.add(str(physical_name).lower())
    return allowed


# ---------------------------------------------------------------------------
# 读写与模拟（接口层使用）
# ---------------------------------------------------------------------------


def _dialect_name(session: AsyncSession) -> str:
    try:
        bind = session.get_bind()
        return str(getattr(getattr(bind, "dialect", None), "name", "") or "")
    except Exception:  # noqa: BLE001 - 拿不到方言时按 MySQL 处理
        return "mysql"


async def grant_extended_fields(
    session: AsyncSession, permission_ids: Sequence[int]
) -> dict[int, dict[str, Any]]:
    """读取授权行的扩展字段（供接口回显）。"""
    return await _load_extended_rows(session, permission_ids)


async def update_grant_scope(
    session: AsyncSession,
    permission_id: int,
    *,
    subject_type: Optional[str] = None,
    scope: Optional[dict[str, Any]] = None,
    row_policy: Optional[str] = None,
    row_condition: Optional[str] = None,
    expire_at: Optional[datetime] = None,
) -> bool:
    """写入授权行的扩展字段。

    库结构尚未迁移时返回 ``False``（调用方据此返回「表级未生效」的提示），
    不会因为缺列而让整个授权接口 500。
    """
    columns = await _table_columns(session)
    if "scope" not in columns:
        return False

    assignments: list[str] = []
    params: dict[str, Any] = {"permission_id": int(permission_id)}

    if "subject_type" in columns:
        assignments.append("subject_type = :subject_type")
        params["subject_type"] = subject_type

    if "scope" in columns:
        cast = "CAST(:scope AS JSONB)" if _dialect_name(session) == "postgresql" else "CAST(:scope AS JSON)"
        assignments.append(f"scope = {cast}")
        params["scope"] = (
            json.dumps(scope, ensure_ascii=False) if scope is not None else None
        )

    if "row_policy" in columns:
        assignments.append("row_policy = :row_policy")
        params["row_policy"] = row_policy

    if "row_condition" in columns:
        assignments.append("row_condition = :row_condition")
        params["row_condition"] = (row_condition or "").strip() or None

    if "expire_at" in columns:
        # 库里是 naive DATETIME，判定侧用 datetime.now()（本地）。带时区的入参
        # （如前端/脚本传 UTC 的 ISO 串）必须先落到本地墙上时间，否则会整体偏 8 小时，
        # 表现为「配了明天到期，今天就失效」。
        if expire_at is not None and getattr(expire_at, "tzinfo", None) is not None:
            expire_at = expire_at.astimezone().replace(tzinfo=None)
        assignments.append("expire_at = :expire_at")
        params["expire_at"] = expire_at

    if not assignments:
        return False

    await session.execute(
        text(
            "UPDATE ai_agent_resource_permissions SET "
            + ", ".join(assignments)
            + " WHERE id = :permission_id"
        ),
        params,
    )
    return True


async def dataset_permission_snapshot(
    session: AsyncSession, user_id: int, dataset_id: int
) -> dict[str, Any]:
    """权限模拟：这个人最终能看到这个数据集的哪些表 / 哪些行，以及命中了谁。"""
    from app.models.permission import Role

    uid = int(user_id)
    did = int(dataset_id)

    role_rows = (
        await session.execute(
            select(Role.id, Role.code, Role.name)
            .join(UserRoleRelation, UserRoleRelation.role_id == Role.id)
            .where(UserRoleRelation.user_id == uid)
        )
    ).all()
    role_ids = [int(row[0]) for row in role_rows]
    role_names = {int(row[0]): str(row[2] or row[1] or "") for row in role_rows}

    stmt = select(
        ResourcePermission.id,
        ResourcePermission.user_id,
        ResourcePermission.role_id,
    ).where(
        ResourcePermission.resource_type == "metadata",
        ResourcePermission.resource_id == str(did),
        ResourcePermission.enabled == True,  # noqa: E712
    )
    # 与判定口径一致：智能体级授权不属于平台级数据集权限，不能算进模拟结果。
    if await _supports_agent_scope_column(session):
        stmt = stmt.where(text("agent_id IS NULL"))
    rows = (await session.execute(stmt)).all()
    extended = await _load_extended_rows(session, [int(row[0]) for row in rows if row[0] is not None])
    now = datetime.now()

    matched: list[dict[str, Any]] = []
    for permission_id, row_user_id, row_role_id in rows:
        info = extended.get(int(permission_id), {})
        subject = _subject_of(row_user_id, row_role_id, info.get("subject_type"))
        if subject is None:
            continue
        if subject == SUBJECT_USER and (row_user_id is None or int(row_user_id) != uid):
            continue
        if subject == SUBJECT_ROLE and (row_role_id is None or int(row_role_id) not in role_ids):
            continue
        if _is_expired(info.get("expire_at"), now):
            continue
        mode, table_ids = _parse_scope(info.get("scope"))
        matched.append(
            {
                "permission_id": int(permission_id),
                "subject": subject,
                "subject_name": role_names.get(int(row_role_id)) if subject == SUBJECT_ROLE and row_role_id else None,
                "mode": mode,
                "table_ids": sorted(int(item) for item in table_ids),
                "row_policy": _parse_row_policy(info.get("row_policy")),
                "row_condition": str(info.get("row_condition") or "").strip() or None,
            }
        )

    scope = await resolve_dataset_scope(session, uid, did)
    row_policy, row_condition = await resolve_effective_row_policy(session, uid, did)

    tables = (
        await session.execute(
            select(MetaTable.id, MetaTable.physical_name, MetaTable.term).where(
                MetaTable.dataset_id == did,
                MetaTable.status == 1,
            )
        )
    ).all()

    visible: list[dict[str, Any]] = []
    for table_id, physical_name, term in tables:
        if scope is not None and scope.allows_table(int(table_id)):
            visible.append(
                {"id": int(table_id), "physical_name": physical_name, "term": term}
            )

    return {
        "user_id": uid,
        "dataset_id": did,
        "has_access": scope is not None,
        "scope": None
        if scope is None
        else {
            "mode": scope.mode,
            "tables": sorted(int(item) for item in scope.table_ids),
            "sources": list(scope.sources),
        },
        "row_policy": row_policy,
        "row_condition": row_condition,
        "visible_tables": visible,
        "total_tables": len(tables),
        "grants": matched,
        "roles": [{"id": int(row[0]), "code": row[1], "name": row[2]} for row in role_rows],
    }


def _example_table_names(example: "dict[str, Any]") -> set[str] | None:
    """示例 SQL 涉及的物理表（lower）；无法判定时返回 None（不做过滤）。"""
    meta = example.get("sql_metadata")
    if isinstance(meta, dict):
        tables = meta.get("tables")
        if isinstance(tables, list):
            names = {str(t).strip().lower() for t in tables if str(t).strip()}
            if names:
                return names

    sql = str(example.get("sql") or "").strip()
    if not sql:
        return None
    try:
        from app.services.sql_query_execution_service import (
            extract_physical_table_refs_from_select_sql,
        )

        err, refs = extract_physical_table_refs_from_select_sql(
            sql, str(example.get("data_source") or "mysql")
        )
    except Exception:  # noqa: BLE001 - 解析失败宁可保留，避免把案例全过滤掉
        return None
    if err or not refs:
        return None
    return set(refs.keys())


async def filter_examples_by_table_scope(
    examples: "list[dict[str, Any]]",
    *,
    user_id: Any,
    is_admin: bool = False,
) -> "list[dict[str, Any]]":
    """剔除引用了「当前用户看不到的表」的历史案例。

    经验库是全平台共享的：A 问「华东销售额」时可能命中 B 的历史 SQL，而那段 SQL
    里的表恰好被 A 的表级授权收窄掉了。这样一来，表名/字段名会通过 few-shot 泄露给 A，
    模型还会照着示例生成一条注定被 ``[Permission Denied]`` 拦下的 SQL，白跑一轮。

    所以注入 prompt 之前先按「用户可见物理表」过滤；判定不了（解析失败 / 无白名单记录）
    时保留原样，由执行层兜底拦截。
    """
    if not examples or not user_id or is_admin:
        return examples

    try:
        from app.core.orm import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            allowed = await allowed_physical_names_for_user(session, int(user_id))
    except Exception as exc:  # noqa: BLE001 - 过滤失败不应影响主流程
        logger.warning("[FewShot] 解析可见表白名单失败，跳过表级过滤: %s", exc)
        return examples

    if not allowed:
        # 用户没有任何数据集授权记录：此时不误杀（执行层仍然会拒绝）。
        return examples

    kept: list[dict[str, Any]] = []
    dropped = 0
    for example in examples:
        names = _example_table_names(example)
        if names is None:
            kept.append(example)
            continue
        bare = {name.split(".")[-1] for name in names}
        if bare.issubset(allowed) or names.issubset(allowed):
            kept.append(example)
        else:
            dropped += 1

    if dropped:
        logger.info(
            "[FewShot] 表级权限过滤：丢弃 %d 条引用了用户不可见表的案例", dropped
        )
    return kept
