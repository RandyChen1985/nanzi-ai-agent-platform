"""脚本工具的请求/响应模型。

与 ``schemas/tool.py``（API 工具）保持同一套惯例：
- 创建/更新用显式字段，不用 ``extra=allow``；
- 响应用 ``from_attributes`` + 把库里存成字符串的 JSON 列解析回 dict/list；
- 试运行只收**草稿**，绝不落库。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.tool import normalize_group_name

SCRIPT_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
SCRIPT_TOOL_MAX_CODE_BYTES = 200_000
SCRIPT_TOOL_SUPPORTED_LANGUAGES = ("python",)


def _normalize_requirements(value: Any) -> List[str]:
    """依赖声明归一：支持 JSON 字符串 / 列表 / 空值，去空白与重复。"""
    if value is None or value == "":
        return []
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = [item for item in re.split(r"[\n,，]", value) if item.strip()]
    if not isinstance(parsed, (list, tuple)):
        raise ValueError("requirements 必须是字符串数组，如 [\"pypdf>=4.0\"]")
    normalized: List[str] = []
    for item in parsed:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _validate_code_text(value: str) -> str:
    """创建与更新共用：源码不能是空白，且不能超过存储上限。"""
    text = str(value)
    if not text.strip():
        raise ValueError("脚本源码不能为空")
    if len(text.encode("utf-8")) > SCRIPT_TOOL_MAX_CODE_BYTES:
        raise ValueError(f"脚本源码不能超过 {SCRIPT_TOOL_MAX_CODE_BYTES} 字节")
    return value


class SysScriptToolBase(BaseModel):
    name: str = Field(..., description="工具名（模型可见，全平台唯一）")
    display_name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    language: str = Field("python", description="脚本语言，当前仅支持 python")
    parameter_schema: Optional[Dict[str, Any]] = Field(default_factory=dict)
    requirements: List[str] = Field(default_factory=list, description="第三方依赖，如 [\"pypdf>=4.0\"]")
    timeout_seconds: Optional[int] = Field(None, ge=1, le=3600, description="单次执行超时；空则跟随平台配置")
    is_active: bool = True
    group_name: Optional[str] = Field(None, max_length=64)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        text = str(v or "").strip()
        if not SCRIPT_TOOL_NAME_PATTERN.match(text):
            raise ValueError("工具名需为小写字母开头，仅含小写字母/数字/下划线，长度 3-64")
        return text

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        text = str(v or "python").strip().lower()
        if text not in SCRIPT_TOOL_SUPPORTED_LANGUAGES:
            raise ValueError("当前仅支持 python 脚本")
        return text

    @field_validator("requirements", mode="before")
    @classmethod
    def normalize_requirements(cls, v):
        return _normalize_requirements(v)

    @field_validator("group_name", mode="before")
    @classmethod
    def normalize_group(cls, v):
        return normalize_group_name(v)


class SysScriptToolCreate(SysScriptToolBase):
    code: str = Field(..., min_length=1, description="脚本源码")

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        return _validate_code_text(v)


class SysScriptToolUpdate(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    language: Optional[str] = None
    code: Optional[str] = None
    parameter_schema: Optional[Dict[str, Any]] = None
    requirements: Optional[List[str]] = None
    timeout_seconds: Optional[int] = Field(None, ge=1, le=3600)
    is_active: Optional[bool] = None
    group_name: Optional[str] = Field(None, max_length=64)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if v is None:
            return v
        text = str(v).strip()
        if not SCRIPT_TOOL_NAME_PATTERN.match(text):
            raise ValueError("工具名需为小写字母开头，仅含小写字母/数字/下划线，长度 3-64")
        return text

    @field_validator("code")
    @classmethod
    def validate_code(cls, v):
        if v is None:
            return v
        return _validate_code_text(v)

    @field_validator("language")
    @classmethod
    def validate_language(cls, v):
        if v is None:
            return v
        text = str(v).strip().lower()
        if text not in SCRIPT_TOOL_SUPPORTED_LANGUAGES:
            raise ValueError("当前仅支持 python 脚本")
        return text

    @field_validator("requirements", mode="before")
    @classmethod
    def normalize_requirements(cls, v):
        if v is None:
            return v
        return _normalize_requirements(v)

    @field_validator("group_name", mode="before")
    @classmethod
    def normalize_group(cls, v):
        return normalize_group_name(v)


class SysScriptToolResponse(SysScriptToolBase):
    id: str
    source: str = "custom"
    code: Optional[str] = None
    # 内置脚本被改过（库里存了 code）：页面要标「已覆盖」，并提供「恢复内置默认」
    is_overridden: bool = False
    # 迁移未执行、该行只来自代码库定义：没有库行可编辑，页面据此禁用写操作。
    # 不能让前端靠 id 前缀猜——库里的内置行 id 本来就是 builtin-script-*。
    is_fallback: bool = False
    # 内置工具在迁移漏跑时由代码兜底返回，此时没有库行时间戳，不能强制要求
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("parameter_schema", mode="before")
    @classmethod
    def parse_schema(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v) if v else {}
            except ValueError:
                return {}
        return v

    @field_validator("requirements", mode="before")
    @classmethod
    def parse_requirements_before(cls, v):
        return _normalize_requirements(v)


class SysScriptToolBatchGroupRequest(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=500)
    group_name: Optional[str] = Field(None, max_length=64)

    @field_validator("group_name", mode="before")
    @classmethod
    def normalize_group(cls, v):
        return normalize_group_name(v)


class SysScriptToolPreviewRequest(BaseModel):
    """保存前试跑。

    ``tool_id`` 存在时用库里已保存的代码（可覆盖其中字段）；否则完全用提交的草稿。
    试跑不写 ``sys_script_tools``，也不需要工具名——少一个字段就少一处误解。
    """

    tool_id: Optional[str] = None
    code: Optional[str] = None
    language: str = "python"
    parameter_schema: Optional[Dict[str, Any]] = Field(default_factory=dict)
    requirements: List[str] = Field(default_factory=list)
    timeout_seconds: Optional[int] = Field(None, ge=1, le=3600)
    params: Dict[str, Any] = Field(default_factory=dict, description="测试参数，只用于本次执行")

    @field_validator("requirements", mode="before")
    @classmethod
    def normalize_requirements(cls, v):
        return _normalize_requirements(v)


class SysScriptToolPreviewResponse(BaseModel):
    ok: bool
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    sandbox_policy: Optional[str] = None
    message: str = ""


class ScriptToolCapabilities(BaseModel):
    """脚本工具的运行环境说明。

    前端据此显示「沙箱内执行」还是「本地工作区执行」——local 策略下不能谎称沙箱。
    """

    sandbox_policy: str = "local"
    sandbox_label: str = "本地工作区"
    notice: str = ""
    sandbox_enabled: bool = False
    # e2b / ssh 没有「宿主工作区 → 沙箱路径」的映射，脚本工具这两种部署跑不了。
    # 与其让用户点保存后每次都失败，不如在页面上先说清楚。
    script_tool_supported: bool = True
