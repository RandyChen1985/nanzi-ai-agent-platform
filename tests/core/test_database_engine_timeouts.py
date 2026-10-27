"""主库连接池与驱动超时的回归测试。

背景：aiomysql 0.3.x 的 ``connect()`` 没有 ``read_timeout``（那是 pymysql 的
API），MySQL 重启 / Docker 网络重置 / 主机睡眠唤醒留下的半开连接会让查询永久
卡在 socket 读上，``pool_pre_ping`` 也卡在同一个 socket，连接永不归还，连接池
被逐步占满，外部表现就是「数据库没反应」，直到数据库侧关闭 socket 才恢复。

这里锁定三层兜底不被改回默认值：
1. ``pool_timeout``：池耗尽时快速失败；
2. ``connect_timeout``：建连阶段不无限等；
3. TCP keepalive：半开连接由内核探测后关闭。
"""

import socket
from unittest.mock import PropertyMock, patch

import pytest

from app.core import orm
from app.core.config import settings

pytestmark = pytest.mark.no_infrastructure


def _as_postgresql():
    return patch.object(
        type(settings),
        "normalized_database_type",
        new_callable=PropertyMock,
        return_value="postgresql",
    )


def test_engine_applies_pool_timeout():
    assert orm.engine.pool._timeout == settings.DATABASE_POOL_TIMEOUT


def test_engine_enables_pre_ping_and_recycle():
    assert orm.engine.pool._pre_ping is True
    assert orm.engine.pool._recycle == settings.MYSQL_POOL_RECYCLE


def test_mysql_connect_args_use_keepalive_creator_and_connect_timeout():
    args = orm._build_connect_args()

    assert args["connect_timeout"] == settings.DATABASE_CONNECT_TIMEOUT
    # aiomysql 不支持 read_timeout，只能通过自定义工厂挂 keepalive
    assert args["async_creator_fn"] is orm._create_mysql_connection
    assert "read_timeout" not in args
    assert "write_timeout" not in args


def test_postgresql_connect_args_use_libpq_keepalive():
    with _as_postgresql():
        args = orm._build_connect_args()

    assert args["connect_timeout"] == settings.DATABASE_CONNECT_TIMEOUT
    assert args["keepalives"] == 1
    assert args["keepalives_idle"] == settings.DATABASE_TCP_KEEPALIVE_IDLE
    assert args["keepalives_interval"] == settings.DATABASE_TCP_KEEPALIVE_INTERVAL
    assert args["keepalives_count"] == settings.DATABASE_TCP_KEEPALIVE_COUNT
    assert "async_creator_fn" not in args


class _FakeSock:
    def __init__(self):
        self.options = []

    def setsockopt(self, level, option, value):
        self.options.append((level, option, value))


def test_configure_tcp_keepalive_sets_kernel_options():
    sock = _FakeSock()

    orm._configure_tcp_keepalive(sock)

    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in sock.options
    idle_option = getattr(socket, "TCP_KEEPIDLE", None) or getattr(
        socket, "TCP_KEEPALIVE", None
    )
    if idle_option is not None:
        assert (
            socket.IPPROTO_TCP,
            idle_option,
            settings.DATABASE_TCP_KEEPALIVE_IDLE,
        ) in sock.options
