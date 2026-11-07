"""RediSearch-backed metadata index service for local vector search."""
import asyncio
import hashlib
import logging
import struct
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set
from app.core.redis import get_redis, get_redis_binary
from app.services.ai.embedding_client import EmbeddingClient
from app.services.ai.redis_index_utils import ensure_vector_index

logger = logging.getLogger(__name__)

METADATA_INDEX_NAME = "nanzi:idx:metadata:dataset"
METADATA_KEY_PREFIX = "metadata:dataset:"


def _metadata_schema(dim: int) -> List[Any]:
    return [
        "dataset_id",
        "TAG",
        "doc_name",
        "TEXT",
        "content",
        "TEXT",
        "embedding",
        "VECTOR",
        "HNSW",
        "6",
        "TYPE",
        "FLOAT32",
        "DIM",
        str(dim),
        "DISTANCE_METRIC",
        "COSINE",
    ]


def _vector_to_bytes(vec: List[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _decode_key(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _find_in_ft_payload(raw: Any, target_key: str) -> Any:
    """Recursively locate a field in FT.INFO / nested RediSearch payloads."""
    if isinstance(raw, dict):
        for key, val in raw.items():
            if _decode_key(key) == target_key:
                return val
        for val in raw.values():
            found = _find_in_ft_payload(val, target_key)
            if found is not None:
                return found
        return None
    if isinstance(raw, (list, tuple)):
        for idx, item in enumerate(raw):
            if _decode_key(item) == target_key and idx + 1 < len(raw):
                return raw[idx + 1]
        for item in raw:
            found = _find_in_ft_payload(item, target_key)
            if found is not None:
                return found
    return None


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, bytes):
        try:
            return int(value.decode("utf-8", errors="replace"))
        except ValueError:
            return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _align_embedding(vec: List[float], expected_dim: int) -> List[float]:
    if len(vec) == expected_dim:
        return vec
    if len(vec) > expected_dim:
        return vec[:expected_dim]
    return vec + [0.0] * (expected_dim - len(vec))


def _dict_get(mapping: Any, *keys: str) -> Any:
    """Read dict field whether keys are str or bytes (binary Redis client)."""
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping:
            return mapping[key]
        bkey = key.encode("utf-8")
        if bkey in mapping:
            return mapping[bkey]
    return None


def _row_from_ft_entry(entry: Any) -> Dict[str, Any]:
    if not isinstance(entry, dict):
        return {}
    attrs = _dict_get(entry, "extra_attributes", "attributes")
    source = attrs if isinstance(attrs, dict) else entry
    row: Dict[str, Any] = {}
    for key, value in source.items():
        name = _decode_key(key)
        if isinstance(value, bytes):
            row[name] = value.decode("utf-8", errors="replace")
        else:
            row[name] = value
    return row


def _tag_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(".", "\\.")
        .replace("<", "\\<")
        .replace(">", "\\>")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace('"', '\\"')
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace(";", "\\;")
        .replace("!", "\\!")
        .replace("@", "\\@")
        .replace("#", "\\#")
        .replace("$", "\\$")
        .replace("%", "\\%")
        .replace("^", "\\^")
        .replace("&", "\\&")
        .replace("*", "\\*")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("-", "\\-")
        .replace("+", "\\+")
        .replace("=", "\\=")
        .replace("~", "\\~")
        .replace("|", "\\|")
        .replace(" ", "\\ ")
    )


class _DebounceHandle:
    def __init__(self, task: Optional[asyncio.Task] = None):
        self.task = task
        self.started_execution: bool = False


def _is_task_done(task: Any) -> bool:
    if task is None:
        return True
    done_fn = getattr(task, "done", None)
    if callable(done_fn):
        try:
            return bool(done_fn())
        except Exception:
            return False
    return False


def _cancel_task(task: Any) -> None:
    if task is None:
        return
    cancel_fn = getattr(task, "cancel", None)
    if callable(cancel_fn):
        try:
            cancel_fn()
        except Exception:
            pass


class MetadataIndexService:
    # 同步协调器状态（单数据集防抖合并、并发锁与 dirty 追赶）
    _dataset_locks: Dict[int, asyncio.Lock] = {}
    _dataset_dirty: Set[int] = set()
    _dataset_debounce_handles: Dict[int, _DebounceHandle] = {}
    _coordinator_lock: asyncio.Lock = asyncio.Lock()
    DEFAULT_DEBOUNCE_SECONDS: float = 0.5

    @classmethod
    async def _get_dataset_lock(cls, dataset_id: int) -> asyncio.Lock:
        async with cls._coordinator_lock:
            if dataset_id not in cls._dataset_locks:
                cls._dataset_locks[dataset_id] = asyncio.Lock()
            return cls._dataset_locks[dataset_id]

    @classmethod
    async def reset_sync_coordinator_state(cls) -> None:
        """重置同步协调器内部状态，供单元测试用例隔离使用。"""
        async with cls._coordinator_lock:
            for handle in list(cls._dataset_debounce_handles.values()):
                if handle and handle.task:
                    _cancel_task(handle.task)
            cls._dataset_debounce_handles.clear()
            cls._dataset_locks.clear()
            cls._dataset_dirty.clear()

    @staticmethod
    async def index_name() -> str:
        return METADATA_INDEX_NAME

    @staticmethod
    def _parse_ft_info(info: Any) -> Dict[str, Any]:
        if isinstance(info, dict):
            return {_decode_key(k): v for k, v in info.items()}
        if not info or not isinstance(info, (list, tuple)):
            return {}
        parsed: Dict[str, Any] = {}
        i = 0
        while i + 1 < len(info):
            parsed[_decode_key(info[i])] = info[i + 1]
            i += 2
        return parsed

    @staticmethod
    def _extract_index_vector_dim(info: Any) -> Optional[int]:
        dim_val = _find_in_ft_payload(info, "dim")
        return _coerce_int(dim_val)

    @staticmethod
    def _extract_num_docs(info: Any) -> Optional[int]:
        return _coerce_int(_find_in_ft_payload(info, "num_docs"))

    @staticmethod
    def _ft_search_total(raw: Any) -> int:
        if raw is None:
            return 0
        if isinstance(raw, dict):
            for key in ("total_results", "total", "count"):
                val = _coerce_int(_dict_get(raw, key))
                if val is not None:
                    return val
            results = _dict_get(raw, "results", "documents")
            if isinstance(results, list):
                return len(results)
            return 0
        if isinstance(raw, (list, tuple)) and raw:
            return _coerce_int(raw[0]) or 0
        return 0

    @staticmethod
    def _append_trace(trace: Optional[List[str]], message: str) -> None:
        if trace is not None:
            trace.append(message)

    @staticmethod
    async def append_index_diagnostics(trace: Optional[List[str]] = None) -> Dict[str, Any]:
        """Collect Redis / RediSearch index state for schema retrieval troubleshooting."""
        from app.core.config import settings

        diag: Dict[str, Any] = {
            "redis_enabled": settings.REDIS_ENABLE,
            "redis_host": settings.REDIS_HOST,
            "redis_port": settings.REDIS_PORT,
            "redis_db": settings.REDIS_DB,
        }
        MetadataIndexService._append_trace(
            trace,
            f"[Redis] REDIS_ENABLE={settings.REDIS_ENABLE}, "
            f"target={settings.REDIS_HOST}:{settings.REDIS_PORT}/db{settings.REDIS_DB}",
        )

        configured_dim = await EmbeddingClient.get_dimensions(use_global=True)
        diag["configured_embed_dim"] = configured_dim
        MetadataIndexService._append_trace(trace, f"[Embedding Config] embed_dimensions={configured_dim}")

        redis = await get_redis()
        if not redis:
            diag["text_client"] = False
            MetadataIndexService._append_trace(trace, "[Redis] Text client unavailable")
            return diag
        diag["text_client"] = True

        binary = await get_redis_binary()
        diag["binary_client"] = binary is not None
        if not binary:
            MetadataIndexService._append_trace(
                trace,
                "[WARN] Redis binary client unavailable — vector KNN will return 0 without raising",
            )

        idx = await MetadataIndexService.index_name()
        try:
            info_raw = await redis.execute_command("FT.INFO", idx)
            num_docs = MetadataIndexService._extract_num_docs(info_raw)
            index_dim = MetadataIndexService._extract_index_vector_dim(info_raw)
            diag["index_exists"] = True
            diag["num_docs"] = num_docs
            diag["index_vector_dim"] = index_dim
            MetadataIndexService._append_trace(
                trace,
                f"[RediSearch] Index '{idx}' num_docs={num_docs if num_docs is not None else '?'}, "
                f"index_vector_dim={index_dim}",
            )
            if index_dim is not None and index_dim != configured_dim:
                MetadataIndexService._append_trace(
                    trace,
                    f"[WARN] Index vector dim ({index_dim}) != embed_dimensions ({configured_dim}) "
                    "— run 「重构本地向量数据」 after fixing config",
                )
        except Exception as exc:
            diag["index_exists"] = False
            MetadataIndexService._append_trace(
                trace,
                f"[RediSearch] FT.INFO '{idx}' failed: {exc}",
            )

        key_count = 0
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor, match=f"{METADATA_KEY_PREFIX}*", count=200)
            key_count += len(keys or [])
            if cursor == 0:
                break
        diag["hash_key_count"] = key_count
        MetadataIndexService._append_trace(
            trace,
            f"[Redis Keys] SCAN '{METADATA_KEY_PREFIX}*' => {key_count} hash key(s)",
        )

        num_docs_val = diag.get("num_docs")
        num_docs_int = num_docs_val if isinstance(num_docs_val, int) else -1
        if key_count > 0 and num_docs_int == 0:
            MetadataIndexService._append_trace(
                trace,
                "[WARN] Hash keys exist but index num_docs=0 — index out of sync; rebuild vectors",
            )
        elif num_docs_int > 0 and key_count == 0:
            MetadataIndexService._append_trace(
                trace,
                "[WARN] Index has documents but no metadata hash keys under prefix",
            )

        if binary and key_count > 0:
            sample_key = None
            cursor = 0
            while sample_key is None:
                cursor, keys = await redis.scan(cursor, match=f"{METADATA_KEY_PREFIX}*", count=20)
                if keys:
                    sample_key = keys[0]
                if cursor == 0:
                    break
            if sample_key:
                key_str = _decode_key(sample_key)
                emb_len = await binary.hstrlen(key_str, "embedding")
                diag["sample_embedding_bytes"] = emb_len
                expected_bytes = configured_dim * 4
                MetadataIndexService._append_trace(
                    trace,
                    f"[Redis Sample] key={key_str!r} HSTRLEN(embedding)={emb_len} "
                    f"(expected {expected_bytes})",
                )
                if emb_len != expected_bytes:
                    MetadataIndexService._append_trace(
                        trace,
                        "[WARN] Stored embedding byte length mismatch — rebuild vectors after fixing embed_dimensions",
                    )
                stored_emb = await binary.hget(key_str, "embedding")
                if isinstance(stored_emb, bytes) and len(stored_emb) == expected_bytes:
                    probe_total = MetadataIndexService._ft_search_total(
                        await MetadataIndexService._execute_ft_search(
                            binary,
                            idx,
                            f"*=>[KNN 1 @embedding $vec AS score]",
                            stored_emb,
                            return_content=False,
                        )
                    )
                    diag["self_knn_probe_total"] = probe_total
                    MetadataIndexService._append_trace(
                        trace,
                        f"[KNN Probe] Self-search with stored embedding => raw total={probe_total}",
                    )
                    if probe_total == 0 and num_docs_int > 0:
                        MetadataIndexService._append_trace(
                            trace,
                            "[WARN] Index has docs but self-KNN returned 0 — check RediSearch / binary client",
                        )

        return diag

    @staticmethod
    async def ensure_index(force: bool = False) -> bool:
        """确保索引存在且向量维度与全局 ``embed_dimensions`` 一致。

        维度不一致时重建（``FT.DROPINDEX`` 不带 ``DD``，保留 HASH 文档）。
        使用无缓存模式：调用方（如「一键重构」）会先 ``FT.DROPINDEX``，若命中
        进程内缓存就会跳过 FT.CREATE，反而把索引丢在半路。
        """
        redis = await get_redis()
        if not redis:
            return False
        idx = await MetadataIndexService.index_name()
        dim = await EmbeddingClient.get_dimensions(use_global=True)
        result = await ensure_vector_index(
            redis,
            index_name=idx,
            prefix=METADATA_KEY_PREFIX,
            schema=_metadata_schema,
            dim=dim,
            log_tag="MetadataIndex",
            cache=None,
            force=force,
        )
        return bool(result.get("ok"))

    @staticmethod
    async def upsert_vector(
        dataset_id: int,
        doc_name: str,
        content: str,
        embedding: List[float],
        content_hash: Optional[str] = None,
        skip_ensure_index: bool = False,
    ) -> None:
        """
        Upsert a metadata document (table or metrics) with embedding into Redis HASH.
        Key is:
          - for table: metadata:dataset:<dataset_id>:table:<table_physical_name>
          - for metrics: metadata:dataset:<dataset_id>:metrics
        """
        redis = await get_redis_binary()
        if not redis:
            return
        
        if doc_name == "_metrics.txt":
            key = f"{METADATA_KEY_PREFIX}{dataset_id}:metrics"
        else:
            table_name = doc_name
            if table_name.endswith(".txt"):
                table_name = table_name[:-4]
            key = f"{METADATA_KEY_PREFIX}{dataset_id}:table:{table_name}"

        if not content_hash:
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        mapping = {
            b"dataset_id": str(dataset_id).encode("utf-8"),
            b"doc_name": doc_name.encode("utf-8"),
            b"content": content.encode("utf-8"),
            b"content_hash": content_hash.encode("utf-8"),
            b"embedding": _vector_to_bytes(embedding),
        }
        await redis.hset(key, mapping=mapping)
        logger.info("[MetadataIndex] Upserted vector for key %s", key)
        if not skip_ensure_index:
            await MetadataIndexService.ensure_index()

    @classmethod
    async def delete_dataset_vectors(cls, dataset_id: int) -> None:
        """
        Cascade delete all keys under prefix metadata:dataset:<dataset_id>:
        """
        async with cls._coordinator_lock:
            handle = cls._dataset_debounce_handles.pop(dataset_id, None)
            if handle:
                _cancel_task(handle.task)
            cls._dataset_dirty.discard(dataset_id)
            cls._dataset_locks.pop(dataset_id, None)

        redis = await get_redis()
        if not redis:
            return
        
        prefix = f"{METADATA_KEY_PREFIX}{dataset_id}:"
        cursor = 0
        keys_to_delete = []
        while True:
            cursor, keys = await redis.scan(cursor, match=f"{prefix}*", count=100)
            if keys:
                keys_to_delete.extend(keys)
            if cursor == 0:
                break
        
        if keys_to_delete:
            await redis.delete(*keys_to_delete)
            logger.info("[MetadataIndex] Deleted %d keys for dataset_id %d", len(keys_to_delete), dataset_id)

    @classmethod
    async def _run_sync_with_dirty_loop(
        cls,
        dataset_id: int,
        progress_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> bool:
        """在持有数据集互斥锁时执行同步，若同步期间产生 dirty 标记则循环追赶最新 DB 状态。"""
        last_success = False
        while True:
            async with cls._coordinator_lock:
                cls._dataset_dirty.discard(dataset_id)

            last_success = await cls._do_sync_dataset(dataset_id, progress_cb=progress_cb)

            async with cls._coordinator_lock:
                if dataset_id in cls._dataset_dirty:
                    logger.info(
                        "[Local Redis Sync] Dataset %d was marked dirty during sync, running follow-up sync",
                        dataset_id,
                    )
                    continue
                break
        return last_success

    @classmethod
    async def _do_sync_dataset(
        cls,
        dataset_id: int,
        progress_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> bool:
        """执行单次数据集元数据到本地 Redis 向量的同步。"""
        from app.core.orm import AsyncSessionLocal
        from app.services.metadata_service import MetadataService
        from app.services.ai.embedding_client import EmbeddingClient
        from app.services.metadata_rag_service import MetadataRagService

        async def _notify(payload: Dict[str, Any]) -> None:
            if progress_cb is None:
                return
            try:
                await progress_cb(payload)
            except Exception as cb_err:
                logger.warning("[Local Redis Sync] progress callback failed: %s", cb_err)

        dataset_name = f"#{dataset_id}"
        table_count = 0
        metric_count = 0
        doc_failed = 0
        doc_skipped = 0
        started = time.monotonic()
        try:
            async with AsyncSessionLocal() as db:
                # 1. Load Dataset Detail (Admin mode)
                dataset = await MetadataService.get_dataset_by_id(db, dataset_id, is_admin=True)
                if not dataset:
                    logger.error(f"[Local Redis Sync] Dataset {dataset_id} not found.")
                    await _notify(
                        {
                            "phase": "dataset_failed",
                            "dataset_id": dataset_id,
                            "dataset_name": dataset_name,
                            "error": "数据集不存在",
                        }
                    )
                    return False

                dataset_name = dataset.display_name or dataset.name or dataset_name
                logger.info(f"[Local Redis Sync] Starting sync for dataset: {dataset.name} (ID: {dataset_id})")

                # Check prefix/index existence first
                await MetadataIndexService.ensure_index()

                expected_docs: Dict[str, str] = {}
                relationships = dataset.relationships or []

                for table in dataset.tables:
                    if hasattr(table, "status") and table.status != 1:
                        continue
                    file_name = f"{table.physical_name}.txt"
                    content = MetadataRagService.generate_table_content(dataset, table, relationships)
                    expected_docs[file_name] = content
                table_count = len(expected_docs)

                if dataset.metrics:
                    metrics_content = MetadataRagService.generate_metrics_content(dataset)
                    if metrics_content:
                        expected_docs["_metrics.txt"] = metrics_content
                        metric_count = 1

                # 2. Get existing keys and their content hashes from Redis for this dataset
                redis = await get_redis()
                if not redis:
                    logger.warning("[Local Redis Sync] Redis not available, skipping.")
                    await _notify(
                        {
                            "phase": "dataset_failed",
                            "dataset_id": dataset_id,
                            "dataset_name": dataset_name,
                            "error": "Redis 不可用",
                        }
                    )
                    return False

                prefix = f"{METADATA_KEY_PREFIX}{dataset_id}:"
                cursor = 0
                existing_keys = []
                while True:
                    cursor, keys = await redis.scan(cursor, match=f"{prefix}*", count=100)
                    if keys:
                        existing_keys.extend(keys)
                    if cursor == 0:
                        break

                # 批量管道读取已存在的 doc_name 与 content_hash，避免串行网络往返
                existing_doc_map: Dict[str, tuple[str, Optional[str]]] = {}
                if existing_keys:
                    pipe = redis.pipeline()
                    for k in existing_keys:
                        pipe.hmget(k, ["doc_name", "content_hash"])
                    raw_res = await pipe.execute()
                    for k, item in zip(existing_keys, raw_res):
                        if not item or not isinstance(item, (list, tuple)) or not item[0]:
                            continue
                        d_name = _decode_key(item[0])
                        c_hash = _decode_key(item[1]) if len(item) > 1 and item[1] is not None else None
                        existing_doc_map[d_name] = (k, c_hash)

                # 3. Delete keys that are no longer expected
                stale_keys = [
                    info[0]
                    for name, info in existing_doc_map.items()
                    if name not in expected_docs
                ]
                if stale_keys:
                    logger.info(f"[Local Redis Sync] Deleting {len(stale_keys)} stale keys from Redis")
                    await redis.delete(*stale_keys)

                # 4. Upsert/Update keys that are expected (增量哈希比对)
                doc_total = len(expected_docs)
                for index, (doc_name, content) in enumerate(expected_docs.items(), start=1):
                    current_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    existing_entry = existing_doc_map.get(doc_name)

                    # 若文档存在且 content_hash 匹配，跳过 Embedding 计算与写操作
                    if existing_entry and existing_entry[1] == current_hash:
                        doc_skipped += 1
                        logger.debug(
                            "[Local Redis Sync] Doc %s (dataset_id=%d) hash unchanged (%s), skipping embedding",
                            doc_name, dataset_id, current_hash[:10]
                        )
                        await _notify(
                            {
                                "phase": "doc",
                                "dataset_id": dataset_id,
                                "dataset_name": dataset_name,
                                "doc_name": doc_name,
                                "done": index,
                                "total": doc_total,
                                "failed": doc_failed,
                                "skipped": True,
                            }
                        )
                        continue

                    try:
                        embedding = await EmbeddingClient.embed_text(content, use_global=True)
                        await MetadataIndexService.upsert_vector(
                            dataset_id=dataset_id,
                            doc_name=doc_name,
                            content=content,
                            embedding=embedding,
                            content_hash=current_hash,
                            skip_ensure_index=True,
                        )
                    except Exception as emb_err:
                        doc_failed += 1
                        logger.error(f"[Local Redis Sync] Embedding failed for doc {doc_name}: {emb_err}")

                    await _notify(
                        {
                            "phase": "doc",
                            "dataset_id": dataset_id,
                            "dataset_name": dataset_name,
                            "doc_name": doc_name,
                            "done": index,
                            "total": doc_total,
                            "failed": doc_failed,
                            "skipped": False,
                        }
                    )

                logger.info(
                    "[Local Redis Sync] Completed sync for dataset: %s (total: %d, skipped: %d, failed: %d)",
                    dataset.name, doc_total, doc_skipped, doc_failed
                )

                from datetime import datetime
                from sqlalchemy import update
                from app.models.metadata import MetaDataset

                await db.execute(
                    update(MetaDataset)
                    .where(MetaDataset.id == dataset_id)
                    .values(
                        rag_sync_status=2,
                        rag_sync_notes="local-redis synced",
                        rag_synced_at=datetime.now(),
                    )
                )
                await db.commit()

            await _notify(
                {
                    "phase": "dataset_done",
                    "dataset_id": dataset_id,
                    "dataset_name": dataset_name,
                    "tables": table_count,
                    "metrics": metric_count,
                    "failed": doc_failed,
                    "skipped": doc_skipped,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
            )
            return True
        except Exception as e:
            logger.error(f"[Local Redis Sync] Sync failed for dataset {dataset_id}: {e}", exc_info=True)
            await _notify(
                {
                    "phase": "dataset_failed",
                    "dataset_id": dataset_id,
                    "dataset_name": dataset_name,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                    "error": str(e),
                }
            )
            return False

    @classmethod
    async def sync_local_redis_vector(
        cls,
        dataset_id: int,
        *,
        progress_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        wait: bool = False,
        debounce_seconds: Optional[float] = None,
    ) -> bool:
        """Synchronize a dataset's metadata to local Redis vectors.

        - ``wait=False``（默认）：启用单数据集防抖（Debounce）合并与后台执行。短时间内连续调用
          将合并为一次执行；执行期间到达的新请求将标记 dirty 并在当期完成后自动追赶一轮最新 DB 数据。
        - ``wait=True``：取消当前挂起的防抖任务，独占等待互斥锁并阻塞完成同步后返回结果，
          供「重构本地向量数据」等管理编排使用。
        """
        if debounce_seconds is None:
            debounce_seconds = cls.DEFAULT_DEBOUNCE_SECONDS

        if wait:
            # 阻塞模式：取消尚未进入执行的防抖任务
            async with cls._coordinator_lock:
                handle = cls._dataset_debounce_handles.pop(dataset_id, None)
                if handle and not handle.started_execution:
                    _cancel_task(handle.task)
                cls._dataset_dirty.add(dataset_id)

            lock = await cls._get_dataset_lock(dataset_id)
            async with lock:
                return await cls._run_sync_with_dirty_loop(dataset_id, progress_cb=progress_cb)

        # 非阻塞模式（wait=False）：防抖合并调度
        async with cls._coordinator_lock:
            cls._dataset_dirty.add(dataset_id)
            handle = cls._dataset_debounce_handles.get(dataset_id)

            # 如果已有 handle 且尚未开始执行，取消以重置计时器
            if handle and not handle.started_execution and not _is_task_done(handle.task):
                _cancel_task(handle.task)
                handle = None

            # 若没有任务，或任务已结束，启动新的防抖任务
            if handle is None or _is_task_done(handle.task):
                new_handle = _DebounceHandle()

                async def _debounced_worker() -> None:
                    try:
                        if debounce_seconds > 0:
                            await asyncio.sleep(debounce_seconds)
                        new_handle.started_execution = True
                        lock = await cls._get_dataset_lock(dataset_id)
                        async with lock:
                            await cls._run_sync_with_dirty_loop(dataset_id, progress_cb=progress_cb)
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        logger.error(
                            "[Local Redis Sync] Debounced sync worker failed for dataset %d: %s",
                            dataset_id, e
                        )
                    finally:
                        async with cls._coordinator_lock:
                            if cls._dataset_debounce_handles.get(dataset_id) is new_handle:
                                cls._dataset_debounce_handles.pop(dataset_id, None)

                worker_task = asyncio.create_task(_debounced_worker())
                new_handle.task = worker_task
                cls._dataset_debounce_handles[dataset_id] = new_handle

            return True

    @staticmethod
    async def _execute_ft_search(
        redis: Any,
        idx: str,
        query_expr: str,
        vec_bytes: bytes,
        *,
        return_content: bool = True,
    ) -> Any:
        return_fields = (
            ["dataset_id", "doc_name", "content", "score"]
            if return_content
            else ["dataset_id", "doc_name", "score"]
        )
        return await redis.execute_command(
            "FT.SEARCH",
            idx,
            query_expr,
            "PARAMS",
            2,
            "vec",
            vec_bytes,
            "SORTBY",
            "score",
            "RETURN",
            len(return_fields),
            *return_fields,
            "DIALECT",
            2,
        )

    @staticmethod
    async def _hydrate_knn_content(redis: Any, items: List[Dict[str, Any]]) -> None:
        for item in items:
            if item.get("content"):
                continue
            ds_id = item.get("dataset_id")
            doc_name = item.get("doc_name") or ""
            if not ds_id or not doc_name:
                continue
            if doc_name == "_metrics.txt":
                key = f"{METADATA_KEY_PREFIX}{ds_id}:metrics"
            else:
                table_name = doc_name[:-4] if doc_name.endswith(".txt") else doc_name
                key = f"{METADATA_KEY_PREFIX}{ds_id}:table:{table_name}"
            raw_content = await redis.hget(key, "content")
            if isinstance(raw_content, bytes):
                item["content"] = raw_content.decode("utf-8", errors="replace")
            elif raw_content is not None:
                item["content"] = str(raw_content)

    @staticmethod
    async def search_knn(
        query_embedding: List[float],
        authorized_dataset_ids: Optional[List[int]],
        top_k: int = 5,
        trace: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute HNSW KNN search with authorized_dataset_ids TAG pre-filtering.
        If authorized_dataset_ids is None, we assume superuser (Admin) and perform search without TAG filter.
        """
        redis = await get_redis_binary()
        if not redis:
            MetadataIndexService._append_trace(
                trace,
                "[KNN] Aborted: Redis binary client unavailable (check REDIS_ENABLE / connection)",
            )
            return []

        vec_dim = len(query_embedding)
        expected_dim = await EmbeddingClient.get_dimensions(use_global=True)
        aligned = _align_embedding(query_embedding, expected_dim)
        vec_bytes = _vector_to_bytes(aligned)
        expected_bytes = expected_dim * 4
        MetadataIndexService._append_trace(
            trace,
            f"[KNN] Query vector dim={vec_dim}, configured embed_dimensions={expected_dim}, "
            f"vec_bytes={len(vec_bytes)} (expected {expected_bytes}), top_k={top_k}",
        )
        if vec_dim != expected_dim:
            MetadataIndexService._append_trace(
                trace,
                f"[WARN] Query vector dim ({vec_dim}) != embed_dimensions ({expected_dim}) — aligned before search",
            )
        if len(vec_bytes) != expected_bytes:
            MetadataIndexService._append_trace(
                trace,
                f"[WARN] Query vector bytes ({len(vec_bytes)}) != expected ({expected_bytes})",
            )

        idx = await MetadataIndexService.index_name()
        index_ready = await MetadataIndexService.ensure_index()
        MetadataIndexService._append_trace(trace, f"[KNN] ensure_index({idx}) => {index_ready}")

        if authorized_dataset_ids is None:
            query_expr = f"*=>[KNN {top_k} @embedding $vec AS score]"
            MetadataIndexService._append_trace(trace, "[KNN] Filter: admin — search all datasets")
        else:
            if not authorized_dataset_ids:
                MetadataIndexService._append_trace(trace, "[KNN] Aborted: empty authorized_dataset_ids")
                return []
            tag_vals = "|".join(_tag_escape(str(ds_id)) for ds_id in authorized_dataset_ids)
            query_expr = f"(@dataset_id:{{{tag_vals}}})=>[KNN {top_k} @embedding $vec AS score]"
            MetadataIndexService._append_trace(
                trace,
                f"[KNN] Filter: dataset_id in {authorized_dataset_ids}",
            )

        MetadataIndexService._append_trace(trace, f"[KNN] FT.SEARCH expr: {query_expr}")

        try:
            raw = await MetadataIndexService._execute_ft_search(
                redis,
                idx,
                query_expr,
                vec_bytes,
                return_content=False,
            )
        except Exception as exc:
            MetadataIndexService._append_trace(trace, f"[KNN] FT.SEARCH error: {exc}")
            raise

        raw_total = MetadataIndexService._ft_search_total(raw)
        MetadataIndexService._append_trace(trace, f"[KNN] FT.SEARCH raw total={raw_total}")

        items = MetadataIndexService._parse_knn_response(raw)
        if items:
            await MetadataIndexService._hydrate_knn_content(redis, items)
        MetadataIndexService._append_trace(trace, f"[KNN] Parsed result count={len(items)}")
        for i, item in enumerate(items):
            sim = item.get("similarity", 0.0)
            MetadataIndexService._append_trace(
                trace,
                f"[KNN] raw[{i + 1}] doc={item.get('doc_name')} "
                f"dataset_id={item.get('dataset_id')} similarity={sim:.4f}",
            )

        if raw_total == 0:
            sample_emb = None
            cursor = 0
            while sample_emb is None:
                cursor, keys = await redis.scan(cursor, match=f"{METADATA_KEY_PREFIX}*", count=20)
                for key in keys or []:
                    key_str = _decode_key(key)
                    emb = await redis.hget(key_str, "embedding")
                    if isinstance(emb, bytes) and len(emb) == expected_bytes:
                        sample_emb = emb
                        MetadataIndexService._append_trace(
                            trace,
                            f"[KNN Probe] Using stored embedding from {key_str!r} ({len(emb)} bytes)",
                        )
                        break
                if cursor == 0:
                    break
            if sample_emb is not None:
                probe_raw = await MetadataIndexService._execute_ft_search(
                    redis,
                    idx,
                    f"*=>[KNN 1 @embedding $vec AS score]",
                    sample_emb,
                    return_content=False,
                )
                probe_total = MetadataIndexService._ft_search_total(probe_raw)
                MetadataIndexService._append_trace(
                    trace,
                    f"[KNN Probe] Self-search with stored embedding => raw total={probe_total}",
                )
                if probe_total > 0:
                    MetadataIndexService._append_trace(
                        trace,
                        "[HINT] Stored vectors are searchable — query embedding from API may be invalid "
                        "or incompatible with indexed vectors",
                    )
                else:
                    MetadataIndexService._append_trace(
                        trace,
                        "[HINT] Self-KNN also 0 — check RediSearch index / binary Redis client",
                    )
            else:
                MetadataIndexService._append_trace(
                    trace,
                    "[HINT] 0 raw KNN hits — no valid stored embedding sample found for probe",
                )

        return items

    @staticmethod
    def _parse_knn_response(raw: Any) -> List[Dict[str, Any]]:
        if isinstance(raw, dict):
            results = _dict_get(raw, "results", "documents") or []
            items: List[Dict[str, Any]] = []
            for entry in results:
                row = _row_from_ft_entry(entry)
                if not row:
                    continue
                score = row.get("score")
                if score is not None:
                    try:
                        row["similarity"] = max(0.0, 1.0 - float(score))
                    except (TypeError, ValueError):
                        row["similarity"] = 0.0
                else:
                    row["similarity"] = 0.0
                items.append(row)
            return items

        if not raw or not isinstance(raw, (list, tuple)) or len(raw) < 2:
            return []
        items: List[Dict[str, Any]] = []
        pos = 1
        while pos + 1 < len(raw):
            fields = raw[pos + 1]
            if not isinstance(fields, (list, tuple)):
                pos += 2
                continue
            row: Dict[str, Any] = {}
            for i in range(0, len(fields) - 1, 2):
                name = MetadataIndexService._hash_field_name(fields[i])
                value = MetadataIndexService._hash_text_value(fields[i + 1])
                row[name] = value

            if "score" in row:
                try:
                    row["similarity"] = max(0.0, 1.0 - float(row["score"]))
                except (TypeError, ValueError):
                    row["similarity"] = 0.0
            else:
                row["similarity"] = 0.0

            items.append(row)
            pos += 2
        return items

    @staticmethod
    def _hash_field_name(key: Any) -> str:
        if isinstance(key, bytes):
            return key.decode("utf-8", errors="replace")
        return str(key)

    @staticmethod
    def _hash_text_value(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value) if value is not None else ""

    @staticmethod
    async def sync_all_datasets(
        *,
        progress_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        wait: bool = False,
    ) -> Dict[str, int]:
        """Synchronize all enabled datasets to Redis vectors.

        ``wait=False``（默认）沿用历史行为：逐个触发后台同步后立即返回。
        ``wait=True`` 按顺序 await 每个数据集，返回 ``{total, success, failed}``，
        供需要真实进度与汇总的「重构本地向量数据」编排使用。
        """
        from app.core.orm import AsyncSessionLocal
        from app.services.metadata_service import MetadataService

        async with AsyncSessionLocal() as db:
            try:
                datasets = await MetadataService.get_datasets(db)
                logger.info("[MetadataIndex] Found %d total datasets for startup sync", len(datasets))
                enabled = [
                    (int(ds.id), ds.display_name or ds.name or f"#{ds.id}")
                    for ds in datasets
                    if ds.status == 1
                ]
            except Exception as e:
                logger.error("[MetadataIndex] Failed during sync_all_datasets: %s", e)
                return {"total": 0, "success": 0, "failed": 0}

        if not wait:
            for dataset_id, _name in enabled:
                await MetadataIndexService.sync_local_redis_vector(
                    dataset_id, progress_cb=progress_cb
                )
            return {"total": len(enabled), "success": 0, "failed": 0}

        success = 0
        failed = 0
        for dataset_id, _name in enabled:
            ok = await MetadataIndexService.sync_local_redis_vector(
                dataset_id, progress_cb=progress_cb, wait=True
            )
            if ok:
                success += 1
            else:
                failed += 1
        return {"total": len(enabled), "success": success, "failed": failed}
