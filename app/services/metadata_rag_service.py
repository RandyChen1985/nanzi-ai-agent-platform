import logging
import asyncio
import yaml
import httpx
from datetime import datetime
from typing import List, Optional, Any, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.models.metadata import MetaDataset, MetaTable, MetaMetric
from app.services.ai.ragflow_client import RagFlowClient
from app.services.metadata_service import MetadataService

logger = logging.getLogger(__name__)


class MetadataServiceUnavailableError(Exception):
    """RAGFlow 元数据检索服务不可用（502/503/504、网关超时、连接失败等）。

    这类故障重试也无济于事，应立即终止并向上层返回明确的「服务不可用」信号，
    而不是把它当成「没有检索到结果」继续重试或让模型臆测表结构。
    """
    pass


# 服务级不可用的错误特征（与「坏数据集 ID」类错误区分开）
_SERVICE_UNAVAILABLE_HINTS = (
    "502", "503", "504",
    "bad gateway", "service unavailable", "gateway timeout",
    "temporarily unavailable", "timed out", "timeout",
    "connection refused", "connection error", "connection aborted",
    "connect timeout", "read timeout", "max retries", "server disconnected",
    "name or service not known", "failed to establish",
)


class MetadataRagService:
    """
    Service to synchronize Metadata (Datasets/Tables) to RAGFlow.
    """

    @staticmethod
    def _is_service_unavailable(e: Exception) -> bool:
        """判断错误是否属于「服务级不可用」（应立即终止，而非重试/剔除坏 ID）。

        通过 httpx 强类型异常与状态码做精准拦截，并用字符串做模糊匹配兜底。
        """
        # 1. 强类型判定：如果属于底层网络连接、断连或超时错误，直接判定不可用
        if isinstance(e, (httpx.TimeoutException, httpx.NetworkError)):
            return True

        # 2. 如果是带状态码的 HTTP 错误，判断是否为 5xx 故障
        if isinstance(e, httpx.HTTPStatusError):
            if e.response.status_code in (502, 503, 504):
                return True

        # 3. 兜底字串模糊匹配
        err_msg = str(e)
        m = (err_msg or "").lower()
        return any(hint in m for hint in _SERVICE_UNAVAILABLE_HINTS)

    @staticmethod
    def unavailable_hint(detail: str = "") -> str:
        """统一的「元数据服务不可用」用户/模型可读提示。"""
        base = (
            "[元数据服务不可用] 元数据检索服务（RAGFlow）当前无法访问，暂时无法获取数据集结构信息。"
            "请直接告知用户「元数据服务暂时不可用，请稍后重试或联系管理员」，"
            "不要重复调用本工具，也不要臆测/编造表结构或字段。"
        )
        if detail:
            base += f"\n（错误详情：{detail}）"
        return base

    @staticmethod
    def knowledge_unavailable_hint(detail: str = "") -> str:
        """统一的「知识库服务不可用」用户/模型可读提示。"""
        base = (
            "[知识库服务不可用] 知识库检索服务（RAGFlow）当前无法访问，暂时无法检索文档内容。"
            "请直接告知用户「知识库服务暂时不可用，请稍后重试或联系管理员」，"
            "不要重复调用本工具，也不要凭记忆编造流程或制度内容。"
        )
        if detail:
            base += f"\n（错误详情：{detail}）"
        return base

    @staticmethod
    def build_table_schema_dict(
        dataset: MetaDataset,
        table: MetaTable,
        relationships: Optional[List[Any]] = None,
        *,
        data_source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """构建单表 Schema 字典（ChatBI 工具输出 canonical 格式）。"""
        relationships = relationships or []
        data: Dict[str, Any] = {
            "table_name": table.physical_name,
            "table_desc": table.term,
            "dataset": dataset.name,
            "meta_name": dataset.display_name or dataset.name,
            "data_source": data_source if data_source is not None else dataset.data_source,
            "description": table.description or "",
            "columns": [],
            "relationships": [],
        }

        for col in table.columns:
            col_data: Dict[str, Any] = {
                "name": col.physical_name,
                "type": col.type,
                "term": col.term,
                "desc": col.description or "",
            }
            if col.enums:
                col_data["enums"] = col.enums
            if col.examples:
                col_data["examples"] = col.examples
            if hasattr(col, "is_primary") and col.is_primary == 1:
                col_data["pk"] = True
            data["columns"].append(col_data)

        if table.synonyms:
            data["synonyms"] = table.synonyms

        related_rels = [
            r for r in relationships
            if r.source_table_id == table.id or r.target_table_id == table.id
        ]
        for r in related_rels:
            is_source = r.source_table_id == table.id
            other_table = r.target_table.physical_name if is_source else r.source_table.physical_name
            data["relationships"].append({
                "target": other_table,
                "direction": "->" if is_source else "<-",
                "type": r.join_type,
                "condition": r.join_condition,
            })
        return data

    @staticmethod
    def build_metrics_schema_dict(
        dataset: MetaDataset,
        *,
        data_source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """构建指标块 Schema 字典。"""
        return {
            "dataset": dataset.name,
            "meta_name": dataset.display_name or dataset.name,
            "data_source": data_source if data_source is not None else dataset.data_source,
            "metrics_scope": dataset.display_name or dataset.name,
            "metrics": [
                {
                    "name": m.name,
                    "display": m.display_name,
                    "desc": m.description or "",
                    "unit": m.unit or "",
                    "sql": m.calculation_logic,
                }
                for m in (dataset.metrics or [])
            ],
        }

    @staticmethod
    def render_table_schema_yaml(
        dataset: MetaDataset,
        table: MetaTable,
        relationships: Optional[List[Any]] = None,
        *,
        data_source: Optional[str] = None,
    ) -> str:
        data = MetadataRagService.build_table_schema_dict(
            dataset,
            table,
            relationships,
            data_source=data_source,
        )
        return yaml.dump(data, allow_unicode=True, sort_keys=False)

    @staticmethod
    def render_metrics_schema_yaml(
        dataset: MetaDataset,
        *,
        data_source: Optional[str] = None,
    ) -> str:
        if not dataset.metrics:
            return ""
        note = (
            "# 说明：本文件仅为指标计算口径参考，不包含任何表结构。\n"
            "# dataset 为数据集标识符（execute_sql_query 的 dataset_name）；"
            "meta_name / metrics_scope 为业务展示名，并非物理表名。\n"
            "# 下方 sql 仅为指标的计算表达式片段；"
            "实际查询请以对应表文件中的 table_name（物理表名）作为 FROM 表，\n"
            "# 严禁将 metrics_scope、meta_name 或指标名当作可查询的表。\n"
        )
        return note + yaml.dump(
            MetadataRagService.build_metrics_schema_dict(dataset, data_source=data_source),
            allow_unicode=True,
            sort_keys=False,
        )

    @staticmethod
    def generate_table_content(dataset: MetaDataset, table: MetaTable, relationships: List[Any] = []) -> str:
        """
        Generate SQL-optimized YAML for a table (One Table per Chunk).
        Ensures strict schema definition for LLM.
        """
        return MetadataRagService.render_table_schema_yaml(dataset, table, relationships)

    @staticmethod
    def generate_metrics_content(
        dataset: MetaDataset,
        *,
        data_source: Optional[str] = None,
    ) -> str:
        """Generate YAML for Dataset Metrics"""
        return MetadataRagService.render_metrics_schema_yaml(dataset, data_source=data_source)

    @staticmethod
    async def sync_dataset(db: AsyncSession, dataset_id: int):
        """
        Main synchronization logic.
        """
        client = RagFlowClient()
        
        # 1. Load Dataset Detail (Admin mode)
        dataset = await MetadataService.get_dataset_by_id(db, dataset_id, is_admin=True)
        if not dataset:
            logger.error(f"[RAG Sync] Dataset {dataset_id} not found.")
            return

        logger.info(f"[RAG Sync] Starting sync for dataset: {dataset.name} (ID: {dataset_id})")
        
        try:
            # Update status to 'Syncing'
            await MetadataRagService._update_sync_status(db, dataset_id, 1, notes="Syncing started...")

            # 2. Check/Create RAGFlow KB
            rag_kb_id = dataset.rag_dataset_id
            kb_name = f"meta-{dataset.name}"
            
            # Verify existence of current ID if present
            if rag_kb_id:
                try:
                    # Lightweight check: List documents with limit 1
                    await client.list_documents(rag_kb_id, page_size=1, page=1)
                except Exception as e:
                    err = str(e).lower()
                    if "not found" in err or "doesn't exist" in err or "you don't own" in err:
                        logger.warning(f"[RAG Sync] Existing KB {rag_kb_id} is invalid/deleted. Resetting to create new one.")
                        rag_kb_id = None
                        dataset.rag_dataset_id = None
                        # 立即提交清除无效 ID
                        await db.commit()
                        logger.info(f"[RAG Sync] Cleared invalid rag_dataset_id for dataset {dataset_id}")
                    else:
                        # Other error? Try to proceed or fail
                        logger.warning(f"[RAG Sync] Probe failed for {rag_kb_id}: {e}. Assuming valid for now.")

            if not rag_kb_id:
                # Search by name first (Client-side filtering implemented in client)
                existing_kbs = await client.list_datasets(name=kb_name)
                match = next((k for k in existing_kbs if k['name'] == kb_name), None)
                
                if match:
                    rag_kb_id = match['id']
                    logger.info(f"[RAG Sync] Found existing KB in RAGFlow: {rag_kb_id}")
                else:
                    logger.info(f"[RAG Sync] Creating new KB in RAGFlow: {kb_name}")
                    new_kb = await client.create_dataset(
                        name=kb_name, 
                        description=f"Metadata mirror for {dataset.display_name}",
                        chunk_method="one"
                    )
                    rag_kb_id = new_kb['id']
                
                # Save ID to DB
                dataset.rag_dataset_id = rag_kb_id
                await db.commit()

            # 3. Get Existing Documents
            existing_docs = await client.list_documents(rag_kb_id, page_size=1000)
            doc_map = {doc['name']: doc['id'] for doc in existing_docs}

            # --- Cleanup Stale Documents (Zombie Files) ---
            # Define what SHOULD be there
            expected_docs = {f"{t.physical_name}.txt" for t in dataset.tables}
            if dataset.metrics:
                expected_docs.add("_metrics.txt")
            
            # Find what is there but shouldn't be
            stale_doc_ids = [doc_id for name, doc_id in doc_map.items() if name not in expected_docs]
            if stale_doc_ids:
                logger.info(f"[RAG Sync] Cleaning up {len(stale_doc_ids)} stale documents from RAGFlow")
                await client.delete_documents(rag_kb_id, stale_doc_ids)
                # Update doc_map to reflect deletions for subsequent logic
                for name in list(doc_map.keys()):
                    if doc_map[name] in stale_doc_ids:
                        del doc_map[name]

            # 4. Sync Tables
            new_doc_ids = []
            
            # Pre-fetch relationships for context
            relationships = dataset.relationships or []

            for table in dataset.tables:
                file_name = f"{table.physical_name}.txt"
                content = MetadataRagService.generate_table_content(dataset, table, relationships)
                blob = content.encode('utf-8')
                
                if file_name in doc_map:
                    logger.info(f"[RAG Sync] Deleting old document: {file_name}")
                    await client.delete_documents(rag_kb_id, [doc_map[file_name]])
                
                logger.info(f"[RAG Sync] Uploading new document: {file_name}")
                new_doc = await client.upload_document(rag_kb_id, file_name, blob)
                new_doc_ids.append(new_doc['id'])

            # 5. Sync Metrics (as a separate file)
            if dataset.metrics:
                metrics_content = MetadataRagService.generate_metrics_content(dataset)
                if metrics_content:
                    metrics_file = "_metrics.txt"
                    metrics_blob = metrics_content.encode('utf-8')
                    
                    if metrics_file in doc_map:
                        logger.info(f"[RAG Sync] Deleting old metrics file")
                        await client.delete_documents(rag_kb_id, [doc_map[metrics_file]])
                        
                    logger.info(f"[RAG Sync] Uploading metrics file")
                    m_doc = await client.upload_document(rag_kb_id, metrics_file, metrics_blob)
                    new_doc_ids.append(m_doc['id'])

            # 6. Trigger Parsing
            if new_doc_ids:
                logger.info(f"[RAG Sync] Triggering parse for {len(new_doc_ids)} documents")
                await client.parse_documents(rag_kb_id, new_doc_ids)

            # 7. Finalize
            success_msg = f"Successfully synced {len(new_doc_ids)} items to RAGFlow (KB: {rag_kb_id})"
            await MetadataRagService._update_sync_status(db, dataset_id, 2, rag_kb_id, notes=success_msg)
            logger.info(f"[RAG Sync] {success_msg}")

        except Exception as e:
            error_msg = f"Sync Failed: {str(e)}"
            logger.error(f"[RAG Sync] Failed to sync dataset {dataset_id}: {str(e)}", exc_info=True)
            await MetadataRagService._update_sync_status(db, dataset_id, -1, notes=error_msg)

    @staticmethod
    async def delete_rag_dataset(rag_kb_id: str):
        """Cascade delete KB in RAGFlow"""
        if not rag_kb_id:
            return
            
        client = RagFlowClient()
        try:
            logger.info(f"[RAG Sync] Deleting remote RAGFlow KB: {rag_kb_id}")
            await client.delete_datasets([rag_kb_id])
        except Exception as e:
            logger.error(f"[RAG Sync] Cascade delete failed for KB {rag_kb_id}: {e}")

    @staticmethod
    async def retrieve_with_retry(
        client: RagFlowClient,
        query: str,
        rag_ids: List[str],
        top_k: int = 5,
        threshold: float = 0.2,
        weight: float = 0.3,
        max_retries: int = 2
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        """
        Perform RAG retrieval with automatic exclusion of bad/unauthorized dataset IDs.
        Returns (chunks, trace_logs).
        """
        trace_logs = []
        chunks = []
        
        for attempt in range(max_retries + 1):
            try:
                chunks = await client.retrieve(
                    query, 
                    rag_ids, 
                    top_k=top_k,
                    similarity_threshold=threshold,
                    vector_similarity_weight=weight
                )
                break # Success
            except MetadataServiceUnavailableError:
                raise
            except Exception as e:
                err_msg = str(e)
                trace_logs.append(f"Attempt {attempt+1} failed: {err_msg}")
                logger.error(f"[RAG Retrieval] Attempt {attempt+1} failed on IDs {rag_ids}: {err_msg}")

                # 服务级不可用（502/503/504、超时、连接失败）：重试无意义，立即终止。
                if MetadataRagService._is_service_unavailable(e):
                    trace_logs.append("Search aborted: metadata service unavailable")
                    logger.error(
                        f"[RAG Retrieval] Metadata service unavailable, abort without retry: {err_msg}"
                    )
                    raise MetadataServiceUnavailableError(err_msg)

                # Identify bad ID from error message
                found_bad_id = None
                for rid in rag_ids:
                    s_rid = str(rid).strip()
                    if s_rid.lower() in err_msg.lower():
                        found_bad_id = rid
                        break
                
                if found_bad_id and attempt < max_retries:
                    rag_ids = [r for r in rag_ids if r != found_bad_id]
                    msg = f"Excluding bad ID: {found_bad_id} and retrying... (Remaining: {len(rag_ids)})"
                    trace_logs.append(msg)
                    logger.warning(f"[RAG Retrieval] {msg}")
                    
                    # 清理数据库中的无效 ID
                    await MetadataRagService._clear_invalid_rag_id(found_bad_id)
                else:
                    fail_reason = "Out of retries" if attempt >= max_retries else "Cannot identify bad ID from error"
                    trace_logs.append(f"Search aborted: {fail_reason}")
                    break
                    
        return chunks, trace_logs

    @staticmethod
    async def _clear_invalid_rag_id(invalid_rag_id: str):
        """清理数据库中的无效 RAGFlow 数据集 ID"""
        from app.core.orm import AsyncSessionLocal
        
        async with AsyncSessionLocal() as db:
            try:
                # 查找所有使用这个无效 ID 的数据集
                result = await db.execute(
                    update(MetaDataset)
                    .where(MetaDataset.rag_dataset_id == invalid_rag_id)
                    .values(rag_dataset_id=None, rag_sync_status=-1, rag_sync_notes="RAGFlow dataset ID is invalid/unauthorized")
                )
                await db.commit()
                
                if result.rowcount > 0:
                    logger.info(f"[RAG Sync] Cleared invalid rag_dataset_id '{invalid_rag_id}' from {result.rowcount} datasets")
                
            except Exception as e:
                logger.error(f"[RAG Sync] Failed to clear invalid rag_dataset_id '{invalid_rag_id}': {e}")
                await db.rollback()

    @staticmethod
    async def _update_sync_status(db: AsyncSession, dataset_id: int, status: int, rag_id: str = None, notes: str = None):
        """Helper to update sync status in DB"""
        values = {
            "rag_sync_status": status,
            "rag_synced_at": datetime.now() if status == 2 else None
        }
        if rag_id:
            values["rag_dataset_id"] = rag_id
        if notes:
            values["rag_sync_notes"] = notes
            
        stmt = update(MetaDataset).where(MetaDataset.id == dataset_id).values(**values)
        await db.execute(stmt)
        await db.commit()
