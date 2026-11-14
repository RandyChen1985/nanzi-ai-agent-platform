"""为需要真实平台方言的测试提供按用例创建和销毁的数据库。"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import Table
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool


@pytest.fixture(scope="session")
def init_infrastructure():
    """覆盖共享业务库和 Redis 初始化，由使用临时库的模块显式导入。"""


@asynccontextmanager
async def temporary_database(*tables: Table) -> AsyncIterator[AsyncEngine]:
    raw_url = os.getenv("TEST_DATABASE_URL")
    if not raw_url:
        pytest.fail("请为临时库测试设置 TEST_DATABASE_URL，见 tests/README.md", pytrace=False)
    try:
        url = make_url(raw_url)
    except (ArgumentError, ValueError):
        pytest.fail("TEST_DATABASE_URL 不是有效的数据库连接 URL", pytrace=False)

    if url.get_backend_name() == "mysql":
        url = url.set(drivername="mysql+aiomysql")
        admin_url = url._replace(database=None)
        database_query = "SELECT DATABASE()"
        create_options = " CHARACTER SET utf8mb4"
    elif url.get_backend_name() == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
        admin_url = url.set(database=url.database or "postgres")
        database_query = "SELECT current_database()"
        create_options = " TEMPLATE template0"
    else:
        pytest.fail("TEST_DATABASE_URL 须指向 MySQL 或 PostgreSQL", pytrace=False)

    database = f"nanzi_test_{uuid4().hex}"
    admin = create_async_engine(
        admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool, hide_parameters=True,
    )
    quoted_name = admin.dialect.identifier_preparer.quote_identifier(database)
    created = False
    try:
        async with admin.connect() as connection:
            await connection.exec_driver_sql(f"CREATE DATABASE {quoted_name}{create_options}")
            created = True

        engine = create_async_engine(url.set(database=database), poolclass=NullPool, hide_parameters=True)
        try:
            async with engine.begin() as connection:
                current_database = (await connection.exec_driver_sql(database_query)).scalar_one()
                if current_database != database:
                    raise RuntimeError("数据库连接未选中本用例新建的临时库")
                await connection.run_sync(tables[0].metadata.create_all, tables=tables)
            yield engine
        finally:
            await engine.dispose()
    finally:
        try:
            if created:
                async with admin.connect() as connection:
                    await connection.exec_driver_sql(f"DROP DATABASE {quoted_name}")
        finally:
            await admin.dispose()
