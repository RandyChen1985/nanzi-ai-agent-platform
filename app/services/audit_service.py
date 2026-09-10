import asyncio
import logging
import json
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy import insert
from app.core.orm import AsyncSessionLocal
from app.models.audit import AccessLog
from app.core.config import settings

logger = logging.getLogger(__name__)

MAX_AUDIT_TEXT_BYTES = 10 * 1024
_TRUNCATION_SUFFIX = "...(truncated)"


def _sanitize_text_field(value: Optional[str]) -> Optional[str]:
    """移除 PostgreSQL 文本字段不允许的 NUL 字节。"""
    if value is None:
        return None
    return value.replace("\x00", "")


def _truncate_text_field(
    value: Optional[str],
    max_bytes: int = MAX_AUDIT_TEXT_BYTES,
) -> Optional[str]:
    """按 UTF-8 字节数截断审计文本，避免超出 MySQL TEXT 字段限制。"""
    value = _sanitize_text_field(value)
    if value is None:
        return None

    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value

    suffix_bytes = _TRUNCATION_SUFFIX.encode("utf-8")
    content_bytes = max(0, max_bytes - len(suffix_bytes))
    prefix = encoded[:content_bytes].decode("utf-8", errors="ignore")
    return prefix + _TRUNCATION_SUFFIX

class AuditService:
    # 进程内回退队列（单机 / Redis 不可用时使用），保持既有接口不破坏。
    _queue = asyncio.Queue()
    _worker_task = None
    _stop_event = asyncio.Event()

    # Redis 共享队列：FIFO（RPUSH 队尾入 + BLPOP 队头出），多节点共享消费。
    # 落库失败会重新放回队列并带上 _retry_count，超过上限才丢弃，避免死循环。
    _REDIS_QUEUE_KEY = "audit:log_queue"
    _RETRY_FIELD = "_retry_count"
    _MAX_RETRIES = 5
    _REDIS_BATCH_TIMEOUT_SEC = 1.0

    # API Endpoint to Feature Name Mapping
    FEATURE_MAP = {
        "/api/portal/auth/login": "用户登录",
        "/api/portal/agents": "智能体中心",
        "/api/portal/prompts": "提示词工程",
        "/api/portal/models": "模型管理",
        "/api/portal/tools": "工具中心",
        "/api/portal/mcp": "MCP服务管理",
        "/api/portal/system/configs": "系统参数配置",
        "/api/portal/slash-commands": "快捷指令管理",
        "/api/portal/audit": "审计日志监控",
        "/api/portal/dashboard": "仪表盘分析",
        "/api/portal/ragflow": "知识库开发平台",
        "/api/portal/roles": "权限与角色",
        "/api/v1/chat": "AI对话服务",
        "/api/v1/tasks": "智能体任务中心"
    }

    @classmethod
    def get_feature_name(cls, endpoint: str) -> str:
        """Resolve feature name from endpoint using prefix matching"""
        # Try exact match first
        if endpoint in cls.FEATURE_MAP:
            return cls.FEATURE_MAP[endpoint]
        
        # Try prefix match (sorted by length descending to get the most specific match)
        sorted_prefixes = sorted(cls.FEATURE_MAP.keys(), key=len, reverse=True)
        for prefix in sorted_prefixes:
            if endpoint.startswith(prefix):
                return cls.FEATURE_MAP[prefix]
        
        return "通用/其它"

    @classmethod
    async def _redis_client(cls):
        """返回审计专用的 Redis 客户端；不可用（配置关/未连接/异常）时返回 None。

        这里不主动 ping 探测，保持低成本；实际读写异常会由各处回退到内存队列。
        """
        try:
            if not settings.AUDIT_USE_REDIS_QUEUE:
                return None
            from app.core import redis as redis_mod
            r = await redis_mod.get_redis()
            return r
        except Exception as e:
            logger.warning(f"[Audit] Redis unavailable, fallback to in-process queue: {e}")
            return None

    @classmethod
    async def _redis_push_batch(cls, batch: List[Dict[str, Any]]):
        """将一批日志以 JSON 序列化后 RPUSH 到 Redis 队列。返回是否成功。"""
        r = await cls._redis_client()
        if r is None:
            return False
        try:
            payloads = [json.dumps(item, ensure_ascii=False, default=str) for item in batch]
            if payloads:
                await r.rpush(cls._REDIS_QUEUE_KEY, *payloads)
            return True
        except Exception as e:
            logger.error(f"❌ Failed to RPUSH audit logs to Redis: {e}")
            return False

    @classmethod
    async def _redis_pop_batch(cls, batch_size: int) -> List[Optional[Dict[str, Any]]]:
        """从 Redis 队列 BLPOP 最多 batch_size 条日志（每条带 1s 超时，累计到 batch_size 或空超时）。

        返回解码后的 log_data 列表。Redis 不可用 / 队列空时返回空列表。
        """
        r = await cls._redis_client()
        if r is None:
            return []
        out: List[Optional[Dict[str, Any]]] = []
        try:
            for _ in range(batch_size):
                item = await r.blpop(cls._REDIS_QUEUE_KEY, timeout=cls._REDIS_BATCH_TIMEOUT_SEC)
                if item is None:
                    break  # 该轮无更多数据
                _key, value = item
                try:
                    out.append(json.loads(value))
                except (json.JSONDecodeError, TypeError):
                    # 脏数据：记录并跳过，不让单条坏数据卡住整条队列
                    logger.error(f"[Audit] Dropped malformed queue item: {value[:200]!r}")
            return out
        except Exception as e:
            logger.error(f"❌ Failed to BLPOP audit logs from Redis: {e}")
            return []

    @classmethod
    async def _redis_len(cls) -> int:
        r = await cls._redis_client()
        if r is None:
            return 0
        try:
            return int(await r.llen(cls._REDIS_QUEUE_KEY))
        except Exception:
            return 0

    @classmethod
    async def start_worker(cls):
        """启动后台日志处理 Worker"""
        if cls._worker_task is not None:
            return
        
        cls._stop_event.clear()
        cls._worker_task = asyncio.create_task(cls._worker_loop())
        logger.info("🚀 Audit Log Worker started.")

    @classmethod
    async def stop_worker(cls):
        """停止后台日志处理 Worker"""
        if cls._worker_task is None:
            return
            
        cls._stop_event.set()
        # Redis 队列模式下队列可能持续有数据（其它节点仍在生产），无需等待排空；
        # 这里只等 worker 退出自己当前轮次的处理循环。
        try:
            await asyncio.wait_for(cls._worker_task, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("⚠️ Audit Log Worker stop timeout.")
        
        cls._worker_task = None
        cls._stop_event.clear()
        logger.info("🛑 Audit Log Worker stopped.")

    @classmethod
    async def enqueue_log(cls, log_data: Dict[str, Any]):
        """将日志放入异步处理队列（Redis 优先，Redis 不可用时回退进程内队列）"""
        if await cls._redis_push_batch([log_data]):
            return
        # Redis 不可用或配置关闭：回退进程内队列（保单机可用、不丢）
        try:
            await cls._queue.put(log_data)
        except Exception as e:
            logger.error(f"Failed to enqueue audit log: {e}")

    @classmethod
    async def flush(cls):
        """强制刷新队列中的日志到数据库（先取 Redis 再取内存，合并落库）"""
        batch = []
        # 1. 从 Redis 批量弹出（非强制——优先取尽当前可立即取到的）
        redis_batch = await cls._redis_pop_batch(200)
        if redis_batch:
            batch.extend(redis_batch)
        # 2. 从内存队列取尽
        while not cls._queue.empty():
            try:
                batch.append(cls._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        
        if batch:
            await cls._flush_batch(batch)

    @classmethod
    async def _worker_loop(cls):
        """后台处理循环：优先消费 Redis 共享队列，Redis 不可用时回退进程内队列；批量落库。"""
        batch = []
        last_flush_time = datetime.now()

        # 停止时若内存/Redis 队列均已空则退出；否则继续消费排空（多节点下本节点排空后交给其它节点）。
        while not (
            cls._stop_event.is_set()
            and cls._queue.empty()
            and (await cls._redis_len()) == 0
        ):
            try:
                # 1. 优先从 Redis 取一批（最多 BATCH 上限内循环消费直到 50 条或拿空）
                if await cls._redis_client() is not None:
                    redis_batch = await cls._redis_pop_batch(50)
                    if redis_batch:
                        batch.extend(redis_batch)
                else:
                    redis_batch = []

                # 2. Redis 拿不到（不可用/空超时）时，回退从内存队列取 1 条（带超时避免忙等）
                if not redis_batch:
                    try:
                        log_item = await asyncio.wait_for(cls._queue.get(), timeout=1.0)
                        batch.append(log_item)
                    except asyncio.TimeoutError:
                        pass

                # 3. 达到批量大小或超过 1 秒未刷新 → 落库
                now = datetime.now()
                if len(batch) >= 50 or (len(batch) > 0 and (now - last_flush_time).total_seconds() >= 1):
                    await cls._flush_batch(batch)
                    batch = []
                    last_flush_time = now

            except Exception as e:
                logger.error(f"Error in Audit Log Worker loop: {e}")
                await asyncio.sleep(1)  # 发生严重错误时避让

    @classmethod
    async def _flush_batch(cls, batch: List[Dict[str, Any]]):
        """执行批量插入；落库失败时把未超重试上限的日志重新放回队列，避免丢失与死循环。"""
        if not batch:
            return

        try:
            async with AsyncSessionLocal() as session:
                # 使用 SQLAlchemy insert() 的参数化批量插入，效率最高且安全
                stmt = insert(AccessLog)
                # 确保映射数据字段一致（不携带内部 _retry_count 字段）
                mappings = []
                for item in batch:
                    mappings.append({
                        "trace_id": item.get("trace_id"),
                        "user_name": item.get("user_name"),
                        "feature_name": item.get("feature_name"),
                        "endpoint": item.get("endpoint"),
                        "method": item.get("method"),
                        "status_code": item.get("status_code"),
                        "process_time_ms": item.get("process_time_ms"),
                        "client_ip": item.get("client_ip"),
                        "request_params": _truncate_text_field(item.get("request_params")),
                        "response_body": _truncate_text_field(item.get("response_body")),
                        "error_message": _truncate_text_field(item.get("error_message"))
                    })

                await session.execute(stmt, mappings)
                await session.commit()
                logger.debug(f"✅ Flushed {len(batch)} audit logs to DB.")
                return
        except Exception as e:
            logger.error(f"❌ Failed to flush audit logs batch: {e}")

        # 落库失败 → 重试：把未超上限的重新入队（retry_count+1），超上限丢弃防死循环。
        requeue = []
        for item in batch:
            retry_count = int(item.get(cls._RETRY_FIELD) or 0)
            if retry_count < cls._MAX_RETRIES:
                item[cls._RETRY_FIELD] = retry_count + 1
                requeue.append(item)
            else:
                logger.warning(
                    f"[Audit] Dropping log after {cls._MAX_RETRIES} failed flush attempts: "
                    f"trace_id={item.get('trace_id')!r}"
                )
        if requeue:
            if not await cls._redis_push_batch(requeue):
                # Redis 也不可用 → 回退内存队列，避免此批在降级场景直接丢失
                for item in requeue:
                    try:
                        cls._queue.put_nowait(item)
                    except asyncio.QueueFull:
                        break

    @classmethod
    async def log_request_data(
        cls, 
        trace_id: str, 
        user_name: Optional[str],
        endpoint: str,
        method: str,
        status_code: int,
        process_time_ms: float,
        client_ip: Optional[str],
        request_params: Optional[str] = None,
        response_body: Optional[str] = None,
        error_message: Optional[str] = None
    ):
        """外部调用的辅助方法，负责数据构造并入队"""
        from app.utils.masking import mask_sensitive_data

        # 应用脱敏逻辑
        masked_request = _truncate_text_field(
            mask_sensitive_data(request_params) if request_params else None
        )
        masked_response = _truncate_text_field(
            mask_sensitive_data(response_body) if response_body else None
        )

        # Resolve human-readable feature name
        feature_name = cls.get_feature_name(endpoint)

        log_data = {
            "trace_id": trace_id,
            "user_name": user_name,
            "feature_name": feature_name,
            "endpoint": endpoint,
            "method": method,
            "status_code": status_code,
            "process_time_ms": process_time_ms,
            "client_ip": client_ip,
            "request_params": masked_request,
            "response_body": masked_response,
            "error_message": error_message
        }
        await cls.enqueue_log(log_data)
