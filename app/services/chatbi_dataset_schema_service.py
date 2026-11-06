"""
ChatBI 数据集 Schema 获取：与工具 `get_dataset_schema` 共用逻辑，供 HTTP 等调用复用。
"""
import logging
import re
from typing import Any, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metadata import MetaDataset, MetaRelationship, MetaTable
from app.services.auth_service import AuthService
from app.services.config_service import ConfigService
from app.services.metadata_service import MetadataService
from app.services.schema_chunk_format import format_schema_chunk, format_schema_hits

logger = logging.getLogger(__name__)


def _schema_dataset_codes(content: Any) -> set[str]:
    return {
        match.strip().casefold()
        for match in re.findall(r"^\s*dataset:\s*([^\s#]+)", str(content or ""), re.MULTILINE)
        if match.strip()
    }


def filter_schema_hits_to_scope(
    hits: list[dict[str, Any]],
    *,
    allowed_metadata_ids: set[str],
    allowed_dataset_names: set[str],
    allowed_rag_ids: set[str],
) -> list[dict[str, Any]]:
    """仅保留可证明属于已授权 MetaDataset 范围的 Schema 命中。

    Local 命中通常带 MetaDataset.id；RAGFlow 命中可能带 rag_dataset_id，或仅能从
    Schema 内容里的 ``dataset: <MetaDataset.name>`` 识别归属。任何无法证明归属的
    命中都不能进入模型上下文。
    """
    allowed_metadata_ids = {str(value).strip() for value in allowed_metadata_ids if str(value).strip()}
    allowed_dataset_names = {str(value).strip().casefold() for value in allowed_dataset_names if str(value).strip()}
    allowed_rag_ids = {str(value).strip() for value in allowed_rag_ids if str(value).strip()}
    accepted: list[dict[str, Any]] = []
    for hit in hits:
        checks: list[bool] = []
        metadata_id = str(hit.get("metadata_dataset_id") or hit.get("dataset_id") or "").strip()
        if metadata_id:
            checks.append(metadata_id in allowed_metadata_ids or metadata_id in allowed_rag_ids)
        rag_id = str(hit.get("rag_dataset_id") or hit.get("ragflow_dataset_id") or "").strip()
        if rag_id:
            checks.append(rag_id in allowed_rag_ids)
        dataset_codes = _schema_dataset_codes(hit.get("content"))
        if dataset_codes:
            checks.append(dataset_codes.issubset(allowed_dataset_names))
        if checks and all(checks):
            accepted.append(hit)
    return accepted


def _metric_physical_names(metric: "dict[str, Any]") -> Optional[set[str]]:
    """指标计算逻辑里引用的物理表（lower）；解析不出来返回 None（保守保留）。"""
    sql = str((metric or {}).get("sql") or "").strip()
    if not sql:
        return None
    try:
        from app.services.sql_query_execution_service import (
            extract_physical_table_refs_from_select_sql,
        )

        err, refs = extract_physical_table_refs_from_select_sql(sql, "mysql")
    except Exception:  # noqa: BLE001 - 解析失败宁可保留，由执行层兜底拦截
        return None
    if err or not refs:
        return None
    return set(refs.keys())


def _metric_sql_columns(sql: str) -> "tuple[dict[str, set[str]], set[str]] | None":
    """指标 SQL 里引用的列：``(限定表→列名, 未限定列名)``，全部小写。

    解析失败返回 ``None``（保守保留，交给执行层兜底）。
    """
    text = str(sql or "").strip()
    if not text:
        return None
    try:
        import sqlglot
        from sqlglot import exp

        from app.services.sql_query_execution_service import _physical_fragment_from_table

        expression = sqlglot.parse_one(text, read="mysql")
    except Exception:  # noqa: BLE001
        return None

    alias_to_physical: dict[str, str] = {}
    for table in expression.find_all(exp.Table):
        physical = _physical_fragment_from_table(table)
        if not physical:
            continue
        key = str(table.alias or "").strip().lower() or physical.lower()
        alias_to_physical[key] = physical.lower()

    qualified: dict[str, set[str]] = {}
    unqualified: set[str] = set()
    for column in expression.find_all(exp.Column):
        name = str(column.name or "").strip().lower()
        if not name:
            continue
        hint = str(column.table or "").strip().lower()
        if hint:
            physical = alias_to_physical.get(hint, hint)
            qualified.setdefault(physical, set()).add(name)
        else:
            unqualified.add(name)
    return qualified, unqualified


def _metric_violates_column_scope(
    metric: "dict[str, Any]",
    column_rules_by_physical: "dict[str, tuple[Any, ...]]",
) -> bool:
    """指标 SQL 是否用到了被字段级权限挡住的列。

    指标块的 ``sql`` 带真实字段名，原样给模型等于把「这个敏感字段叫什么、口径怎么算」
    一起交出去；模型随后照着写注定被拦的 SQL，白跑一轮。
    """
    if not column_rules_by_physical:
        return False
    parsed = _metric_sql_columns(str((metric or {}).get("sql") or ""))
    if parsed is None:
        return False
    qualified, unqualified = parsed
    for physical, rules in column_rules_by_physical.items():
        if not rules:
            continue
        columns = set(qualified.get(physical) or ())
        # 未限定列名的归属无法确定：只要该表在指标里出现过，就按可能命中处理（保守剔除）。
        appears = physical in qualified or bool(unqualified)
        if not appears:
            continue
        columns |= unqualified
        for rule in rules:
            mode = str(getattr(rule, "mode", "") or "")
            names = set(getattr(rule, "names", ()) or ())
            if mode == "include" and (columns - names):
                return True
            if mode == "exclude" and (columns & names):
                return True
    return False


def _filter_metrics_chunk_by_table_scope(
    content: str,
    allowed: "set[str] | None",
    column_rules_by_physical: "dict[str, tuple[Any, ...]] | None" = None,
) -> Optional[str]:
    """按表级白名单过滤指标块，逐指标判定。

    指标块的 ``sql`` 字段是**完整 SQL**（`SELECT count(*) ... FROM ai_agent_users`），
    直接原样返回等于把无权表的表名、字段名和口径一起交给模型；模型随后会照着它生成
    一条注定被 ``[Permission Denied]`` 拦下的 SQL，白跑好几轮。
    这里逐条剔除「计算逻辑引用了白名单外表」的指标，全部被剔除时整块丢弃。

    返回 ``None`` 表示整块丢弃；返回原文表示无需改动。
    """
    text = str(content or "")
    if not text.strip():
        return content

    import yaml

    try:
        data = yaml.safe_load(text)
    except Exception:  # noqa: BLE001 - YAML 异常时保留原文，交给执行层兜底
        return content
    if not isinstance(data, dict) or not isinstance(data.get("metrics"), list):
        return content

    metrics = data["metrics"]
    kept = []
    for metric in metrics:
        if not isinstance(metric, dict):
            kept.append(metric)
            continue
        names = _metric_physical_names(metric)
        # allowed 为 None = 不限表（只有字段级收窄时是这种形态）
        if names is not None and allowed is not None and not names.issubset(allowed):
            continue
        # 表可见 ≠ 字段可见：指标 SQL 里引用了被挡住的列也要整条剔除
        if _metric_violates_column_scope(metric, column_rules_by_physical or {}):
            continue
        kept.append(metric)

    if len(kept) == len(metrics):
        return content
    if not kept:
        return None

    # 保留文件头的说明注释（yaml.dump 不会输出它们）
    header = [line for line in text.splitlines() if line.startswith("#")]
    data["metrics"] = kept
    body = yaml.dump(data, allow_unicode=True, sort_keys=False)
    return ("\n".join(header) + "\n" + body) if header else body


def _chunk_table_name(content: str) -> Optional[str]:
    """从表 chunk 的 YAML 里取物理表名（lower）；指标块没有 table_name，返回 None。"""
    import re

    match = re.search(r"^table_name:\s*['\"]?([A-Za-z0-9_.$]+)", str(content or ""), re.MULTILINE)
    return match.group(1).lower() if match else None


def _append_column_scope_notice(schema_text: str, hidden_count: int) -> str:
    """字段级收窄的说明：只给数量，不给被隐藏的列名（列名本身也是敏感信息）。"""
    if hidden_count <= 0:
        return schema_text
    notice = (
        f"\n\n# 提示：另有 {hidden_count} 个字段不在你的授权范围内，已从上述表结构中省略。\n"
        "# 你只能查询这里列出的字段；引用未列出的字段会被权限门禁拒绝，\n"
        "# 也不要猜测字段名或改用 SELECT * 绕过。\n"
    )
    return f"{schema_text}{notice}"


def _trim_chunk_columns(
    content: str,
    rules: "tuple[Any, ...]",
) -> tuple[str, list[str]]:
    """按字段级授权裁剪表 chunk 的 ``columns``。

    只删「不可见的列」，其余结构原样保留（comments/relationships 都不动）——
    模型看到的就是它能查的，不会去 SELECT 一个被排除的字段再被门禁拒掉。
    返回 (新内容, 被隐藏的列名)：列名只用于计数，不回传给模型。
    """
    text = str(content or "")
    if not text.strip() or not rules:
        return text, []

    import yaml

    try:
        data = yaml.safe_load(text)
    except Exception:  # noqa: BLE001 - YAML 解析失败就不裁剪，交给执行层兜底
        return text, []
    if not isinstance(data, dict) or not isinstance(data.get("columns"), list):
        return text, []

    all_names = [
        str(col.get("name") or "").lower()
        for col in data["columns"]
        if isinstance(col, dict) and col.get("name")
    ]
    visible = set(all_names)
    for rule in rules:
        visible = rule.apply(visible)

    kept = [
        col
        for col in data["columns"]
        if not isinstance(col, dict)
        or not col.get("name")
        or str(col.get("name")).lower() in visible
    ]
    hidden = [name for name in all_names if name not in visible]
    if not hidden:
        return text, []

    data["columns"] = kept
    header = [line for line in text.splitlines() if line.startswith("#")]
    body = yaml.dump(data, allow_unicode=True, sort_keys=False)
    return ("\n".join(header) + "\n" + body) if header else body, hidden


def _filter_hits_by_table_scope(
    items: list[dict[str, Any]],
    *,
    authorized_datasets: list[Any],
    allowed_table_names_by_dataset: dict[int, set[str] | None],
    dropped_out: list[str] | None = None,
    column_rules_by_table: "dict[tuple[int, str], tuple[Any, ...]] | None" = None,
) -> list[dict[str, Any]]:
    """在数据集级过滤之后，再按表级白名单剔除命中的 schema 块。

    表块必须命中白名单，否则模型会看到并尝试查询被收窄掉的表；
    指标块没有 ``table_name``，但它的 ``sql`` 字段是完整 SQL，按每条指标引用的表过滤。
    归属数据集识别不出来的命中（索引缺字段等）用「各数据集白名单的并集」兜底，
    不能因为「查不到归属」就整块放行。
    """
    hit_list = list(items)
    if not allowed_table_names_by_dataset:
        return hit_list

    from app.services.schema_chunk_format import infer_schema_chunk_meta

    accepted: list[dict[str, Any]] = []
    for hit in hit_list:
        dataset_id = _metadata_dataset_id_from_hit(hit, authorized_datasets)
        allowed: set[str] | None = None
        if dataset_id:
            try:
                allowed = allowed_table_names_by_dataset.get(int(dataset_id))
            except (TypeError, ValueError):
                allowed = None
        else:
            # 归属不明：用所有数据集白名单的并集兜底。并集为空说明全是「不限表」，
            # 此时才真正放行；否则按并集判定，避免「查不到归属」变成一条绕过路径。
            union: set[str] = set()
            for allowed_set in allowed_table_names_by_dataset.values():
                if allowed_set:
                    union |= allowed_set
            if union:
                allowed = union
        if allowed is None:
            accepted.append(hit)
            continue
        _, table_name, _ = infer_schema_chunk_meta(
            str(hit.get("content") or ""), str(hit.get("doc_name") or "")
        )
        if table_name and table_name.lower() not in allowed:
            # 记下来只用于「有几张表被挡了」的计数与排查日志，绝不回传给模型：
            # 表名本身也可能是敏感信息。
            if dropped_out is not None:
                dropped_out.append(str(table_name))
            continue
        if not table_name:
            # 指标块：没有 table_name，但它的 sql 字段是完整 SQL，同样会泄露表名与字段名。
            dataset_rules = {
                physical: rules
                for (rule_dataset_id, physical), rules in (column_rules_by_table or {}).items()
                if dataset_id and int(rule_dataset_id) == int(dataset_id)
            }
            filtered_content = _filter_metrics_chunk_by_table_scope(
                str(hit.get("content") or ""), allowed, dataset_rules
            )
            if filtered_content is None:
                if dropped_out is not None:
                    dropped_out.append("<metrics>")
                continue
            if filtered_content != hit.get("content"):
                hit = {**hit, "content": filtered_content}
        accepted.append(hit)
    return accepted


def _append_table_scope_notice(schema_text: str, dropped_count: int) -> str:
    """被表级白名单挡掉的表要告诉模型「有表但没权限」，而不是让它以为「没有这张表」。

    静默剔除的副作用是模型会回答「数据集里没有用户表」，或者凭记忆编一个表名再试。
    这里只给数量不给表名，并明确要求它照实说明权限问题。
    """
    if dropped_count <= 0 or not str(schema_text or "").strip():
        return schema_text
    return schema_text + (
        f"\n\n（另有 {dropped_count} 张与本次检索相关、但不在你授权范围内的表未展示。"
        "如果用户问到的表没有出现在上面的 Schema 中，通常是权限范围不含它："
        "请直接说明「该表不在你的授权范围内，需要管理员在「权限管理」中授权」，"
        "不要猜测表名，也不要用其它数据集里的同名表绕过。）"
    )


def _metadata_dataset_id_from_hit(hit: dict[str, Any], authorized_datasets: list[Any]) -> str | None:
    """把 local/RAG 命中的来源 ID 统一映射为 MetaDataset.id。"""
    metadata_id = str(hit.get("metadata_dataset_id") or hit.get("dataset_id") or "").strip()
    allowed_metadata_ids = {
        str(getattr(dataset, "id", "") or "").strip()
        for dataset in authorized_datasets
        if str(getattr(dataset, "id", "") or "").strip()
    }
    if metadata_id in allowed_metadata_ids:
        return metadata_id
    rag_id = str(hit.get("rag_dataset_id") or hit.get("ragflow_dataset_id") or "").strip()
    if rag_id:
        for dataset in authorized_datasets:
            if rag_id == str(getattr(dataset, "rag_dataset_id", "") or "").strip():
                dataset_id = str(getattr(dataset, "id", "") or "").strip()
                return dataset_id or None
    if metadata_id:
        for dataset in authorized_datasets:
            if metadata_id == str(getattr(dataset, "rag_dataset_id", "") or "").strip():
                dataset_id = str(getattr(dataset, "id", "") or "").strip()
                return dataset_id or None
    return None


def _set_metadata_scope_debug(
    *,
    authorized_datasets: list[Any],
    effective_datasets: list[Any],
    provider: str,
) -> None:
    from app.core.context import get_current_agent_context
    from app.core.context import get_execution_option

    ctx = get_current_agent_context()
    if ctx is None:
        return
    request_scope = get_execution_option("metadata_dataset_scope", {}) or {}

    def serialize(items: list[Any]) -> list[dict[str, str]]:
        return [
            {
                "id": str(getattr(item, "id", "") or ""),
                "name": str(getattr(item, "name", "") or getattr(item, "dataset_name", "") or ""),
                "display_name": str(getattr(item, "display_name", "") or ""),
                "rag_dataset_id": str(getattr(item, "rag_dataset_id", "") or ""),
            }
            for item in items
        ]

    ctx.metadata_dataset_scope_debug = {
        "source": request_scope.get("source", "user"),
        "request_ids": list(request_scope.get("request_ids") or []),
        "session": list(request_scope.get("session") or []),
        "authorized": serialize(authorized_datasets),
        "effective": serialize(effective_datasets),
        "provider": str(provider or "local"),
    }


async def fetch_dataset_schema_core(
    session: AsyncSession,
    *,
    keywords: Optional[str] = None,
    user_id: Optional[int] = None,
    is_admin: bool = False,
    api_key: Optional[str] = None,
    authorized_dataset_ids: Optional[list[int]] = None,
) -> str:
    """
    按当前用户权限获取数据集 Schema 文本（local/ragflow 均优先返回检索片段）。

    Args:
        keywords: 元数据检索 query；local 模式下用于本地向量检索，向量异常时用于 MySQL LIKE 兜底。
        user_id: 用户 ID；可与 api_key 二选一补全（工具场景）。
        is_admin: 是否平台管理员。
        api_key: 无 user_id 时用于解析用户身份。
    """
    try:
        return await _fetch_dataset_schema_impl(
            session,
            keywords=keywords,
            user_id=user_id,
            is_admin=is_admin,
            api_key=api_key,
            authorized_dataset_ids=authorized_dataset_ids,
        )
    except Exception as e:
        logger.error("[fetch_dataset_schema_core] Schema retrieval failed: %s", e, exc_info=True)
        return f"[Tool Error] Failed to retrieve metadata: {str(e)}"


async def _format_fallback_dataset_chunks(
    session: AsyncSession,
    dataset_id: int,
    start_index: int,
    *,
    allowed_table_names: set[str] | None = None,
    column_rules_by_table: "dict[tuple[int, str], tuple[Any, ...]] | None" = None,
    hidden_columns_out: "list[str] | None" = None,
) -> tuple[list[str], int]:
    """MySQL 兜底：导出与向量索引一致的单表/指标 YAML 块。

    ``allowed_table_names`` 为 ``None`` 表示不限表；空集合表示该数据集一张表都不可见
    （必须显式传空列表，不能让 ``build_dataset_schema_chunk_contents`` 回退成不过滤）。
    ``column_rules_by_table`` 非空时按字段级授权裁剪每张表的 ``columns``。
    """
    table_names = None if allowed_table_names is None else sorted(allowed_table_names)
    raw_chunks = await MetadataService.build_dataset_schema_chunk_contents(
        session, dataset_id, table_names=table_names
    )
    formatted: list[str] = []
    index = start_index
    # 本数据集的「物理表名 → 列规则」，供指标块逐条判定（表可见 ≠ 字段可见）
    dataset_column_rules = {
        physical: rules
        for (rule_dataset_id, physical), rules in (column_rules_by_table or {}).items()
        if int(rule_dataset_id) == int(dataset_id)
    }
    for content in raw_chunks:
        if "metrics_scope:" in str(content):
            # 兜底路径的指标块也是完整 YAML（含每条指标的 sql），表级与字段级都要逐条过滤
            filtered = _filter_metrics_chunk_by_table_scope(
                content,
                set(allowed_table_names) if allowed_table_names is not None else None,
                dataset_column_rules,
            )
            if filtered is None:
                continue
            content = filtered
        elif column_rules_by_table:
            table_name = _chunk_table_name(content)
            rules = column_rules_by_table.get((int(dataset_id), table_name)) if table_name else None
            if rules:
                content, hidden = _trim_chunk_columns(content, rules)
                if hidden and hidden_columns_out is not None:
                    hidden_columns_out.extend(hidden)
        piece = format_schema_chunk(index, content, dataset_id=dataset_id)
        if piece:
            formatted.append(piece)
            index += 1
    return formatted, index


async def _fetch_dataset_schema_impl(
    session: AsyncSession,
    *,
    keywords: Optional[str] = None,
    user_id: Optional[int] = None,
    is_admin: bool = False,
    api_key: Optional[str] = None,
    authorized_dataset_ids: Optional[list[int]] = None,
) -> str:
    user_id_eff = user_id
    is_admin_eff = bool(is_admin)

    if user_id_eff is None and api_key:
        u_info = await AuthService.verify_api_key(api_key, session)
        if u_info:
            user_id_eff = int(u_info["user_id"])
            if u_info.get("role") == "admin":
                is_admin_eff = True

    provider = await ConfigService.get("metadata_provider", default="local")
    authorized_datasets = await MetadataService.search_datasets(
        session,
        query=None,
        user_id=user_id_eff,
        is_admin=is_admin_eff,
        status=1,
    )

    if not authorized_datasets:
        _set_metadata_scope_debug(
            authorized_datasets=[],
            effective_datasets=[],
            provider=str(provider),
        )
        return "No authorized datasets found. You do not have permission to view any data."

    all_authorized_datasets = list(authorized_datasets)
    configured_dataset_ids = _normalize_authorized_dataset_ids(authorized_dataset_ids)
    if configured_dataset_ids is None:
        # 工具参数没绑定（智能体没配数据集）时，还有一层「本轮请求 / 会话固定」。
        # 这层之前只进日志不参与过滤，等于「会话固定了数据集 1」也能检索到别的数据集。
        from app.services.ai.metadata_dataset_scope import resolve_mounted_dataset_scope_ids

        mounted_ids = resolve_mounted_dataset_scope_ids()
        if mounted_ids:
            configured_dataset_ids = _normalize_authorized_dataset_ids(mounted_ids)
    if configured_dataset_ids is not None:
        authorized_datasets = [
            ds for ds in authorized_datasets
            if str(getattr(ds, "id", "")).strip() in configured_dataset_ids
            or str(getattr(ds, "name", "")).strip() in configured_dataset_ids
            or str(getattr(ds, "dataset_name", "")).strip() in configured_dataset_ids
            or str(getattr(ds, "rag_dataset_id", "")).strip() in configured_dataset_ids
        ]
        if not authorized_datasets:
            _set_metadata_scope_debug(
                authorized_datasets=all_authorized_datasets,
                effective_datasets=[],
                provider=str(provider),
            )
            # 区分「没权限」和「参数里根本没有能识别的数据集」：后者多半是模型传了
            # 不存在的 ID/名称，把请求范围回显出去，它才知道要改参数而不是继续硬试。
            return (
                "No configured metadata datasets are authorized for current user. "
                f"Requested scope: {sorted(configured_dataset_ids)}"
            )

    _set_metadata_scope_debug(
        authorized_datasets=all_authorized_datasets,
        effective_datasets=authorized_datasets,
        provider=str(provider),
    )
    logger.info("[fetch_dataset_schema_core] provider=%s", str(provider).upper())

    # 表级授权：把每个数据集的白名单翻译成物理表名（lower）。
    # None = 不限表（历史行为）；空集合 = 数据集内一张表都不可见（fail-closed）。
    allowed_table_names_by_dataset: dict[int, set[str] | None] = {}
    # 字段级授权：(dataset_id, 物理表名 lower) -> 列规则。空 = 没有字段级收窄。
    column_rules_by_table: dict[tuple[int, str], tuple[Any, ...]] = {}
    if not is_admin_eff and user_id_eff is not None:
        try:
            from app.services.dataset_scope_service import (
                allowed_table_names_by_dataset as _resolve_allowed_table_names,
                column_rules_by_table_name as _resolve_column_rules,
                resolve_user_dataset_scopes,
            )

            dataset_scope_map = await resolve_user_dataset_scopes(session, user_id_eff)
            allowed_table_names_by_dataset = await _resolve_allowed_table_names(session, dataset_scope_map)
            column_rules_by_table = await _resolve_column_rules(session, dataset_scope_map)
        except Exception as ex:  # noqa: BLE001 - 表级解析失败时退回数据集级，避免整页不可用
            logger.warning("[fetch_dataset_schema_core] 解析表级授权失败，退回数据集级: %s", ex)
            allowed_table_names_by_dataset = {}
            column_rules_by_table = {}

    allowed_metadata_ids = {str(getattr(ds, "id", "")).strip() for ds in authorized_datasets}
    allowed_dataset_names = {
        str(getattr(ds, "name", "") or getattr(ds, "dataset_name", "")).strip().casefold()
        for ds in authorized_datasets
    }
    allowed_rag_ids = {str(getattr(ds, "rag_dataset_id", "")).strip() for ds in authorized_datasets}

    # --- RAGFlow Mode ---
    if provider == "ragflow":
        from app.services.ai.ragflow_client import RagFlowClient
        from app.services.metadata_rag_service import MetadataRagService, MetadataServiceUnavailableError

        rag_ids = [ds.rag_dataset_id for ds in authorized_datasets if ds.rag_dataset_id]

        if not rag_ids:
            return "Authorized datasets found, but none are synced to RAG knowledge base."

        # If no keywords provided, return a directory of datasets instead of searching
        if not keywords:
            directory = ["Available Datasets (Please provide keywords to search specific tables):"]
            for ds in authorized_datasets:
                if ds.rag_dataset_id:
                    directory.append(f"- {ds.display_name or ds.name} (Source: {ds.data_source or 'clickhouse'})")
                    if ds.description:
                        directory.append(f"  Description: {ds.description}")
            return "\n".join(directory)

        query = keywords
        threshold = float(await ConfigService.get("ragflow_similarity_threshold") or 0.2)
        weight = float(await ConfigService.get("ragflow_vector_weight") or 0.3)
        top_k = int(await ConfigService.get("ragflow_metadata_top_k") or 5)

        logger.info(
            "[fetch_dataset_schema_core] ragflow top_k=%s threshold=%s weight=%s",
            top_k,
            threshold,
            weight,
        )

        client = RagFlowClient()
        try:
            chunks, trace_logs = await MetadataRagService.retrieve_with_retry(
                client,
                query,
                rag_ids,
                top_k=top_k,
                threshold=threshold,
                weight=weight,
            )
        except MetadataServiceUnavailableError as e:
            logger.error("[fetch_dataset_schema_core] Metadata service unavailable: %s", e)
            return MetadataRagService.unavailable_hint(str(e))

        if not chunks:
            return f"No relevant schema info found for '{query}'.\nDebug Logs: {'; '.join(trace_logs)}"

        dropped_table_hits = []
        hidden_column_hits: list[str] = []
        filtered_hits = []
        for chunk in _filter_hits_by_table_scope(
            filter_schema_hits_to_scope(
                chunks,
                allowed_metadata_ids=allowed_metadata_ids,
                allowed_dataset_names=allowed_dataset_names,
                allowed_rag_ids=allowed_rag_ids,
            ),
            authorized_datasets=authorized_datasets,
            allowed_table_names_by_dataset=allowed_table_names_by_dataset,
            dropped_out=dropped_table_hits,
            column_rules_by_table=column_rules_by_table,
        ):
            try:
                similarity = float(chunk.get("similarity", 0.0) or 0.0)
            except (TypeError, ValueError):
                similarity = 0.0
            if similarity < threshold:
                continue
            content = str(chunk.get("content") or "").strip()
            if not content:
                continue
            _hit_dataset_id = _metadata_dataset_id_from_hit(chunk, authorized_datasets)
            if column_rules_by_table:
                _tbl = _chunk_table_name(content)
                if _tbl and _hit_dataset_id:
                    _rules = column_rules_by_table.get((int(_hit_dataset_id), _tbl))
                    if _rules:
                        content, _hidden = _trim_chunk_columns(content, _rules)
                        if _hidden:
                            hidden_column_hits.extend(_hidden)
            filtered_hits.append({
                "content": content,
                "similarity": similarity,
                "doc_name": chunk.get("doc_name", "unknown"),
                "metadata_dataset_id": _hit_dataset_id,
                "rag_dataset_id": (
                    chunk.get("rag_dataset_id")
                    or chunk.get("ragflow_dataset_id")
                ),
            })

        if filtered_hits:
            schema_text = format_schema_hits(filtered_hits)
            # 跨数据集关联补全
            schema_text = await _enrich_with_cross_dataset_schema(
                session,
                schema_text,
                user_id=user_id_eff,
                is_admin=is_admin_eff,
                authorized_dataset_ids=authorized_dataset_ids,
                allowed_table_names_by_dataset=allowed_table_names_by_dataset,
            )
            schema_text = _append_column_scope_notice(schema_text, len(set(hidden_column_hits)))
            return _append_table_scope_notice(schema_text, len(dropped_table_hits))
        return f"No relevant schema info found for '{query}'.\nDebug Logs: {'; '.join(trace_logs)}"

    # --- Local Mode (Default) ---
    query = (keywords or "").strip()
    threshold = float(await ConfigService.get("ragflow_similarity_threshold") or 0.2)
    top_k = int(await ConfigService.get("ragflow_metadata_top_k") or 5)

    schema_text: str | None = None  # 先收集到变量，再统一做跨数据集补全
    dropped_table_hits: list[str] = []  # 被表级白名单挡掉的表（只计数，不回传表名）

    if query:
        try:
            from app.services.ai.embedding_client import EmbeddingClient
            from app.services.ai.metadata_index_service import MetadataIndexService

            authorized_ids = (
                [ds.id for ds in authorized_datasets if getattr(ds, "id", None) is not None]
                if configured_dataset_ids is not None or not is_admin_eff
                else None
            )
            dropped_table_hits = []
            query_embedding = await EmbeddingClient.embed_text(query, use_global=True)
            redis_results = await MetadataIndexService.search_knn(
                query_embedding=query_embedding,
                authorized_dataset_ids=authorized_ids,
                top_k=top_k,
            )

            filtered_hits = []
            for item in _filter_hits_by_table_scope(
                filter_schema_hits_to_scope(
                    redis_results,
                    allowed_metadata_ids=allowed_metadata_ids,
                    allowed_dataset_names=allowed_dataset_names,
                    allowed_rag_ids=allowed_rag_ids,
                ),
                authorized_datasets=authorized_datasets,
                allowed_table_names_by_dataset=allowed_table_names_by_dataset,
                dropped_out=dropped_table_hits,
            ):
                try:
                    similarity = float(item.get("similarity", 0.0) or 0.0)
                except (TypeError, ValueError):
                    similarity = 0.0
                if similarity < threshold:
                    continue
                content = str(item.get("content") or "").strip()
                if not content:
                    continue
                filtered_hits.append({
                    "content": content,
                    "similarity": similarity,
                    "doc_name": item.get("doc_name", "unknown"),
                    "metadata_dataset_id": (
                        _metadata_dataset_id_from_hit(item, authorized_datasets)
                    ),
                    "rag_dataset_id": (
                        item.get("rag_dataset_id")
                        or item.get("ragflow_dataset_id")
                    ),
                })

            if filtered_hits:
                schema_text = format_schema_hits(filtered_hits)
            else:
                return (
                    f"No relevant schema info found for '{query}'.\n"
                    f"Debug Logs: Redis Vector Search completed. Found {len(redis_results)} raw items; "
                    f"no hit above threshold {threshold}"
                )
        except Exception as ex:
            logger.warning(
                "[fetch_dataset_schema_core] Local Redis vector search failed: %s. Falling back to MySQL keyword search.",
                ex,
            )

    if schema_text is None:
        if not query:
            return "No relevant schema info found for ''.\nDebug Logs: local metadata query is empty"

        found_datasets = await MetadataService.search_datasets(
            session,
            query=query,
            user_id=user_id_eff,
            is_admin=is_admin_eff,
            status=1,
        )
        if not found_datasets:
            return f"No relevant schema info found for '{query}'.\nDebug Logs: local MySQL keyword search returned 0 datasets"
        if configured_dataset_ids is not None:
            found_datasets = [
                ds for ds in found_datasets
                if str(getattr(ds, "id", "")).strip() in configured_dataset_ids
            ]
            if not found_datasets:
                return (
                    f"No relevant schema info found for '{query}'.\n"
                    "Debug Logs: local MySQL keyword search returned 0 datasets in configured scope"
                )

        results: list[str] = []
        next_index = 1
        # 兜底路径同样要把「被白名单挡掉的表」算进提示：这里按数据集总表数
        # 与白名单大小做差，否则走 MySQL 关键词兜底时模型还是以为「没有这张表」。
        for _ds in found_datasets:
            _allowed = allowed_table_names_by_dataset.get(int(getattr(_ds, "id", 0) or 0))
            if _allowed is None:
                continue
            _total = await session.scalar(
                select(func.count())
                .select_from(MetaTable)
                .where(MetaTable.dataset_id == int(_ds.id), MetaTable.status == 1)
            )
            dropped_table_hits.extend(["_"] * max(0, int(_total or 0) - len(_allowed)))
        hidden_column_hits = []
        for ds in found_datasets:
            chunks, next_index = await _format_fallback_dataset_chunks(
                session,
                ds.id,
                next_index,
                allowed_table_names=allowed_table_names_by_dataset.get(int(ds.id)),
                column_rules_by_table=column_rules_by_table,
                hidden_columns_out=hidden_column_hits,
            )
            results.extend(chunks)

        schema_text = "\n\n".join(results)
        schema_text = _append_column_scope_notice(schema_text, len(set(hidden_column_hits)))

    # 跨数据集关联补全：在已有 schema 结果基础上，追加跨数据集关联表的 schema chunk
    schema_text = await _enrich_with_cross_dataset_schema(
        session,
        schema_text,
        user_id=user_id_eff,
        is_admin=is_admin_eff,
        authorized_dataset_ids=authorized_dataset_ids,
        allowed_table_names_by_dataset=allowed_table_names_by_dataset,
    )

    return _append_table_scope_notice(schema_text, len(dropped_table_hits))


def _normalize_authorized_dataset_ids(raw: Optional[list[Any]]) -> Optional[set[str]]:
    if raw is None:
        return None
    normalized: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if text:
            normalized.add(text)
    return normalized or None


def _extract_schema_table_refs(schema_text: str) -> list[tuple[str | None, str]]:
    refs: list[tuple[str | None, str]] = []
    current_dataset: str | None = None
    for raw_line in (schema_text or "").splitlines():
        stripped = raw_line.strip()
        dataset_match = re.match(r"dataset:\s*(\S+)", stripped, flags=re.IGNORECASE)
        if dataset_match:
            current_dataset = dataset_match.group(1).strip()
            continue
        table_match = re.match(r"table_name:\s*(\S+)", stripped, flags=re.IGNORECASE)
        if table_match:
            refs.append((current_dataset, table_match.group(1).strip()))
    return refs


_HAS_RELATIONSHIP_CACHE_KEY = "sys:meta:has_cross_dataset_relationship:v1"
_HAS_RELATIONSHIP_CACHE_TTL = 60  # 秒；短 TTL 自恢复，无需在 CRUD 处显式失效


async def _has_any_relationship(session: AsyncSession) -> bool:
    """全局是否存在 MetaRelationship 的短路径判断（Redis TTL 缓存）。

    跨数据集关联补全属于较稀有的配置；绝大多数数据集没有任何关联。
    热路径上先查一次缓存/计数，若系统整体没有 relationship 则直接短路，
    避免后续多次 DB 查询与鉴权开销。
    """
    from app.core.redis import get_redis

    try:
        redis = await get_redis()
    except Exception:
        redis = None

    if redis:
        try:
            cached = await redis.get(_HAS_RELATIONSHIP_CACHE_KEY)
            if cached is not None:
                return cached == "1"
        except Exception as ex:
            logger.warning("[cross_dataset_enrich] Failed to read relationship cache: %s", ex)

    # 未命中/Redis 不可用时回退 DB 快速计数
    try:
        cnt = (await session.execute(select(func.count(MetaRelationship.id)))).scalar() or 0
        has_any = cnt > 0
    except Exception as ex:
        # 即使计数失败也不阻断开原路径——按“存在”处理，交给后续逻辑去兜底
        logger.warning("[cross_dataset_enrich] Failed to count relationships: %s", ex)
        return True

    if redis:
        try:
            await redis.set(_HAS_RELATIONSHIP_CACHE_KEY, "1" if has_any else "0", ex=_HAS_RELATIONSHIP_CACHE_TTL)
        except Exception as ex:
            logger.warning("[cross_dataset_enrich] Failed to cache relationship flag: %s", ex)
    return has_any


async def _filter_tables_by_metadata_permission(
    session: AsyncSession,
    tables: list[MetaTable],
    *,
    user_id: Optional[int],
    is_admin: bool,
) -> list[MetaTable]:
    if is_admin:
        return tables
    if user_id is None:
        return []

    from app.services.permission_service import PermissionService

    # 批量鉴权：一次取用户完整权限（已有 Redis 缓存），取出 metadata 数据集 ID 集合，
    # 全部在内存内过滤，避免逐表重复命中缓存/DB（原实现每表一次 check_permission）。
    permission_service = PermissionService(session)
    try:
        perms = await permission_service.get_user_permissions(int(user_id))
    except Exception as ex:
        logger.warning("[cross_dataset_enrich] Failed to load user permissions for %s: %s", user_id, ex)
        return []

    # Admin bypass：权限在 roles 中携带，与 check_permission 保持一致语义
    roles = getattr(perms, "roles", []) or []
    if "admin" in roles:
        return tables

    permission_set = getattr(perms, "permissions", None)
    if permission_set is None:
        return []
    allowed_dataset_ids = set(getattr(permission_set, "metadata", None) or [])
    allowed: list[MetaTable] = []
    for table in tables:
        dataset_id = getattr(table, "dataset_id", None)
        if dataset_id is None and getattr(table, "dataset", None) is not None:
            dataset_id = getattr(table.dataset, "id", None)
        if dataset_id is None:
            continue
        if str(dataset_id) in allowed_dataset_ids:
            allowed.append(table)
    return allowed


async def _enrich_with_cross_dataset_schema(
    session: AsyncSession,
    schema_text: str,
    *,
    user_id: Optional[int] = None,
    is_admin: bool = False,
    authorized_dataset_ids: Optional[list[int]] = None,
    allowed_table_names_by_dataset: Optional[dict[int, set[str] | None]] = None,
) -> str:
    """从 schema 文本中解析 table_name，查询其跨数据集关联表，追加对应 schema chunk。

    只在确有跨数据集关联配置时才做额外 DB 查询，正常情况下几乎无开销（无关联则直接返回）。
    """
    from app.services.metadata_rag_service import MetadataRagService

    # 1. 从 schema YAML 中提取 dataset + table_name 列表，避免不同数据集同名物理表误命中。
    table_refs = _extract_schema_table_refs(schema_text)
    if not table_refs:
        return schema_text

    # 1.1 全局短路径：系统没有任何 MetaRelationship 时直接返回，避免后续多余 DB 查询与鉴权。
    if not await _has_any_relationship(session):
        return schema_text

    # 2. 查询这些 table_name 对应的 table_id（物理表名匹配）
    scoped_conditions = [
        and_(MetaDataset.name == dataset_name, MetaTable.physical_name == table_name)
        for dataset_name, table_name in table_refs
        if dataset_name and table_name
    ]
    unscoped_table_names = [table_name for dataset_name, table_name in table_refs if not dataset_name and table_name]
    stmt = select(MetaTable).join(MetaDataset, MetaDataset.id == MetaTable.dataset_id)
    if scoped_conditions and unscoped_table_names:
        stmt = stmt.where(or_(or_(*scoped_conditions), MetaTable.physical_name.in_(unscoped_table_names)))
    elif scoped_conditions:
        stmt = stmt.where(or_(*scoped_conditions))
    else:
        stmt = stmt.where(MetaTable.physical_name.in_(unscoped_table_names))
    result = await session.execute(stmt)
    source_tables = result.scalars().all()
    source_table_ids = [t.id for t in source_tables]

    if not source_table_ids:
        return schema_text

    # 3. 查询跨数据集关联目标表
    cross_tables = await MetadataService.get_cross_dataset_related_tables(session, source_table_ids)
    if not cross_tables:
        return schema_text
    configured_dataset_ids = _normalize_authorized_dataset_ids(authorized_dataset_ids)
    if configured_dataset_ids is not None:
        cross_tables = [
            table for table in cross_tables
            if str(getattr(table, "dataset_id", "")).strip() in configured_dataset_ids
        ]
        if not cross_tables:
            return schema_text
    cross_tables = await _filter_tables_by_metadata_permission(
        session,
        cross_tables,
        user_id=user_id,
        is_admin=is_admin,
    )
    if not cross_tables:
        return schema_text

    # 表级白名单：跨数据集补全同样不能泄露被收窄掉的表。
    # 注意 `allowed_table_names_by_dataset` 里没有 key 表示「这个数据集不在本轮范围内」，
    # 有 key 且值为 None 才表示「不限表」——用 .get() 会把前者误当成后者，
    # 于是限定 ds1 时仍可能把 ds54 的关联表补进来。
    if allowed_table_names_by_dataset:
        scoped_tables = []
        for table in cross_tables:
            try:
                dataset_id = int(getattr(table, "dataset_id", 0) or 0)
            except (TypeError, ValueError):
                dataset_id = 0
            if dataset_id not in allowed_table_names_by_dataset:
                continue
            allowed = allowed_table_names_by_dataset[dataset_id]
            if allowed is None or str(getattr(table, "physical_name", "") or "").lower() in allowed:
                scoped_tables.append(table)
        cross_tables = scoped_tables
        if not cross_tables:
            return schema_text

    # 4. 对这些跨数据集表生成 schema chunk 并追加
    ds_source_default = await ConfigService.get("external_sql_data_source", default="default_clickhouse")
    extra_chunks: list[str] = []
    chunk_index = schema_text.count("---\n") + 1  # 粗略估算 index 起点

    for table in cross_tables:
        dataset = table.dataset
        if not dataset:
            continue
        ds_source = dataset.data_source or ds_source_default
        try:
            yaml_chunk = MetadataRagService.render_table_schema_yaml(
                dataset, table, [], data_source=ds_source
            )
            formatted = format_schema_chunk(
                chunk_index,
                yaml_chunk,
                dataset_id=getattr(dataset, "id", None),
            )
            if formatted:
                extra_chunks.append(
                    f"# [跨数据集关联补全: {dataset.name}.{table.physical_name}]\n{formatted}"
                )
                chunk_index += 1
        except Exception as ex:
            logger.warning(
                "[cross_dataset_enrich] Failed to render schema for table %s: %s",
                table.physical_name,
                ex,
            )

    if extra_chunks:
        logger.info(
            "[cross_dataset_enrich] Appended %d cross-dataset schema chunks: %s",
            len(extra_chunks),
            [t.physical_name for t in cross_tables],
        )
        return schema_text + "\n\n" + "\n\n".join(extra_chunks)

    return schema_text
