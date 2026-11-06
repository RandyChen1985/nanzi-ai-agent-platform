"""
只读 SQL 执行核心逻辑：数据集解析、物理表权限、行级权限 SQL 重写、语法校验、调用外部执行 API。
供 `execute_sql_query` 工具与 `/api/v1/chatbi/*` 等 HTTP 接口复用。
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import sqlglot
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlglot import exp
from sqlglot.errors import ParseError

from app.core.context import AgentContext
from app.models.metadata import MetaColumn, MetaTable
from app.services.ai.tools.data_api import call_external_sql_api, validate_sql
from app.services.auth_service import AuthService
from app.services.metadata_service import MetadataService
from app.services.permission_service import PermissionService

logger = logging.getLogger(__name__)

# 方言内置虚拟表：不参与数据集归属与元数据注册校验（如 Oracle 的 dual）
_DIALECT_BUILTIN_TABLE_EXEMPTIONS: dict[str, frozenset[str]] = {
    "oracle": frozenset({"dual"}),
}
# 系统库/系统表不是业务数据：显式拒绝，而不是“跳过校验后放行”。
# 历史实现把它们从 refs 中剔除，导致 mysql.user / information_schema.* 绕过表权限。
_SYSTEM_SCHEMA_DENYLIST: dict[str, frozenset[str]] = {
    "clickhouse": frozenset({"system", "information_schema"}),
    "mysql": frozenset({"information_schema", "mysql", "performance_schema", "sys"}),
    "oracle": frozenset({"sys", "system"}),
    "postgres": frozenset({"pg_catalog", "information_schema", "pg_toast"}),
    "postgresql": frozenset({"pg_catalog", "information_schema", "pg_toast"}),
}


def _is_exempt_builtin_table(table_lower: str, dialect: str, schema_lower: Optional[str] = None) -> bool:
    dialect_lower = str(dialect or "").lower()
    table_exemptions = _DIALECT_BUILTIN_TABLE_EXEMPTIONS.get(dialect_lower, frozenset())
    return table_lower in table_exemptions


def _is_system_schema(schema_lower: str, dialect: str) -> bool:
    if not schema_lower:
        return False
    return schema_lower in _SYSTEM_SCHEMA_DENYLIST.get(str(dialect or "").lower(), frozenset())


def _effective_dry_run(dry_run: Optional[bool]) -> bool:
    if dry_run is not None:
        return bool(dry_run)
    # 在函数内导入，便于测试 patch `app.core.context.get_execution_option` 与工具内联 import 行为一致
    from app.core.context import get_execution_option as _get_execution_option

    return bool(_get_execution_option("dry_run"))


def _append_trace(agent_context: Optional[AgentContext], trace_logs: Optional[List[str]], message: str) -> None:
    if agent_context is not None and hasattr(agent_context, "trace_logs"):
        agent_context.trace_logs.append(message)
    elif trace_logs is not None:
        trace_logs.append(message)


def _mark_row_filter_notice(
    permission_notice: Optional[Dict[str, Any]],
    *,
    dataset_name: Optional[str],
    rule_count: int,
    executed_sql: Optional[str] = None,
) -> None:
    if permission_notice is None:
        return
    payload: Dict[str, Any] = {
        "row_filter_applied": True,
        "dataset_name": dataset_name,
        "rule_count": rule_count,
        "message": "已按你的数据权限自动过滤结果",
    }
    rewritten_sql = str(executed_sql or "").strip()
    if rewritten_sql:
        payload["executed_sql"] = rewritten_sql
    permission_notice.update(payload)


def _has_permission_notice(permission_notice: Optional[Dict[str, Any]]) -> bool:
    """这份提示里有没有值得告诉前端/模型的内容。

    不能只认 ``row_filter_applied``：**只发生了字段级剔除、没有行级过滤**是最常见的情况，
    按行级过滤做唯一判据会让「某某字段没权限」这条提示静默消失，用户以为查询本身少查了东西。
    """
    if not isinstance(permission_notice, dict) or not permission_notice:
        return False
    return permission_notice.get("row_filter_applied") is True or bool(
        permission_notice.get("omitted_columns")
    )


def attach_permission_notice_to_payload(
    payload: Dict[str, Any],
    permission_notice: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if _has_permission_notice(permission_notice):
        payload["permission_notice"] = dict(permission_notice)
    return payload


def attach_permission_notice_to_json_result(
    result_str: str,
    permission_notice: Optional[Dict[str, Any]],
) -> str:
    if not _has_permission_notice(permission_notice):
        return result_str
    raw = result_str.strip()
    if not raw or raw.startswith("["):
        return result_str
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return result_str
    if isinstance(parsed, dict):
        return json.dumps(
            attach_permission_notice_to_payload(parsed, permission_notice),
            ensure_ascii=False,
        )
    return json.dumps(
        attach_permission_notice_to_payload({"rows": parsed}, permission_notice),
        ensure_ascii=False,
    )


def _user_dims_for_rewrite(agent_context: Optional[AgentContext], user_dimensions: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if agent_context is not None and agent_context.user_dimensions:
        return dict(agent_context.user_dimensions)
    return dict(user_dimensions or {})


def to_sqlglot_dialect(dialect: str) -> str:
    """将平台/数据源方言名映射为 sqlglot 可识别的 read 方言。"""
    dialect_lower = str(dialect or "clickhouse").strip().lower()
    if dialect_lower in ("sqlserver", "mssql"):
        return "tsql"
    if dialect_lower in ("postgres", "postgresql", "pg"):
        return "postgres"
    return dialect_lower or "clickhouse"


def dialect_from_data_source(data_source: Optional[str]) -> str:
    dialect = "clickhouse"
    ds_lower = data_source.lower() if data_source else ""
    if "mysql" in ds_lower or "doris" in ds_lower:
        dialect = "mysql"
    elif "oracle" in ds_lower:
        dialect = "oracle"
    elif any(token in ds_lower for token in ("sqlserver", "mssql", "tsql")):
        dialect = "tsql"
    elif any(token in ds_lower for token in ("postgresql", "postgres", "pg")):
        dialect = "postgres"
    return dialect


def build_unbounded_count_sql(sql: str, dialect: str) -> str:
    """为已完成权限改写的查询构造不带行数限制的精确 COUNT SQL。"""
    base_sql = str(sql or "").strip().rstrip(";").strip()
    if not base_sql:
        raise ValueError("SQL 为空")

    expression = sqlglot.parse_one(base_sql, read=to_sqlglot_dialect(dialect))
    expression.set("limit", None)
    expression.set("offset", None)
    expression.set("order", None)
    count_query = exp.select(exp.Count(this=exp.Star())).from_(expression.subquery("_count_query"))
    return count_query.sql(dialect=to_sqlglot_dialect(dialect))


def _physical_fragment_from_table(table: exp.Table) -> str:
    parts = getattr(table, "parts", None) or []
    if parts:
        ident = parts[-1]
        name = getattr(ident, "name", None)
        if name:
            return str(name).strip('"').strip("`").strip("'")
    name = getattr(table, "name", None)
    if name is None:
        return ""
    return str(name).strip('"').strip("`").strip("'")


def _schema_fragment_from_table(table: exp.Table) -> str:
    schema = getattr(table, "db", None)
    if schema:
        return str(schema).strip('"').strip("`").strip("'")
    parts = getattr(table, "parts", None) or []
    if len(parts) >= 2:
        ident = parts[-2]
        name = getattr(ident, "name", None)
        if name:
            return str(name).strip('"').strip("`").strip("'")
    return ""


def _collect_cte_alias_lowers(expression: exp.Expression) -> set[str]:
    aliases: set[str] = set()
    for with_expr in expression.find_all(exp.With):
        for cte in with_expr.expressions:
            if isinstance(cte, exp.CTE) and cte.alias:
                aliases.add(cte.alias.lower())
    return aliases


def extract_physical_table_refs_from_select_sql(sql: str, dialect: str) -> Tuple[Optional[str], Dict[str, str]]:
    """
    解析单条只读 SQL，提取物理表引用（视图与普通表同等对待）。
    支持 SELECT、WITH...SELECT、UNION/EXCEPT/INTERSECT、EXPLAIN/DESCRIBE 包裹的 SELECT。
    对 SHOW 等无表引用的只读语句返回空字典；系统库/系统表直接拒绝。
    返回 (错误信息或 None, {physical_lower -> SQL 中的展示名（保留原始大小写）})
    """
    sql_clean = sql.strip()
    if not sql_clean:
        return "Empty SQL query.", {}

    if "/*!" in sql_clean:
        return "MySQL version comments (/*! ... */) are not allowed.", {}

    try:
        parsed = sqlglot.parse(sql_clean, read=to_sqlglot_dialect(dialect))
    except ParseError as e:
        msg = str(e)
        if e.errors:
            error = e.errors[0]
            if isinstance(error, dict):
                msg = error.get("message") or error.get("description") or msg
        return msg, {}
    except Exception as e:
        return f"SQL parse failed: {str(e)}", {}

    if len(parsed) != 1:
        return "Multi-statement queries are prohibited.", {}

    expression = parsed[0]

    # Block known write/DDL types
    _WRITE_TYPE_NAMES = ("Insert", "Update", "Delete", "Drop", "Create", "AlterTable", "Merge")
    _WRITE_TYPES = tuple(getattr(exp, n) for n in _WRITE_TYPE_NAMES if hasattr(exp, n))
    if isinstance(expression, _WRITE_TYPES):
        return "Write/DDL operations are not allowed.", {}

    set_operation = getattr(exp, "SetOperation", None)
    read_roots: list[type] = [exp.Select, exp.Describe]
    if isinstance(set_operation, type):
        read_roots.append(set_operation)
    # SHOW / 其它只读命令没有可提取的物理表：保持空 refs，由后续逻辑决定是否允许。
    if not isinstance(expression, tuple(read_roots)):
        return None, {}

    # CTE 别名不是物理表；子查询别名不能参与剪枝（否则 `(SELECT * FROM t) t`
    # 会把真实表 t 从 refs 中抹掉）。
    skip_aliases = _collect_cte_alias_lowers(expression)

    refs: Dict[str, str] = {}
    for table in expression.find_all(exp.Table):
        frag = _physical_fragment_from_table(table)
        if not frag:
            continue
        lk = frag.lower()
        schema_lower = _schema_fragment_from_table(table).lower()
        if _is_system_schema(schema_lower, dialect):
            return (
                f"System schema '{schema_lower}' is not allowed for business queries.",
                {},
            )
        if lk in skip_aliases:
            continue
        if _is_exempt_builtin_table(lk, dialect, schema_lower=schema_lower):
            continue
        if lk not in refs:
            refs[lk] = frag

    return None, refs


def _is_table_free_read(sql: str, dialect: str) -> bool:
    """这条只读语句是否**完全不碰任何表**（``SELECT 1`` / ``SELECT now()``）。

    用来给「refs 为空」兜底：``SHOW TABLES``、``SHOW CREATE TABLE x`` 这类命令
    解析不出表引用，却能把整库表结构列出来 —— 四道表级门禁全都因为 ``refs`` 为空而跳过，
    等于一条绕开数据集/表授权的信息泄露通道。这里只放行真正不需要表就能算出的语句。
    """
    try:
        expression = sqlglot.parse_one(str(sql or "").strip(), read=to_sqlglot_dialect(dialect))
    except Exception:  # noqa: BLE001 - 解析不了就按不放行处理
        return False
    if not isinstance(expression, exp.Select):
        return False
    if expression.find(exp.Table) is not None:
        return False
    return True


def _collect_sql_column_usage(
    expression: Any,
) -> Tuple[Dict[str, set], set, Optional[set]]:
    """从已解析的单条 SELECT 里收集列引用。

    返回 ``(qualified, unqualified, star_tables)``：
    - ``qualified``：``物理表名 lower -> {列名 lower}``（按表别名解析回物理表）；
    - ``unqualified``：没有表限定的列名（``WHERE id = 1`` 这种）；
    - ``star_tables``：用了 ``*`` 的表集合；``None`` 表示**无表限定**的 ``SELECT *``
      （等于对所有参与表生效）。``COUNT(*)`` 不算通配。
    """
    alias_to_physical: Dict[str, str] = {}
    for table in expression.find_all(exp.Table):
        frag = _physical_fragment_from_table(table)
        if not frag:
            continue
        physical = frag.lower()
        alias = str(table.alias or "").strip().lower()
        alias_to_physical[alias or physical] = physical

    qualified: Dict[str, set] = {}
    unqualified: set = set()
    star_tables: set = set()
    star_all = False

    for column in expression.find_all(exp.Column):
        name = str(column.name or "").strip()
        if not name:
            continue
        table_hint = str(column.table or "").strip().lower()
        if name == "*":
            physical = alias_to_physical.get(table_hint)
            if physical:
                star_tables.add(physical)
            else:
                star_all = True
            continue
        if table_hint:
            physical = alias_to_physical.get(table_hint, table_hint)
            qualified.setdefault(physical, set()).add(name.lower())
        else:
            unqualified.add(name.lower())

    for star in expression.find_all(exp.Star):
        # COUNT(*) / COUNT(t.*) 里的星号不展开任何列，不能算通配
        if isinstance(star.parent, exp.Count):
            continue
        # 带表限定的 `t.*` 已在 Column 分支处理
        if isinstance(star.parent, exp.Column):
            continue
        star_all = True

    return qualified, unqualified, (None if star_all else star_tables)


def _column_clause(column: Any) -> str:
    """判断一个列引用出现在哪个子句里。

    ``projection`` = 直接挂在 SELECT 上（可以安全剔除）；其余位置一旦剔掉就会
    改变筛选/分组/排序语义，只能拒绝。
    """
    node = column.parent
    while node is not None and not isinstance(node, exp.Select):
        for node_type, name in (
            (exp.Where, "where"),
            (exp.Having, "having"),
            (exp.Group, "group"),
            (exp.Order, "order"),
            (exp.Join, "join"),
            (exp.Window, "window"),
            (exp.Qualify, "qualify"),
        ):
            if isinstance(node, node_type):
                return name
        node = node.parent
    return "projection"


_CLAUSE_LABELS = {
    "where": "筛选条件（WHERE）",
    "having": "聚合后筛选（HAVING）",
    "group": "分组（GROUP BY）",
    "order": "排序（ORDER BY）",
    "join": "关联条件（JOIN ON）",
    "window": "窗口函数分区/排序",
    "qualify": "窗口筛选（QUALIFY）",
}


def _resolve_column_owner(
    table_hint: str,
    column: str,
    *,
    alias_to_physical: Dict[str, str],
    full_columns: Optional[Dict[str, set]],
    single_table: bool,
    ambiguous_aliases: Optional[set] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """把列引用落到具体物理表。

    返回 ``(physical, reason)``：``reason`` 为 ``None`` 表示归属确定；
    否则是 ``"ambiguous"``（多张表都有同名列，SQL 本身也会歧义）或 ``"unknown"``
    （列清单里找不到，无法判断归属）。

    多表查询里未加表前缀的列不能再默认放行（历史实现直接当「无权字段」或「全放」
    两种极端处理）：必须按列清单判定归属，判不出来时由调用方 fail-closed。
    """
    hint = str(table_hint or "").strip().lower()
    if hint:
        if ambiguous_aliases and hint in ambiguous_aliases:
            # CTE / 派生表由多张物理表 UNION/JOIN 而来，单个别名无法归属到具体表的字段规则
            return None, "ambiguous"
        return alias_to_physical.get(hint, hint), None
    if single_table:
        return next(iter(alias_to_physical.values()), None), None

    column_lower = str(column or "").strip().lower()
    if not column_lower:
        return None, "unknown"
    owners = [
        physical
        for physical in dict.fromkeys(alias_to_physical.values())
        if column_lower in (full_columns or {}).get(physical, set())
    ]
    if len(owners) == 1:
        return owners[0], None
    return None, "ambiguous" if len(owners) > 1 else "unknown"


def _column_permitted(
    physical: Optional[str],
    column: str,
    allowed_columns: Dict[str, Optional[set]],
) -> bool:
    """``physical`` 已确定归属时的字段判定；``None`` = 该表不受字段限制。"""
    if physical is None:
        return False
    if physical not in allowed_columns:
        # 不在候选表里的列由表级门禁兜底，这里不额外收窄（保持既有口径）
        return True
    allowed = allowed_columns.get(physical)
    if allowed is None:
        return True
    return str(column or "").strip().lower() in allowed


async def _load_allowed_columns_by_table(
    session: AsyncSession,
    *,
    scopes: Any,
    candidates: Any,
) -> Tuple[Dict[str, Optional[set]], Dict[str, set], set[str]]:
    """算出每张表对该用户「允许的列」。``None`` = 不受限（全部列可见）。

    与表级 allowed_by_any 同口径：同一张物理表注册在多个数据集时，只要有一条路径
    不受字段限制，这张表就算全开，避免用别处的收窄误伤。

    第三个返回值是「配了字段规则、但读不到列清单」的表：这种表无法判定列归属，
    调用方必须 fail-closed 拒绝查询 —— 历史实现按「全开」处理，等于配置了字段级
    限制却因为元数据没同步而静默失效。
    """
    from app.models.metadata import MetaColumn

    # candidates 是 {物理表名: [(table_id, dataset_id, dataset_name), ...]}，
    # 别直接迭代 dict（拿到的是字符串 key，int(key[0]) 会抛 ValueError）。
    table_ids = [int(row[0]) for rows in candidates.values() for row in rows]
    columns_by_table: Dict[int, set] = {}
    if table_ids:
        try:
            column_rows = (
                await session.execute(
                    select(MetaColumn.table_id, MetaColumn.physical_name).where(
                        MetaColumn.table_id.in_(table_ids)
                    )
                )
            ).all()
            for table_id, physical_name in column_rows:
                name = str(physical_name or "").strip().lower()
                if name:
                    columns_by_table.setdefault(int(table_id), set()).add(name)
        except Exception as ex:  # noqa: BLE001
            logger.warning("[ColumnScope] 读取列清单失败: %s", ex)

    allowed: Dict[str, Optional[set]] = {}
    full_columns: Dict[str, set] = {}
    unavailable: set[str] = set()
    for physical, rows in (candidates or {}).items():
        allowed[physical] = None  # 默认全开，下面逐条收窄
        table_columns: set = set()
        for table_id, _dataset_id, _dataset_name in rows:
            table_columns |= columns_by_table.get(int(table_id), set())
        # 全列清单供「多表未限定列」判定归属；没有规则的路径也要有
        full_columns[physical] = table_columns
        for table_id, dataset_id, _dataset_name in rows:
            scope = scopes.get(dataset_id)
            if scope is None:
                # 这个数据集没有授权行，不算一条路径
                continue
            rules = (scope.column_rules or {}).get(int(table_id))
            if not rules:
                allowed[physical] = None
                break
            all_cols = columns_by_table.get(int(table_id), set())
            if not all_cols:
                # 配了字段规则却拿不到列清单：无法判定可见列，必须拒绝（fail-closed）
                logger.warning(
                    "[ColumnScope] 表 %s (id=%s) 配了字段级规则但读不到列清单，拒绝执行",
                    physical,
                    table_id,
                )
                unavailable.add(physical)
                allowed[physical] = set()
                break
            visible = set(all_cols)
            for rule in rules:
                visible = rule.apply(visible)
            current = allowed[physical]
            allowed[physical] = visible if current is None else (current & visible)
    return allowed, full_columns, unavailable


def _is_wildcard_projection(projection: Any) -> bool:
    """这个投影项是不是「整个通配符」：``*`` 或 ``t.*``。

    ``COUNT(*)`` / ``COUNT(t.*)`` **不算** —— 它们的通配符在聚合函数内部，
    展开成列清单会彻底改变语义（计数变成明细）。
    """
    if isinstance(projection, exp.Star):
        return True
    return isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)


def _has_positional_ordering(expression: Any) -> bool:
    """ORDER BY / GROUP BY 里是否有 `ORDER BY 2` 这种按**序号**排序/分组。

    这是改写投影的硬约束：删掉一列后所有位置参数都会指向别的列，
    轻则报错，重则静默换了一列去排序。有它就只能拒绝，不能改写。
    """
    for node_type in (exp.Order, exp.Group):
        for node in expression.find_all(node_type):
            for ordered in node.find_all(exp.Ordered):
                if isinstance(ordered.this, exp.Literal) and ordered.this.is_int:
                    return True
            # GROUP BY 直接挂字面量（不带 Ordered）
            for item in node.expressions:
                if isinstance(item, exp.Literal) and item.is_int:
                    return True
    return False


def _rewrite_select_projections(
    expression: Any,
    *,
    alias_to_physical: Dict[str, str],
    allowed_columns: Dict[str, Optional[set]],
    full_columns: Optional[Dict[str, set]] = None,
    single_table: bool = True,
    ambiguous_aliases: Optional[set] = None,
) -> Tuple[list, list, Optional[str]]:
    """把 SELECT 里引用了无权字段的投影项剔掉。

    返回 ``(保留的投影, 被剔除的列名, 无法改写的理由)``。第三个值非空表示**不能改写**
    （改名会改变语义），调用方必须改为拒绝，绝不能退回原始 SQL 执行。
    """
    kept: list = []
    dropped: list = []

    def _physical_of(table_hint: str) -> Optional[str]:
        if table_hint:
            return alias_to_physical.get(table_hint, table_hint)
        if single_table:
            return next(iter(alias_to_physical.values()), None)
        return None

    def _column_allowed(table_hint: str, column: str) -> bool:
        physical, reason = _resolve_column_owner(
            table_hint,
            column,
            alias_to_physical=alias_to_physical,
            full_columns=full_columns,
            single_table=single_table,
            ambiguous_aliases=ambiguous_aliases,
        )
        # 归属判不出来（多表同名列 / 列清单缺失）时保守按无权处理：
        # 投影剔掉比把受限列原样返回安全。
        if reason is not None:
            return False
        return _column_permitted(physical, column, allowed_columns)

    for projection in expression.selects:
        # 只认**整个投影就是通配符**的两种形态：`*`（投影节点是 Star）与 `t.*`（Column 的 this 是 Star）。
        # 不能用 find(exp.Star)：`COUNT(*)` 的 this 同样是 Star，会被误判成通配符，
        # 于是 `SELECT COUNT(*)` 被改写成 `SELECT col1, col2` —— 统计查询变成全量明细导出。
        if _is_wildcard_projection(projection):
            hint = str(getattr(projection, "table", "") or "").strip().lower()
            physical = _physical_of(hint)
            if physical is None:
                # 多表 JOIN + 不带表名的 `*`：展开成谁在前谁在后都不确定，
                # 改名会改变结果列序甚至重名列，只能让模型显式列字段。
                return [], [], "multi_table_star"
            allowed = allowed_columns.get(physical)
            if allowed is None:
                kept.append(projection)
                continue
            # 展开了就得真的用改写后的 SQL：否则 dropped 为空，调用方会退回原始
            # `SELECT *`，把被隐藏的列原样返回（字段级权限对 * 查询完全失效）。
            dropped.extend(sorted((full_columns or {}).get(physical, set()) - allowed))
            for name in sorted(allowed):
                kept.append(exp.column(name, table=hint or None))
            continue

        referenced = list(projection.find_all(exp.Column))
        blocked = [
            str(col.name)
            for col in referenced
            if not _column_allowed(str(col.table or "").strip().lower(), str(col.name or ""))
        ]
        if blocked:
            dropped.extend(blocked)
            continue
        kept.append(projection)

    return kept, dropped, None


async def apply_column_scope_to_select_sql(
    session: AsyncSession,
    *,
    sql: str,
    dialect: str,
    user_id_eff: Optional[int],
    is_admin_eff: bool = False,
    restrict_dataset_ids: Optional[set[int]] = None,
    user_identity_label: Optional[str] = None,
    scopes: Any = None,
) -> Tuple[str, List[str], Optional[str]]:
    """字段级权限：**能剔的剔掉，不能剔的才拒绝**。

    与表级的关键区别：表看不到 = 这次查询没意义，只能拒绝；字段看不到 = 少一列而已，
    把这一列从结果里拿掉、其余照常返回，再明确告诉用户「某某字段没有权限」更好用。

    但「剔掉」只在 **SELECT 投影** 里安全。同一个列如果出现在

        WHERE / HAVING / GROUP BY / ORDER BY / JOIN ON / 窗口分区

    就不能删 —— 删掉会让条件失效（``WHERE id_card LIKE '110%'`` 一删就从「北京客户」
    变成「全部客户」），保留则是拿无权字段做筛选，等于泄露该字段的取值分布。
    这些位置一律拒绝。

    返回 ``(可能要执行的 sql, 被剔除的列名, 拒绝原因)``：
    - ``拒绝原因`` 非空 → 上层直接返回该错误，不改写、不执行；
    - 否则用返回的 sql 执行；``被剔除的列名`` 用于向用户和模型说明。
    """
    original = str(sql or "")
    if is_admin_eff or user_id_eff is None or not original.strip():
        return original, [], None

    try:
        parsed = sqlglot.parse(original, read=to_sqlglot_dialect(dialect))
    except Exception:  # noqa: BLE001 - 解析失败交给上游语法校验
        return original, [], None
    if len(parsed) != 1:
        return original, [], None
    expression = parsed[0]

    from app.services.dataset_scope_service import (
        column_rules_by_table_name,
        resolve_user_dataset_scopes,
    )

    from app.models.metadata import MetaDataset

    try:
        if callable(scopes):
            scopes = await scopes()
        if scopes is None:
            scopes = await resolve_user_dataset_scopes(session, user_id_eff)
        rules_by_table = await column_rules_by_table_name(session, scopes)
    except Exception as ex:  # noqa: BLE001
        # 字段级是安全边界：解析失败必须 fail-closed，不能退化成「字段全可见」
        logger.error(
            "[ColumnScope] 解析字段级授权失败，按 fail-closed 拒绝: %s", ex, exc_info=True
        )
        return original, [], (
            f"[Permission Denied] {user_identity_label or ''}"
            "无法解析字段级权限（授权服务暂时不可用），为避免越权本次查询已拒绝。请稍后重试。"
        )
    if not rules_by_table:
        return original, [], None

    alias_to_physical: Dict[str, str] = {}
    tables_in_sql: set = set()
    for table in expression.find_all(exp.Table):
        frag = _physical_fragment_from_table(table)
        if not frag:
            continue
        physical = frag.lower()
        tables_in_sql.add(physical)
        alias = str(table.alias or "").strip().lower()
        alias_to_physical[alias or physical] = physical

    # CTE / 派生表别名不是物理表：`WITH t AS (SELECT * FROM orders) SELECT t.secret FROM t`
    # 里的 `t` 必须还原到 `orders` 才能套用字段规则，否则光改个别名就能绕过字段级。
    # 单数据源还原到那张表；多数据源（UNION/JOIN）无法归属，记为歧义 → fail-closed。
    ambiguous_aliases: set[str] = set()
    for node in [*expression.find_all(exp.CTE), *expression.find_all(exp.Subquery)]:
        alias = str(getattr(node, "alias", "") or "").strip().lower()
        body = getattr(node, "this", None)
        if not alias or body is None:
            continue
        sources = {
            frag.lower()
            for table in body.find_all(exp.Table)
            if (frag := _physical_fragment_from_table(table))
        }
        if len(sources) == 1:
            alias_to_physical[alias] = next(iter(sources))
        elif len(sources) > 1:
            ambiguous_aliases.add(alias)

    # 链式 CTE（`b AS (SELECT * FROM a)`，`a` 又是 CTE）要一路还原到物理表，
    # 只还原一层仍然会被 `b.secret` 绕过。
    for alias in list(alias_to_physical):
        value = alias_to_physical[alias]
        seen: set[str] = set()
        while (
            value in alias_to_physical
            and alias_to_physical[value] != value
            and value not in seen
        ):
            seen.add(value)
            value = alias_to_physical[value]
        alias_to_physical[alias] = value

    if not tables_in_sql:
        return original, [], None

    try:
        rows = (
            await session.execute(
                select(
                    MetaTable.id,
                    MetaTable.physical_name,
                    MetaTable.dataset_id,
                    MetaDataset.name,
                )
                .join(MetaDataset, MetaDataset.id == MetaTable.dataset_id)
                .where(func.lower(MetaTable.physical_name).in_(sorted(tables_in_sql)))
            )
        ).all()
    except Exception as ex:  # noqa: BLE001
        logger.error("[ColumnScope] 查询表归属失败，按 fail-closed 拒绝: %s", ex, exc_info=True)
        return original, [], (
            f"[Permission Denied] {user_identity_label or ''}"
            "无法确认 SQL 引用的表归属，字段级权限无法校验，本次查询已拒绝。请稍后重试。"
        )
    if not rows:
        return original, [], None

    candidates: Dict[str, list] = {}
    for table_id, physical_name, dataset_id, dataset_name in rows:
        if not physical_name:
            continue
        candidates.setdefault(str(physical_name).lower(), []).append(
            (int(table_id), int(dataset_id), str(dataset_name or ""))
        )

    # 本轮限定了数据集时只看限定范围内的那张表（与表级门禁同一口径）
    if restrict_dataset_ids is not None:
        for physical in list(candidates):
            restricted = [row for row in candidates[physical] if row[1] in restrict_dataset_ids]
            if restricted:
                candidates[physical] = restricted
            elif not restrict_dataset_ids:
                return original, [], (
                    f"[Permission Denied] {user_identity_label or ''}本次会话限定的数据集已不存在，"
                    "无法校验字段级权限，请重新选择数据集后再试。"
                )
        candidates = {key: value for key, value in candidates.items() if value}

    allowed_columns, full_columns, unavailable_tables = await _load_allowed_columns_by_table(
        session,
        scopes=scopes,
        candidates=candidates,
    )
    subject = f"{user_identity_label} " if user_identity_label else ""
    single_table = len(alias_to_physical) == 1

    def _select_of(node: Any) -> Any:
        current = node
        while current is not None and not isinstance(current, exp.Select):
            current = current.parent
        return current

    def _owner(column: Any) -> Tuple[Optional[str], Optional[str]]:
        return _resolve_column_owner(
            str(column.table or "").strip().lower(),
            str(column.name or ""),
            alias_to_physical=alias_to_physical,
            full_columns=full_columns,
            single_table=single_table,
            ambiguous_aliases=ambiguous_aliases,
        )

    # ORDER BY / GROUP BY / HAVING 里引用的 SELECT 别名不是物理列：
    # `SELECT SUM(amount) AS total ... ORDER BY total` 不能因为表里恰好没有
    # `total` 这一列就被判成越权。别名是否安全取决于它背后的投影项：
    # 投影项引用了被隐藏字段 → 该别名会被剔除，引用它就是悬空，必须拒绝。
    alias_states: Dict[int, Dict[str, str]] = {}
    for select_node in expression.find_all(exp.Select):
        states: Dict[str, str] = {}
        for projection in select_node.selects:
            alias = (
                str(projection.alias or "").strip().lower()
                if isinstance(projection, exp.Alias)
                else ""
            )
            if not alias:
                continue
            blocked = False
            for col in projection.find_all(exp.Column):
                physical, reason = _owner(col)
                if reason is not None or not _column_permitted(
                    physical, str(col.name or ""), allowed_columns
                ):
                    blocked = True
                    break
            states[alias] = "blocked" if blocked else "safe"
        alias_states[id(select_node)] = states

    if unavailable_tables:
        return original, [], (
            f"[Permission Denied] {subject}字段元数据读取失败（表："
            + "、".join(f"'{name}'" for name in sorted(unavailable_tables)[:5])
            + "），无法校验字段级权限，本次查询已拒绝。"
            "请联系管理员重新同步这些表的元数据后再试。"
        )
    # 没有任何收窄 → 直接走原 SQL
    if all(value is None for value in allowed_columns.values()):
        return original, [], None

    # ---- 1. 非投影区的无权列：只能拒绝 ----
    def _format_sites(sites: Dict[str, List[str]]) -> str:
        parts = []
        for site, names in sites.items():
            uniq = sorted({name for name in names if name}, key=str.lower)
            sample = "、".join(f"'{name}'" for name in uniq[:5])
            more = f" 等 {len(uniq)} 个" if len(uniq) > 5 else ""
            parts.append(f"{site} 里用到了 {sample}{more}")
        return "；".join(parts)

    bad_sites: Dict[str, List[str]] = {}
    unresolved_sites: Dict[str, List[str]] = {}
    blocked_alias_sites: Dict[str, List[str]] = {}
    misplaced_alias_sites: Dict[str, List[str]] = {}
    for column in expression.find_all(exp.Column):
        clause = _column_clause(column)
        if clause == "projection":
            continue
        name = str(column.name or "")
        hint = str(column.table or "").strip().lower()
        label = _CLAUSE_LABELS.get(clause, clause)
        if not hint and clause in ("order", "group", "having"):
            state = alias_states.get(id(_select_of(column)), {}).get(name.strip().lower())
            if state == "safe":
                continue
            if state == "blocked":
                blocked_alias_sites.setdefault(label, []).append(name)
                continue
        elif not hint:
            # WHERE / JOIN ON 引用了 SELECT 别名：SQL 本身不合法，也容易让人误以为
            # 「别名豁免」到处都生效。单独提示，别混进「字段无权」。
            state = alias_states.get(id(_select_of(column)), {}).get(name.strip().lower())
            if state is not None:
                misplaced_alias_sites.setdefault(label, []).append(name)
                continue
        physical, reason = _owner(column)
        if reason is not None:
            # 多表查询里没写表前缀、列清单又判不出归属：不能默认放行
            unresolved_sites.setdefault(label, []).append(name)
            continue
        if _column_permitted(physical, name, allowed_columns):
            continue
        bad_sites.setdefault(label, []).append(name)

    if blocked_alias_sites:
        return original, [], (
            f"[Permission Denied] {subject}排序/分组/聚合后筛选引用了「将被隐藏字段」的别名："
            + _format_sites(blocked_alias_sites)
            + "。这些别名依赖你无权访问的字段，会被一并剔除，请改用有权限的字段重新提问。"
        )
    if misplaced_alias_sites:
        return original, [], (
            f"[Permission Denied] {subject}筛选/关联条件里引用了 SELECT 别名："
            + _format_sites(misplaced_alias_sites)
            + "。WHERE / JOIN ON 不能引用 SELECT 里的别名，请直接写聚合表达式，"
            "或把条件放到 HAVING / ORDER BY / GROUP BY 里。"
        )
    if unresolved_sites:
        return original, [], (
            f"[Permission Denied] {subject}有字段无法判定属于哪张表，字段级权限无法校验："
            + _format_sites(unresolved_sites)
            + "。多表查询请给字段加表名/别名（例如 t.字段名）；"
            "WHERE / JOIN ON 不能引用 SELECT 里的别名。"
        )
    if bad_sites:
        return original, [], (
            f"[Permission Denied] {subject}这些字段不在你的授权范围内，"
            "且它们出现在筛选/分组/排序条件里，删掉会改变结果口径，因此本次查询已终止："
            + _format_sites(bad_sites)
            + "。请换用你有权限的字段重新提问。"
        )

    # ---- 2. 投影区：能剔就剔 ----
    selects = list(expression.find_all(exp.Select))
    if len(selects) > 1:
        # 子查询 / UNION：改投影会连带影响外层结构，退化成拒绝（安全优先）
        denied = []
        for column in expression.find_all(exp.Column):
            name = str(column.name or "")
            hint = str(column.table or "").strip().lower()
            clause = _column_clause(column)
            if not hint and clause in ("order", "group", "having"):
                state = alias_states.get(id(_select_of(column)), {}).get(name.strip().lower())
                if state == "safe":
                    continue
                if state == "blocked":
                    denied.append(name)
                    continue
            physical, reason = _owner(column)
            if reason is not None or not _column_permitted(physical, name, allowed_columns):
                denied.append(name)
        if denied:
            uniq = sorted(set(denied))
            return original, [], (
                f"[Permission Denied] {subject}无权访问字段 "
                + "、".join(f"'{name}'" for name in uniq[:5])
                + (" 等" if len(uniq) > 5 else "")
                + "。当前 SQL 含子查询或 UNION，无法安全改写，请改写成单表简单查询后重试。"
            )
        return original, [], None

    # 改写投影会动列的位置与行去重口径，这两种写法下只能拒绝：
    #   * `ORDER BY 2` / `GROUP BY 2` 按序号引用列，删一列后序号全错位；
    #   * `SELECT DISTINCT` 的列变少 → 去重粒度变粗，行数会变多。
    if _has_positional_ordering(expression):
        return original, [], (
            f"[Permission Denied] {subject}查询里有你无权访问的字段，且 SQL 使用了"
            "按序号排序/分组（ORDER BY 2 / GROUP BY 1）。删列会让序号错位，"
            "因此本次查询已终止。请把序号改成字段名后重试。"
        )
    if expression.args.get("distinct"):
        return original, [], (
            f"[Permission Denied] {subject}查询里有你无权访问的字段，且 SQL 使用了 DISTINCT。"
            "删列会改变去重口径（同样的数据会多出行），因此本次查询已终止。"
            "请显式列出你有权限的字段后重试。"
        )

    kept, dropped, blocked = _rewrite_select_projections(
        expression,
        alias_to_physical=alias_to_physical,
        allowed_columns=allowed_columns,
        full_columns=full_columns,
        single_table=single_table,
        ambiguous_aliases=ambiguous_aliases,
    )
    if blocked == "multi_table_star":
        return original, [], (
            f"[Permission Denied] {subject}多表 JOIN 时不能用不带表名的 SELECT *（部分表有字段级限制，"
            "展开后列序不确定）。请显式列出你需要的字段，例如 SELECT t1.a, t2.b。"
        )
    if not dropped:
        return original, [], None
    if not kept:
        uniq = sorted(set(dropped), key=str.lower)
        return original, [], (
            f"[Permission Denied] {subject}你请求的字段 "
            + "、".join(f"'{name}'" for name in uniq[:5])
            + (" 等" if len(uniq) > 5 else "")
            + " 都不在你的授权范围内，没有任何可返回的字段。请换一个问法。"
        )

    expression.set("expressions", kept)
    try:
        rewritten = expression.sql(dialect=to_sqlglot_dialect(dialect))
    except Exception as ex:  # noqa: BLE001 - 改写失败退回拒绝，绝不执行原 SQL
        logger.warning("[ColumnScope] SQL 改写失败: %s", ex)
        return original, [], (
            f"[Permission Denied] {subject}查询涉及你无权访问的字段（"
            + "、".join(sorted(set(dropped))[:5])
            + "），且系统无法安全改写该查询。请换用你有权限的字段。"
        )

    logger.info(
        "[ColumnScope] 字段级权限：从投影中剔除 %s，改写后执行", sorted(set(dropped))
    )
    return rewritten, sorted(set(dropped), key=str.lower), None


async def _fetch_all_registered_physical_lowers(session: AsyncSession) -> set[str]:
    r = await session.execute(select(MetaTable.physical_name).where(MetaTable.status == 1))
    return {str(x).lower() for x in r.scalars().all() if x}


async def _fetch_allowed_physical_lowers_for_user(
    session: AsyncSession,
    user_id: int,
    *,
    scopes: Any = None,
) -> set[str]:
    """用户最终可见的物理表（lower）。

    从「数据集权限 ⇒ 数据集内全部表」升级为「数据集权限 ∩ 表级白名单」：
    没有表级授权（历史数据 scope 为空）时结果与旧实现完全一致。
    """
    from app.services.dataset_scope_service import allowed_physical_names_for_user

    if callable(scopes):
        scopes = await scopes()
    if scopes is None:
        return await allowed_physical_names_for_user(session, user_id)
    return await allowed_physical_names_for_user(session, user_id, scopes=scopes)


async def check_physical_table_refs_permission(
    session: AsyncSession,
    *,
    refs: Dict[str, str],
    user_id: Optional[int],
    is_admin: bool,
    dialect: str = "clickhouse",
    user_identity_label: Optional[str] = None,
) -> Optional[str]:
    """
    校验给定物理表引用是否已在元数据注册且当前用户有权访问。
    refs: {physical_lower -> SQL 展示名}
    """
    if is_admin or not refs:
        return None

    if user_id is None:
        return "[Permission Denied] 无法校验表权限：缺少用户身份"

    filtered_refs: Dict[str, str] = {}
    for lk, display in refs.items():
        if _is_exempt_builtin_table(lk, dialect):
            continue
        filtered_refs[lk] = display
    if not filtered_refs:
        return None

    allowed_lower = await _fetch_allowed_physical_lowers_for_user(session, user_id)
    registered_lower = await _fetch_all_registered_physical_lowers(session)

    for lk, display in filtered_refs.items():
        if lk in allowed_lower:
            continue
        subject = f"{user_identity_label} " if user_identity_label else ""
        if lk in registered_lower:
            return f"[Permission Denied] {subject}无权访问表 '{display}'"
        return (
            f"[Validation Failed] {subject}物理表 '{display}' 未在元数据中注册或不存在。"
            "请使用 get_dataset_schema 工具确认当前数据集的可查询表，严禁凭空猜测表名！"
        )

    return None


async def enforce_physical_table_permissions_for_select(
    session: AsyncSession,
    *,
    sql: str,
    dialect: str,
    user_id_eff: Optional[int],
    is_admin_eff: bool,
    user_identity_label: Optional[str] = None,
) -> Optional[str]:
    """
    按 MetaTable.physical_name（不区分大小写）校验 SQL 中出现的物理表是否在用户可访问的数据集中。
    管理员跳过；缺少用户身份则拒绝。
    """
    if is_admin_eff:
        return None

    err, refs = extract_physical_table_refs_from_select_sql(sql, dialect)
    if err:
        return f"[Validation Failed] {err}"

    if user_id_eff is None:
        return "[Permission Denied] 无法校验表权限：缺少用户身份"

    if not refs:
        return None

    allowed_lower = await _fetch_allowed_physical_lowers_for_user(session, user_id_eff)
    registered_lower = await _fetch_all_registered_physical_lowers(session)

    for lk, display in refs.items():
        if lk in allowed_lower:
            continue
        subject = f"{user_identity_label} " if user_identity_label else ""
        if lk in registered_lower:
            return f"[Permission Denied] {subject}无权访问表 '{display}'"
        return f"[Validation Failed] {subject}物理表 '{display}' 未在元数据中注册或不存在。请使用 get_dataset_schema 工具确认当前数据集的可查询表，严禁凭空猜测表名！"

    return None


async def execute_sql_query_core(
    session: AsyncSession,
    *,
    sql: str,
    data_source: str,
    dataset_name: Optional[str] = None,
    user_id: Optional[int] = None,
    user_dimensions: Optional[Dict[str, Any]] = None,
    trace_logs: Optional[List[str]] = None,
    api_key: Optional[str] = None,
    agent_context: Optional[AgentContext] = None,
    dry_run: Optional[bool] = None,
    is_admin: bool = False,
    auth_check_only: bool = False,
    bypass_table_auth: bool = False,
    sql_query_binding: Any | None = None,
    permission_notice: Optional[Dict[str, Any]] = None,
    include_total: bool = True,
    for_export: bool = False,
) -> str:
    """
    在给定 DB 会话下完成权限重写与执行；调用方负责会话生命周期。

    Args:
        dry_run: 若为 None，则与工具一致，回退到请求级 `get_execution_option("dry_run")`；
                 HTTP 接口应显式传入 False，避免受请求级执行选项影响。
        is_admin: 平台管理员；为 True 时跳过物理表权限校验（仍会做语法与安全校验）。
        auth_check_only: 为 True 时走与执行相同的权限与语法校验（含行级重写），**不调用**外部 SQL API；
                 成功时返回 JSON 字符串 `{"allowed": true}`。与 `dry_run` 互斥使用（HTTP 校验接口传
                 `auth_check_only=True` 且 `dry_run=False`）。
        sql_query_binding: 可选的 ChatBI 表/数据集/字段绑定；未传时尝试读取 ContextVar，
                 Agent 路径在 execute 前由 tool gate 注入。
        permission_notice: 可选可变字典；当行级权限 SQL 重写实际生效时写入给 HTTP/前端使用的
                 非敏感提示元信息；可含 `executed_sql` 供工具日志展示实际执行的 SQL，
                 不包含具体过滤条件表达式。
        include_total: 是否执行不带行数限制的 COUNT 查询并在结果中附加精确总数；
                       ChatBI 业务查询默认开启，诊断/样例探查可关闭。
        for_export: 是否为完整明细导出通道；为 True 时透传给 call_external_sql_api，
                    放宽行数上限（10 万行）并跳过 2MB 返回体硬限制，且不写入 AI 分析缓存。
    """
    from app.services.ai.chatbi_sql_query_binding import (
        build_data_perm_table_metadata,
        enforce_dataset_table_scope,
        enforce_physical_table_permissions,
        get_current_sql_query_binding,
        prepare_binding_for_execution,
        validate_sql_columns_with_binding,
    )
    from app.services.dataset_scope_service import (
        ROW_POLICY_CUSTOM,
        ROW_POLICY_INHERIT,
        ROW_POLICY_NONE,
    )

    binding = sql_query_binding or get_current_sql_query_binding()
    user_id_eff = user_id
    key = api_key or (agent_context.api_key if agent_context else None)

    if user_id_eff is None and key:
        u_info = await AuthService.verify_api_key(key, session)
        if u_info:
            user_id_eff = int(u_info["user_id"])
            dims = {
                "id": user_id_eff,
                "user_name": u_info.get("user_name"),
                "real_name": u_info.get("real_name"),
                "role": u_info.get("role"),
                "dept_code": u_info.get("dept_code"),
                "org_path": u_info.get("org_path"),
                "extra_data": u_info.get("extra_data"),
            }
            if agent_context is not None:
                agent_context.user_dimensions = dims

    is_admin_eff = bool(is_admin)
    if agent_context is not None and getattr(agent_context, "is_admin", False):
        is_admin_eff = True
    ud = _user_dims_for_rewrite(agent_context, user_dimensions)
    user_identity_label = None
    if user_id_eff is not None:
        user_name = str(ud.get("user_name") or "").strip()
        user_identity_label = f"{user_name}({user_id_eff})" if user_name else f"user_id={user_id_eff}"
    if str(ud.get("role") or "").strip().lower() == "admin":
        is_admin_eff = True

    # 真实执行前必须拿到可信身份，否则一律 fail-closed：
    #   1) 表级权限在 refs 为空（SELECT 1 / CTE / 内置表）或 bypass_table_auth 时会整段跳过；
    #   2) 行级权限拿不到用户/角色，只能落到 default_policy 兜底（配置为空时等于不过滤）；
    #   3) 结果缓存会退化成跨调用共享的作用域。
    # 与其静默放行，不如明确拒绝并把上下文写进日志，方便定位是哪条链路丢了身份。
    # dry_run 与 auth_check_only 不产生真实数据访问，保持原有行为不受此门禁影响。
    if (
        user_id_eff is None
        and not is_admin_eff
        and not bypass_table_auth
        and not auth_check_only
        and not _effective_dry_run(dry_run)
    ):
        logger.warning(
            "[SQL Auth] 拒绝执行：缺少可信用户身份 (dataset=%s, data_source=%s, "
            "has_api_key=%s, has_agent_context=%s, conversation_id=%s)",
            dataset_name or "-",
            data_source or "-",
            bool(key),
            agent_context is not None,
            getattr(agent_context, "conversation_id", None) or "-",
        )
        return (
            "[Permission Denied] 无法执行 SQL：缺少可信用户身份。"
            "请确认调用方已注入 user_id 或有效 api_key，并重新登录后重试。"
        )

    # 数据集授权在本次执行里只解析一次，供后面四道门禁（物理表 / 表范围 / 字段级 /
    # 行级）复用。原来每次门禁各查一遍：同一个事务里 4 次
    # resolve_user_dataset_scopes（每次 2~3 条 SQL），单次查询光权限就要十几条查询。
    #
    # 用「懒加载 callable」而不是立即解析：内部调用（bypass_table_auth 且无数据集）
    # 和只走语法校验的链路不需要它；门禁被替换/短路时也不会白查一次库。
    # 解析失败必须 fail-closed —— 拿不到授权就无从判断能否放行。
    _scopes_cache: Dict[str, Any] = {}

    async def _load_dataset_scopes() -> dict[int, Any]:
        if "value" not in _scopes_cache:
            from app.services.dataset_scope_service import (
                DatasetScopeResolutionError,
                resolve_user_dataset_scopes,
            )

            try:
                _scopes_cache["value"] = await resolve_user_dataset_scopes(
                    session, user_id_eff
                )
            except DatasetScopeResolutionError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[SQL Auth] 解析数据集授权失败，按 fail-closed 拒绝 (user=%s, dataset=%s): %s",
                    user_id_eff,
                    dataset_name or "-",
                    exc,
                    exc_info=True,
                )
                raise DatasetScopeResolutionError(str(exc)) from exc
        return _scopes_cache["value"]

    _scopes_provider: Any = None
    if (
        user_id_eff is not None
        and not is_admin_eff
        and (not bypass_table_auth or bool(dataset_name))
    ):
        _scopes_provider = _load_dataset_scopes

    ds = None
    if dataset_name:
        ds = await MetadataService.get_dataset_by_name(session, dataset_name)
        if not ds:
            return f"Error: Dataset '{dataset_name}' not found. Please verify the dataset name."

    dialect = dialect_from_data_source(data_source)
    if dialect == "postgres":
        # PostgreSQL 数据源需要兼容历史业务指标中遗留的 ClickHouse 日期函数。
        from app.services.data_adapter.postgresql import normalize_postgresql_sql

        original_sql = sql
        sql = normalize_postgresql_sql(sql)
        if sql != original_sql:
            logger.info(
                "已为 PostgreSQL 查询应用 SQL 方言兼容转换: data_source=%s, dataset=%s",
                data_source,
                dataset_name or "未指定",
            )
    # 先做只读/多语句/危险函数校验，再做表引用解析：否则 DROP 之类的语句
    # 会先被“提取表引用”逻辑拦截，返回与 HTTP 契约不一致的错误文案。
    error = validate_sql(sql, dialect=dialect)
    if error:
        return f"[Validation Failed] {error}"

    if binding is not None:
        binding = await prepare_binding_for_execution(
            session,
            binding,
            sql=sql,
            dialect=dialect,
            dataset_name=dataset_name,
        )

    if not bypass_table_auth:
        from app.services.dataset_scope_service import DatasetScopeResolutionError

        def _scope_denied() -> str:
            return (
                "[Permission Denied] 无法解析你的数据集权限（授权服务暂时不可用），"
                "为避免越权本次查询已拒绝。请稍后重试或联系管理员。"
            )

        err_ref, refs = extract_physical_table_refs_from_select_sql(sql, dialect)
        if err_ref:
            return f"[Validation Failed] {err_ref}"

        # refs 为空是「无法归属到任何表」的信号，不是「没有表」。SHOW / DESCRIBE 这类
        # 命令会走到这里，而它们能列出整库表结构；只放行真正不需要表的常量查询。
        if not refs and not _is_table_free_read(sql, dialect):
            return (
                "[Permission Denied] 本次查询无法确定实际访问的表，数据权限无法校验，已拒绝执行。"
                "请使用标准 SELECT 语句并写明表名；查看表结构请使用数据集 Schema 工具，"
                "不要用 SHOW / DESCRIBE 直接查库。"
            )

        column_err = validate_sql_columns_with_binding(sql, binding)
        if column_err:
            return column_err

        if ds and refs:
            scope_err = await enforce_dataset_table_scope(
                session,
                refs=refs,
                dataset_name=str(dataset_name or ""),
                ds=ds,
                binding=binding,
            )
            if scope_err:
                return scope_err

        try:
            perm_err = await enforce_physical_table_permissions(
                session,
                refs=refs,
                dialect=dialect,
                user_id_eff=user_id_eff,
                is_admin_eff=is_admin_eff,
                user_identity_label=user_identity_label,
                binding=binding,
                scopes=_scopes_provider,
            )
        except DatasetScopeResolutionError:
            return _scope_denied()
        if perm_err:
            return perm_err

        from app.services.dataset_scope_service import enforce_table_scope_for_refs
        from app.services.ai.metadata_dataset_scope import resolve_mounted_dataset_id_set

        # 本轮限定了数据集时，表级门禁要按「限定范围」判定：否则同一张表在别的
        # 数据集里被全开，就会把本次收窄整个抹掉（实测 ds1 只给 1 张表，
        # 却能经 ds9/ds54 的 all 授权查到 ai_agent_users）。
        restrict_ids = await resolve_mounted_dataset_id_set(session)

        try:
            table_scope_err = await enforce_table_scope_for_refs(
                session,
                refs=refs,
                user_id=user_id_eff,
                is_admin=is_admin_eff,
                user_identity_label=user_identity_label,
                restrict_dataset_ids=restrict_ids,
                scopes=_scopes_provider,
            )
        except DatasetScopeResolutionError:
            return _scope_denied()
        if table_scope_err:
            return table_scope_err

        # 字段级：能剔的剔掉（SELECT 投影），不能剔的才拒绝（筛选/分组/排序）。
        # 放在表级之后 —— 表本身都无权时，报「表无权」比报「列无权」更准确。
        sql, dropped_columns, column_scope_err = await apply_column_scope_to_select_sql(
            session,
            sql=sql,
            dialect=dialect,
            user_id_eff=user_id_eff,
            is_admin_eff=is_admin_eff,
            restrict_dataset_ids=restrict_ids,
            user_identity_label=user_identity_label,
            scopes=_scopes_provider,
        )
        if column_scope_err:
            return column_scope_err
        if dropped_columns and permission_notice is not None:
            # 让结果里带上「哪几列被权限拿掉了」，否则模型会以为自己少查了东西
            permission_notice.setdefault("omitted_columns", [])
            permission_notice["omitted_columns"] = sorted(
                set(permission_notice.get("omitted_columns") or []) | set(dropped_columns)
            )
    elif binding is not None:
        column_err = validate_sql_columns_with_binding(sql, binding)
        if column_err:
            return column_err

    # 行级策略：授权行上的 row_policy 优先于数据集级 enable_data_perm。
    #   - none   ：显式跳过行级隔离（仅给运维/审计类个人授权）；
    #   - custom ：使用授权行上的独立条件，不再走数据集的三层策略；
    #   - inherit：沿用 meta_datasets.row_filter_config（历史行为）。
    row_policy = ROW_POLICY_INHERIT
    row_condition: Optional[str] = None
    if ds and user_id_eff is not None and not is_admin_eff:
        try:
            from app.services.dataset_scope_service import (
                DatasetScopeResolutionError,
                resolve_effective_row_policy,
            )

            row_policy, row_condition = await resolve_effective_row_policy(
                session, user_id_eff, int(ds.id), scopes=_scopes_provider
            )
        except DatasetScopeResolutionError:
            return (
                "[Permission Denied] 无法解析你的行级数据权限（授权服务暂时不可用），"
                "为避免越权本次查询已拒绝。请稍后重试或联系管理员。"
            )
        except Exception as exc:  # noqa: BLE001 - 解析失败按 inherit，避免误跳过隔离
            logger.warning("[DataPerm] 解析行级策略失败，按 inherit 处理: %s", exc)

    custom_row_filter = row_policy == ROW_POLICY_CUSTOM and bool(row_condition)
    inherit_row_filter = row_policy == ROW_POLICY_INHERIT and bool(ds and ds.enable_data_perm)

    if ds and row_policy != ROW_POLICY_NONE and (custom_row_filter or inherit_row_filter):
        _append_trace(
            agent_context,
            trace_logs,
            f"🛡️ 权限校验: 数据集 '{dataset_name}' (ID: {ds.id})",
        )
        _append_trace(agent_context, trace_logs, "🔍 正在构建数据权限隔离条件...")

        from app.services.ai.rewriters.sql_rewriter import SQLPermissionError, SQLRewriter

        try:
            rewriter_dialect = dialect

            table_metadata: Dict[str, Any] = build_data_perm_table_metadata(binding, str(dataset_name or "")) or {}
            if not table_metadata:
                try:
                    # 一次性 join 查询拿到该数据集全部 表->列 映射，避免逐表查询的 N+1 开销。
                    cols_stmt = (
                        select(MetaTable.physical_name, MetaColumn.physical_name)
                        .join(MetaColumn, MetaColumn.table_id == MetaTable.id)
                        .where(MetaTable.dataset_id == ds.id)
                    )
                    cols_result = await session.execute(cols_stmt)
                    table_metadata: Dict[str, Any] = {}
                    for table_name, column_name in cols_result.all():
                        table_metadata.setdefault(table_name, set()).add(column_name)

                    logger.info(
                        "[DataPerm] Loaded metadata for %s tables, %s columns",
                        len(table_metadata),
                        sum(len(cols) for cols in table_metadata.values()),
                    )
                except Exception as e:
                    logger.warning("[DataPerm] Failed to load table metadata: %s", e)
                    table_metadata = {}
            elif table_metadata:
                logger.info(
                    "[DataPerm] Reused SqlQueryBinding metadata for %s tables, %s columns",
                    len(table_metadata),
                    sum(len(cols) for cols in table_metadata.values()),
                )

            rewriter = SQLRewriter(dialect=rewriter_dialect, table_metadata=table_metadata)

            if custom_row_filter:
                # 授权行上的独立条件：不再叠加数据集的三层策略。
                user_roles = []
                filters = [{"condition": str(row_condition)}]
            else:
                user_roles: List[Any] = []
                if user_id_eff:
                    try:
                        ps = PermissionService(session)
                        perm_data = await ps.get_user_permissions(user_id_eff)
                        user_roles = getattr(perm_data, "roles", [])
                    except Exception as e:
                        logger.error("[DataPerm] Failed to fetch roles for user %s: %s", user_id_eff, e)

                filters = rewriter.resolve_strategy(user_id_eff, user_roles, ds.row_filter_config)

            if filters:
                user_dims = _user_dims_for_rewrite(agent_context, user_dimensions)
                original_sql = sql
                rewrite_stats: Dict[str, int] = {}
                sql = rewriter.rewrite(sql, filters, user_dims, rewrite_stats=rewrite_stats)
                rewrite_applied = sql != original_sql
                applied_rule_count = int(rewrite_stats.get("applied_rule_count") or 0)
                if custom_row_filter and not rewrite_applied:
                    # 重写器对「条件引用的表不在本次查询里」是静默返回原 SQL 的。
                    # 对 inherit 这是既有语义（条件按表生效），但对 custom 授权等于
                    # 「配了行级限制却查到了全表」——属于权限放大，必须拒绝而不是放行。
                    return (
                        f"[Permission Denied] {user_identity_label or 'user_id=' + str(user_id_eff)} "
                        "配置的自定义行级条件未生效：它引用的表不在本次查询里，"
                        "为避免越权已拒绝执行。请联系数据集管理员把行级条件改成覆盖所查表的字段，"
                        "或改用「不做行级过滤」授权。"
                    )
                logger.info(
                    "[DataPerm] SQL Rewritten for user %s. Applied %s/%s filter conditions.",
                    user_id_eff,
                    applied_rule_count,
                    len(filters),
                )
                if rewrite_applied:
                    _mark_row_filter_notice(
                        permission_notice,
                        dataset_name=dataset_name,
                        rule_count=applied_rule_count,
                        executed_sql=sql,
                    )
                _append_trace(
                    agent_context,
                    trace_logs,
                    f"🔒 数据权限校验完成: 命中了 {len(filters)} 条规则，尝试重新构建 WHERE 条件。",
                )
            else:
                _append_trace(
                    agent_context,
                    trace_logs,
                    "ℹ️ 数据权限校验: 开启了校验但未命中任何过滤规则。",
                )
        except SQLPermissionError as perm_exc:
            # 行级条件无法覆盖整条 SQL（如 UNION 有分支未过滤）：原样返回权限文案，
            # 不能落到下面的「语法错误」分支。
            logger.warning("[DataPerm] 行级权限无法安全覆盖查询: %s", perm_exc)
            return str(perm_exc)
        except Exception as e:
            logger.error("[DataPerm] SQL Rewrite failed: %s", e, exc_info=True)
            error_str = str(e)
            if _effective_dry_run(dry_run):
                return f"[DRY_RUN] 权限解析异常: {error_str}"
            if "Expected table name" in error_str or "SQL transformation failed" in error_str:
                return f"[Validation Failed] SQL语法错误: {error_str}"
            return f"[Security Error] Failed to apply data permissions: {error_str}"

    # 只读与静态安全校验必须先于任何外部调用：沙箱会对 SQL 发 EXPLAIN，
    # 不能让未通过校验的 SQL（含/*!版本注释*/）先到达数据源。
    error = validate_sql(sql, dialect=dialect)
    if error:
        return f"[Validation Failed] {error}"

    if _effective_dry_run(dry_run):
        return f"[DRY_RUN] Dialect: {dialect} | Source: {data_source} | Scope: {dataset_name}\nSQL: {sql}"

    # auth_check_only 只做权限/语法校验，不应触发 EXPLAIN 等外部 SQL 调用。
    if auth_check_only:
        return json.dumps({"allowed": True}, ensure_ascii=False)

    # SQL 性能与安全沙箱网关预检
    try:
        from app.services.ai.sql_sandbox_gate import SQLSandboxGate
        sandbox_res = await SQLSandboxGate.verify_and_optimize(
            session,
            sql=sql,
            data_source=data_source,
            dialect=dialect,
        )
        if not sandbox_res.allowed:
            return sandbox_res.message
        sql = sandbox_res.optimized_sql
    except Exception as sandbox_err:
        logger.warning(f"[SQL Sandbox] Pre-check failed or skipped: {sandbox_err}")

    return await call_external_sql_api(
        sql,
        data_source=data_source,
        cache_scope=str(user_id_eff) if user_id_eff is not None else None,
        include_total=include_total,
        for_export=for_export,
    )
