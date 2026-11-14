"""用已有 Redis 通知各进程刷新本地 MCP 工具定义。"""

import asyncio
import logging
from uuid import uuid4

from app.core.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)
_KEY = "nanzi:mcp:tool_config_revision"
_IO_TIMEOUT = 0.35


async def current_tool_revision() -> str | None:
    """None 表示不能校验版本，本次从主库重读。"""
    try:
        async with asyncio.timeout(_IO_TIMEOUT):
            redis = await get_redis()
            if redis is None:
                return None
            value = await redis.get(_KEY)
            return str(value) if value is not None else ""
    except Exception:
        logger.debug("MCP 工具配置版本读取失败，本次重读工具配置")
        return None


async def advance_tool_revision() -> None:
    if not settings.REDIS_ENABLE:
        return
    try:
        async with asyncio.timeout(_IO_TIMEOUT):
            redis = await get_redis()
            if redis is not None:
                await redis.set(_KEY, uuid4().hex)
    except Exception:
        # 配置已经提交。保留短 TTL 和执行前的权限/版本核验，不重放写入或出站调用。
        logger.warning("MCP 工具配置已保存，其他进程的缓存通知暂不可用")


async def invalidate_runtime_tools() -> None:
    """调用方提交工具配置事务之后，统一清理本进程和其他 worker 的缓存。"""
    from app.services.ai.tools.registry import ToolRegistry

    ToolRegistry.clear_db_tool_cache()
    await advance_tool_revision()
