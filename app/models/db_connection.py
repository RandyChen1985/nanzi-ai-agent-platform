from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint, ForeignKey, JSON, SmallInteger, Text
from datetime import datetime
from app.core.orm import Base


class MetaDbConnectionConfig(Base):
    """元数据导入 - 数据库连接配置（持久化到 DB，避免每次重复输入）"""
    __tablename__ = "meta_db_connection_configs"
    __table_args__ = (
        UniqueConstraint("name", name="uk_meta_db_connection_configs_name"),
    )

    id            = Column(Integer, primary_key=True, index=True)
    name          = Column(String(100), nullable=False, comment='连接别名，如"生产-业务库"')
    db_type       = Column(String(20), nullable=False, comment='数据库类型: mysql|clickhouse|oracle|sqlserver|postgresql')
    host          = Column(String(255), nullable=False, comment='主机地址')
    port          = Column(Integer, nullable=False, comment='端口号')
    db_user       = Column(String(100), nullable=False, comment='数据库用户名')
    password      = Column(String(255), nullable=False, default='', comment='密码（明文）')
    database_name = Column(String(100), nullable=False, comment='数据库/库名')
    description   = Column(String(500), nullable=False, default='', comment='备注/用途说明')
    created_by    = Column(Integer, nullable=False, default=0, comment='创建者用户 ID')
    created_at    = Column(DateTime, default=datetime.now)
    updated_at    = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class DbProfileTask(Base):
    """数据源摸排任务状态管理表"""
    __tablename__ = "db_profile_tasks"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    connection_id    = Column(Integer, ForeignKey("meta_db_connection_configs.id", ondelete="CASCADE"), nullable=False, unique=True)
    status           = Column(SmallInteger, nullable=False, default=0, comment='任务状态: 0-排队中, 1-进行中, 2-完成, 3-异常中断')
    total_tables     = Column(Integer, nullable=False, default=0, comment='总表/视图数')
    processed_tables = Column(Integer, nullable=False, default=0, comment='已处理表/视图数')
    current_table    = Column(String(100), nullable=True, comment='当前正在处理的物理表名')
    error_message    = Column(Text, nullable=True, comment='异常中断的说明')
    created_at       = Column(DateTime, default=datetime.now)
    updated_at       = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class DbTableProfile(Base):
    """外部数据源表和视图的智能摸排草稿库"""
    __tablename__ = "db_table_profiles"
    __table_args__ = (
        UniqueConstraint("connection_id", "table_name", name="uk_conn_table"),
    )

    id              = Column(Integer, primary_key=True, autoincrement=True)
    connection_id   = Column(Integer, ForeignKey("meta_db_connection_configs.id", ondelete="CASCADE"), nullable=False)
    table_name      = Column(String(100), nullable=False, comment='数据库物理表/视图名')
    table_type      = Column(String(20), nullable=False, comment='对象物理类型: table|view')
    engine          = Column(String(50), nullable=True, comment='存储引擎')
    ddl             = Column(Text, nullable=True, comment='物理建表 DDL')
    sample_data     = Column(Text, nullable=True, comment='3条采样数据样例(JSON格式)')
    ai_term         = Column(String(100), nullable=True, comment='AI识别的中文备注名/术语')
    ai_description  = Column(String(500), nullable=True, comment='AI分析的真实用途描述')
    ai_tags         = Column(JSON, nullable=True, comment='AI生成的分类标签数组')
    columns_profile = Column(JSON, nullable=True, comment='AI生成的字段描述画像(JSON数组)')
    status            = Column(SmallInteger, default=0, comment='摸排状态: 0-待开始, 1-摸排中, 2-摸排成功, 3-摸排失败')
    confidence_score  = Column(SmallInteger, nullable=False, default=100, comment='置信度/业务相关度评分(0-100)')
    is_temporary      = Column(SmallInteger, nullable=False, default=0, comment='是否为临时/备份表')
    is_ignored        = Column(SmallInteger, nullable=False, default=0, comment='分析中是否忽略该表')
    confidence_reason = Column(Text, nullable=True, comment='置信度判定与扣分原因')
    error_message     = Column(Text, nullable=True, comment='分析失败时的异常说明')
    created_at        = Column(DateTime, default=datetime.now)
    updated_at        = Column(DateTime, default=datetime.now, onupdate=datetime.now)
