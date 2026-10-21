import redis.asyncio as redis
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

redis_client = None
redis_client_binary = None


def _redis_connect_kwargs(*, decode_responses: bool) -> dict:
    kwargs = {
        "host": settings.REDIS_HOST,
        "port": settings.REDIS_PORT,
        "db": settings.REDIS_DB,
        "decode_responses": decode_responses,
        # 防止 Redis 主从切换/网络分区时锁、状态、配置读取无限挂起；
        # 3s 大于业务里最长的阻塞读（BLPOP 1s / XREAD block 1s）。
        "socket_timeout": 3.0,
        "socket_connect_timeout": 1.0,
    }
    if decode_responses:
        kwargs["encoding"] = "utf-8"
    pw = settings.REDIS_PASSWORD
    if pw and pw.strip() and pw.lower() not in ["none", "null", ""]:
        kwargs["password"] = pw
    return kwargs


async def init_redis():
    global redis_client, redis_client_binary
    if settings.REDIS_ENABLE:
        logger.info(f"🔌 Connecting to Redis: {settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}")

        redis_client = redis.Redis(**_redis_connect_kwargs(decode_responses=True))
        redis_client_binary = redis.Redis(**_redis_connect_kwargs(decode_responses=False))

        await redis_client.ping()
        await redis_client_binary.ping()
        logger.info("✅ Redis connected successfully")
    else:
        logger.info("⚠️  Redis is disabled in settings")

async def close_redis():
    global redis_client, redis_client_binary
    if redis_client:
        await redis_client.aclose()
        redis_client = None
    if redis_client_binary:
        await redis_client_binary.aclose()
        redis_client_binary = None


async def get_redis():
    if not redis_client and settings.REDIS_ENABLE:
        await init_redis()
    return redis_client


async def get_redis_binary():
    """用于读取含 FLOAT32 embedding 等二进制 HASH 字段（decode_responses=False）。"""
    if not redis_client_binary and settings.REDIS_ENABLE:
        await init_redis()
    return redis_client_binary
