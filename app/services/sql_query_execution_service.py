"""
只读 SQL 执行核心逻辑：数据集解析、物理表权限、行级权限 SQL 重写、语法校验、调用外部执行 API。
供 `execute_sql_query` 工具与 `/api/v1/chatbi/*` 等 HTTP 接口复用。
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import sqlglot
from sqlalchemy import select
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
_DIALECT_BUILTIN_SCHEMA_EXEMPTIONS: dict[str, frozenset[str]] = {
    "clickhouse": frozenset({"system", "information_schema"}),
    "mysql": frozenset({"information_schema", "mysql", "performance_schema", "sys"}),
    "oracle": frozenset({"sys", "system"}),
}


def _is_exempt_builtin_table(table_lower: str, dialect: str, schema_lower: Optional[str] = None) -> bool:
    dialect_lower = str(dialect or "").lower()
    table_exemptions = _DIALECT_BUILTIN_TABLE_EXEMPTIONS.get(dialect_lower, frozenset())
    if table_lower in table_exemptions:
        return True
    if not schema_lower:
        return False
    schema_exemptions = _DIALECT_BUILTIN_SCHEMA_EXEMPTIONS.get(dialect_lower, frozenset())
    return schema_lower in schema_exemptions


def _effective_dry_run(dry_run: Optional[bool]) -> bool:
    if dry_run is not None:
        return bool(dry_run)
    # 在函数内导入，便于测试 patch `app.core.context.get_debug_option` 与工具内联 import 行为一致
    from app.core.context import get_debug_option as _get_debug_option

    return bool(_get_debug_option("dry_run"))


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


def attach_permission_notice_to_payload(
    payload: Dict[str, Any],
    permission_notice: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if permission_notice and permission_notice.get("row_filter_applied") is True:
        payload["permission_notice"] = dict(permission_notice)
    return payload


def attach_permission_notice_to_json_result(
    result_str: str,
    permission_notice: Optional[Dict[str, Any]],
) -> str:
    if not permission_notice or permission_notice.get("row_filter_applied") is not True:
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


def dialect_from_data_source(data_source: Optional[str]) -> str:
    dialect = "clickhouse"
    ds_lower = data_source.lower() if data_source else ""
    if "mysql" in ds_lower:
        dialect = "mysql"
    elif "oracle" in ds_lower:
        dialect = "oracle"
    elif any(token in ds_lower for token in ("sqlserver", "mssql", "tsql")):
        dialect = "sqlserver"
    return dialect


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


def _collect_subquery_alias_lowers(expression: exp.Expression) -> set[str]:
    aliases: set[str] = set()
    for sq in expression.find_all(exp.Subquery):
        if sq.alias:
            aliases.add(sq.alias.lower())
    return aliases


def extract_physical_table_refs_from_select_sql(sql: str, dialect: str) -> Tuple[Optional[str], Dict[str, str]]:
    """
    解析单条只读 SQL，提取物理表引用（视图与普通表同等对待）。
    支持 SELECT、WITH...SELECT；对 EXPLAIN / SHOW / DESCRIBE 等无表引用的只读语句返回空字典。
    返回 (错误信息或 None, {physical_lower -> SQL 中的展示名（保留原始大小写）})
    """
    sql_clean = sql.strip()
    if not sql_clean:
        return "Empty SQL query.", {}

    try:
        parsed = sqlglot.parse(sql_clean, read=dialect)
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

    # For EXPLAIN / SHOW / DESCRIBE etc., no physical table refs to extract
    if not isinstance(expression, exp.Select):
        return None, {}

    skip_aliases = _collect_cte_alias_lowers(expression) | _collect_subquery_alias_lowers(expression)

    refs: Dict[str, str] = {}
    for table in expression.find_all(exp.Table):
        frag = _physical_fragment_from_table(table)
        if not frag:
            continue
        lk = frag.lower()
        schema_lower = _schema_fragment_from_table(table).lower()
        if lk in skip_aliases:
            continue
        if _is_exempt_builtin_table(lk, dialect, schema_lower=schema_lower):
            continue
        if lk not in refs:
            refs[lk] = frag

    return None, refs


async def _fetch_all_registered_physical_lowers(session: AsyncSession) -> set[str]:
    r = await session.execute(select(MetaTable.physical_name).where(MetaTable.status == 1))
    return {str(x).lower() for x in r.scalars().all() if x}


async def _fetch_allowed_physical_lowers_for_user(session: AsyncSession, user_id: int) -> set[str]:
    datasets = await MetadataService.search_datasets(session, status=1, user_id=user_id, is_admin=False)
    allowed: set[str] = set()
    for d in datasets:
        for tb in d.tables:
            if tb.status == 1:
                allowed.add(tb.physical_name.lower())
    return allowed


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
) -> str:
    """
    在给定 DB 会话下完成权限重写与执行；调用方负责会话生命周期。

    Args:
        dry_run: 若为 None，则与工具一致，回退到请求级 `get_debug_option("dry_run")`；
                 HTTP 接口应显式传入 False，避免受调试上下文影响。
        is_admin: 平台管理员；为 True 时跳过物理表权限校验（仍会做语法与安全校验）。
        auth_check_only: 为 True 时走与执行相同的权限与语法校验（含行级重写），**不调用**外部 SQL API；
                 成功时返回 JSON 字符串 `{"allowed": true}`。与 `dry_run` 互斥使用（HTTP 校验接口传
                 `auth_check_only=True` 且 `dry_run=False`）。
        sql_query_binding: 可选的 ChatBI 表/数据集/字段绑定；未传时尝试读取 ContextVar，
                 Agent 路径在 execute 前由 tool gate 注入。
        permission_notice: 可选可变字典；当行级权限 SQL 重写实际生效时写入给 HTTP/前端使用的
                 非敏感提示元信息；可含 `executed_sql` 供工具日志展示实际执行的 SQL，
                 不包含具体过滤条件表达式。
    """
    from app.services.ai.chatbi_sql_query_binding import (
        build_data_perm_table_metadata,
        enforce_dataset_table_scope,
        enforce_physical_table_permissions,
        get_current_sql_query_binding,
        prepare_binding_for_execution,
        validate_sql_columns_with_binding,
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

    ds = None
    if dataset_name:
        ds = await MetadataService.get_dataset_by_name(session, dataset_name)
        if not ds:
            return f"Error: Dataset '{dataset_name}' not found. Please verify the dataset name."

    dialect = dialect_from_data_source(data_source)
    if binding is not None:
        binding = await prepare_binding_for_execution(
            session,
            binding,
            sql=sql,
            dialect=dialect,
            dataset_name=dataset_name,
        )

    if not bypass_table_auth:
        err_ref, refs = extract_physical_table_refs_from_select_sql(sql, dialect)
        if err_ref:
            return f"[Validation Failed] {err_ref}"

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

        perm_err = await enforce_physical_table_permissions(
            session,
            refs=refs,
            dialect=dialect,
            user_id_eff=user_id_eff,
            is_admin_eff=is_admin_eff,
            user_identity_label=user_identity_label,
            binding=binding,
        )
        if perm_err:
            return perm_err
    elif binding is not None:
        column_err = validate_sql_columns_with_binding(sql, binding)
        if column_err:
            return column_err

    if ds and ds.enable_data_perm:
        _append_trace(
            agent_context,
            trace_logs,
            f"🛡️ 权限校验: 数据集 '{dataset_name}' (ID: {ds.id})",
        )
        _append_trace(agent_context, trace_logs, "🔍 正在构建数据权限隔离条件...")

        from app.services.ai.rewriters.sql_rewriter import SQLRewriter

        try:
            rewriter_dialect = dialect

            table_metadata: Dict[str, Any] = build_data_perm_table_metadata(binding, str(dataset_name or "")) or {}
            if ds.enable_data_perm and not table_metadata:
                try:
                    tables_stmt = select(MetaTable).where(MetaTable.dataset_id == ds.id)
                    tables_result = await session.execute(tables_stmt)
                    tables = tables_result.scalars().all()

                    for table in tables:
                        cols_stmt = select(MetaColumn.physical_name).where(MetaColumn.table_id == table.id)
                        cols_result = await session.execute(cols_stmt)
                        columns = {col.physical_name for col in cols_result.scalars().all()}
                        table_metadata[table.physical_name] = columns

                    logger.info(
                        "[DataPerm] Loaded metadata for %s tables, %s columns",
                        len(tables),
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
        except Exception as e:
            logger.error("[DataPerm] SQL Rewrite failed: %s", e, exc_info=True)
            error_str = str(e)
            if _effective_dry_run(dry_run):
                return f"[DRY_RUN] 权限解析异常: {error_str}"
            if "Expected table name" in error_str or "SQL transformation failed" in error_str:
                return f"[Validation Failed] SQL语法错误: {error_str}"
            return f"[Security Error] Failed to apply data permissions: {error_str}"

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

    error = validate_sql(sql, dialect=dialect)
    if error:
        return f"[Validation Failed] {error}"

    if _effective_dry_run(dry_run):
        return f"[DRY_RUN] Dialect: {dialect} | Source: {data_source} | Scope: {dataset_name}\nSQL: {sql}"

    if auth_check_only:
        return json.dumps({"allowed": True}, ensure_ascii=False)

    return await call_external_sql_api(
        sql,
        data_source=data_source,
        cache_scope=str(user_id_eff) if user_id_eff is not None else None,
    )
