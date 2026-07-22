import logging
import aiomysql
import asynch
import oracledb
import asyncio
import os
import psycopg
from typing import List, Dict, Any, Optional
from app.services.data_adapter.postgresql import (
    POSTGRESQL_TYPES,
    build_postgresql_conninfo,
    quote_postgresql_identifier,
    split_postgresql_identifier,
)

logger = logging.getLogger(__name__)

class DBImportService:
    @staticmethod
    def _postgresql_type_aliases() -> tuple:
        return POSTGRESQL_TYPES

    @staticmethod
    async def _postgresql_connect(config: Dict[str, Any]):
        return await psycopg.AsyncConnection.connect(**build_postgresql_conninfo(config))

    @staticmethod
    async def test_postgresql_connection(config: Dict[str, Any]) -> bool:
        conn = None
        try:
            conn = await DBImportService._postgresql_connect(config)
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                await cur.fetchone()
            return True
        except Exception as e:
            logger.error(f"PostgreSQL connection test failed: {e}")
            raise Exception(f"PostgreSQL 连接失败: {str(e)}")
        finally:
            if conn:
                await conn.close()

    @staticmethod
    async def get_postgresql_tables(config: Dict[str, Any]) -> List[Dict[str, str]]:
        conn = None
        try:
            conn = await DBImportService._postgresql_connect(config)
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT table_schema, table_name,
                           COALESCE(obj_description(
                               (quote_ident(table_schema) || '.' || quote_ident(table_name))::regclass,
                               'pg_class'
                           ), ''),
                           table_type
                    FROM information_schema.tables
                    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
                      AND table_type IN ('BASE TABLE', 'VIEW')
                    ORDER BY table_schema, table_name
                    """
                )
                rows = await cur.fetchall()
            return [
                {
                    "name": f"{row[0]}.{row[1]}",
                    "comment": row[2] or "",
                    "type": "view" if row[3] == "VIEW" else "table",
                }
                for row in rows
            ]
        except Exception as e:
            logger.error(f"Failed to get PostgreSQL tables: {e}")
            raise Exception(f"获取 PostgreSQL 表列表失败: {str(e)}")
        finally:
            if conn:
                await conn.close()

    @staticmethod
    def _format_postgresql_type(row: tuple) -> str:
        data_type, udt_name, char_len, precision, scale = row[:5]
        if data_type == "character varying" and char_len:
            return f"varchar({char_len})"
        if data_type == "character" and char_len:
            return f"char({char_len})"
        if data_type in ("numeric", "decimal") and precision is not None:
            return f"{data_type}({precision},{scale or 0})"
        if data_type == "timestamp with time zone":
            return "timestamptz"
        if data_type == "timestamp without time zone":
            return "timestamp"
        if data_type == "USER-DEFINED":
            return udt_name or data_type
        return data_type or udt_name or "text"

    @staticmethod
    async def _get_postgresql_ddl_from_connection(conn, table_names: List[str]) -> str:
        ddls = []
        async with conn.cursor() as cur:
            for table_name in table_names:
                schema_name, physical_name = split_postgresql_identifier(table_name)
                schema_name = schema_name or "public"
                await cur.execute(
                    """
                    SELECT table_type
                    FROM information_schema.tables
                    WHERE table_schema = %s AND table_name = %s
                    """,
                    (schema_name, physical_name),
                )
                type_row = await cur.fetchone()
                if not type_row:
                    continue
                qualified = ".".join(
                    [quote_postgresql_identifier(schema_name), quote_postgresql_identifier(physical_name)]
                )
                if type_row[0] == "VIEW":
                    await cur.execute(
                        """
                        SELECT view_definition
                        FROM information_schema.views
                        WHERE table_schema = %s AND table_name = %s
                        """,
                        (schema_name, physical_name),
                    )
                    view_row = await cur.fetchone()
                    if view_row and view_row[0]:
                        ddls.append(f"CREATE VIEW {qualified} AS\n{view_row[0].strip()};")
                    continue

                await cur.execute(
                    """
                    SELECT column_name, data_type, udt_name,
                           character_maximum_length, numeric_precision,
                           numeric_scale, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (schema_name, physical_name),
                )
                columns = await cur.fetchall()
                if not columns:
                    continue
                column_defs = []
                for column in columns:
                    column_type = DBImportService._format_postgresql_type(column[1:6])
                    nullable = " NOT NULL" if column[6] == "NO" else ""
                    default = f" DEFAULT {column[7]}" if column[7] else ""
                    column_defs.append(
                        f"    {quote_postgresql_identifier(column[0])} {column_type}{nullable}{default}"
                    )
                ddls.append(f"CREATE TABLE {qualified} (\n" + ",\n".join(column_defs) + "\n);")
        return "\n\n".join(ddls)

    @staticmethod
    async def get_postgresql_ddl(config: Dict[str, Any], table_names: List[str]) -> str:
        conn = None
        try:
            conn = await DBImportService._postgresql_connect(config)
            return await DBImportService._get_postgresql_ddl_from_connection(conn, table_names)
        except Exception as e:
            logger.error(f"Failed to get PostgreSQL DDL: {e}")
            raise Exception(f"获取 PostgreSQL DDL 失败: {str(e)}")
        finally:
            if conn:
                await conn.close()

    @staticmethod
    async def test_mysql_connection(config: Dict[str, Any]) -> bool:
        """测试 MySQL 连接"""
        try:
            conn = await aiomysql.connect(
                host=config.get('host'),
                port=int(config.get('port', 3306)),
                user=config.get('user'),
                password=config.get('password'),
                db=config.get('database'),
                connect_timeout=10
            )
            conn.close()
            return True
        except Exception as e:
            logger.error(f"MySQL connection test failed: {e}")
            raise Exception(f"MySQL 连接失败: {str(e)}")

    @staticmethod
    async def test_clickhouse_connection(config: Dict[str, Any]) -> bool:
        """测试 ClickHouse 连接"""
        try:
            # Note: ClickHouse common ports are 9000 (TCP) and 8123 (HTTP).
            # 'asynch' uses the native TCP protocol.
            port = int(config.get('port', 9000))
            if port == 8123:
                 raise Exception("端口 8123 为 HTTP 协议，当前系统仅支持 9000 (TCP) 协议。请确认端口设置。")
            
            conn = asynch.Connection(
                host=config.get('host'),
                port=port,
                user=config.get('user'),
                password=config.get('password'),
                database=config.get('database'),
                connect_timeout=10
            )
            await conn.connect()
            await conn.close()
            return True
        except Exception as e:
            logger.error(f"ClickHouse connection test failed: {e}")
            raise Exception(f"ClickHouse 连接失败: {str(e)}")

    @staticmethod
    async def get_mysql_tables(config: Dict[str, Any]) -> List[Dict[str, str]]:
        """获取 MySQL 表列表及其备注 (包含视图类型)"""
        try:
            conn = await aiomysql.connect(
                host=config.get('host'),
                port=int(config.get('port', 3306)),
                user=config.get('user'),
                password=config.get('password'),
                db=config.get('database')
            )
            async with conn.cursor() as cur:
                # Query information_schema for names, comments, and types
                query = """
                    SELECT TABLE_NAME, TABLE_COMMENT, TABLE_TYPE 
                    FROM information_schema.TABLES 
                    WHERE TABLE_SCHEMA = %s
                """
                await cur.execute(query, (config.get('database'),))
                res = await cur.fetchall()
                return [
                    {
                        "name": row[0], 
                        "comment": row[1] or "", 
                        "type": "view" if "VIEW" in row[2].upper() else "table"
                    } for row in res
                ]
        finally:
            conn.close()

    @staticmethod
    async def get_clickhouse_tables(config: Dict[str, Any]) -> List[Dict[str, str]]:
        """获取 ClickHouse 表列表及其备注 (包含视图类型)"""
        try:
            port = int(config.get('port', 9000))
            conn = asynch.Connection(
                host=config.get('host'),
                port=port,
                user=config.get('user'),
                password=config.get('password'),
                database=config.get('database'),
                connect_timeout=10
            )
            await conn.connect()
            async with conn.cursor() as cur:
                # Use system.tables for name, comment and engine. 
                try:
                    query = "SELECT name, comment, engine FROM system.tables WHERE database = currentDatabase()"
                    await cur.execute(query)
                    res = await cur.fetchall()
                    logger.info(f"Loaded {len(res)} tables/views from ClickHouse database: {config.get('database')}")
                    return [
                        {
                            "name": row[0], 
                            "comment": row[1] or "", 
                            "type": "view" if "View" in (row[2] or "") else "table"
                        } for row in res
                    ]
                except Exception as e:
                    # Fallback for ClickHouse versions without 'comment' column
                    if "Missing columns" in str(e) or "Code: 47" in str(e):
                        logger.warning(f"ClickHouse system.tables missing 'comment' column, falling back. Error: {e}")
                        query = "SELECT name, engine FROM system.tables WHERE database = currentDatabase()"
                        await cur.execute(query)
                        res = await cur.fetchall()
                        return [
                            {
                                "name": row[0], 
                                "comment": "", 
                                "type": "view" if "View" in (row[1] or "") else "table"
                            } for row in res
                        ]
                    raise e
        except Exception as e:
            logger.error(f"Failed to get ClickHouse tables: {e}")
            raise Exception(f"获取 ClickHouse 表列表失败: {str(e)}")
        finally:
            if 'conn' in locals():
                await conn.close()

    @staticmethod
    async def get_mysql_ddl(config: Dict[str, Any], table_names: List[str]) -> str:
        """获取 MySQL DDL"""
        ddls = []
        try:
            conn = await aiomysql.connect(
                host=config.get('host'),
                port=int(config.get('port', 3306)),
                user=config.get('user'),
                password=config.get('password'),
                db=config.get('database')
            )
            async with conn.cursor() as cur:
                for table in table_names:
                    await cur.execute(f"SHOW CREATE TABLE `{table}`")
                    res = await cur.fetchone()
                    if res:
                        ddls.append(res[1] + ";")
            return "\n\n".join(ddls)
        finally:
            conn.close()

    @staticmethod
    async def get_clickhouse_ddl(config: Dict[str, Any], table_names: List[str]) -> str:
        """获取 ClickHouse DDL"""
        ddls = []
        try:
            port = int(config.get('port', 9000))
            conn = asynch.Connection(
                host=config.get('host'),
                port=port,
                user=config.get('user'),
                password=config.get('password'),
                database=config.get('database'),
                connect_timeout=10
            )
            await conn.connect()
            async with conn.cursor() as cur:
                for table in table_names:
                    await cur.execute(f"SHOW CREATE TABLE `{table}`")
                    res = await cur.fetchone()
                    if res:
                        ddls.append(res[0] + ";")
            return "\n\n".join(ddls)
        except Exception as e:
            logger.error(f"Failed to get ClickHouse DDL: {e}")
            raise Exception(f"获取 ClickHouse DDL 失败: {str(e)}")
        finally:
            if 'conn' in locals():
                await conn.close()

    @staticmethod
    async def _get_oracle_dsn(config: Dict[str, Any]) -> str:
        """构建 Oracle DSN"""
        return oracledb.makedsn(
            config.get('host'),
            int(config.get('port', 1521)),
            sid=config.get('database') if not config.get('service_name') else None,
            service_name=config.get('service_name')
        )

    @staticmethod
    async def test_oracle_connection(config: Dict[str, Any]) -> bool:
        """测试 Oracle 连接"""
        try:
            dsn = await DBImportService._get_oracle_dsn(config)
            user = config.get('user')
            password = config.get('password')

            if os.environ.get("USE_ORACLE_THICK_MODE") == "1":
                # 厚模式：使用同步驱动 + 线程包装
                def sync_op():
                    with oracledb.connect(user=user, password=password, dsn=dsn) as conn:
                        with conn.cursor() as cur:
                            cur.execute("SELECT 1 FROM DUAL")
                            cur.fetchone()
                    return True
                return await asyncio.to_thread(sync_op)
            else:
                # 薄模式：原生异步
                conn = await oracledb.connect_async(user=user, password=password, dsn=dsn)
                async with conn:
                    async with conn.cursor() as cur:
                        await cur.execute("SELECT 1 FROM DUAL")
                        await cur.fetchone()
                return True
        except Exception as e:
            logger.error(f"Oracle connection test failed: {e}")
            raise Exception(f"Oracle 连接失败: {str(e)}")

    @staticmethod
    async def get_oracle_tables(config: Dict[str, Any]) -> List[Dict[str, str]]:
        """获取 Oracle 表列表及其备注 (包含视图类型)"""
        try:
            dsn = await DBImportService._get_oracle_dsn(config)
            user = config.get('user')
            password = config.get('password')

            query = "SELECT TABLE_NAME, COMMENTS, TABLE_TYPE FROM USER_TAB_COMMENTS WHERE TABLE_TYPE IN ('TABLE', 'VIEW')"

            if os.environ.get("USE_ORACLE_THICK_MODE") == "1":
                # 厚模式：使用同步驱动 + 线程包装
                def sync_op():
                    with oracledb.connect(user=user, password=password, dsn=dsn) as conn:
                        with conn.cursor() as cur:
                            cur.execute(query)
                            res = cur.fetchall()
                            return [
                                {
                                    "name": row[0], 
                                    "comment": row[1] or "", 
                                    "type": "view" if row[2] == "VIEW" else "table"
                                } for row in res
                            ]
                return await asyncio.to_thread(sync_op)
            else:
                # 薄模式：原生异步
                conn = await oracledb.connect_async(user=user, password=password, dsn=dsn)
                async with conn:
                    async with conn.cursor() as cur:
                        await cur.execute(query)
                        res = await cur.fetchall()
                        return [
                            {
                                "name": row[0], 
                                "comment": row[1] or "", 
                                "type": "view" if row[2] == "VIEW" else "table"
                            } for row in res
                        ]
        except Exception as e:
            logger.error(f"Failed to get Oracle tables: {e}")
            raise Exception(f"获取 Oracle 表列表失败: {str(e)}")

    @staticmethod
    async def get_oracle_ddl(config: Dict[str, Any], table_names: List[str]) -> str:
        """获取 Oracle DDL (支持 TABLE 和 VIEW)"""
        try:
            dsn = await DBImportService._get_oracle_dsn(config)
            user = config.get('user')
            password = config.get('password')

            # 首先查询这些对象的类型
            names_placeholders = ",".join([f":{i+1}" for i in range(len(table_names))])
            type_query = f"SELECT TABLE_NAME, TABLE_TYPE FROM USER_TAB_COMMENTS WHERE TABLE_NAME IN ({names_placeholders})"
            
            async def process_ddls(cur, table_types_map):
                ddls = []
                # 设置转换参数
                await cur.execute("BEGIN DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'SEGMENT_ATTRIBUTES', false); END;")
                await cur.execute("BEGIN DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'STORAGE', false); END;")
                await cur.execute("BEGIN DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'SQLTERMINATOR', true); END;")
                
                for table in table_names:
                    obj_type = table_types_map.get(table.upper(), "TABLE")
                    # DBMS_METADATA 对于视图需要使用 'VIEW'
                    await cur.execute(f"SELECT DBMS_METADATA.GET_DDL(:1, :2) FROM DUAL", [obj_type, table.upper()])
                    res = await cur.fetchone()
                    if res:
                        ddls.append(str(res[0]).strip())
                return "\n\n".join(ddls)

            def sync_process_ddls(cur, table_types_map):
                ddls = []
                cur.execute("BEGIN DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'SEGMENT_ATTRIBUTES', false); END;")
                cur.execute("BEGIN DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'STORAGE', false); END;")
                cur.execute("BEGIN DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'SQLTERMINATOR', true); END;")
                for table in table_names:
                    obj_type = table_types_map.get(table.upper(), "TABLE")
                    cur.execute(f"SELECT DBMS_METADATA.GET_DDL(:1, :2) FROM DUAL", [obj_type, table.upper()])
                    res = cur.fetchone()
                    if res:
                        ddls.append(str(res[0]).strip())
                return "\n\n".join(ddls)

            if os.environ.get("USE_ORACLE_THICK_MODE") == "1":
                def sync_op():
                    with oracledb.connect(user=user, password=password, dsn=dsn) as conn:
                        with conn.cursor() as cur:
                            cur.execute(type_query, [t.upper() for t in table_names])
                            type_res = cur.fetchall()
                            table_types_map = {row[0].upper(): row[1] for row in type_res}
                            return sync_process_ddls(cur, table_types_map)
                return await asyncio.to_thread(sync_op)
            else:
                conn = await oracledb.connect_async(user=user, password=password, dsn=dsn)
                async with conn:
                    async with conn.cursor() as cur:
                        await cur.execute(type_query, [t.upper() for t in table_names])
                        type_res = await cur.fetchall()
                        table_types_map = {row[0].upper(): row[1] for row in type_res}
                        return await process_ddls(cur, table_types_map)
        except Exception as e:
            logger.error(f"Failed to get Oracle DDL: {e}")
            raise Exception(f"获取 Oracle DDL 失败: {str(e)}")

    @staticmethod
    def _sqlserver_type_aliases() -> tuple:
        return ("sqlserver", "mssql", "tsql")

    @staticmethod
    async def test_sqlserver_connection(config: Dict[str, Any]) -> bool:
        """测试 SQL Server 连接"""
        try:
            import aioodbc
            from app.services.data_adapter.sqlserver import build_sqlserver_odbc_dsn

            dsn = build_sqlserver_odbc_dsn(config)
            conn = await aioodbc.connect(dsn=dsn, timeout=10)
            try:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
                    await cur.fetchone()
            finally:
                await conn.close()
            return True
        except Exception as e:
            logger.error(f"SQL Server connection test failed: {e}")
            raise Exception(f"SQL Server 连接失败: {str(e)}")

    @staticmethod
    async def get_sqlserver_tables(config: Dict[str, Any]) -> List[Dict[str, str]]:
        """获取 SQL Server 表列表及其备注 (包含视图类型)"""
        try:
            import aioodbc
            from app.services.data_adapter.sqlserver import build_sqlserver_odbc_dsn

            dsn = build_sqlserver_odbc_dsn(config)
            conn = await aioodbc.connect(dsn=dsn, timeout=10)
            try:
                query = """
                    SELECT
                        t.TABLE_NAME,
                        ISNULL(CAST(ep.value AS NVARCHAR(MAX)), '') AS TABLE_COMMENT,
                        CASE WHEN t.TABLE_TYPE = 'VIEW' THEN 'view' ELSE 'table' END AS obj_type
                    FROM INFORMATION_SCHEMA.TABLES t
                    LEFT JOIN sys.tables st ON st.name = t.TABLE_NAME
                    LEFT JOIN sys.extended_properties ep
                        ON ep.major_id = st.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description'
                    WHERE t.TABLE_TYPE IN ('BASE TABLE', 'VIEW')
                      AND t.TABLE_CATALOG = DB_NAME()
                    ORDER BY t.TABLE_NAME
                """
                async with conn.cursor() as cur:
                    await cur.execute(query)
                    res = await cur.fetchall()
                    return [
                        {"name": row[0], "comment": row[1] or "", "type": row[2]}
                        for row in res
                    ]
            finally:
                await conn.close()
        except Exception as e:
            logger.error(f"Failed to get SQL Server tables: {e}")
            raise Exception(f"获取 SQL Server 表列表失败: {str(e)}")

    @staticmethod
    def _format_sqlserver_column_type(row: tuple) -> str:
        data_type, char_len, num_precision, num_scale = row[1], row[2], row[3], row[4]
        data_type = (data_type or "").lower()
        if data_type in ("varchar", "nvarchar", "char", "nchar") and char_len:
            if int(char_len) == -1:
                return f"{data_type}(max)"
            return f"{data_type}({int(char_len)})"
        if data_type in ("decimal", "numeric") and num_precision is not None:
            scale = int(num_scale or 0)
            return f"{data_type}({int(num_precision)},{scale})"
        return data_type

    @staticmethod
    async def get_sqlserver_ddl(config: Dict[str, Any], table_names: List[str]) -> str:
        """获取 SQL Server DDL (支持 TABLE 和 VIEW)"""
        try:
            import aioodbc
            from app.services.data_adapter.sqlserver import build_sqlserver_odbc_dsn

            dsn = build_sqlserver_odbc_dsn(config)
            conn = await aioodbc.connect(dsn=dsn, timeout=10)
            ddls: List[str] = []
            try:
                async with conn.cursor() as cur:
                    for table in table_names:
                        await cur.execute(
                            """
                            SELECT TABLE_TYPE
                            FROM INFORMATION_SCHEMA.TABLES
                            WHERE TABLE_NAME = ? AND TABLE_CATALOG = DB_NAME()
                            """,
                            (table,),
                        )
                        type_row = await cur.fetchone()
                        if not type_row:
                            continue

                        if type_row[0] == "VIEW":
                            await cur.execute(
                                """
                                SELECT m.definition
                                FROM sys.sql_modules m
                                INNER JOIN sys.objects o ON m.object_id = o.object_id
                                WHERE o.name = ? AND o.type = 'V'
                                """,
                                (table,),
                            )
                            view_row = await cur.fetchone()
                            if view_row and view_row[0]:
                                ddls.append(str(view_row[0]).strip())
                            continue

                        await cur.execute(
                            """
                            SELECT
                                COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH,
                                NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE, COLUMN_DEFAULT
                            FROM INFORMATION_SCHEMA.COLUMNS
                            WHERE TABLE_NAME = ? AND TABLE_CATALOG = DB_NAME()
                            ORDER BY ORDINAL_POSITION
                            """,
                            (table,),
                        )
                        columns = await cur.fetchall()
                        if not columns:
                            continue

                        col_defs = []
                        for col in columns:
                            col_type = DBImportService._format_sqlserver_column_type(col)
                            nullable = "" if col[5] == "NO" else " NULL"
                            default = f" DEFAULT {col[6]}" if col[6] else ""
                            col_defs.append(f"    [{col[0]}] {col_type}{nullable}{default}")
                        ddls.append(f"CREATE TABLE [{table}] (\n" + ",\n".join(col_defs) + "\n);")
            finally:
                await conn.close()
            return "\n\n".join(ddls)
        except Exception as e:
            logger.error(f"Failed to get SQL Server DDL: {e}")
            raise Exception(f"获取 SQL Server DDL 失败: {str(e)}")


class DbDdlSession:
    """摸排任务期间复用单连接批量抓取 DDL，避免大库逐表建连。"""

    def __init__(self, db_type: str, config: Dict[str, Any]):
        self.db_type = db_type.strip().lower()
        self.config = config
        self._conn: Any = None
        self._oracle_table_types: Dict[str, str] = {}
        self._use_oracle_thick = os.environ.get("USE_ORACLE_THICK_MODE") == "1"

    async def __aenter__(self) -> "DbDdlSession":
        if self.db_type == "mysql":
            self._conn = await aiomysql.connect(
                host=self.config.get("host"),
                port=int(self.config.get("port", 3306)),
                user=self.config.get("user"),
                password=self.config.get("password"),
                db=self.config.get("database"),
            )
        elif self.db_type == "clickhouse":
            port = int(self.config.get("port", 9000))
            self._conn = asynch.Connection(
                host=self.config.get("host"),
                port=port,
                user=self.config.get("user"),
                password=self.config.get("password"),
                database=self.config.get("database"),
                connect_timeout=10,
            )
            await self._conn.connect()
        elif self.db_type == "oracle":
            dsn = await DBImportService._get_oracle_dsn(self.config)
            user = self.config.get("user")
            password = self.config.get("password")
            if self._use_oracle_thick:
                def open_conn():
                    conn = oracledb.connect(user=user, password=password, dsn=dsn)
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT TABLE_NAME, TABLE_TYPE FROM USER_TAB_COMMENTS "
                            "WHERE TABLE_TYPE IN ('TABLE', 'VIEW')"
                        )
                        type_map = {row[0].upper(): row[1] for row in cur.fetchall()}
                    return conn, type_map

                self._conn, self._oracle_table_types = await asyncio.to_thread(open_conn)
            else:
                self._conn = await oracledb.connect_async(user=user, password=password, dsn=dsn)
                async with self._conn.cursor() as cur:
                    await cur.execute(
                        "SELECT TABLE_NAME, TABLE_TYPE FROM USER_TAB_COMMENTS "
                        "WHERE TABLE_TYPE IN ('TABLE', 'VIEW')"
                    )
                    rows = await cur.fetchall()
                    self._oracle_table_types = {row[0].upper(): row[1] for row in rows}
        elif self.db_type in DBImportService._sqlserver_type_aliases():
            import aioodbc
            from app.services.data_adapter.sqlserver import build_sqlserver_odbc_dsn

            dsn = build_sqlserver_odbc_dsn(self.config)
            self._conn = await aioodbc.connect(dsn=dsn, timeout=10)
        elif self.db_type in DBImportService._postgresql_type_aliases():
            self._conn = await DBImportService._postgresql_connect(self.config)
        else:
            raise ValueError(f"不支持的数据库类型: {self.db_type}")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if not self._conn:
            return
        try:
            if self.db_type == "mysql":
                self._conn.close()
            elif self.db_type == "clickhouse":
                await self._conn.close()
            elif self.db_type == "oracle":
                if self._use_oracle_thick:
                    await asyncio.to_thread(self._conn.close)
                else:
                    await self._conn.close()
            elif self.db_type in DBImportService._sqlserver_type_aliases():
                await self._conn.close()
            elif self.db_type in DBImportService._postgresql_type_aliases():
                await self._conn.close()
        except Exception as close_err:
            logger.warning(f"[DbDdlSession] close connection failed: {close_err}")
        finally:
            self._conn = None

    async def get_table_ddl(self, table_name: str, table_type: str = "table") -> str:
        if self.db_type == "mysql":
            async with self._conn.cursor() as cur:
                await cur.execute(f"SHOW CREATE TABLE `{table_name}`")
                res = await cur.fetchone()
                return (res[1] + ";") if res else ""

        if self.db_type == "clickhouse":
            async with self._conn.cursor() as cur:
                await cur.execute(f"SHOW CREATE TABLE `{table_name}`")
                res = await cur.fetchone()
                return (res[0] + ";") if res else ""

        if self.db_type == "oracle":
            obj_type = self._oracle_table_types.get(table_name.upper(), "TABLE" if table_type != "view" else "VIEW")
            if obj_type not in ("TABLE", "VIEW"):
                obj_type = "VIEW" if table_type == "view" else "TABLE"

            if self._use_oracle_thick:
                def fetch_ddl():
                    with self._conn.cursor() as cur:
                        cur.execute(
                            "BEGIN DBMS_METADATA.SET_TRANSFORM_PARAM("
                            "DBMS_METADATA.SESSION_TRANSFORM, 'SEGMENT_ATTRIBUTES', false); END;"
                        )
                        cur.execute(
                            "BEGIN DBMS_METADATA.SET_TRANSFORM_PARAM("
                            "DBMS_METADATA.SESSION_TRANSFORM, 'STORAGE', false); END;"
                        )
                        cur.execute(
                            "BEGIN DBMS_METADATA.SET_TRANSFORM_PARAM("
                            "DBMS_METADATA.SESSION_TRANSFORM, 'SQLTERMINATOR', true); END;"
                        )
                        cur.execute(
                            "SELECT DBMS_METADATA.GET_DDL(:1, :2) FROM DUAL",
                            [obj_type, table_name.upper()],
                        )
                        res = cur.fetchone()
                        return str(res[0]).strip() if res else ""

                return await asyncio.to_thread(fetch_ddl)

            async with self._conn.cursor() as cur:
                await cur.execute(
                    "BEGIN DBMS_METADATA.SET_TRANSFORM_PARAM("
                    "DBMS_METADATA.SESSION_TRANSFORM, 'SEGMENT_ATTRIBUTES', false); END;"
                )
                await cur.execute(
                    "BEGIN DBMS_METADATA.SET_TRANSFORM_PARAM("
                    "DBMS_METADATA.SESSION_TRANSFORM, 'STORAGE', false); END;"
                )
                await cur.execute(
                    "BEGIN DBMS_METADATA.SET_TRANSFORM_PARAM("
                    "DBMS_METADATA.SESSION_TRANSFORM, 'SQLTERMINATOR', true); END;"
                )
                await cur.execute(
                    "SELECT DBMS_METADATA.GET_DDL(:1, :2) FROM DUAL",
                    [obj_type, table_name.upper()],
                )
                res = await cur.fetchone()
                return str(res[0]).strip() if res else ""

        if self.db_type in DBImportService._sqlserver_type_aliases():
            async with self._conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT TABLE_TYPE
                    FROM INFORMATION_SCHEMA.TABLES
                    WHERE TABLE_NAME = ? AND TABLE_CATALOG = DB_NAME()
                    """,
                    (table_name,),
                )
                type_row = await cur.fetchone()
                if not type_row:
                    return ""

                if type_row[0] == "VIEW":
                    await cur.execute(
                        """
                        SELECT m.definition
                        FROM sys.sql_modules m
                        INNER JOIN sys.objects o ON m.object_id = o.object_id
                        WHERE o.name = ? AND o.type = 'V'
                        """,
                        (table_name,),
                    )
                    view_row = await cur.fetchone()
                    return str(view_row[0]).strip() if view_row and view_row[0] else ""

                await cur.execute(
                    """
                    SELECT
                        COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH,
                        NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE, COLUMN_DEFAULT
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_NAME = ? AND TABLE_CATALOG = DB_NAME()
                    ORDER BY ORDINAL_POSITION
                    """,
                    (table_name,),
                )
                columns = await cur.fetchall()
                if not columns:
                    return ""

                col_defs = []
                for col in columns:
                    col_type = DBImportService._format_sqlserver_column_type(col)
                    nullable = "" if col[5] == "NO" else " NULL"
                    default = f" DEFAULT {col[6]}" if col[6] else ""
                    col_defs.append(f"    [{col[0]}] {col_type}{nullable}{default}")
                return f"CREATE TABLE [{table_name}] (\n" + ",\n".join(col_defs) + "\n);"

        if self.db_type in DBImportService._postgresql_type_aliases():
            return await DBImportService._get_postgresql_ddl_from_connection(
                self._conn, [table_name]
            )

        raise ValueError(f"不支持的数据库类型: {self.db_type}")
