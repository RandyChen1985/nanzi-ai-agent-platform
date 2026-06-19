from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime

class ChangelogResponse(BaseModel):
    """变更日志响应模型"""
    id: int
    resource_type: str = Field(..., description="资源类型")
    resource_id: str = Field(..., description="资源ID")
    operation: str = Field(..., description="操作类型")
    old_data: Optional[Dict[str, Any]] = Field(None, description="变更前数据")
    new_data: Optional[Dict[str, Any]] = Field(None, description="变更后数据")
    changed_fields: Optional[List[str]] = Field(None, description="变更字段列表")
    user_id: Optional[int] = Field(None, description="操作用户ID")
    user_name: Optional[str] = Field(None, description="操作用户名")
    reason: Optional[str] = Field(None, description="变更原因")
    created_at: datetime = Field(..., description="创建时间")
    
    model_config = ConfigDict(from_attributes=True)

class ChangelogQueryParams(BaseModel):
    """变更日志查询参数"""
    resource_type: Optional[str] = Field(None, description="资源类型")
    operation: Optional[str] = Field(None, description="操作类型")
    user_id: Optional[int] = Field(None, description="用户ID")
    user_name: Optional[str] = Field(None, description="用户名")
    start_date: Optional[datetime] = Field(None, description="开始时间")
    end_date: Optional[datetime] = Field(None, description="结束时间")
    limit: int = Field(50, ge=1, le=200, description="每页数量")
    offset: int = Field(0, ge=0, description="偏移量")

class ChangelogStatsResponse(BaseModel):
    """变更统计响应模型"""
    change_date: str = Field(..., description="变更日期")
    resource_type: str = Field(..., description="资源类型")
    operation: str = Field(..., description="操作类型")
    change_count: int = Field(..., description="变更次数")
    user_count: int = Field(..., description="操作用户数")
    resource_count: int = Field(..., description="涉及资源数")

class ChangeDiffResponse(BaseModel):
    """变更对比响应模型"""
    operation: str = Field(..., description="操作类型")
    resource_name: str = Field(..., description="资源名称")
    changes: List[Dict[str, Any]] = Field(..., description="变更详情")
    summary: str = Field(..., description="变更摘要")
