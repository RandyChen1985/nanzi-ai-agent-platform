"""Platform tool: request user confirmation of business data fields."""
from __future__ import annotations

import json
import secrets
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.services.ai.tools.tool_compat import BaseTool

ValueType = Literal["string", "number", "boolean", "text"]


class ConfirmationField(BaseModel):
    key: str = Field(description="字段稳定标识，回传时使用")
    label: str = Field(description="展示给用户的字段名")
    value: Any = Field(default="", description="当前字段值")
    editable: bool = Field(default=True, description="是否允许用户在确认卡中编辑")
    value_type: ValueType = Field(default="string", description="值类型：string/number/boolean/text")

    @field_validator("key", "label")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("key/label 不能为空")
        return text


class RequestUserConfirmationArgs(BaseModel):
    title: str = Field(description="确认卡标题")
    fields: list[ConfirmationField] = Field(description="待确认字段列表，至少一项")
    summary: str = Field(default="", description="标题下的补充说明")
    confirm_label: str = Field(default="确定", description="确定按钮文案")
    cancel_label: str = Field(default="取消", description="取消按钮文案")
    risk_note: str = Field(default="", description="可选风险提示")

    @field_validator("title")
    @classmethod
    def _title_non_empty(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("title 不能为空")
        return text

    @field_validator("fields")
    @classmethod
    def _fields_non_empty(cls, value: list[ConfirmationField]) -> list[ConfirmationField]:
        if not value:
            raise ValueError("fields 不能为空")
        return value


class RequestUserConfirmationTool(BaseTool):
    name = "request_user_confirmation"
    description = (
        "在录入/修改/删除业务数据前，向用户展示可编辑的业务确认卡。"
        "调用后返回 awaiting_user，必须停止并等待用户下一条消息："
        "若收到「【业务确认】用户已确定」则按快照字段继续并视需要调用写入工具；"
        "若收到「【业务确认】用户已取消」则立即终止本次流程，不得调用写入类工具，"
        "且禁止再次调用本工具重新弹确认卡——只能用文字确认已取消并询问用户；"
        "仅当用户随后明确提供新的/修改后的数据并要求继续时，才可再次调用本工具。"
        "本工具只展示确认信息，不会写入任何业务系统。"
    )
    args_schema = RequestUserConfirmationArgs

    async def ainvoke(self, arguments: dict[str, Any] | None = None) -> str:
        from app.core.context import execution_options_context
        from app.services.ai.business_confirmation import (
            cancel_gate_block_payload,
            is_cancel_confirmation_gate_armed,
        )
        from app.services.ai.tool_nudge_policy import is_automatic_delivery_context

        if is_cancel_confirmation_gate_armed():
            return json.dumps(cancel_gate_block_payload(), ensure_ascii=False)

        # 业务确认卡没有委派透传通道（``business_confirmation`` 不在委派流白名单里）：
        # 子代理若以为「已展示」，就会原地等一个永远到不了的【业务确认】回执。
        # 与 ask_user_question 不同，这里连委派也一并拒绝——卡片的回执语义更重，
        # 未经父确认就代问用户反而容易让用户以为自己在批准一次真实写入。
        options = execution_options_context.get() or {}
        if is_automatic_delivery_context(None, options):
            return json.dumps(
                {
                    "status": "error",
                    "error": "当前为无人值守上下文（委派或定时/订阅任务），无法向用户展示确认卡。",
                    "hint": "不要等待确认回执。请把待确认的字段、你的建议取值和风险写进结论，"
                    "由主智能体向用户确认后再继续。",
                },
                ensure_ascii=False,
            )

        arguments = arguments or {}
        try:
            args = RequestUserConfirmationArgs.model_validate(arguments)
        except Exception as exc:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"入参无效: {exc}",
                    "hint": "请提供非空 title，以及至少一项含 key/label 的 fields",
                },
                ensure_ascii=False,
            )

        confirmation_id = f"bc_{secrets.token_hex(8)}"
        ui = {
            "title": args.title,
            "summary": args.summary or "",
            "fields": [field.model_dump(mode="json") for field in args.fields],
            "confirm_label": args.confirm_label or "确定",
            "cancel_label": args.cancel_label or "取消",
            "risk_note": args.risk_note or "",
        }
        return json.dumps(
            {
                "status": "awaiting_user",
                "confirmation_id": confirmation_id,
                "message": "已向用户展示确认卡，请等待用户确定或取消后再继续。",
                "ui": ui,
            },
            ensure_ascii=False,
        )


request_user_confirmation = RequestUserConfirmationTool()
