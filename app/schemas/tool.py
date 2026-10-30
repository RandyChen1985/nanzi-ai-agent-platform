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
    signing_enabled: bool = Field(
        False,
        description="是否开启请求签名；关闭时运行时完全忽略签名配置",
    )
    signing_key_id: Optional[str] = Field(
        None,
        max_length=64,
        description="签名 Key-Id（16 位小写十六进制，全局唯一）",
    )
    signing_algorithm: str = Field(
        "hmac-sha256",
        description="签名算法；v1 只支持 hmac-sha256",
    )
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
    signing_secret: Optional[str] = Field(
        None,
        description="签名密钥（只写；服务端加密保存，任何响应都不回显）",
    )


class SysApiToolUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    method: Optional[str] = None
    url_template: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    parameter_schema: Optional[Dict[str, Any]] = None
    signing_enabled: Optional[bool] = None
    signing_key_id: Optional[str] = Field(None, max_length=64)
    signing_algorithm: Optional[str] = None
    signing_secret: Optional[str] = Field(
        None,
        description="轮换签名密钥时与新 Key-Id 一起提交；留空表示沿用已保存密钥",
    )
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
    signing_secret_configured: bool = False
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
    signing_enabled: bool = False
    signing_algorithm: str = "hmac-sha256"
    signing_key_id: Optional[str] = Field(None, max_length=64)
    signing_secret: Optional[str] = Field(
        None,
        description="新建草稿的签名密钥；只用于本次试跑，不落库",
    )
    tool_id: Optional[str] = Field(
        None,
        description="已保存工具试跑时传 id，服务端据此读取已加密的签名密钥",
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
    response_headers: Dict[str, str] = Field(
        default_factory=dict,
        description="目标接口返回的响应头；凭据类字段已脱敏",
    )
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
            "missing_user_variable（{user.xxx} 取不到值）/ "
            "missing_credential（导入的工具还带着 ${CREDENTIAL:...} 占位符）/ "
            "invalid_signing_config（签名配置不合法或密钥无法解密）"
        ),
    )
    message: str = ""
