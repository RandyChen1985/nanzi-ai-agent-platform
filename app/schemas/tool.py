from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, Dict, Any
from datetime import datetime
import json

class SysApiToolBase(BaseModel):
    name: str = Field(..., description="Unique tool identifier")
    description: Optional[str] = None
    method: str = Field("GET", description="HTTP Method")
    url_template: str = Field(..., description="Target API URL")
    headers: Optional[Dict[str, str]] = Field(default_factory=dict)
    parameter_schema: Optional[Dict[str, Any]] = Field(default_factory=dict)
    is_active: bool = True

class SysApiToolCreate(SysApiToolBase):
    pass

class SysApiToolUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    method: Optional[str] = None
    url_template: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    parameter_schema: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None

class SysApiToolResponse(SysApiToolBase):
    id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator('headers', 'parameter_schema', mode='before')
    @classmethod
    def parse_json(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v) if v else {}
            except ValueError:
                return {}
        return v
