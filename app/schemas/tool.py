from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, Dict, Any, List
from datetime import datetime
import json


def normalize_group_name(value: Optional[str]) -> Optional[str]:
    """业务分组名归一：去首尾空白，空白串视为「未分组」（None）。

    既允许使用者清空分组（回到未分组），也避免把 ``"   "`` 存成看不见的空分组。
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class SysApiToolBase(BaseModel):
    name: str = Field(..., description="Unique tool identifier")
    description: Optional[str] = None
    method: str = Field("GET", description="HTTP Method")
    url_template: str = Field(..., description="Target API URL")
    headers: Optional[Dict[str, str]] = Field(default_factory=dict)
    parameter_schema: Optional[Dict[str, Any]] = Field(default_factory=dict)
    is_active: bool = True
    group_name: Optional[str] = Field(
        None,
        max_length=64,
        description="业务分组名：智能体配置的「工具能力」步骤按它归组；为空表示未分组",
    )

    @field_validator("group_name", mode="before")
    @classmethod
    def normalize_group(cls, v):
        return normalize_group_name(v)


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
    group_name: Optional[str] = Field(None, max_length=64)

    @field_validator("group_name", mode="before")
    @classmethod
    def normalize_group(cls, v):
        return normalize_group_name(v)


class SysApiToolBatchGroupRequest(BaseModel):
    """批量设置业务分组（注册表页多选后一次改完）。"""

    ids: List[str] = Field(..., min_length=1, max_length=500, description="要更新的工具 ID 列表")
    group_name: Optional[str] = Field(
        None,
        max_length=64,
        description="目标业务分组名；留空表示清除分组（回到未分组）",
    )

    @field_validator("group_name", mode="before")
    @classmethod
    def normalize_group(cls, v):
        return normalize_group_name(v)


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


class SysApiToolPreviewRequest(BaseModel):
    """保存前试跑：提交**草稿**（不落库），由服务端代发一次请求。

    没有 ``name`` 字段是刻意的——试跑不需要工具名，少一个字段就少一处
    「看起来会被保存」的误解。
    """

    method: str = Field("GET", description="HTTP Method")
    url_template: str = Field(..., min_length=1, description="目标 API URL 模板")
    headers: Optional[Dict[str, str]] = Field(default_factory=dict)
    parameter_schema: Optional[Dict[str, Any]] = Field(default_factory=dict)
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="试跑测试值：只用于这一次请求，绝不写入 sys_api_tools",
    )


class SysApiToolPreviewRequestInfo(BaseModel):
    """试跑实际发出的 HTTP 请求快照（用于界面核对最终 URL 与参数）。"""

    method: str
    url: str
    headers: Dict[str, str] = Field(default_factory=dict)
    query: Dict[str, Any] = Field(default_factory=dict)
    body: Any = None


class SysApiToolPreviewResponse(BaseModel):
    ok: bool
    status_code: Optional[int] = None
    elapsed_ms: int = 0
    body: str = ""
    body_truncated: bool = False
    request: Optional[SysApiToolPreviewRequestInfo] = Field(
        None,
        description="实际发出请求的最终 URL、Query、Body 与脱敏后的 Headers；预检失败时为空",
    )
    error_kind: Optional[str] = Field(
        None,
        description=(
            "http_error（接口通了但拒绝）/ connection（根本没连上）/ timeout / "
            "invalid_url / unsupported_method / missing_param / "
            "invalid_parameter_schema（系统级参数配置不合法）/ "
            "missing_user_variable（{user.xxx} 取不到值）"
        ),
    )
    message: str = ""
