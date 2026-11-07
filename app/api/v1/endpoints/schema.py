import logging
from fastapi import APIRouter, Depends, HTTPException, Body
from app.core.config import settings
from app.core.orm import get_db_session
from app.core.dependencies import verify_v1_api_access
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()

from app.schemas.response import StandardResponse

class SchemaRequest(BaseModel):
    query: Optional[str] = Field(None, description="检索关键词", json_schema_extra={"example": "销售数据"})
    metadata_provider: Optional[str] = Field(None, description="临时指定元数据提供方 (local/ragflow)")
    ragflow_metadata_top_k: Optional[int] = Field(None, description="临时指定 Top K 数量")
    ragflow_similarity_threshold: Optional[float] = Field(None, description="临时指定相似度阈值")
    ragflow_vector_weight: Optional[float] = Field(None, description="临时指定混合检索中向量检索权重")

class SchemaHit(BaseModel):
    id: int = Field(..., description="数据集ID")
    name: str = Field(..., description="数据集名称")
    display_name: str = Field(..., description="中文显示名")

class SchemaResponse(BaseModel):
    schema_context: str = Field(..., description="YAML格式的Schema定义")
    hits: List[SchemaHit] = Field(..., description="命中的数据集列表")
    provider: str = Field(..., description="元数据提供方 (local/ragflow)")
    logs: List[str] = Field(default=[], description="执行过程日志")

@router.post("/schema", 
    response_model=StandardResponse[SchemaResponse],
    summary="检索元数据 Schema",
    description="统一 Schema 检索接口 (Gateway)。根据系统配置 (metadata_provider) 路由请求到 Local Service 或 RAGFlow。"
)
async def get_database_schema(
    request: SchemaRequest,
    conn: AsyncSession = Depends(get_db_session),
    current_user: dict = Depends(verify_v1_api_access)
):
    """
    统一 Schema 检索接口 (Gateway)。
    根据系统配置 (metadata_provider) 路由请求到 Local Service 或 RAGFlow。
    """
    from app.services.config_service import ConfigService
    from app.services.metadata_service import MetadataService
    from app.core.context import get_current_agent_context
    
    trace_logs = []
    
    provider = request.metadata_provider or await ConfigService.get("metadata_provider", default="local")
    msg = f"[Metadata Gateway] Routing request to provider: {provider.upper()}"
    logger.info(msg)
    trace_logs.append(msg)
    
    # Resolve user context
    # Priority 1: From Dependency (API Call)
    user_id = int(current_user.get("user_id")) if current_user.get("user_id") else None
    is_admin = current_user.get("role") == "admin"
    
    # Priority 2: From Agent Context (Tool Call override if needed, though Tool doesn't use this API endpoint directly)
    # The Tool calls the function logic, but usually Tool logic is inside data_api.py, not here.
    # If this endpoint is ONLY called by HTTP clients (Frontend/Postman), then current_user is sufficient.
    
    ctx = get_current_agent_context()
    if ctx and ctx.user_id:
        # If running in Agent Context (unlikely for this specific endpoint unless internal call), rely on context
        user_id = ctx.user_id
        is_admin = ctx.is_admin

    trace_logs.append(f"User Context: user_id={user_id}, is_admin={is_admin}")

    # 1. RAGFlow Provider
    if provider == 'ragflow':
        from app.services.ai.ragflow_client import RagFlowClient
        
        # Log RAGFlow Config
        rag_url = await ConfigService.get("ragflow_api_url")
        
        # Load Parameters
        if request.ragflow_similarity_threshold is not None:
            threshold = float(request.ragflow_similarity_threshold)
        else:
            threshold = float(await ConfigService.get("ragflow_similarity_threshold") or 0.2)

        if request.ragflow_vector_weight is not None:
            weight = float(request.ragflow_vector_weight)
        else:
            weight = float(await ConfigService.get("ragflow_vector_weight") or 0.3)

        if request.ragflow_metadata_top_k is not None:
            top_k = int(request.ragflow_metadata_top_k)
        else:
            top_k = 5
        
        trace_logs.append(f"RAGFlow Endpoint: {rag_url}")
        trace_logs.append(f"Params: threshold={threshold}, weight={weight}, top_k={top_k}")
        
        # A. Find all authorized RAG dataset IDs
        authorized_datasets = await MetadataService.search_datasets(
            conn, 
            status=1, 
            user_id=user_id, 
            is_admin=is_admin
        )
        rag_ids = [ds.rag_dataset_id for ds in authorized_datasets if ds.rag_dataset_id]
        trace_logs.append(f"Authorized Datasets: {len(authorized_datasets)} found. RAG IDs: {len(rag_ids)}")
        
        if not rag_ids:
            return StandardResponse(data=SchemaResponse(
                schema_context="[System] No authorized RAG metadata found.",
                hits=[],
                provider="ragflow",
                logs=trace_logs
            ))
            
        # B. Retrieve from RAGFlow with Auto-Retry
        from app.services.metadata_rag_service import MetadataRagService, MetadataServiceUnavailableError
        client = RagFlowClient()
        query = request.query or "latest schema"
        msg = f"[Metadata Gateway] RAG Retrieval Query: '{query}' on {len(rag_ids)} IDs."
        logger.info(msg)
        trace_logs.append(msg)
        
        try:
            chunks, r_logs = await MetadataRagService.retrieve_with_retry(
                client,
                query, 
                rag_ids, 
                top_k=top_k,
                threshold=threshold,
                weight=weight
            )
        except MetadataServiceUnavailableError as e:
            logger.error(f"[Metadata Gateway] RAGFlow unavailable: {e}")
            trace_logs.append(f"RAGFlow service unavailable, aborted without retry: {e}")
            raise HTTPException(
                status_code=503,
                detail="元数据检索服务（RAGFlow）暂时不可用，请稍后重试或联系管理员。"
            )
        trace_logs.extend(r_logs)
        
        if not chunks:
            trace_logs.append("RAGFlow returned 0 chunks (after potential retries).")
            return StandardResponse(data=SchemaResponse(
                schema_context="[System] No relevant knowledge found in RAGFlow metadata.",
                hits=[],
                provider="ragflow",
                logs=trace_logs
            ))
            
        # C. Format result
        trace_logs.append(f"RAGFlow returned {len(chunks)} chunks.")
        from app.services.schema_chunk_format import format_schema_hits

        filtered_hits = []
        for chunk in chunks:
            try:
                sim = float(chunk.get("similarity", 0.0) or 0.0)
            except (TypeError, ValueError):
                sim = 0.0
            if sim < threshold:
                continue
            content = str(chunk.get("content") or "").strip()
            if not content:
                continue
            doc_name = chunk.get("doc_name", "unknown")
            trace_logs.append(f"Hit: {doc_name} (Sim: {sim:.2f})")
            filtered_hits.append({
                "content": content,
                "similarity": sim,
                "doc_name": doc_name,
            })

        schema_context = format_schema_hits(filtered_hits) if filtered_hits else "[System] No relevant knowledge found in RAGFlow metadata."
            
        return StandardResponse(data=SchemaResponse(
            schema_context=schema_context,
            hits=[SchemaHit(id=0, name="rag_hit", display_name="RAG Results")],
            provider="ragflow",
            logs=trace_logs
        ))

    # 2. Local Provider (Default)
    trace_logs.append(f"Searching local datasets with query: '{request.query}'")
    
    # 获取用户有权访问的已启用数据集列表
    authorized_datasets = await MetadataService.search_datasets(
        conn,
        status=1,
        user_id=user_id,
        is_admin=is_admin
    )
    authorized_ids = None if is_admin else [ds.id for ds in authorized_datasets]

    # 表级 / 字段级授权：数据集级放行只是第一层。用户在同一数据集里可能只能看部分表、
    # 部分列，主链路（chatbi 取 schema）已经按这两层裁剪，这个检索调试入口如果不过同一道
    # 关，被收窄的表名/列名照样会从这里漏出去。
    allowed_table_names_by_dataset: dict[int, set[str] | None] = {}
    column_rules_by_table: dict[tuple[int, str], tuple] = {}
    if not is_admin and user_id is not None:
        try:
            from app.services.dataset_scope_service import (
                allowed_table_names_by_dataset as _resolve_allowed_tables,
                column_rules_by_table_name as _resolve_column_rules,
                resolve_user_dataset_scopes,
            )

            _scope_map = await resolve_user_dataset_scopes(conn, user_id)
            allowed_table_names_by_dataset = await _resolve_allowed_tables(conn, _scope_map)
            column_rules_by_table = await _resolve_column_rules(conn, _scope_map)
        except Exception as ex:  # noqa: BLE001 - 解析失败退回数据集级，不阻断调试
            trace_logs.append(f"[WARN] 表级/字段级授权解析失败，退回数据集级: {ex}")
            allowed_table_names_by_dataset = {}
            column_rules_by_table = {}
    
    if is_admin:
        trace_logs.append(
            f"Authorized datasets (enabled): {len(authorized_datasets)} — admin, no dataset_id TAG filter"
        )
    else:
        trace_logs.append(
            f"Authorized datasets (enabled): {len(authorized_datasets)}, "
            f"filter ids={authorized_ids}"
        )
    for ds in authorized_datasets[:10]:
        table_count = len(ds.tables or [])
        trace_logs.append(
            f"  - dataset id={ds.id} name={ds.name!r} tables={table_count} status={ds.status}"
        )
    if len(authorized_datasets) > 10:
        trace_logs.append(f"  ... and {len(authorized_datasets) - 10} more dataset(s)")
    
    if not is_admin and not authorized_ids:
        trace_logs.append("No authorized datasets found for user.")
        return StandardResponse(data=SchemaResponse(
            schema_context="[System] No authorized metadata found.",
            hits=[],
            provider="local",
            logs=trace_logs
        ))

    # 获取系统配置参数对齐检索门槛
    from app.services.config_service import ConfigService
    if request.ragflow_similarity_threshold is not None:
        threshold = float(request.ragflow_similarity_threshold)
    else:
        threshold = float(await ConfigService.get("ragflow_similarity_threshold") or 0.2)

    if request.ragflow_metadata_top_k is not None:
        top_k = int(request.ragflow_metadata_top_k)
    else:
        top_k = 5

    trace_logs.append(f"Search params: top_k={top_k}, similarity_threshold={threshold}")

    embed_url = await ConfigService.get("embed_api_url") or ""
    embed_model = await ConfigService.get("embed_model_name") or ""
    embed_dim_cfg = await ConfigService.get("embed_dimensions") or ""
    trace_logs.append(
        f"Embedding service: model={embed_model!r}, "
        f"url={embed_url[:80] + '...' if len(embed_url) > 80 else embed_url or '(empty)'}, "
        f"embed_dimensions={embed_dim_cfg or '(default)'}"
    )

    redis_results = []
    vector_search_success = False
    query = (request.query or "").strip()
    
    if not query:
        trace_logs.append("[WARN] Query is empty — skip vector search")
    else:
        from app.services.ai.metadata_index_service import MetadataIndexService
        await MetadataIndexService.append_index_diagnostics(trace_logs)

        try:
            from app.services.ai.embedding_client import EmbeddingClient
            
            trace_logs.append(f"[Embedding] Requesting vector for query ({len(query)} chars)...")
            query_embedding = await EmbeddingClient.embed_text(query, use_global=True)
            trace_logs.append(f"[Embedding] Received vector, dim={len(query_embedding)}")
            
            redis_results = await MetadataIndexService.search_knn(
                query_embedding=query_embedding,
                authorized_dataset_ids=authorized_ids,
                top_k=top_k,
                trace=trace_logs,
            )
            vector_search_success = True
            trace_logs.append(
                f"Redis Vector Search completed. Found {len(redis_results)} raw items "
                f"(before threshold {threshold})."
            )
        except Exception as ex:
            logger.warning("[Local Search] Redis Vector Search failed: %s. Falling back to keyword search.", ex)
            trace_logs.append(f"Redis Vector Search failed: {type(ex).__name__}: {ex}")
            trace_logs.append("Falling back to MySQL keyword search.")

    if vector_search_success:
        hits = []
        from app.services.schema_chunk_format import format_schema_hits

        dataset_id_to_obj = {ds.id: ds for ds in authorized_datasets}
        unique_hit_datasets = {}
        filtered_hits = []
        
        if not redis_results:
            trace_logs.append(
                "[Result] No raw vector hits. If FT.INFO num_docs>0, compare backend Redis "
                "with CLI host and verify embedding dimensions."
            )
        
        for item in redis_results:
            sim = item.get("similarity", 0.0)
            doc_name = item.get("doc_name", "unknown")
            if sim < threshold:
                trace_logs.append(
                    f"Filtered out {doc_name}: similarity {sim:.4f} < threshold {threshold}"
                )
                continue
                
            content = item.get("content", "")
            ds_id = int(item.get("dataset_id", 0))

            # 表级白名单：值 None = 不限表；集合 = 只允许这些表；缺 key = 不在本轮范围
            _allowed_tables = allowed_table_names_by_dataset.get(ds_id)
            if _allowed_tables is not None:
                from app.services.chatbi_dataset_schema_service import chunk_table_name

                _table = chunk_table_name(content)
                if _table and _table not in _allowed_tables:
                    trace_logs.append(
                        f"Filtered out {doc_name}: 表 {_table} 不在授权范围内"
                    )
                    continue

            # 字段级：受限列不能从调试入口露给用户
            if column_rules_by_table and ds_id:
                from app.services.chatbi_dataset_schema_service import (
                    chunk_table_name,
                    trim_chunk_columns,
                )

                _table = chunk_table_name(content)
                if _table:
                    _rules = column_rules_by_table.get((ds_id, _table))
                    if _rules:
                        content, _hidden = trim_chunk_columns(content, _rules)
                        if _hidden:
                            trace_logs.append(
                                f"Trimmed {len(_hidden)} hidden column(s) from {doc_name}"
                            )

            trace_logs.append(f"Accepted hit: {doc_name} (Sim: {sim:.4f}, dataset_id={ds_id})")
            filtered_hits.append({
                "content": content,
                "similarity": sim,
                "doc_name": doc_name,
            })
            
            if ds_id and ds_id not in unique_hit_datasets:
                if ds_id in dataset_id_to_obj:
                    ds = dataset_id_to_obj[ds_id]
                    unique_hit_datasets[ds_id] = SchemaHit(id=ds.id, name=ds.name, display_name=ds.display_name)
        
        if redis_results and not filtered_hits:
            trace_logs.append(
                f"[Result] All {len(redis_results)} raw hit(s) filtered by threshold {threshold} — "
                "try lowering similarity_threshold in advanced settings"
            )
                    
        schema_context = format_schema_hits(filtered_hits) if filtered_hits else "[System] No relevant metadata found above the similarity threshold."
        
        return StandardResponse(data=SchemaResponse(
            schema_context=schema_context,
            hits=list(unique_hit_datasets.values()),
            provider="local",
            logs=trace_logs
        ))
        
    else:
        # 兜底降级: MySQL LIKE 模糊匹配检索
        trace_logs.append("Running fallback MySQL keyword search.")
        found_datasets = []
        if query:
            found_datasets = await MetadataService.search_datasets(
                conn, 
                query=query, 
                user_id=user_id, 
                is_admin=is_admin
            )
        
        if not found_datasets:
            return StandardResponse(data=SchemaResponse(
                schema_context="[System] No relevant metadata found. Please refine your query.",
                hits=[],
                provider="local",
                logs=trace_logs
            ))
            
        yaml_outputs = []
        hits = []
        from app.services.chatbi_dataset_schema_service import _format_fallback_dataset_chunks

        next_index = 1
        for ds in found_datasets:
            chunks, next_index = await _format_fallback_dataset_chunks(conn, ds.id, next_index)
            yaml_outputs.extend(chunks)
            hits.append(SchemaHit(id=ds.id, name=ds.name, display_name=ds.display_name))
            
        final_context = "\n\n".join(yaml_outputs)
        return StandardResponse(data=SchemaResponse(
            schema_context=final_context,
            hits=hits,
            provider="local",
            logs=trace_logs
        ))
