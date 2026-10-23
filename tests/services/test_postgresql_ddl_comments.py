"""PostgreSQL DDL 生成必须带出库表注释。

背景：`information_schema` 不暴露注释，PG 的备注只能通过 `obj_description()` /
`col_description()` 读取。历史实现只拼 `CREATE TABLE ...`，于是"从数据库加载 DDL"
选表导入后，表备注与字段备注（尤其是中文备注）全部丢失；MySQL 因为走
`SHOW CREATE TABLE` 天然带注释，问题只在 PG 侧暴露。

本文件锁定：
- 表注释渲染为 `COMMENT ON TABLE`
- 列注释渲染为 `COMMENT ON COLUMN`，空注释不产出语句
- 注释中的单引号按 SQL 规则翻倍，中文原样保留
- 视图注释渲染为 `COMMENT ON VIEW`
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.db_import_service import DBImportService


pytestmark = pytest.mark.no_infrastructure


def _fake_connection(fetchone_rows, fetchall_batches):
    """构造只实现 cursor()/close() 的假连接。"""
    cursor = AsyncMock()
    cursor.fetchone.side_effect = list(fetchone_rows)
    cursor.fetchall.side_effect = list(fetchall_batches)
    cursor_cm = MagicMock()
    cursor_cm.__aenter__ = AsyncMock(return_value=cursor)
    connection = MagicMock()
    connection.cursor.return_value = cursor_cm
    connection.close = AsyncMock()
    return connection, cursor


@pytest.mark.asyncio
async def test_postgresql_ddl_emits_chinese_table_and_column_comments():
    connection, _ = _fake_connection(
        fetchone_rows=[("BASE TABLE", "客户主数据，含中文备注")],
        fetchall_batches=[
            [
                ("customer_id", "integer", "int4", None, None, None, "NO", None, "客户编号"),
                ("customer_name", "character varying", "varchar", 120, None, None, "YES", None, "客户名称"),
            ]
        ],
    )

    ddl = await DBImportService._get_postgresql_ddl_from_connection(connection, ["demo.customers"])

    assert 'CREATE TABLE "demo"."customers"' in ddl
    assert 'COMMENT ON TABLE "demo"."customers" IS \'客户主数据，含中文备注\';' in ddl
    assert 'COMMENT ON COLUMN "demo"."customers"."customer_id" IS \'客户编号\';' in ddl
    assert 'COMMENT ON COLUMN "demo"."customers"."customer_name" IS \'客户名称\';' in ddl


@pytest.mark.asyncio
async def test_postgresql_ddl_skips_empty_comments():
    connection, _ = _fake_connection(
        fetchone_rows=[("BASE TABLE", "")],
        fetchall_batches=[
            [
                ("id", "integer", "int4", None, None, None, "NO", None, ""),
                ("label", "text", "text", None, None, None, "YES", None, None),
            ]
        ],
    )

    ddl = await DBImportService._get_postgresql_ddl_from_connection(connection, ["public.demo_table"])

    assert "COMMENT ON" not in ddl
    assert 'CREATE TABLE "public"."demo_table"' in ddl


@pytest.mark.asyncio
async def test_postgresql_ddl_escapes_single_quotes_in_comments():
    connection, _ = _fake_connection(
        fetchone_rows=[("BASE TABLE", "用户的'昵称'记录")],
        fetchall_batches=[[("nick", "text", "text", None, None, None, "YES", None, "展示名：'昵称'")]],
    )

    ddl = await DBImportService._get_postgresql_ddl_from_connection(connection, ["public.users"])

    assert 'COMMENT ON TABLE "public"."users" IS \'用户的\'\'昵称\'\'记录\';' in ddl
    assert 'COMMENT ON COLUMN "public"."users"."nick" IS \'展示名：\'\'昵称\'\'\';' in ddl


@pytest.mark.asyncio
async def test_postgresql_ddl_emits_view_comment():
    connection, _ = _fake_connection(
        fetchone_rows=[
            ("VIEW", "订单宽表视图"),
            ("SELECT customer_id FROM demo.customers",),
        ],
        fetchall_batches=[],
    )

    ddl = await DBImportService._get_postgresql_ddl_from_connection(connection, ["demo.v_orders"])

    assert 'CREATE VIEW "demo"."v_orders" AS' in ddl
    assert 'COMMENT ON VIEW "demo"."v_orders" IS \'订单宽表视图\';' in ddl


def test_comment_literal_helper_only_escapes_quotes():
    """注释字面量不做语言/编码层面的改写，中文与标点必须原样。"""
    assert DBImportService._format_postgresql_comment_literal("订单金额，等于 quantity * unit_price") == (
        "'订单金额，等于 quantity * unit_price'"
    )
    assert DBImportService._format_postgresql_comment_literal("it's") == "'it''s'"
