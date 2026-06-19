from typing import Generic, TypeVar, Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime

T = TypeVar("T")

class StandardResponse(BaseModel, Generic[T]):
    code: int = Field(200, description="业务状态码，200表示成功", json_schema_extra={"example": 200})
    message: str = Field("success", description="响应消息", json_schema_extra={"example": "success"})
    data: Optional[T] = Field(None, description="业务数据")
    timestamp: Optional[datetime] = Field(default_factory=datetime.now, description="响应时间戳")
    trace_id: Optional[str] = Field(None, description="请求追踪ID")
    execution_mode: Optional[str] = Field(None, description="执行模式: local 或 remote", json_schema_extra={"example": "local"})

class ErrorResponse(BaseModel):
    code: int = Field(..., description="业务错误码", json_schema_extra={"example": 4001})
    message: str = Field(..., description="错误描述", json_schema_extra={"example": "Invalid Parameter"})
    detail: Optional[Any] = Field(None, description="错误详情（调试用）")
    data: Optional[Any] = Field(None, description="始终为 null")
    timestamp: datetime = Field(..., description="错误发生时间")
    trace_id: str = Field(..., description="追踪ID")
    execution_mode: Optional[str] = Field(None, description="执行模式: local 或 remote", json_schema_extra={"example": "local"})

class ListResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
