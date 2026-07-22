from typing import Dict, Optional, Any, Tuple
import asyncio
import json
import logging
import inspect
import hashlib
import os
import platform

logger = logging.getLogger(__name__)

# Oracle Thick Mode 初始化状态
_thick_mode_initialized = False

def init_oracle_thick_mode():
    """显式初始化 Oracle Thick Mode 客户端"""
    global _thick_mode_initialized
    if _thick_mode_initialized:
        return True
    
    if os.environ.get("USE_ORACLE_THICK_MODE") != "1":
        logger.info("[Oracle] 默认使用 Thin (纯Python) 模式连接。设置环境变量 USE_ORACLE_THICK_MODE=1 可启用 Thick 模式。")
        return False

    try:
        import oracledb
    except ImportError:
        logger.warning("[Oracle] 未安装 oracledb 依赖，跳过 Thick Mode 初始化。")
        return False

    try:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        lib_dir = None
        system = platform.system().lower()
        if system == "darwin":
            mac_lib = os.path.join(project_root, "libs", "oracle", "mac")
            if os.path.exists(mac_lib):
                lib_dir = mac_lib
        elif system == "linux":
            linux_lib = os.path.join(project_root, "libs", "oracle", "linux")
            docker_lib = os.path.join(project_root, "docker", "libs", "instantclient_19_30")
            if os.path.exists(linux_lib):
                lib_dir = linux_lib
            elif os.path.exists(docker_lib):
                lib_dir = docker_lib
            elif os.path.exists("/opt/oracle/instantclient"):
                lib_dir = "/opt/oracle/instantclient"

        for attempt in range(3):
            try:
                if lib_dir:
                    oracledb.init_oracle_client(lib_dir=lib_dir)
                    logger.info(f"[Oracle] Thick Mode 客户端初始化成功 (lib_dir: {lib_dir})")
                else:
                    if os.environ.get("LD_LIBRARY_PATH"):
                        oracledb.init_oracle_client()
                        logger.info("[Oracle] Thick Mode 通过 LD_LIBRARY_PATH 初始化成功")
                    else:
                        logger.warning("[Oracle] 未找到 Oracle Instant Client 动态库，跳过 Thick Mode 初始化。")
                        return False
                
                _thick_mode_initialized = True
                return True
            except Exception as init_error:
                error_msg = str(init_error)
                if "DPY-2053" in error_msg or "already been enabled" in error_msg or "already initialized" in error_msg:
                    logger.info(f"[Oracle] Thick Mode 客户端已处于启用状态: {error_msg}")
                    _thick_mode_initialized = True
                    return True
                elif attempt < 2:
                    continue
                else:
                    logger.error(f"[Oracle] Thick Mode 所有初始化尝试均已失败: {init_error}")
                    return False
    except Exception as e:
        logger.error(f"[Oracle] Thick Mode 初始化过程中发生未预期错误: {e}")
        return False


class DataSourcePoolManager:
    """系统物理数据源连接池的单例管理器：负责连接池的生命周期、自愈、配置失效及进程退出优雅释放"""
    
    # 缓存池的映射结构：{ source_id: (pool_instance, config_fingerprint) }
    _pools: Dict[int, Tuple[Any, str]] = {}
    _lock = asyncio.Lock()

    @staticmethod
    def _get_config_fingerprint(db_config: Any) -> str:
        """根据数据源配置参数生成唯一的哈希指纹，用于配置变更对比"""
        raw_str = (
            f"{db_config.db_type}:{db_config.host}:{db_config.port}:"
            f"{db_config.db_user}:{db_config.password}:{db_config.database_name}"
        )
        return hashlib.md5(raw_str.encode("utf-8")).hexdigest()

    @classmethod
    async def get_pool(cls, source_id: int) -> Any:
        """获取或创建指定数据源 ID 的数据库连接池（自带自愈和动态配置失效检测）"""
        # 1. 从本地数据库异步查询最新的数据源连接配置
        from app.core.orm import AsyncSessionLocal
        from app.services.db_connection_service import DbConnectionService

        async with AsyncSessionLocal() as session:
            db_config = await DbConnectionService.get_config(session, source_id)

        if not db_config:
            await cls.invalidate_pool(source_id)
            raise ValueError(f"未找到对应的数据源配置记录 (ID: {source_id})。")

        current_fingerprint = cls._get_config_fingerprint(db_config)

        # 2. 对比当前缓存，如果配置发生变动，自动销毁老连接池并失效它
        async with cls._lock:
            if source_id in cls._pools:
                cached_pool, cached_fingerprint = cls._pools[source_id]
                if cached_fingerprint == current_fingerprint:
                    logger.debug(f"[Pool Manager] 复用已有连接池 (ID: {source_id})")
                    return cached_pool
                else:
                    logger.info(f"[Pool Manager] 检测到数据源配置已更新，正在失效销毁旧连接池 (ID: {source_id})")
                    # 释放连接池，避免句柄泄漏
                    await cls._close_pool_internal(cached_pool, source_id)
                    cls._pools.pop(source_id, None)

            # 3. 按类型创建连接池
            pool = None
            db_type = db_config.db_type.strip().lower()
            if db_type == "clickhouse":
                pool = await cls._create_clickhouse_pool(db_config)
            elif db_type == "mysql":
                pool = await cls._create_mysql_pool(db_config)
            elif db_type == "oracle":
                pool = await cls._create_oracle_pool(db_config)
            elif db_type in ("sqlserver", "mssql", "tsql"):
                pool = await cls._create_sqlserver_pool(db_config)
            elif db_type in ("postgres", "postgresql", "pg"):
                pool = await cls._create_postgresql_pool(db_config)
            else:
                raise NotImplementedError(f"不支持的连接池数据库类型: '{db_config.db_type}'")

            cls._pools[source_id] = (pool, current_fingerprint)
            logger.info(f"[Pool Manager] 成功为数据源 '{db_config.name}' (ID: {source_id}) 创建连接池。")
            return pool

    @classmethod
    async def _create_mysql_pool(cls, db_config: Any) -> Any:
        """创建 MySQL 连接池"""
        import aiomysql
        pool = await aiomysql.create_pool(
            host=db_config.host,
            port=int(db_config.port),
            db=db_config.database_name,
            user=db_config.db_user,
            password=db_config.password,
            minsize=1,
            maxsize=50,  # 适度控制单进程池大小以防止多 worker 爆表
            autocommit=False
        )
        return pool

    @classmethod
    async def _create_clickhouse_pool(cls, db_config: Any) -> Any:
        """创建 ClickHouse 连接池"""
        from asynch.pool import Pool as AsynchPool
        pool = AsynchPool(
            host=db_config.host,
            port=int(db_config.port),
            database=db_config.database_name or "default",
            user=db_config.db_user or "default",
            password=db_config.password or "",
            minsize=1,
            maxsize=50,
            encoding_errors="replace"
        )
        return pool

    @classmethod
    async def _create_sqlserver_pool(cls, db_config: Any) -> Any:
        """创建 SQL Server 连接池"""
        import aioodbc
        from app.services.data_adapter.sqlserver import build_sqlserver_odbc_dsn

        dsn = build_sqlserver_odbc_dsn(
            {
                "host": db_config.host,
                "port": db_config.port,
                "database": db_config.database_name,
                "user": db_config.db_user,
                "password": db_config.password,
            }
        )
        pool = await aioodbc.create_pool(
            dsn=dsn,
            minsize=1,
            maxsize=50,
            autocommit=True,
        )
        return pool

    @classmethod
    async def _create_postgresql_pool(cls, db_config: Any) -> Any:
        """创建 PostgreSQL 异步连接池。"""
        from psycopg_pool import AsyncConnectionPool
        from app.services.data_adapter.postgresql import build_postgresql_conninfo

        async def configure_connection(connection: Any) -> None:
            """让未带 schema 的表名也能解析到用户业务 schema。"""
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT schema_name
                    FROM information_schema.schemata
                    WHERE schema_name NOT IN ('pg_catalog', 'information_schema')
                    ORDER BY CASE WHEN schema_name = 'public' THEN 1 ELSE 0 END, schema_name
                    """
                )
                schemas = [row[0] for row in await cursor.fetchall()]
                if schemas:
                    search_path = ", ".join(
                        '"' + str(schema).replace('"', '""') + '"' for schema in schemas
                    )
                    await cursor.execute(f"SET search_path TO {search_path}")
            await connection.commit()

        conninfo = build_postgresql_conninfo(
            {
                "host": db_config.host,
                "port": db_config.port,
                "database": db_config.database_name,
                "user": db_config.db_user,
                "password": db_config.password,
            }
        )
        pool = AsyncConnectionPool(
            kwargs=conninfo,
            configure=configure_connection,
            min_size=1,
            max_size=50,
            open=False,
        )
        await pool.open(wait=True)
        return pool

    @classmethod
    async def _create_oracle_pool(cls, db_config: Any) -> Any:
        """创建 Oracle 连接池"""
        import oracledb
        
        # 组装 DSN：由于智能体平台没有 extra_params，我们将 database_name 默认作为 SID 构建。
        # 如果用户名/密码/端口配置正确，可以直接通过 SID 连接。
        # 兼容性设计：若 database_name 包含斜杠（如 '/ORCL'），则默认当作服务名构建 DSN。
        db_name = db_config.database_name.strip()
        if db_name.startswith("/"):
            dsn = f"{db_config.host}:{db_config.port}{db_name}"
        else:
            dsn = oracledb.makedsn(db_config.host, int(db_config.port), sid=db_name)

        use_thick_mode = os.environ.get("USE_ORACLE_THICK_MODE") == "1"
        
        if use_thick_mode:
            logger.info("[Pool Manager] 正在创建 Oracle Thick Mode 连接池 (同步连接池)")
            init_oracle_thick_mode()
            pool = oracledb.create_pool(
                user=db_config.db_user,
                password=db_config.password,
                dsn=dsn,
                min=1,
                max=20,
                increment=1
            )
            return pool
        else:
            logger.info("[Pool Manager] 正在创建 Oracle Thin Mode 连接池 (异步连接池)")
            pool = await oracledb.create_pool_async(
                user=db_config.db_user,
                password=db_config.password,
                dsn=dsn,
                min=1,
                max=20,
                increment=1
            )
            return pool

    @classmethod
    async def invalidate_pool(cls, source_id: int):
        """外部主动释放/使特定数据源 ID 连接池失效的方法"""
        async with cls._lock:
            if source_id in cls._pools:
                pool, _ = cls._pools.pop(source_id)
                logger.info(f"[Pool Manager] 主动失效连接池 (ID: {source_id})")
                await cls._close_pool_internal(pool, source_id)

    @classmethod
    async def _close_pool_internal(cls, pool: Any, source_id: int):
        """内部关闭连接池句柄"""
        try:
            if hasattr(pool, "close"):
                res = pool.close()
                if asyncio.iscoroutine(res) or inspect.isawaitable(res):
                    await res
            if hasattr(pool, "wait_closed"):
                await pool.wait_closed()
            logger.info(f"[Pool Manager] 成功关闭并回收连接池 (ID: {source_id})")
        except Exception as e:
            logger.error(f"[Pool Manager] 关闭连接池时发生异常 (ID: {source_id}): {e}")

    @classmethod
    async def close_all_pools(cls):
        """优雅释放所有已初始化的连接池句柄（常用于 lifespan shutdown 退出时）"""
        async with cls._lock:
            logger.info(f"[Pool Manager] 正在优雅关闭全部已创建的本地连接池，当前池总数: {len(cls._pools)}")
            temp_pools = list(cls._pools.items())
            cls._pools.clear()
            
            for sid, (pool, _) in temp_pools:
                await cls._close_pool_internal(pool, sid)
            logger.info("[Pool Manager] 所有本地连接池释放完毕。")
