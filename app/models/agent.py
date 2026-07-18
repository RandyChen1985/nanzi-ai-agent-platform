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
    is_system = Column(Boolean, default=False)
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
    system_prompt = Column(Text, nullable=False)
    tools = Column(JSON) # List of tool names ["tool1", "tool2"]
    skills_custom = Column(Boolean, default=False, nullable=False)  # 是否自定义公共 Skills
    skills = Column(JSON)  # 自定义公共技能 ID 列表 ["skill-id", ...]
    status = Column(String(20), default="DRAFT") # DRAFT, PUBLISHED, ARCHIVED
    comment = Column(String(255))
    created_at = Column(DateTime, default=datetime.now)

    # Relationship back to agent
    agent = relationship("AIAgent", back_populates="versions")
