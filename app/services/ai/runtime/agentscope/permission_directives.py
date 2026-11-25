"""子代理权限交接指令的固定前缀与判定。

子代理的确认卡属于内部子会话，确认后由父会话以**一条新用户消息**续聊
（``resume.py`` 的 ``_handoff_subagent_permission``）。这条消息是系统生成的指令，
不是用户说的话，因此：

1. 路由必须沿用上一轮智能体（对话粘性），不能交给路由 LLM——文案里显式点名了
   子智能体名与工具名，LLM 极可能把它派给子智能体本人；
2. 子智能体在主会话直接跑时没有 ``sub_agent_call``、``parent_conversation_id``
   为空，拿不到写在 ``{parent_conv}::sub::{agent}`` 上的授权，同一个工具会**再弹
   一次卡**，用户陷入「点允许 → 又弹 → 再点」的死循环。

所以前缀常量与生成侧、路由判定侧共用同一份定义，避免两处文案漂移后静默失效。
"""

from __future__ import annotations

#: 授权通过：``【用户授权】子智能体「X」请求执行的工具 Y 已获允许（…）。``
SUBAGENT_PERMISSION_GRANT_PREFIX = "【用户授权】子智能体"
#: 授权被拒：``【用户拒绝】用户拒绝子智能体「X」执行工具 Y 的请求。``
SUBAGENT_PERMISSION_DENY_PREFIX = "【用户拒绝】用户拒绝子智能体"


def is_subagent_permission_directive(text: str | None) -> bool:
    """这条用户消息是否是子代理权限交接回执（而非真人发言）。

    必须是行首前缀，不能是子串包含：直接粘一段旧回执进对话框、或让模型解释这段协议
    文案时，子串匹配会把一条真实提问强行粘到上一轮智能体上、还跳过路由。
    """
    normalized = str(text or "").lstrip()
    if not normalized:
        return False
    return normalized.startswith(
        SUBAGENT_PERMISSION_GRANT_PREFIX
    ) or normalized.startswith(SUBAGENT_PERMISSION_DENY_PREFIX)
