import logging
import socket
from typing import Any, AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _configure_tcp_keepalive(raw_sock: Any) -> None:
    """打开 TCP keepalive 并收紧探测节奏。

    Linux 用 ``TCP_KEEPIDLE``，macOS 用 ``TCP_KEEPALIVE``（同一个含义），
    两者都提供 ``TCP_KEEPINTVL`` / ``TCP_KEEPCNT``。
    """
    raw_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    idle_option = getattr(socket, "TCP_KEEPIDLE", None) or getattr(
        socket, "TCP_KEEPALIVE", None
    )
    for option, value in (
        (idle_option, settings.DATABASE_TCP_KEEPALIVE_IDLE),
        (getattr(socket, "TCP_KEEPINTVL", None), settings.DATABASE_TCP_KEEPALIVE_INTERVAL),
        (getattr(socket, "TCP_KEEPCNT", None), settings.DATABASE_TCP_KEEPALIVE_COUNT),
    ):
        if option is not None and value and value > 0:
            raw_sock.setsockopt(socket.IPPROTO_TCP, option, int(value))


async def _create_mysql_connection(*args: Any, **kwargs: Any) -> Any:
    """aiomysql 连接工厂：建连后打开 TCP keepalive。

    为什么需要它：aiomysql 0.3.x 的 ``connect()`` 没有 ``read_timeout`` 参数
    （那是 pymysql 的 API），半开连接会让查询永久卡在 socket 读上，
    ``pool_pre_ping`` 也卡在同一个 socket，连接永不归还，池最终被占死，表现为
    「数据库没反应」，直到数据库侧关闭 socket 才恢复。开启 keepalive 后，内核会在
    约 ``idle + interval * count`` 秒内判定死连接并关闭，读操作随即报错，
    SQLAlchemy 丢弃坏连接并重建。

    SQLAlchemy 的 aiomysql 方言会把 ``connect_args`` 里的 ``async_creator_fn``
    取出来当作连接工厂调用。
    """
    import aiomysql

    conn = await aiomysql.connect(*args, **kwargs)
    try:
        writer = getattr(conn, "_writer", None)
        transport = getattr(writer, "transport", None)
        raw_sock = transport.get_extra_info("socket", default=None) if transport else None
        if raw_sock is not None:
            transport.pause_reading()
            try:
                _configure_tcp_keepalive(raw_sock)
            finally:
                transport.resume_reading()
    except Exception as exc:  # keepalive 是加固项，失败不能阻断建连
        logger.warning("设置 MySQL TCP keepalive 失败（不影响本次连接）: %s", exc)
    return conn


def _build_connect_args() -> dict:
    """按数据库类型给出驱动级连接参数。

    MySQL 走自定义工厂（只为挂 keepalive），PostgreSQL 由 libpq 直接支持
    ``keepalives*`` 系列参数。
    """
    if settings.normalized_database_type == "postgresql":
        return {
            "connect_timeout": settings.DATABASE_CONNECT_TIMEOUT,
            "keepalives": 1,
            "keepalives_idle": settings.DATABASE_TCP_KEEPALIVE_IDLE,
            "keepalives_interval": settings.DATABASE_TCP_KEEPALIVE_INTERVAL,
            "keepalives_count": settings.DATABASE_TCP_KEEPALIVE_COUNT,
        }
    return {
        "connect_timeout": settings.DATABASE_CONNECT_TIMEOUT,
        "async_creator_fn": _create_mysql_connection,
    }


# SQLAlchemy Async Engine
engine = create_async_engine(
    settings.DATABASE_ASYNC_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=settings.MYSQL_POOL_SIZE,
    max_overflow=settings.MYSQL_MAX_OVERFLOW,
    pool_recycle=settings.MYSQL_POOL_RECYCLE,
    # 池耗尽时等待 10s 就报 TimeoutError，而不是静默卡 30s
    pool_timeout=settings.DATABASE_POOL_TIMEOUT,
    connect_args=_build_connect_args(),
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting async session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
