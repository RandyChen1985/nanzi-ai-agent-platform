from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, Integer, JSON, Float
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.orm import Base

class AIAgent(Base):
    __tablename__ = "ai_agents"

    id = Column(String(36), primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    display_name = Column(String(100), nullable=False)
    description = Column(Text)
    avatar_url = Column(String(255))
    capabilities = Column(JSON) # Tags for routing e.g. ["data", "coding"]
    agent_type = Column(String(32), nullable=False, default="GENERAL")
    onboarding_key = Column(String(64), nullable=True)
    onboarding_step = Column(String(20), nullable=False, default="COMPLETE")
    is_system = Column(Boolean, default=False)
    # 注意：system_status / system_pending_request_id 两列**刻意不在这里声明**。
    # 它们由 V185 / V85 迁移加入，但代码可能先于迁移上线；一旦映射进 ORM，
    # SELECT/INSERT 都会带上不存在的列，迁移前智能体列表、对话、委派全部 1054。
    # 读写统一走 app/services/ai/agent_system_columns.py 的原生 SQL + 列探测（同
    # ResourcePermission.agent_id 的既有做法）。
    sort_order = Column(Integer, default=0)
    is_enabled = Column(Boolean, default=True)
    engine_type = Column(String(20), default='LOCAL')
    engine_config = Column(JSON, nullable=True)
    created_by = Column(String(64), nullable=True)
    owner_group = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # Relationship to versions
    versions = relationship("AIAgentVersion", back_populates="agent", cascade="all, delete-orphan")

class AIAgentVersion(Base):
    __tablename__ = "ai_agent_versions"

    id = Column(String(36), primary_key=True)
    agent_id = Column(String(36), ForeignKey("ai_agents.id", ondelete="CASCADE"), nullable=False)
    version_number = Column(Integer, nullable=False)
    model_name = Column(String(100))
    temperature = Column(Float, default=0.0)
    synthesis_model_name = Column(String(100), nullable=True)
    synthesis_temperature = Column(Float, nullable=True)
    # 是否允许用户在输入框手动切换模型；历史数据与未配置场景一律按 True（允许）处理
    allow_model_switch = Column(Boolean, nullable=False, default=True)
    system_prompt = Column(Text, nullable=False)
    tools = Column(JSON) # List of tool names ["tool1", "tool2"]
    toolcall_timeout_seconds = Column(Integer, nullable=True)  # 版本级工具调用超时；NULL 表示跟随全局
    skills_custom = Column(Boolean, default=False, nullable=False)  # 是否自定义公共 Skills
    skills = Column(JSON)  # 自定义公共技能 ID 列表 ["skill-id", ...]
    welcome_config = Column(JSON, nullable=True)  # 欢迎页三卡片配置（版本级）
    status = Column(String(20), default="DRAFT") # DRAFT, PUBLISHED, ARCHIVED
    comment = Column(String(255))
    created_at = Column(DateTime, default=datetime.now)

    # Relationship back to agent
    agent = relationship("AIAgent", back_populates="versions")
