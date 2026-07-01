from .base import DataSourceAdapter
from .clickhouse import ClickHouseAdapter
from .mysql import MySQLAdapter
from .oracle import OracleAdapter
from .sqlserver import SQLServerAdapter, normalize_sqlserver_type
import logging

logger = logging.getLogger(__name__)

async def get_adapter(data_source_name: str) -> DataSourceAdapter:
    """
    通过数据源别名（如 'default_clickhouse' 或 'mysql_oa'）从本地数据库拉取配置并返回匹配的适配器。
    匹配依据：精确匹配 meta_db_connection_configs.name。
    """
    from app.core.orm import AsyncSessionLocal
    from app.services.db_connection_service import DbConnectionService
    
    # 建立本地临时会话从数据库读取连接元数据
    async with AsyncSessionLocal() as session:
        db_config = await DbConnectionService.get_config_by_name(session, data_source_name)
        
    if not db_config:
        logger.error(f"[Adapter Factory] 未找到对应的数据源配置，别名: {data_source_name}")
        raise ValueError(f"未找到对应的数据源配置: '{data_source_name}'。请检查数据源管理命名或配置是否一致。")
        
    logger.debug(f"[Adapter Factory] 成功匹配本地数据源: {data_source_name} (类型: {db_config.db_type}, ID: {db_config.id})")

    # 根据数据库类型返回匹配的适配器
    db_type_lower = db_config.db_type.strip().lower()
    
    if db_type_lower == "clickhouse":
        return ClickHouseAdapter(db_config.id)
    elif db_type_lower == "mysql":
        return MySQLAdapter(db_config.id)
    elif db_type_lower == "oracle":
        return OracleAdapter(db_config.id)
    elif normalize_sqlserver_type(db_config.db_type):
        return SQLServerAdapter(db_config.id)
    else:
        logger.error(f"[Adapter Factory] 不支持的数据库类型: {db_config.db_type}")
        raise NotImplementedError(f"适配器暂未实现对 '{db_config.db_type}' 类型的支持。")
