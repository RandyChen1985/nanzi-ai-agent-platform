"""管理员站内信群发的请求 / 响应模型。

契约要点（改动前先读）：

1. 前端只提交「范围」（用户 / 部门 / 角色 ID），**不接受最终收件人名单**。
   名单必须由服务端解析，否则「预览人数」与「实际发送名单」会分叉。
2. ``confirm_total`` 是防误发闸门：前端把预览到的命中人数带回来，与发送时
   服务端重新解析的结果不一致就拒绝发送（409）——中间的账号启用/停用、
   部门成员变动都会让人数漂移，静默按新人数发出去正是这类功能最该防的事故。
3. 标题长度对齐 ``portal_notifications.title VARCHAR(200)``；正文上限刻意低于
   MySQL TEXT 的 65535 字节，避免中文长文在严格模式下整行被拒。
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

# 与 portal_notifications.title VARCHAR(200) 对齐
BROADCAST_TITLE_MAX_LENGTH = 200
# 约 30KB（UTF-8 中文 3 字节/字），远低于 TEXT 的 65535 字节上限
BROADCAST_CONTENT_MAX_LENGTH = 10000
BROADCAST_LEVELS = ("info", "success", "warning", "error")


def _normalize_positive_ids(values: Optional[List[int]]) -> List[int]:
    """去重、丢弃非正整数、保持提交顺序。

    群发入参来自表单与用户搜索，脏值（0、负数、None、字符串数字）必须在这里
    就地清掉：SQL 里带个 0 会让 in_() 命中垃圾条件，带重复 ID 会让命中人数虚高。
    """
    seen = set()
    result: List[int] = []
    for raw in values or []:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in seen:
            seen.add(value)
            result.append(value)
    return result


class BroadcastAudience(BaseModel):
    user_ids: List[int] = Field(default_factory=list, description="指定用户 ID")
    dept_ids: List[int] = Field(
        default_factory=list,
        description="部门 ID：按物化路径自动包含全部下级部门",
    )
    role_ids: List[int] = Field(default_factory=list, description="角色 ID")

    @field_validator("user_ids", "dept_ids", "role_ids")
    @classmethod
    def _clean_ids(cls, value: Optional[List[int]]) -> List[int]:
        return _normalize_positive_ids(value)

    def is_empty(self) -> bool:
        """三个维度都没选时为 True —— 端点据此返回 400，而不是静默 0 人。"""
        return not (self.user_ids or self.dept_ids or self.role_ids)


class BroadcastPreviewRequest(BaseModel):
    audience: BroadcastAudience
    limit: int = Field(20, ge=0, le=100, description="抽样展示的人数上限")


class BroadcastSendRequest(BroadcastPreviewRequest):
    title: str = Field(..., min_length=1, max_length=BROADCAST_TITLE_MAX_LENGTH)
    content: str = Field(..., min_length=1, max_length=BROADCAST_CONTENT_MAX_LENGTH)
    level: str = Field("info", description="info / success / warning / error")
    confirm_total: Optional[int] = Field(
        None,
        ge=0,
        description="前端预览到的命中人数；与发送时重新解析的结果不一致将被拒绝",
    )

    @field_validator("title", "content")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        stripped = (value or "").strip()
        if not stripped:
            raise ValueError("不能为空")
        return stripped

    @field_validator("level")
    @classmethod
    def _clean_level(cls, value: str) -> str:
        normalized = str(value or "info").strip().lower()
        return normalized if normalized in BROADCAST_LEVELS else "info"


class BroadcastSampleUser(BaseModel):
    user_id: int
    user_name: str
    real_name: Optional[str] = None


class BroadcastPreviewData(BaseModel):
    total: int = Field(..., description="命中的启用账号数（去重后）")
    sample: List[BroadcastSampleUser] = Field(default_factory=list, description="抽样名单")


class BroadcastSendData(BaseModel):
    sent: int = Field(..., description="实际写入的站内信条数")


# --------------------------------------------------------------------------- #
# 历史发送记录
# --------------------------------------------------------------------------- #


class BroadcastRecordSummary(BaseModel):
    """历史列表里的一行。

    正文只给截断后的预览：一次群发正文上限 10000 字，列表 10 条就把响应撑到
    100KB 以上，而列表页根本不需要全文。全文走详情接口。
    """

    id: int
    title: str
    level: str
    content_preview: str
    audience: BroadcastAudience
    recipient_count: int
    sender_user_name: str = ""
    sender_real_name: Optional[str] = None
    created_at: Optional[str] = Field(None, description="ISO 时间；库里为 NULL 时保留空")


class BroadcastRecordDetail(BroadcastRecordSummary):
    content: str = Field(..., description="群发正文原文，用于回填重发")


class BroadcastRecordListData(BaseModel):
    total: int
    items: List[BroadcastRecordSummary] = Field(default_factory=list)
