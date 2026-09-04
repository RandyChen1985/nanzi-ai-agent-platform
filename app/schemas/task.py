from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from datetime import datetime

def _validate_cron_expr(v: Optional[str]) -> Optional[str]:
    """用 APScheduler CronTrigger 预校验 cron 表达式，避免非法值入库引发调度崩溃。"""
    if v is None:
        return v
    from apscheduler.triggers.cron import CronTrigger
    try:
        CronTrigger.from_crontab(v)
    except (ValueError, KeyError) as e:
        raise ValueError(f"无效的 Cron 表达式 '{v}'：{e}")
    return v


class TaskBase(BaseModel):
    name: str = Field(..., description="任务名称")
    agent_id: str = Field(..., description="绑定的智能体ID")
    cron_expr: str = Field(..., description="Cron 表达式")
    prompt: str = Field(..., description="执行指令")
    config: Optional[Dict[str, Any]] = None

    @field_validator("cron_expr")
    @classmethod
    def validate_cron_expr(cls, v: str) -> str:
        return _validate_cron_expr(v)  # type: ignore[return-value]

class TaskCreate(TaskBase):
    pass

class TaskUpdate(BaseModel):
    name: Optional[str] = None
    agent_id: Optional[str] = None
    cron_expr: Optional[str] = None
    prompt: Optional[str] = None
    status: Optional[int] = None
    config: Optional[Dict[str, Any]] = None

    @field_validator("cron_expr")
    @classmethod
    def validate_cron_expr(cls, v: Optional[str]) -> Optional[str]:
        return _validate_cron_expr(v)

class TaskResponse(TaskBase):
    # 覆盖父类严格校验：从 DB 读取时宽容处理历史非法 cron，不崩溃列表页
    @field_validator("cron_expr", mode="before")
    @classmethod
    def validate_cron_expr(cls, v: str) -> str:  # type: ignore[override]
        try:
            return _validate_cron_expr(v)  # type: ignore[return-value]
        except ValueError:
            # 历史脏数据：保留原值，不阻断列表接口
            return v

    cron_valid: bool = True  # 历史非法 cron 时为 False，供前端显示警告

    @model_validator(mode="after")
    def _set_cron_valid(self) -> "TaskResponse":
        try:
            _validate_cron_expr(self.cron_expr)
            self.cron_valid = True
        except ValueError:
            self.cron_valid = False
        return self

    id: int
    user_id: int
    creator_name: Optional[str] = None
    agent_name: Optional[str] = None
    conversation_id: str
    source: str = "web"
    status: int
    run_count: int = 0
    trigger_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    skipped_count: int = 0
    consecutive_failures: int = 0
    health_status: str = "unknown"
    last_status: Optional[str] = None
    last_message: Optional[str] = None
    last_error: Optional[str] = None
    last_attempt_at: Optional[str] = None
    last_finished_at: Optional[str] = None
    last_alert_at: Optional[str] = None
    last_delivery_status: Optional[str] = None
    last_delivery_error: Optional[str] = None
    last_delivery_at: Optional[str] = None
    last_run_id: Optional[str] = None
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class TaskLogResponse(BaseModel):
    id: int
    trace_id: str
    query: str
    summary: Optional[str] = None
    status: str
    execution_time_ms: float
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskExecutionHistoryItem(BaseModel):
    """管理员全局执行记录：history 行 + 关联定时任务上下文。"""

    id: int
    trace_id: str
    query: Optional[str] = None
    summary: Optional[str] = None
    status: str
    execution_time_ms: Optional[float] = None
    created_at: datetime
    conversation_id: Optional[str] = None
    username: Optional[str] = None
    task_id: Optional[int] = None
    task_name: Optional[str] = None
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    user_id: Optional[int] = None
    creator_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
