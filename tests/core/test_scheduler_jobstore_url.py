"""APScheduler jobstore 同步连接串的超时参数回归测试。

背景：``SQLAlchemyJobStore`` 走**同步** pymysql/psycopg，而且这些 SQL 是在事件循环
线程里执行的（``AsyncIOScheduler.start()`` 与每次 ``add_job()``）。默认没有 connect/
read 超时，于是主库不可达、或表被元数据锁挡住（迁移脚本 ALTER 未提交）时，启动会在
事件循环里无限期同步等待——表现为日志停在调度器装载之前、端口不监听、没有任何报错，
上层再套 ``asyncio.wait_for`` 也救不回来（计时器轮不上）。

这里锁定 ``_build_jobstore_url`` 给连接串补的参数：
- MySQL：connect_timeout + read/write_timeout（pymysql 支持语句级超时）；
- PostgreSQL：只补 connect_timeout（libpq 没有 read_timeout，传了会被当成非法连接参数）；
- 已有 query 参数保留、同名参数不重复追加。
"""

from unittest.mock import PropertyMock, patch

import pytest

from app.core.config import settings
from app.services.ai import scheduler_service as svc

pytestmark = pytest.mark.no_infrastructure

_MYSQL_URL = "mysql+pymysql://nanzi:p%40ss@db.local:3306/nanzi"
_PG_URL = "postgresql+psycopg://nanzi:secret@db.local:5432/nanzi"


def _patch_sync_url(url: str):
    return patch.object(
        type(settings), "DATABASE_SYNC_URL", new_callable=PropertyMock, return_value=url
    )


def _patch_db_type(value: str):
    return patch.object(
        type(settings),
        "normalized_database_type",
        new_callable=PropertyMock,
        return_value=value,
    )


def _query_of(url: str) -> str:
    return url.split("?", 1)[1] if "?" in url else ""


def test_mysql_jobstore_url_carries_driver_timeouts():
    with _patch_sync_url(_MYSQL_URL), _patch_db_type("mysql"):
        url = svc._build_jobstore_url()

    assert url.startswith(_MYSQL_URL)
    query = _query_of(url)
    assert f"connect_timeout={settings.DATABASE_CONNECT_TIMEOUT}" in query
    assert f"read_timeout={settings.DATABASE_SYNC_READ_TIMEOUT}" in query
    assert f"write_timeout={settings.DATABASE_SYNC_READ_TIMEOUT}" in query


def test_postgresql_jobstore_url_omits_unsupported_params():
    with _patch_sync_url(_PG_URL), _patch_db_type("postgresql"):
        url = svc._build_jobstore_url()

    assert url.startswith(_PG_URL)
    query = _query_of(url)
    assert f"connect_timeout={settings.DATABASE_CONNECT_TIMEOUT}" in query
    assert "read_timeout" not in query
    assert "write_timeout" not in query


def test_existing_query_is_preserved():
    base = _MYSQL_URL + "?charset=utf8mb4"
    with _patch_sync_url(base), _patch_db_type("mysql"):
        url = svc._build_jobstore_url()

    assert "charset=utf8mb4" in url
    # 不能出现两个 '?'，否则驱动会把第二个当值的一部分
    assert url.count("?") == 1
    assert "&connect_timeout=" in url


def test_explicit_connect_timeout_is_not_overridden():
    base = _MYSQL_URL + "?connect_timeout=3"
    with _patch_sync_url(base), _patch_db_type("mysql"):
        url = svc._build_jobstore_url()

    assert "connect_timeout=3" in url
    assert url.count("connect_timeout=") == 1


def test_append_url_query_returns_url_untouched_when_all_params_present():
    url = "mysql+pymysql://u:p@h:3306/d?connect_timeout=3&read_timeout=4&write_timeout=5"

    assert svc._append_url_query(url, {"connect_timeout": 9, "read_timeout": 9}) == url
