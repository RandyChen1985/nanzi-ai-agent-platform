from fastapi import APIRouter, Depends, HTTPException, Body, File, UploadFile
from fastapi.responses import StreamingResponse
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from app.core.dependencies import require_admin, require_permission
from app.core.config import settings
from app.core import redis
from app.services.config_service import ConfigService
from app.schemas.branding import BrandingSettingsUpdate
from app.services.branding_settings_service import BrandingSettingsService
from app.services.ai.ragflow_client import RagFlowClient
from app.schemas.system_config import ConfigHistoryItem
import logging
import asyncio
import traceback
import os
import time
import json
import httpx

router = APIRouter()

BRANDING_UPLOAD_DIR = "data/branding"
ALLOWED_ICON_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}
MAX_ICON_BYTES = 512 * 1024

@router.get("/configs/{key}/history", response_model=List[ConfigHistoryItem])
async def get_config_history(
    key: str,
    limit: int = 50,
    user: Dict = Depends(require_permission("menu", "menu:system:config"))
):
    """
    Get history for a specific config key.
    """
    try:
        return await ConfigService.get_config_history(key, limit)
    except Exception as e:
        logging.error(f"Failed to fetch config history for {key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class ConnectionTestResponse(BaseModel):
    status: str
    message: str
    logs: List[str]
    dataset_count: Optional[int] = None

class EmbedConnectionTestPayload(BaseModel):
    embed_api_url: Optional[str] = None
    embed_api_key: Optional[str] = None
    embed_model_name: Optional[str] = None
    ragflow_api_url: Optional[str] = None
    ragflow_api_key: Optional[str] = None
    use_saved_api_key: bool = False
    external_sql_api_url: Optional[str] = None
    external_sql_api_key: Optional[str] = None
    external_sql_data_source: Optional[str] = None
    use_saved_external_sql_api_key: bool = False


class RagFlowConnectionTestPayload(EmbedConnectionTestPayload):
    """RAGFlow 元数据连接测试的显式请求模型。"""

@router.post("/test-connection/{component}", response_model=ConnectionTestResponse)
async def test_connection(
    component: str,
    payload: Optional[RagFlowConnectionTestPayload] = None,
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """
    Test connection to infrastructure components (Redis, Global Embeddings) with detailed logs.
    """
    logs = []
    status = "failed"
    message = ""
    dataset_count: Optional[int] = None
    sensitive_values: List[str] = []

    def log(msg: str):
        logs.append(msg)
        logging.info(f"[SystemCheck] {msg}")

    try:
        if component == "redis":
            log(f"Target: Redis ({settings.REDIS_HOST}:{settings.REDIS_PORT})")
            log(f"DB: {settings.REDIS_DB}, Enabled: {settings.REDIS_ENABLE}")
            
            if not settings.REDIS_ENABLE:
                log("Redis is disabled in configuration.")
                status = "skipped"
                message = "Redis is disabled."
            else:
                log("Getting Redis client...")
                r = await redis.get_redis()
                if not r:
                     # Attempt to init if not ready (though lifespan should have done it)
                     log("Redis client not ready, attempting initialization...")
                     await redis.init_redis()
                     r = await redis.get_redis()
                
                if not r:
                    raise Exception("Failed to initialize Redis client")

                log("Executing: PING")
                result = await r.ping()
                log(f"PING Result: {result}")
                
                status = "success"
                message = "Redis connection successful."

        elif component == "chatbi_kb":
            log("Target: ChatBI RAGFlow KB (chatbi-example-meta)")
            log("Ensuring KB initialization (Auto-create if not exists)...")
            from app.services.chatbi_example_service import ExampleService
            kb_id = await ExampleService.ensure_chatbi_sample_kb_id()
            log(f"Successfully ensured KB. ID: {kb_id}")
            
            # Verify connectivity to RAGFlow by listing documents
            client = RagFlowClient()
            log("Testing connection to RAGFlow by listing documents...")
            docs = await client.list_documents(kb_id, page_size=5)
            log(f"Successfully connected. Found {len(docs)} documents in dataset.")
            
            status = "success"
            message = f"ChatBI KB connection successful. ID: {kb_id}"

        elif component == "ragflow_metadata":
            log("Target: RAGFlow metadata service")
            test_url = (payload.ragflow_api_url if payload else "") or ""
            test_key = (payload.ragflow_api_key if payload else "") or ""
            test_url = test_url.strip().rstrip("/")
            test_key = test_key.strip()

            if payload and payload.use_saved_api_key:
                test_key = (await ConfigService.get("ragflow_api_key") or "").strip()
            elif "****" in test_key:
                # 兼容未更新的前端：脱敏值不能用于外部认证，应回退到保存值。
                test_key = (await ConfigService.get("ragflow_api_key") or "").strip()

            if test_key:
                sensitive_values.append(test_key)
            if not test_url:
                raise ValueError("RAGFlow 服务地址为空，请先填写 URL。")
            if not test_key:
                raise ValueError("RAGFlow API Key 为空，请先填写 API Key。")

            log(f"API URL: {test_url}")
            log("Sending dataset list request (page=1, page_size=1)...")
            client = RagFlowClient(
                config_prefix="ragflow",
                override_url=test_url,
                override_key=test_key,
            )
            datasets = await client.list_datasets(page=1, page_size=1)
            dataset_count = len(datasets or [])
            status = "success"
            if dataset_count:
                message = f"RAGFlow 连接成功，已获取 {dataset_count} 个数据集"
            else:
                message = "RAGFlow 连接成功，当前没有数据集"

        elif component == "remote_sql":
            log("Target: remote SQL execution service")
            test_url = ((payload.external_sql_api_url if payload else "") or "").strip().rstrip("/")
            test_key = ((payload.external_sql_api_key if payload else "") or "").strip()
            data_source = ((payload.external_sql_data_source if payload else "") or "").strip()

            if payload and payload.use_saved_external_sql_api_key or "****" in test_key:
                test_key = (await ConfigService.get("external_sql_api_key") or "").strip()
            if test_key:
                sensitive_values.append(test_key)
            if not test_url:
                raise ValueError("远程 SQL 服务地址为空，请先填写 URL。")
            if not test_key:
                raise ValueError("远程 SQL API Key 为空，请先填写 API Key。")
            if not data_source:
                raise ValueError("远程 SQL 数据源 ID 为空，请先填写数据源。")

            log(f"API URL: {test_url}")
            log(f"Data source: {data_source}")
            log("Executing: SELECT 1")
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    test_url,
                    headers={"Content-Type": "application/json", "X-API-Key": test_key},
                    json={"data_source": data_source, "sql": "SELECT 1", "params": {}},
                )
                response.raise_for_status()
                response_data = response.json()
            if isinstance(response_data, dict) and response_data.get("code") not in (None, 200):
                raise ValueError(response_data.get("message") or "远程 SQL 服务返回失败")
            status = "success"
            message = "远程 SQL 连接成功，SELECT 1 执行正常"

        elif component == "global_embed":
            log("Target: Global Embedding Service (local-redis backend)")
            
            # 优先读取 Payload 中的临时测试参数
            test_url = payload.embed_api_url if payload else None
            test_key = payload.embed_api_key if payload else None
            test_model = payload.embed_model_name if payload else None
            
            # 清洗掩码参数（如果包含 '*' 或者是全 '.'，说明是前端脱敏展示的伪密钥，不能用于真实测试，需降级读取数据库）
            if test_key and ("*" in test_key or all(c == "." for c in test_key)):
                test_key = None
                
            # 如果没有，从数据库拉取
            if not test_url:
                test_url = await ConfigService.get("embed_api_url")
            if not test_key:
                test_key = await ConfigService.get("embed_api_key")
            if not test_model:
                test_model = await ConfigService.get("embed_model_name", default="bge-m3")

            # 降级：LLM 底座配置
            if not test_url:
                test_url = await ConfigService.get("llm_base_url")
            if not test_key:
                test_key = await ConfigService.get("llm_api_key")
                
            test_url = (test_url or "").strip()
            test_key = (test_key or "").strip()
            test_model = (test_model or "").strip()
            
            log(f"API URL: {test_url}")
            log(f"Model Name: {test_model}")
            
            if not test_url:
                raise Exception("Embedding API URL 为空，未完成配置。")

            from app.utils.model_providers import normalize_embedding_endpoint

            url = normalize_embedding_endpoint(test_url)
                
            log(f"Sending test vector request to: {url}")
            
            headers = {"Content-Type": "application/json"}
            if test_key:
                headers["Authorization"] = f"Bearer {test_key}"
            payload_data = {"model": test_model, "input": "hello"}
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload_data, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                
            items = data.get("data") or []
            if not items:
                raise Exception("Embedding API 返回空 data")
            emb = items[0].get("embedding")
            if not emb:
                raise Exception("Embedding API 返回结果无 embedding 字段")
                
            log(f"Successfully generated embedding vector of length: {len(emb)}")
            status = "success"
            message = "Embedding connection test successful."

        else:
            raise HTTPException(status_code=400, detail=f"Unknown component: {component}")

    except HTTPException:
        # Re-raise HTTP exceptions to be handled by FastAPI's exception handlers
        raise
    except Exception as e:
        error_message = str(e)
        for sensitive_value in sensitive_values:
            if sensitive_value:
                error_message = error_message.replace(sensitive_value, "[REDACTED]")
        log(f"❌ Error: {error_message}")
        traceback_text = traceback.format_exc()
        for sensitive_value in sensitive_values:
            if sensitive_value:
                traceback_text = traceback_text.replace(sensitive_value, "[REDACTED]")
        log(f"Traceback: {traceback_text}")
        status = "error"
        message = f"Connection failed: {error_message}"

    return ConnectionTestResponse(
        status=status,
        message=message,
        logs=logs,
        dataset_count=dataset_count,
    )

class RedisKeysResponse(BaseModel):
    count: int
    keys: List[str]

@router.post("/redis/keys", response_model=RedisKeysResponse)
async def get_redis_keys(
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """
    Get Redis total key count and list all keys.
    """
    try:
        if not settings.REDIS_ENABLE:
             raise HTTPException(status_code=400, detail="Redis is disabled")
             
        r = await redis.get_redis()
        if not r:
            await redis.init_redis()
            r = await redis.get_redis()
            
        if not r:
             raise HTTPException(status_code=500, detail="Redis client not available")

        # Get count
        count = await r.dbsize()
        
        # Get keys (using KEYS * as requested, careful in prod)
        keys = await r.keys("*")
        # keys are already decoded because decode_responses=True in app.core.redis
        
        return RedisKeysResponse(count=count, keys=keys)
        
    except Exception as e:
        logging.error(f"Failed to scan redis keys: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/redis/flush")
async def flush_redis_keys(
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """
    Clear all keys in the current Redis database.
    """
    try:
        if not settings.REDIS_ENABLE:
             raise HTTPException(status_code=400, detail="Redis is disabled")
             
        r = await redis.get_redis()
        if not r:
            await redis.init_redis()
            r = await redis.get_redis()
            
        if not r:
             raise HTTPException(status_code=500, detail="Redis client not available")

        # Intelligent Flush: Preserve conversation history
        # Instead of flushdb(), we scan and delete selective keys
        cursor = '0'
        deleted_count = 0
        preserved_count = 0
        
        while cursor != 0:
            cursor, keys = await r.scan(cursor=cursor, match='*', count=1000)
            if keys:
                keys_to_delete = []
                for key in keys:
                    # Check if key should be preserved
                    # Pattern: conversation:{user_id}:{conversation_id}:history
                    if key.startswith("conversation:"):
                        preserved_count += 1
                    else:
                        keys_to_delete.append(key)
                
                if keys_to_delete:
                    await r.delete(*keys_to_delete)
                    deleted_count += len(keys_to_delete)

        logging.info(f"Redis cleanup by {user.get('user_name')}: Deleted {deleted_count}, Preserved {preserved_count} conversation keys.")
        return {
            "status": "success", 
            "message": f"Cleaned {deleted_count} keys. Preserved {preserved_count} conversation histories."
        }
        
    except Exception as e:
        logging.error(f"Failed to flush redis keys: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class RedisDeleteKeysRequest(BaseModel):
    keys: List[str]

class RedisDeleteKeysResponse(BaseModel):
    status: str
    deleted_count: int
    message: str

@router.post("/redis/delete-keys", response_model=RedisDeleteKeysResponse)
async def delete_redis_keys_batch(
    data: RedisDeleteKeysRequest,
    user: Dict = Depends(require_permission("element", "element:system:config_save")),
):
    """Batch delete selected Redis keys."""
    try:
        if not settings.REDIS_ENABLE:
            raise HTTPException(status_code=400, detail="Redis is disabled")

        if not data.keys:
            raise HTTPException(status_code=400, detail="No keys specified")

        if len(data.keys) > 5000:
            raise HTTPException(status_code=400, detail="Too many keys (max 5000)")

        r = await redis.get_redis()
        if not r:
            await redis.init_redis()
            r = await redis.get_redis()

        if not r:
            raise HTTPException(status_code=500, detail="Redis client not available")

        deleted_count = 0
        chunk_size = 500
        for i in range(0, len(data.keys), chunk_size):
            chunk = data.keys[i : i + chunk_size]
            if chunk:
                deleted_count += await r.delete(*chunk)

        logging.info(
            "Redis selective cleanup by %s: deleted %s keys",
            user.get("user_name"),
            deleted_count,
        )
        return RedisDeleteKeysResponse(
            status="success",
            deleted_count=deleted_count,
            message=f"Deleted {deleted_count} key(s).",
        )

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Failed to batch delete redis keys: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/redis/rebuild-vectors")
async def rebuild_vector_indexes(
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """启动一次任务化的本地向量重构（删除旧索引 → 重建 → 全量重新向量化）。

    立即返回 ``task_id``，进度与逐条日志通过
    ``GET /redis/rebuild-vectors/{task_id}/events``（SSE）订阅。
    """
    try:
        from app.services.ai.local_vector_rebuild import (
            get_active_rebuild_task_id,
            start_local_vector_rebuild,
        )
        from app.services.task_lock import describe_lock_owner

        task_id = await start_local_vector_rebuild(trigger="manual")
        if not task_id:
            running = await get_active_rebuild_task_id()
            raise HTTPException(
                status_code=409,
                detail=(
                    f"已有重构任务正在执行（{describe_lock_owner(running)}）。"
                    "该锁带 30 分钟 TTL，会自动释放；若确认没有任务在跑，可稍后重试。"
                ),
            )
        return {
            "status": "success",
            "task_id": task_id,
            "message": "本地向量重构任务已启动，正在后台执行，可在进度抽屉中查看实时日志",
        }
    except HTTPException:
        raise
    except RuntimeError as e:
        detail = str(e)
        if "disabled" in detail.lower():
            raise HTTPException(status_code=400, detail=detail)
        raise HTTPException(status_code=500, detail=detail)
    except Exception as e:
        logging.error(f"Failed to rebuild vector indexes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/redis/rebuild-vectors/{task_id}/events")
async def rebuild_vector_indexes_events(
    task_id: str,
    user: Dict = Depends(require_permission("element", "element:system:config_save")),
):
    """订阅本地向量重构任务的实时进度与日志（先回放历史事件，再阻塞推送新事件）。"""
    from app.services.task_log_service import task_log_service

    task = await task_log_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="重构任务不存在或已过期")

    async def event_stream():
        last_id = "0-0"
        try:
            while True:
                events = await task_log_service.read_events(task_id, after_id=last_id)
                if not events:
                    events = await task_log_service.read_new_events(
                        task_id, after_id=last_id, block_ms=1000
                    )
                for item in events:
                    event_id = item.pop("id", None)
                    if event_id:
                        last_id = event_id
                    event_name = item.get("event", "progress")
                    yield (
                        f"id: {last_id}\nevent: {event_name}\n"
                        f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                    )
                    if task_log_service.is_terminal(event_name):
                        return
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

# --- Redis Browser Endpoints ---

class RedisKeyListItem(BaseModel):
    name: str
    type: str

class RedisKeyListResponse(BaseModel):
    count: int
    keys: List[RedisKeyListItem]

@router.get("/redis/keys-list", response_model=RedisKeyListResponse)
async def list_redis_keys(
    pattern: str = "*",
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """
    List Redis keys matching a pattern, along with their data type.
    """
    try:
        if not settings.REDIS_ENABLE:
            raise HTTPException(status_code=400, detail="Redis is disabled")
            
        r = await redis.get_redis()
        if not r:
            await redis.init_redis()
            r = await redis.get_redis()
            
        if not r:
            raise HTTPException(status_code=500, detail="Redis client not available")

        # Use SCAN to scan matched keys safely
        keys = []
        cursor = '0'
        
        # Limit to 5000 keys maximum to prevent OOM
        while len(keys) < 5000:
            cursor, batch = await r.scan(cursor=cursor, match=pattern, count=1000)
            keys.extend(batch)
            if cursor == 0 or int(cursor) == 0:
                break
                
        # Fetch types
        results = []
        for key in keys[:5000]:
            k_type = await r.type(key)
            results.append(RedisKeyListItem(name=key, type=k_type))
            
        return RedisKeyListResponse(count=len(results), keys=results)
        
    except Exception as e:
        logging.error(f"Failed to list redis keys: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class RedisKeyDetailResponse(BaseModel):
    name: str
    type: str
    ttl: int
    value: Any

@router.get("/redis/key-detail", response_model=RedisKeyDetailResponse)
async def get_redis_key_detail(
    key: str,
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """
    Get detailed value and metadata of a specific Redis key.
    """
    try:
        if not settings.REDIS_ENABLE:
            raise HTTPException(status_code=400, detail="Redis is disabled")
            
        r = await redis.get_redis()
        if not r:
            await redis.init_redis()
            r = await redis.get_redis()
            
        if not r:
            raise HTTPException(status_code=500, detail="Redis client not available")

        # Verify key exists
        exists = await r.exists(key)
        if not exists:
            raise HTTPException(status_code=404, detail="Key not found")

        # Get type & ttl
        k_type = await r.type(key)
        ttl = await r.ttl(key)

        # Retrieve value based on type
        value = None
        if k_type == "string":
            value = await r.get(key)
        elif k_type == "hash":
            # 使用 binary 客户端读取，防止含 embedding 等二进制字段时 UnicodeDecodeError
            r_binary = await redis.get_redis_binary()
            raw_hash = await r_binary.hgetall(key)
            value = {}
            for field_bytes, val_bytes in raw_hash.items():
                # 字段名解码
                try:
                    field_str = field_bytes.decode("utf-8") if isinstance(field_bytes, bytes) else field_bytes
                except Exception:
                    field_str = f"<binary-key: {len(field_bytes)} bytes>"
                # 字段值解码
                if isinstance(val_bytes, bytes):
                    try:
                        value[field_str] = val_bytes.decode("utf-8")
                    except Exception:
                        value[field_str] = f"<binary: {len(val_bytes)} bytes>"
                else:
                    value[field_str] = val_bytes
        elif k_type == "list":
            value = await r.lrange(key, 0, -1)
        elif k_type == "set":
            value = list(await r.smembers(key))
        elif k_type == "zset":
            zset_data = await r.zrange(key, 0, -1, withscores=True)
            # Format as [{"member": m, "score": s}]
            value = [{"member": m, "score": s} for m, s in zset_data]
        else:
            value = f"(Unsupported type: {k_type})"

        return RedisKeyDetailResponse(name=key, type=k_type, ttl=ttl, value=value)

    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Failed to get redis key detail for {key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/redis/key")
async def delete_redis_key(
    key: str,
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """
    Delete a specific key from Redis.
    """
    try:
        if not settings.REDIS_ENABLE:
            raise HTTPException(status_code=400, detail="Redis is disabled")
            
        r = await redis.get_redis()
        if not r:
            await redis.init_redis()
            r = await redis.get_redis()
            
        if not r:
            raise HTTPException(status_code=500, detail="Redis client not available")

        deleted = await r.delete(key)
        if not deleted:
            raise HTTPException(status_code=404, detail="Key not found or already deleted")

        return {"status": "success", "message": f"Key '{key}' deleted successfully."}

    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Failed to delete redis key {key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- New Configuration Endpoints ---

class ConfigItem(BaseModel):
    key: str
    value: str
    description: Optional[str] = None
    is_secret: bool = False

class ConfigUpdateRequest(BaseModel):
    updates: List[ConfigItem]


DEPLOYMENT_CHECKLIST_KEY = "deployment_setup_checklist"
DEPLOYMENT_CHECKLIST_VERSION = "post_install_v2"
DEPLOYMENT_CHECKLIST_ITEMS = (
    "model_config",
    "knowledge_environment",
    "system_config",
    "agent_config",
)


class DeploymentChecklistUpdate(BaseModel):
    item_id: str
    completed: bool = True


def _default_deployment_checklist() -> Dict[str, Any]:
    return {
        "version": DEPLOYMENT_CHECKLIST_VERSION,
        "completed": {item_id: False for item_id in DEPLOYMENT_CHECKLIST_ITEMS},
    }


async def _get_deployment_checklist() -> Dict[str, Any]:
    default = _default_deployment_checklist()
    raw = await ConfigService.get(DEPLOYMENT_CHECKLIST_KEY)
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default
    if not isinstance(value, dict) or value.get("version") != DEPLOYMENT_CHECKLIST_VERSION:
        return default
    completed = value.get("completed")
    if not isinstance(completed, dict):
        return default
    return {
        "version": DEPLOYMENT_CHECKLIST_VERSION,
        "completed": {
            item_id: bool(completed.get(item_id, False))
            for item_id in DEPLOYMENT_CHECKLIST_ITEMS
        },
    }


@router.get("/setup-checklist")
async def get_deployment_checklist(user: Dict = Depends(require_admin)):
    """Return the deployment checklist; only administrators can see it."""
    return await _get_deployment_checklist()


@router.put("/setup-checklist")
async def update_deployment_checklist(
    request: DeploymentChecklistUpdate,
    user: Dict = Depends(require_admin),
):
    """Update one deployment checklist item for the whole installation."""
    if request.item_id not in DEPLOYMENT_CHECKLIST_ITEMS:
        raise HTTPException(status_code=400, detail="Invalid deployment checklist item")
    checklist = await _get_deployment_checklist()
    checklist["completed"][request.item_id] = request.completed
    await ConfigService.set_config(
        DEPLOYMENT_CHECKLIST_KEY,
        json.dumps(checklist, ensure_ascii=False, separators=(",", ":")),
        description="首次部署后的管理员检查清单状态",
        category="internal",
        is_secret=False,
        changed_by=user.get("user_name", "admin"),
        change_reason="Update deployment setup checklist",
    )
    return checklist

@router.get("/configs", response_model=Dict[str, List[Dict[str, Any]]])
async def get_system_configs(
    user: Dict = Depends(require_permission("menu", "menu:system:config"))
):
    """
    Get all system configurations grouped by category.
    Sensitive values are masked.
    """
    try:
        return await ConfigService.get_all_configs_grouped()
    except Exception as e:
        logging.error(f"Failed to get configs: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/configs")
async def update_system_configs(
    request: ConfigUpdateRequest,
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """
    Bulk update system configurations.
    """
    try:
        updates = [item.model_dump() for item in request.updates]
        # Pass the username to the service for audit logging
        await ConfigService.bulk_update(updates, changed_by=user.get("user_name", "admin"))

        touched_keys = {str(item.get("key") or "") for item in updates}
        if "platform_timezone" in touched_keys:
            from app.services.platform_timezone import (
                PLATFORM_TIMEZONE_CONFIG_KEY,
                refresh_platform_timezone,
                validate_timezone_name,
            )
            from app.services.ai.scheduler_service import scheduler_service
            from app.services.ai.time_anchor import get_default_timezone

            # Re-validate persisted value and refresh caches
            raw = await ConfigService.get(PLATFORM_TIMEZONE_CONFIG_KEY, "Asia/Shanghai")
            validated = validate_timezone_name(raw)
            if validated != raw:
                await ConfigService.update_config_value(
                    PLATFORM_TIMEZONE_CONFIG_KEY,
                    validated,
                    changed_by=user.get("user_name", "admin"),
                    change_reason="Normalize invalid platform_timezone",
                )
            await refresh_platform_timezone()
            get_default_timezone.cache_clear()
            try:
                await scheduler_service.apply_platform_timezone_change()
            except Exception as sched_exc:
                logging.warning(f"Scheduler reload after timezone change failed: {sched_exc}")

        return {"status": "success", "message": "Configurations updated successfully."}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logging.error(f"Failed to update configs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/branding")
async def get_branding_settings(
    user: Dict = Depends(require_permission("menu", "menu:system:config"))
):
    """获取品牌个性化配置（管理端）。"""
    return await BrandingSettingsService.get_raw_settings()


@router.put("/branding")
async def update_branding_settings(
    body: BrandingSettingsUpdate,
    user: Dict = Depends(require_permission("element", "element:system:config_save")),
):
    """保存品牌个性化配置。"""
    await BrandingSettingsService.update_settings(
        enabled=body.enabled,
        product_name=body.product_name,
        login_subtitle=body.login_subtitle,
        icon_url=body.icon_url,
        hide_login_sso=body.hide_login_sso,
        hide_version_link=body.hide_version_link,
        contact_markdown=body.contact_markdown,
        copyright_text=body.copyright_text,
        default_agent_name=body.default_agent_name,
        changed_by=user.get("user_name", "admin"),
    )
    return await BrandingSettingsService.get_raw_settings()


@router.post("/branding/icon")
async def upload_branding_icon(
    file: UploadFile = File(...),
    user: Dict = Depends(require_permission("element", "element:system:config_save")),
):
    """上传品牌 Logo / Favicon（PNG/JPEG/WebP/SVG，最大 512KB）。"""
    content_type = (file.content_type or "").lower()
    ext = ALLOWED_ICON_TYPES.get(content_type)
    if not ext:
        raise HTTPException(status_code=400, detail="仅支持 PNG、JPEG、WebP、SVG 图片")

    data = await file.read()
    if len(data) > MAX_ICON_BYTES:
        raise HTTPException(status_code=400, detail="图片大小不能超过 512KB")

    os.makedirs(BRANDING_UPLOAD_DIR, exist_ok=True)
    filename = f"icon{ext}"
    save_path = os.path.join(BRANDING_UPLOAD_DIR, filename)
    with open(save_path, "wb") as f:
        f.write(data)

    icon_url = f"/branding/{filename}?t={int(time.time())}"
    return {"icon_url": icon_url}


# --- Log & Partition Management Endpoints (Admin Only) ---

from app.core.orm import get_db_session
from sqlalchemy.ext.asyncio import AsyncSession

class LogConfigUpdateRequest(BaseModel):
    audit_log_retention_days: int

@router.get("/logs/config")
async def get_logs_config(
    user: Dict = Depends(require_admin)
):
    """
    Get audit log retention configuration days.
    """
    retention = await ConfigService.get("audit_log_retention_days", default="90")
    return {"audit_log_retention_days": int(retention) if retention.isdigit() else 90}

@router.post("/logs/config")
async def update_logs_config(
    payload: LogConfigUpdateRequest,
    user: Dict = Depends(require_admin)
):
    """
    Update log retention days configuration.
    """
    days = payload.audit_log_retention_days
    if days <= 0 or days > 3650:
        raise HTTPException(status_code=400, detail="日志保留天数必须在 1 到 3650 之间")
    
    await ConfigService.update_config_value(
        "audit_log_retention_days", 
        str(days), 
        changed_by=user.get("user_name", "admin"),
        change_reason="Update via Log Management Tab"
    )
    return {"status": "success", "message": "配置更新成功"}

@router.get("/logs/partitions")
async def get_logs_partitions(
    user: Dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get MySQL physical partition lists for audit and trace logs.
    """
    from app.services.partition_service import PartitionService
    try:
        partitions = await PartitionService.get_partition_status(db)
        return partitions
    except Exception as e:
        logging.error(f"Failed to fetch partitions: {e}")
        raise HTTPException(status_code=500, detail=f"获取分区状态失败: {str(e)}")

@router.post("/logs/cleanup")
async def manual_cleanup_logs(
    user: Dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Manually trigger cleanup of expired audit and trace logs.
    """
    from app.services.partition_service import PartitionService
    retention_str = await ConfigService.get("audit_log_retention_days", default="90")
    try:
        retention_days = int(retention_str)
    except (ValueError, TypeError):
        retention_days = 90
        
    try:
        res = await PartitionService.prune_expired_logs(db, retention_days)
        return res
    except Exception as e:
        logging.error(f"Failed to cleanup logs: {e}")
        raise HTTPException(status_code=500, detail=f"清理历史日志失败: {str(e)}")
