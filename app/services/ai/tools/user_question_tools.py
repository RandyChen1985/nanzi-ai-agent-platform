"""Platform tool for AI-initiated clarification questions."""
from __future__ import annotations

import json
import secrets
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.ai.tools.tool_compat import BaseTool


class QuestionOption(BaseModel):
    id: str = Field(description="选项稳定标识，回传时使用")
    label: str = Field(description="展示给用户的选项名称")
    description: str | None = Field(default=None, description="选项补充说明")
    action: str | None = Field(
        default=None,
        description=(
            "选项附带的页面动作标识（如 open_dataset_picker）。用户点击该项时，"
            "前端在正常选中之外额外触发对应页面动作；未知 action 应被忽略。"
            "动作不影响回执内容，选中与提交行为保持不变。"
        ),
    )
    disabled: bool = Field(
        default=False,
        description=(
            "置灰不可选，用于「看得到但当前用不了」的情形（如该知识库尚无权限、"
            "智能体未绑定任何库）。仅作前端提示，不代表后端会拒绝该选项的回执——"
            "真正的约束必须在业务侧判断。"
        ),
    )

    @field_validator("id", "label")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("选项 id/label 不能为空")
        return text


class AskUserQuestionArgs(BaseModel):
    question: str = Field(description="向用户提出的核心问题")
    options: list[QuestionOption] = Field(description="供用户选择的选项列表，至少两项")
    is_multi_select: bool = Field(default=False, description="是否支持多选")
    allow_custom_input: bool = Field(default=True, description="是否允许用户补充说明")
    context: str | None = Field(default=None, description="问题背景说明")
    purpose: str | None = Field(default=None, description="受控恢复用途标识，普通提问无需填写")

    @field_validator("question")
    @classmethod
    def _question_non_empty(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("question 不能为空")
        if len(text) > 500:
            raise ValueError("question 不能超过 500 个字符")
        return text

    @field_validator("options")
    @classmethod
    def _options_valid(cls, value: list[QuestionOption]) -> list[QuestionOption]:
        if len(value) < 2:
            raise ValueError("options 至少需要两项")
        if len(value) > 12:
            raise ValueError("options 不能超过 12 项")
        ids = [item.id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("options 的 id 必须唯一")
        if any(len(item.label) > 200 for item in value):
            raise ValueError("选项 label 不能超过 200 个字符")
        # 全部置灰等于把用户堵死：卡片看得见、一项都点不了，还占着等待回答的状态。
        if all(item.disabled for item in value):
            raise ValueError("options 至少要保留一项可选，不能全部 disabled")
        return value

    @field_validator("context")
    @classmethod
    def _context_limited(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if len(text) > 1000:
            raise ValueError("context 不能超过 1000 个字符")
        return text or None

    @field_validator("purpose")
    @classmethod
    def _purpose_limited(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if len(text) > 100:
            raise ValueError("purpose 不能超过 100 个字符")
        return text or None


#: 子代理提问的额外上限。父自己的提问不受这些限制。
#:
#: 子代理读过网页、上传文档、知识库片段，它的提问内容属于不可信输入：提示词注入
#: 可以把攻击者的话包装成提问卡显示在主会话里，还带着子智能体的权威感。所以这里
#: 比 ``AskUserQuestionArgs`` 的通用上限更严，并且额外禁掉 ``action``。
_DELEGATED_ASK_MAX_QUESTION_CHARS = 200
_DELEGATED_ASK_MAX_OPTION_LABEL_CHARS = 60
_DELEGATED_ASK_MAX_OPTIONS = 6


def _enabled_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def validate_delegated_ask(args: "AskUserQuestionArgs") -> str | None:
    """校验子代理提问的内容边界，返回违规原因（None 表示通过）。"""
    if len(args.question) > _DELEGATED_ASK_MAX_QUESTION_CHARS:
        return (
            f"子代理提问的问题不能超过 {_DELEGATED_ASK_MAX_QUESTION_CHARS} 个字符"
            f"（当前 {len(args.question)}）"
        )
    if len(args.options) > _DELEGATED_ASK_MAX_OPTIONS:
        return (
            f"子代理提问的选项不能超过 {_DELEGATED_ASK_MAX_OPTIONS} 项"
            f"（当前 {len(args.options)}）"
        )
    for item in args.options:
        if item.action:
            return "子代理提问的选项不允许携带 action：页面动作只能由主智能体发起"
        if len(item.label) > _DELEGATED_ASK_MAX_OPTION_LABEL_CHARS:
            return (
                f"子代理提问的选项名称不能超过 {_DELEGATED_ASK_MAX_OPTION_LABEL_CHARS} 个字符"
                f"（当前 {len(item.label)}）"
            )
    for text in (args.context or "", args.purpose or ""):
        if "http://" in text or "https://" in text:
            return "子代理提问的背景说明不允许包含链接"
    return None


class AskUserQuestionTool(BaseTool):
    name = "ask_user_question"
    description = (
        "当用户明确要求互动式提问（如‘随便问我几个问题’、‘考考我’、‘请逐个问我’），"
        "或缺少会实质改变结果的条件、存在多个同等合理的执行分支时，主动向用户提问。"
        "‘列出几个问题’属于文字生成，不等同于‘问我几个问题’。"
        "支持单选、多选和补充输入；调用后必须停止当前执行并等待【用户回答】回执。"
        "能够依据上下文安全推断时不要重复提问；每轮最多提出一个问题。"
        "在委派子任务中同样可以提问：卡片会直达用户，你这一轮停下等回答，"
        "用户答完后主智能体会带着答案让你继续。委派场景的边界更严：问题不超过 "
        f"{_DELEGATED_ASK_MAX_QUESTION_CHARS} 字、给 2-4 个选项"
        f"（最多 {_DELEGATED_ASK_MAX_OPTIONS} 个）、选项名称不超过 "
        f"{_DELEGATED_ASK_MAX_OPTION_LABEL_CHARS} 字，且不得包含链接或页面动作。"
    )
    args_schema = AskUserQuestionArgs

    async def ainvoke(self, arguments: dict[str, Any] | None = None) -> str:
        # 两种非交互上下文要区别对待：
        #   * 定时 / 订阅任务——真的没有在线用户，提问只会空等，直接拒绝；
        #   * 委派子代理——提问卡会由 agent_delegate_tool 转登记到父会话并直达用户，
        #     父这一轮停下来等回答，所以放行（但要过下面的子代理专用安全边界）。
        from app.core.context import execution_options_context
        from app.services.ai.tool_nudge_policy import is_automatic_delivery_context

        options = execution_options_context.get() or {}
        delegated_sub_agent = _enabled_flag(options.get("delegated_sub_agent"))
        # 定时 / 订阅任务整条链路都没有在线用户，委派出去的子代理同样没有：
        # 必须无视 delegated_sub_agent 直接拒绝，否则卡片会塞进一个没人消费的队列。
        unattended = _enabled_flag(options.get("unattended"))
        if unattended or (is_automatic_delivery_context(None, options) and not delegated_sub_agent):
            return json.dumps(
                {
                    "status": "error",
                    "error": "当前为无人值守上下文（委派或定时/订阅任务），没有可交互的用户。",
                    "hint": "不要再尝试向用户提问。请基于现有信息直接给出结论；若确实缺少关键条件，"
                    "在回答中说明缺少什么，由主智能体决定是否转问用户。",
                },
                ensure_ascii=False,
            )
        try:
            args = AskUserQuestionArgs.model_validate(arguments or {})
        except Exception as exc:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"入参无效: {exc}",
                    "hint": "请提供非空 question，以及至少两项 id 唯一的 options",
                },
                ensure_ascii=False,
            )
        if delegated_sub_agent:
            violation = validate_delegated_ask(args)
            if violation:
                return json.dumps(
                    {
                        "status": "error",
                        "error": violation,
                        "hint": "不要在提问里夹带页面动作、链接或长段指令。"
                        "请把情况写进结论，由主智能体决定是否转问用户。",
                    },
                    ensure_ascii=False,
                )
        source_agent_name: str | None = None
        if delegated_sub_agent:
            from app.core.context import get_current_agent_context

            source_ctx = get_current_agent_context()
            source_agent_name = (
                str(getattr(source_ctx, "agent_name", "") or "").strip() or None
            )
        return json.dumps(
            {
                "status": "awaiting_user",
                "interaction_type": "question",
                "source_agent_name": source_agent_name,
                "question_id": f"uq_{secrets.token_hex(8)}",
                "message": "已向用户展示提问卡，请等待用户回答后再继续执行。",
                "question": args.question,
                "options": [option.model_dump(mode="json") for option in args.options],
                "is_multi_select": args.is_multi_select,
                "allow_custom_input": args.allow_custom_input,
                "context": args.context or "",
                "purpose": args.purpose or "",
            },
            ensure_ascii=False,
        )


ask_user_question = AskUserQuestionTool()
