from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from datetime import datetime

class AIModelBase(BaseModel):
    name: str = Field(..., description="Display Name")
    model_id: str = Field(..., description="Actual Model ID for API")
    provider: str = Field(..., description="Provider: openai, azure, ollama, etc")
    type: str = Field(..., description="Type: llm, embedding, etc")
    api_base_url: Optional[str] = None
    is_active: bool = True

class AIModelCreate(AIModelBase):
    api_key: Optional[str] = None

class AIModelUpdate(BaseModel):
    name: Optional[str] = None
    model_id: Optional[str] = None
    provider: Optional[str] = None
    type: Optional[str] = None
    api_base_url: Optional[str] = None
    api_key: Optional[str] = None
    is_active: Optional[bool] = None

class AIModelResponse(AIModelBase):
    id: str
    created_at: datetime
    updated_at: datetime
    # We do NOT return the full api_key for security
    has_api_key: bool = False

    model_config = ConfigDict(from_attributes=True)

    @staticmethod
    def from_orm_custom(obj):
        """Custom converter to handle masked API key logic"""
        data = AIModelResponse.model_validate(obj)
        data.has_api_key = bool(obj.api_key)
        # Ensure API key is never leaked in the default response if Pydantic included it
        # (Though it is not in AIModelBase, better safe)
        return data
