"""ChatBI SQL 查询绑定：统一 Schema / SQL / 元数据中的表-数据集-字段信息。"""

from __future__ import annotations

import re
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metadata import MetaColumn, MetaDataset, MetaTable
from app.services.ai.runners.chatbi.sql_gates import (
    SchemaColumnMeta,
    build_sql_schema_preflight_error,
    collect_preflight_unknown_tables,
    extract_schema_table_column_meta,
    normalize_sql_identifier,
)
from app.services.schema_chunk_format import _SCHEMA_HEADER_RE

_DATASET_LINE_RE = re.compile(r"^\s*dataset:\s*(\S+)\s*$", re.MULTILINE | re.IGNORECASE)
_TABLE_NAME_LINE_RE = re.compile(r"^\s*table_name:\s*(\S+)\s*$", re.MULTILINE | re.IGNORECASE)
_DATA_SOURCE_LINE_RE = re.compile(r"^\s*data_source:\s*(\S+)\s*$", re.MULTILINE | re.IGNORECASE)


@dataclass
class TableBinding:
    physical_name: str
    dataset_name: str = ""
    data_source: str = ""
    columns: list[SchemaColumnMeta] = field(default_factory=list)

    @property
    def table_key(self) -> str:
        return normalize_sql_identifier(self.physical_name)


@dataclass
class SqlQueryBinding:
    sql: str = ""
    primary_dataset_name: str = ""
    schema_output: str = ""
    tables: dict[str, TableBinding] = field(default_factory=dict)
    preflight_validated: bool = False

    def get_table(self, physical_or_key: str) -> TableBinding | None:
        key = normalize_sql_identifier(physical_or_key)
        return self.tables.get(key)

    def involved_datasets(self) -> set[str]:
        return {
            binding.dataset_name.strip()
            for binding in self.tables.values()
            if str(binding.dataset_name or "").strip()
        }

    def table_dataset_map(self) -> dict[str, str]:
        return {
            binding.physical_name: binding.dataset_name
            for binding in self.tables.values()
            if binding.physical_name and binding.dataset_name
        }

    def schema_table_columns(self) -> dict[str, list[str]]:
        return bindings_to_table_columns(self.tables)


def _extract_physical_table_refs(sql: str, dialect: str) -> tuple[str | None, dict[str, str]]:
    from app.services.sql_query_execution_service import extract_physical_table_refs_from_select_sql

    return extract_physical_table_refs_from_select_sql(sql, dialect)


def set_current_sql_query_binding(binding: SqlQueryBinding | None) -> Token:
    return _current_sql_query_binding.set(binding)


def reset_current_sql_query_binding(token: Token) -> None:
    _current_sql_query_binding.reset(token)


def get_current_sql_query_binding() -> SqlQueryBinding | None:
    return _current_sql_query_binding.get()


_current_sql_query_binding: ContextVar[SqlQueryBinding | None] = ContextVar(
    "sql_query_binding",
    default=None,
)


def bindings_to_table_columns(tables: dict[str, TableBinding]) -> dict[str, list[str]]:
    return {
        key: [column.name for column in binding.columns if column.name]
        for key, binding in tables.items()
        if binding.columns
    }


def extract_schema_dialect_map(schema_output: str) -> dict[str, str]:
    dialect_map: dict[str, str] = {}
    current_dataset: str | None = None
    for line in (schema_output or "").splitlines():
        stripped = line.strip()
        dataset_match = _DATASET_LINE_RE.match(stripped)
        if dataset_match:
            current_dataset = dataset_match.group(1).strip()
            continue
        data_source_match = _DATA_SOURCE_LINE_RE.match(stripped)
        if data_source_match and current_dataset:
            ds_type = data_source_match.group(1).strip()
            if ds_type and current_dataset not in dialect_map:
                dialect_map[current_dataset] = ds_type
            current_dataset = None
    return dialect_map


def _extract_schema_table_refs(schema_text: str) -> list[tuple[str | None, str]]:
    refs: list[tuple[str | None, str]] = []
    current_dataset: str | None = None
    for raw_line in (schema_text or "").splitlines():
        stripped = raw_line.strip()
        dataset_match = _DATASET_LINE_RE.match(stripped)
        if dataset_match:
            current_dataset = dataset_match.group(1).strip()
            continue
        table_match = _TABLE_NAME_LINE_RE.match(stripped)
        if table_match:
            refs.append((current_dataset, table_match.group(1).strip()))
    return refs


def extract_schema_table_bindings(schema_output: str) -> dict[str, TableBinding]:
    """从 get_dataset_schema 输出解析表 → 数据集 → 列 meta。"""
    text = str(schema_output or "")
    if not text.strip():
        return {}

    column_meta = extract_schema_table_column_meta(text)
    dialect_map = extract_schema_dialect_map(text)
    bindings: dict[str, TableBinding] = {}

    def upsert(physical_name: str, *, dataset_name: str | None = None) -> TableBinding:
        key = normalize_sql_identifier(physical_name)
        binding = bindings.get(key)
        if binding is None:
            binding = TableBinding(physical_name=physical_name)
            bindings[key] = binding
        if dataset_name:
            if binding.dataset_name and binding.dataset_name != dataset_name:
                binding.dataset_name = ""
                binding.data_source = ""
            else:
                binding.dataset_name = dataset_name
                binding.data_source = dialect_map.get(dataset_name, binding.data_source)
        if key in column_meta and not binding.columns:
            binding.columns = list(column_meta[key])
        return binding

    for dataset_name, physical_name in _extract_schema_table_refs(text):
        upsert(physical_name, dataset_name=dataset_name)

    for match in _SCHEMA_HEADER_RE.finditer(text):
        dataset_name = (match.group(3) or "").strip() or None
        physical_name = (match.group(4) or "").strip()
        if physical_name:
            upsert(physical_name, dataset_name=dataset_name)

    for table_key, columns in column_meta.items():
        binding = bindings.get(table_key)
        if binding is None:
            binding = TableBinding(physical_name=table_key, columns=list(columns))
            bindings[table_key] = binding
        elif not binding.columns:
            binding.columns = list(columns)

    return bindings


def merge_sql_tables_into_binding(
    binding: SqlQueryBinding,
    *,
    sql: str,
    dialect: str,
) -> SqlQueryBinding:
    """将 SQL 中出现的物理表合并进 binding（保留已有 dataset / 列 meta）。"""
    err, refs = _extract_physical_table_refs(sql, dialect)
    if err or not refs:
        binding.sql = sql
        return binding

    binding.sql = sql
    for lk, display in refs.items():
        existing = binding.tables.get(lk)
        if existing is None:
            binding.tables[lk] = TableBinding(physical_name=display)
        elif not existing.physical_name:
            existing.physical_name = display
    return binding


def build_sql_query_binding(
    *,
    schema_output: str = "",
    sql: str = "",
    primary_dataset_name: str = "",
    table_bindings: dict[str, TableBinding] | None = None,
    dialect: str = "clickhouse",
) -> SqlQueryBinding:
    bindings = dict(table_bindings or {})
    if schema_output.strip() and not bindings:
        bindings = extract_schema_table_bindings(schema_output)

    binding = SqlQueryBinding(
        sql=sql,
        primary_dataset_name=str(primary_dataset_name or "").strip(),
        schema_output=str(schema_output or ""),
        tables=bindings,
    )
    if sql.strip():
        merge_sql_tables_into_binding(binding, sql=sql, dialect=dialect)
    return binding


def pick_table_binding_candidate(
    candidates: list[TableBinding],
    *,
    preferred_dataset_name: str = "",
    preferred_datasets: set[str] | None = None,
    hint_dataset_name: str = "",
) -> TableBinding | None:
    """在跨 dataset 同名物理表候选中消歧；无法唯一确定时返回 None。"""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    for hint in (hint_dataset_name, preferred_dataset_name):
        hint = str(hint or "").strip()
        if not hint:
            continue
        matched = [item for item in candidates if item.dataset_name == hint]
        if len(matched) == 1:
            return matched[0]

    if preferred_datasets:
        matched = [item for item in candidates if item.dataset_name in preferred_datasets]
        if len(matched) == 1:
            return matched[0]

    return None


async def _fetch_table_binding_candidates_from_db(
    session: AsyncSession,
    physical_names: list[str],
) -> dict[str, list[TableBinding]]:
    names = [str(name).strip() for name in physical_names if str(name).strip()]
    if not names:
        return {}

    lowered = {name.lower() for name in names}
    stmt = (
        select(
            MetaTable.physical_name,
            MetaDataset.name,
            MetaDataset.data_source,
            MetaColumn.physical_name,
            MetaColumn.type,
        )
        .join(MetaDataset, MetaTable.dataset_id == MetaDataset.id)
        .outerjoin(MetaColumn, MetaColumn.table_id == MetaTable.id)
        .where(
            MetaTable.status == 1,
            MetaDataset.status == 1,
            MetaTable.physical_name.in_(names),
        )
    )
    result = await session.execute(stmt)
    grouped: dict[str, dict[str, TableBinding]] = {}
    for physical_name, dataset_name, data_source, column_name, column_type in result.all():
        physical = str(physical_name or "").strip()
        if not physical or physical.lower() not in lowered:
            continue
        key = normalize_sql_identifier(physical)
        ds_name = str(dataset_name or "").strip()
        per_dataset = grouped.setdefault(key, {})
        binding = per_dataset.get(ds_name)
        if binding is None:
            binding = TableBinding(
                physical_name=physical,
                dataset_name=ds_name,
                data_source=str(data_source or "").strip(),
            )
            per_dataset[ds_name] = binding
        column = str(column_name or "").strip()
        if column:
            existing = {normalize_sql_identifier(item.name) for item in binding.columns}
            col_key = normalize_sql_identifier(column)
            if col_key and col_key not in existing:
                binding.columns.append(
                    SchemaColumnMeta(name=column, col_type=str(column_type or "").strip())
                )
    return {key: list(items.values()) for key, items in grouped.items()}


async def resolve_table_bindings_from_db(
    session: AsyncSession,
    physical_names: list[str],
    *,
    preferred_dataset_name: str = "",
    preferred_datasets: set[str] | None = None,
    hints: dict[str, str] | None = None,
) -> dict[str, TableBinding]:
    """按物理表名从 MetaTable/MetaColumn 补全 dataset / data_source / 列信息。

    元数据允许不同 dataset 注册同名 physical_name；多候选时按 hint / primary / involved datasets 消歧，
    仍无法唯一确定时不写入结果（避免静默覆盖）。
    """
    candidates_map = await _fetch_table_binding_candidates_from_db(session, physical_names)
    resolved: dict[str, TableBinding] = {}
    hint_map = hints or {}
    for key, candidates in candidates_map.items():
        picked = pick_table_binding_candidate(
            candidates,
            preferred_dataset_name=preferred_dataset_name,
            preferred_datasets=preferred_datasets,
            hint_dataset_name=str(hint_map.get(key) or "").strip(),
        )
        if picked is not None:
            resolved[key] = picked
    return resolved


def _binding_resolution_hints(binding: SqlQueryBinding | None) -> dict[str, str]:
    if binding is None:
        return {}
    return {
        key: table_binding.dataset_name
        for key, table_binding in binding.tables.items()
        if table_binding.dataset_name
    }


def _binding_preferred_datasets(binding: SqlQueryBinding | None) -> set[str]:
    if binding is None:
        return set()
    datasets = binding.involved_datasets()
    primary = str(binding.primary_dataset_name or "").strip()
    if primary:
        datasets.add(primary)
    return datasets


async def enrich_binding_from_db(
    session: AsyncSession,
    binding: SqlQueryBinding,
) -> SqlQueryBinding:
    """为 binding 中缺 dataset 的表补全元数据。"""
    missing = [
        table_binding.physical_name
        for table_binding in binding.tables.values()
        if table_binding.physical_name and not table_binding.dataset_name
    ]
    if not missing:
        return binding

    db_bindings = await resolve_table_bindings_from_db(
        session,
        missing,
        preferred_dataset_name=binding.primary_dataset_name,
        preferred_datasets=_binding_preferred_datasets(binding) or None,
        hints=_binding_resolution_hints(binding),
    )
    for key, db_binding in db_bindings.items():
        current = binding.tables.get(key)
        if current is None:
            binding.tables[key] = db_binding
            continue
        if not current.dataset_name:
            current.dataset_name = db_binding.dataset_name
        if not current.data_source:
            current.data_source = db_binding.data_source
        if not current.columns:
            current.columns = list(db_binding.columns)
    return binding


async def build_federated_upgrade_binding(
    session: AsyncSession,
    *,
    sql: str,
    dialect: str,
    schema_output: str = "",
    schema_bindings: dict[str, TableBinding] | None = None,
    primary_dataset_name: str = "",
) -> SqlQueryBinding:
    """SQL 跨库升级时构造完整 binding（Schema + SQL 表 + DB 补全）。"""
    binding = build_sql_query_binding(
        schema_output=schema_output,
        sql=sql,
        primary_dataset_name=primary_dataset_name,
        table_bindings=schema_bindings,
        dialect=dialect,
    )
    binding = await enrich_binding_from_db(session, binding)

    err, refs = _extract_physical_table_refs(sql, dialect)
    if refs:
        db_bindings = await resolve_table_bindings_from_db(
            session,
            [display for display in refs.values()],
            preferred_dataset_name=primary_dataset_name,
            preferred_datasets=_binding_preferred_datasets(binding) or None,
            hints=_binding_resolution_hints(binding),
        )
        for key, db_binding in db_bindings.items():
            current = binding.tables.get(key)
            if current is None:
                binding.tables[key] = db_binding
            else:
                if not current.dataset_name:
                    current.dataset_name = db_binding.dataset_name
                if not current.data_source:
                    current.data_source = db_binding.data_source
                if not current.columns:
                    current.columns = list(db_binding.columns)
    return binding


def _cross_dataset_scope_error(display: str, dataset_name: str) -> str:
    return (
        f"[Validation Failed] 表 '{display}' 不属于当前指定的数据集 '{dataset_name}'，"
        f"普通 execute_sql_query 严禁跨数据集或凭空猜表。"
        f"如果用户明确要求跨数据集/跨库/联合查询，请走跨数据集联邦查询流程："
        f"先分别在各自 dataset 内执行子查询，再在内存联邦阶段关联。"
        f"如果只是补全姓名/部门/客户名称等维度，请只查询当前数据集的外键字段，"
        f"后端会尝试按 relation/维表做维度补全。"
        f"请通过 get_dataset_schema 重新确认当前数据集下相关表的 table_name（物理表名）后再查询。"
    )


def build_data_perm_table_metadata(
    binding: SqlQueryBinding | None,
    dataset_name: str,
) -> dict[str, set[str]] | None:
    if not binding or not dataset_name:
        return None
    metadata: dict[str, set[str]] = {}
    for table_binding in binding.tables.values():
        if table_binding.dataset_name != dataset_name or not table_binding.columns:
            continue
        metadata[table_binding.physical_name] = {
            column.name for column in table_binding.columns if column.name
        }
    return metadata or None


def validate_sql_columns_with_binding(sql: str, binding: SqlQueryBinding | None) -> str | None:
    if not binding or binding.preflight_validated:
        return None
    table_columns = binding.schema_table_columns()
    if not table_columns:
        return None
    return build_sql_schema_preflight_error(sql, table_columns) or None


async def resolve_sql_schema_preflight_with_binding(
    session: AsyncSession,
    *,
    sql: str,
    binding: SqlQueryBinding | None,
    schema_table_columns: dict[str, list[str]] | None = None,
    data_source: str = "",
    user_id: int | None = None,
    is_admin: bool = False,
) -> str:
    """基于 SqlQueryBinding 做 SQL 预检；权限放行的未知表会回填进 binding。"""
    table_columns = (
        binding.schema_table_columns()
        if binding and binding.tables
        else dict(schema_table_columns or {})
    )
    if not sql.strip() or not table_columns:
        return ""

    from app.services.sql_query_execution_service import (
        check_physical_table_refs_permission,
        dialect_from_data_source,
    )

    dialect = dialect_from_data_source(data_source)
    unknown_tables = collect_preflight_unknown_tables(
        sql,
        table_columns,
        dialect=dialect,
    )
    extra_allowed: set[str] = set()
    if unknown_tables:
        perm_err = await check_physical_table_refs_permission(
            session,
            refs=unknown_tables,
            user_id=user_id,
            is_admin=is_admin,
            dialect=dialect,
        )
        if perm_err:
            return perm_err
        extra_allowed = set(unknown_tables.keys())
        if binding:
            db_bindings = await resolve_table_bindings_from_db(
                session,
                [display for display in unknown_tables.values()],
                preferred_dataset_name=binding.primary_dataset_name,
                preferred_datasets=_binding_preferred_datasets(binding) or None,
                hints=_binding_resolution_hints(binding),
            )
            for key, table_binding in db_bindings.items():
                current = binding.tables.get(key)
                if current is None:
                    binding.tables[key] = table_binding
                else:
                    if not current.dataset_name:
                        current.dataset_name = table_binding.dataset_name
                    if not current.data_source:
                        current.data_source = table_binding.data_source
                    if not current.columns:
                        current.columns = list(table_binding.columns)

    err = build_sql_schema_preflight_error(
        sql,
        table_columns,
        extra_allowed_tables=extra_allowed,
    )
    if binding and not err:
        binding.sql = sql
        binding.preflight_validated = True
    return err or ""


async def enforce_dataset_table_scope(
    session: AsyncSession,
    *,
    refs: dict[str, str],
    dataset_name: str,
    ds: Any,
    binding: SqlQueryBinding | None = None,
) -> str | None:
    if not refs or ds is None:
        return None

    t_stmt = select(MetaTable.physical_name, MetaTable.term).where(
        MetaTable.dataset_id == ds.id,
        MetaTable.status == 1,
    )
    t_res = await session.execute(t_stmt)
    rows = list(t_res.all())
    dataset_tables = {str(p).lower() for p, _ in rows if p}
    term_to_physical = {
        str(t).lower(): str(p) for p, t in rows if p and t and str(t).strip()
    }
    dataset_alias_lowers = {
        str(x).lower()
        for x in (getattr(ds, "display_name", None), getattr(ds, "name", None))
        if x
    }

    for lk, display in refs.items():
        table_binding = binding.get_table(lk) if binding else None
        if table_binding and table_binding.dataset_name:
            if table_binding.dataset_name != dataset_name:
                return _cross_dataset_scope_error(display, dataset_name)
            continue

        if lk in dataset_tables:
            continue
        if lk in term_to_physical:
            return (
                f"[Validation Failed] '{display}' 是业务术语，并非物理表名，不能直接用于 SQL。"
                f"请改用 get_dataset_schema 返回的物理表名 '{term_to_physical[lk]}'。"
            )
        if lk in dataset_alias_lowers:
            return (
                f"[Validation Failed] '{display}' 是数据集名称，并非物理表名，不能直接用于 SQL 的 FROM/JOIN。"
                f"请使用 get_dataset_schema 返回的 table_name（物理表名）进行查询。"
            )
        return _cross_dataset_scope_error(display, dataset_name)

    return None


async def enforce_physical_table_permissions(
    session: AsyncSession,
    *,
    refs: dict[str, str],
    dialect: str,
    user_id_eff: int | None,
    is_admin_eff: bool,
    user_identity_label: str | None = None,
    binding: SqlQueryBinding | None = None,
) -> str | None:
    from app.services.metadata_service import MetadataService
    from app.services.permission_service import PermissionService
    from app.services.sql_query_execution_service import (
        _fetch_all_registered_physical_lowers,
        _fetch_allowed_physical_lowers_for_user,
        _is_exempt_builtin_table,
    )

    if is_admin_eff or not refs:
        return None
    if user_id_eff is None:
        return "[Permission Denied] 无法校验表权限：缺少用户身份"

    filtered_refs: dict[str, str] = {}
    for lk, display in refs.items():
        if _is_exempt_builtin_table(lk, dialect):
            continue
        filtered_refs[lk] = display
    if not filtered_refs:
        return None

    permission_service = PermissionService(session)
    dataset_permission_cache: dict[str, bool] = {}
    allowed_lower: set[str] | None = None
    registered_lower: set[str] | None = None

    async def has_dataset_permission(dataset_name: str) -> bool:
        if dataset_name in dataset_permission_cache:
            return dataset_permission_cache[dataset_name]
        dataset = await MetadataService.get_dataset_by_name(session, dataset_name)
        allowed = False
        if dataset:
            allowed = await permission_service.check_permission(
                int(user_id_eff),
                "metadata",
                str(dataset.id),
            )
        dataset_permission_cache[dataset_name] = allowed
        return allowed

    for lk, display in filtered_refs.items():
        table_binding = binding.get_table(lk) if binding else None
        if table_binding and table_binding.dataset_name:
            if await has_dataset_permission(table_binding.dataset_name):
                continue

        if allowed_lower is None:
            allowed_lower = await _fetch_allowed_physical_lowers_for_user(session, user_id_eff)
        if lk in allowed_lower:
            continue

        subject = f"{user_identity_label} " if user_identity_label else ""
        if registered_lower is None:
            registered_lower = await _fetch_all_registered_physical_lowers(session)
        if lk in registered_lower:
            return f"[Permission Denied] {subject}无权访问表 '{display}'"
        return (
            f"[Validation Failed] {subject}物理表 '{display}' 未在元数据中注册或不存在。"
            "请使用 get_dataset_schema 工具确认当前数据集的可查询表，严禁凭空猜测表名！"
        )

    return None


async def prepare_binding_for_execution(
    session: AsyncSession,
    binding: SqlQueryBinding | None,
    *,
    sql: str,
    dialect: str,
    dataset_name: str | None,
) -> SqlQueryBinding | None:
    if binding is None:
        return None
    if dataset_name and not binding.primary_dataset_name:
        binding.primary_dataset_name = dataset_name
    if sql.strip():
        merge_sql_tables_into_binding(binding, sql=sql, dialect=dialect)
        binding.sql = sql
    return await enrich_binding_from_db(session, binding)


def format_table_dataset_binding_block(binding: SqlQueryBinding | None) -> str:
    """生成联邦 plan prompt 中的表 → 数据集硬约束块。"""
    if binding is None or not binding.tables:
        return ""

    lines = [
        "【物理表与数据集绑定（硬约束，必须严格遵守）】",
        "以下映射来自本轮 Schema 与元数据反查，编写每个 <sub_query> 时：",
        "- `dataset_name` 必须与表中「所属数据集」完全一致；",
        "- 子查询 SQL 只能引用该数据集下列出的物理表；",
        "- 禁止把不同数据集的表写到同一个 sub_query，也禁止写错 dataset_name。",
    ]
    for table_binding in sorted(binding.tables.values(), key=lambda item: item.physical_name):
        if not table_binding.physical_name:
            continue
        dataset = table_binding.dataset_name or "（未知，请按 Schema 中 dataset: 填写）"
        ds_type = f", 数据源={table_binding.data_source}" if table_binding.data_source else ""
        column_preview = ", ".join(column.name for column in table_binding.columns[:12])
        if len(table_binding.columns) > 12:
            column_preview += f", ... 共 {len(table_binding.columns)} 列"
        column_part = f"；可用字段: {column_preview}" if column_preview else ""
        lines.append(f"- 表 `{table_binding.physical_name}` → 数据集 `{dataset}`{ds_type}{column_part}")
    return "\n".join(lines)


def apply_subquery_dataset_bindings(
    sub_queries: list[dict[str, Any]],
    binding: SqlQueryBinding | None,
    *,
    default_dialect: str = "clickhouse",
) -> list[dict[str, Any]]:
    """根据 binding 修正 LLM 计划里 sub_query 的 dataset_name。"""
    if not binding or not sub_queries:
        return sub_queries

    corrected: list[dict[str, Any]] = []
    for sub_query in sub_queries:
        item = dict(sub_query)
        sql = str(item.get("sql") or "")
        dialect = default_dialect
        declared = str(item.get("dataset_name") or "").strip()
        if declared:
            from app.services.sql_query_execution_service import dialect_from_data_source

            for table_binding in binding.tables.values():
                if table_binding.dataset_name == declared and table_binding.data_source:
                    dialect = dialect_from_data_source(table_binding.data_source)
                    break

        _, refs = _extract_physical_table_refs(sql, dialect)
        datasets: set[str] = set()
        for lk in refs:
            table_binding = binding.get_table(lk)
            if table_binding and table_binding.dataset_name:
                datasets.add(table_binding.dataset_name)

        if len(datasets) == 1:
            resolved = next(iter(datasets))
            if resolved and resolved != declared:
                item["dataset_name"] = resolved
        corrected.append(item)
    return corrected
