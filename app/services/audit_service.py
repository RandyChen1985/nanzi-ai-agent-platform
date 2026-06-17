import asyncio
import logging
import json
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy import insert
from app.core.orm import AsyncSessionLocal
from app.models.audit import AccessLog

logger = logging.getLogger(__name__)

class AuditService:
    _queue = asyncio.Queue()
    _worker_task = None
    _stop_event = asyncio.Event()

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
        # 等待队列中的剩余日志处理完（或超时）
        try:
            await asyncio.wait_for(cls._worker_task, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("⚠️ Audit Log Worker stop timeout, some logs might be lost.")
        
        cls._worker_task = None
        logger.info("🛑 Audit Log Worker stopped.")

    @classmethod
    async def enqueue_log(cls, log_data: Dict[str, Any]):
        """将日志放入异步处理队列"""
        try:
            await cls._queue.put(log_data)
        except Exception as e:
            logger.error(f"Failed to enqueue audit log: {e}")

    @classmethod
    async def flush(cls):
        """强制刷新队列中的所有日志到数据库"""
        batch = []
        while not cls._queue.empty():
            try:
                batch.append(cls._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        
        if batch:
            await cls._flush_batch(batch)

    @classmethod
    async def _worker_loop(cls):
        """后台处理循环，支持批量落库"""
        batch = []
        last_flush_time = datetime.now()
        
        while not cls._stop_event.is_set() or not cls._queue.empty():
            try:
                # 尝试从队列获取，带超时
                try:
                    log_item = await asyncio.wait_for(cls._queue.get(), timeout=1.0)
                    batch.append(log_item)
                except asyncio.TimeoutError:
                    pass

                # 达到批量大小或超过 1 秒未刷新，执行落库
                now = datetime.now()
                if len(batch) >= 50 or (len(batch) > 0 and (now - last_flush_time).total_seconds() >= 1):
                    await cls._flush_batch(batch)
                    batch = []
                    last_flush_time = now

                    
            except Exception as e:
                logger.error(f"Error in Audit Log Worker loop: {e}")
                await asyncio.sleep(1) # 发生严重错误时避让

    @classmethod
    async def _flush_batch(cls, batch: List[Dict[str, Any]]):
        """执行批量插入"""
        if not batch:
            return
            
        try:
            async with AsyncSessionLocal() as session:
                # 使用 SQLAlchemy insert() 的参数化批量插入，效率最高且安全
                stmt = insert(AccessLog)
                # 确保映射数据字段一致
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
                        "request_params": item.get("request_params"),
                        "response_body": item.get("response_body"),
                        "error_message": item.get("error_message")
                    })
                
                await session.execute(stmt, mappings)
                await session.commit()
                logger.debug(f"✅ Flushed {len(batch)} audit logs to DB.")
        except Exception as e:
            logger.error(f"❌ Failed to flush audit logs batch: {e}")
            # 注意：此处未实现重试机制，若落库失败则该批次日志会丢失。
            # 在生产环境中通常会写入本地临时文件或 Redis 以便重试。

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
        masked_request = mask_sensitive_data(request_params) if request_params else None
        masked_response = mask_sensitive_data(response_body) if response_body else None

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
