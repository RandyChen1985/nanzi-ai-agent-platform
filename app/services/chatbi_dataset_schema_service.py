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


async def _format_fallback_dataset_chunks(session: AsyncSession, dataset_id: int, start_index: int) -> tuple[list[str], int]:
    """MySQL 兜底：导出与向量索引一致的单表/指标 YAML 块。"""
    raw_chunks = await MetadataService.build_dataset_schema_chunk_contents(session, dataset_id)
    formatted: list[str] = []
    index = start_index
    for content in raw_chunks:
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
            return "No configured metadata datasets are authorized for current user."

    _set_metadata_scope_debug(
        authorized_datasets=all_authorized_datasets,
        effective_datasets=authorized_datasets,
        provider=str(provider),
    )
    logger.info("[fetch_dataset_schema_core] provider=%s", str(provider).upper())

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

        filtered_hits = []
        for chunk in filter_schema_hits_to_scope(
            chunks,
            allowed_metadata_ids=allowed_metadata_ids,
            allowed_dataset_names=allowed_dataset_names,
            allowed_rag_ids=allowed_rag_ids,
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
            filtered_hits.append({
                "content": content,
                "similarity": similarity,
                "doc_name": chunk.get("doc_name", "unknown"),
                "metadata_dataset_id": (
                    _metadata_dataset_id_from_hit(chunk, authorized_datasets)
                ),
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
            )
            return schema_text
        return f"No relevant schema info found for '{query}'.\nDebug Logs: {'; '.join(trace_logs)}"

    # --- Local Mode (Default) ---
    query = (keywords or "").strip()
    threshold = float(await ConfigService.get("ragflow_similarity_threshold") or 0.2)
    top_k = int(await ConfigService.get("ragflow_metadata_top_k") or 5)

    schema_text: str | None = None  # 先收集到变量，再统一做跨数据集补全

    if query:
        try:
            from app.services.ai.embedding_client import EmbeddingClient
            from app.services.ai.metadata_index_service import MetadataIndexService

            authorized_ids = (
                [ds.id for ds in authorized_datasets if getattr(ds, "id", None) is not None]
                if configured_dataset_ids is not None or not is_admin_eff
                else None
            )
            query_embedding = await EmbeddingClient.embed_text(query, use_global=True)
            redis_results = await MetadataIndexService.search_knn(
                query_embedding=query_embedding,
                authorized_dataset_ids=authorized_ids,
                top_k=top_k,
            )

            filtered_hits = []
            for item in filter_schema_hits_to_scope(
                redis_results,
                allowed_metadata_ids=allowed_metadata_ids,
                allowed_dataset_names=allowed_dataset_names,
                allowed_rag_ids=allowed_rag_ids,
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
        for ds in found_datasets:
            chunks, next_index = await _format_fallback_dataset_chunks(session, ds.id, next_index)
            results.extend(chunks)

        schema_text = "\n\n".join(results)

    # 跨数据集关联补全：在已有 schema 结果基础上，追加跨数据集关联表的 schema chunk
    schema_text = await _enrich_with_cross_dataset_schema(
        session,
        schema_text,
        user_id=user_id_eff,
        is_admin=is_admin_eff,
        authorized_dataset_ids=authorized_dataset_ids,
    )

    return schema_text


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
