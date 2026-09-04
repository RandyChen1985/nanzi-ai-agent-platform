from sqlalchemy import Column, String, Boolean, DateTime, Text, Integer, Float, UniqueConstraint
from datetime import datetime
from app.core.orm import Base

class AIModel(Base):
    __tablename__ = "ai_models"
    __table_args__ = (
        UniqueConstraint("model_id", name="uq_ai_models_model_id"),
    )

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    model_id = Column(String(255), nullable=False)  # Actual Model ID for API
    provider = Column(String(50), nullable=False)   # e.g., openai, azure
    type = Column(String(50), nullable=False)       # e.g., llm, embedding
    
    api_base_url = Column(String(512), nullable=True)
    api_key = Column(Text, nullable=True)
    context_size = Column(Integer, nullable=True)  # Model context window in tokens
    max_output_tokens = Column(Integer, nullable=True)  # Per-request output cap in tokens
    temperature = Column(Float, nullable=True)  # Model test/default temperature; NULL follows global config
    thinking_enable = Column(Boolean, nullable=False, default=False)
    thinking_only = Column(Boolean, nullable=False, default=False)
    allow_disable_thinking = Column(Boolean, nullable=False, default=True)
    reasoning_effort = Column(String(32), nullable=True, default=None)
    supported_reasoning_efforts = Column(
        Text,
        nullable=True,
        default='["low","high","max"]',
    )
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
