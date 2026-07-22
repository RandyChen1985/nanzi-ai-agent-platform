"""TaskCenter 结果通知渠道：与 UI / create_recurring_task / 调度提示词补充同源。"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

NOTIFICATION_CHANNELS_KEY = "notification_channels"

# channel_id -> (tool_name, 中文标签, 调用提示)
CHANNEL_SPECS: Dict[str, tuple[str, str, str]] = {
    "portal": (
        "send_portal_notification",
        "站内消息",
        "调用 send_portal_notification(title, content, level=info) 写入门户铃铛站内信",
    ),
    "dingtalk": (
        "send_dingtalk_message",
        "钉钉",
        "调用 send_dingtalk_message(title, content)；凭据来自个人中心→消息通知，勿再索要 webhook",
    ),
    "wechat_work": (
        "send_wechat_work_message",
        "企业微信",
        "调用 send_wechat_work_message(content)；凭据来自个人中心→消息通知，勿再索要 webhook",
    ),
    "email": (
        "send_email",
        "邮件",
        "调用 send_email(to_email, subject, content)；SMTP 与收件人来自个人中心→消息通知，勿再索要服务器配置",
    ),
}

VALID_CHANNEL_IDS = frozenset(CHANNEL_SPECS.keys())


def normalize_notification_channels(raw: Any) -> List[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    seen = set()
    result: List[str] = []
    for item in raw:
        channel = str(item or "").strip().lower()
        if channel in VALID_CHANNEL_IDS and channel not in seen:
            seen.add(channel)
            result.append(channel)
    return result


def channels_from_task_config(config: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(config, dict):
        return []
    return normalize_notification_channels(config.get(NOTIFICATION_CHANNELS_KEY))


def merge_notification_channels_into_config(
    config: Optional[Dict[str, Any]],
    channels: Optional[Sequence[str]],
) -> Dict[str, Any]:
    merged = dict(config or {})
    normalized = normalize_notification_channels(channels)
    if normalized:
        merged[NOTIFICATION_CHANNELS_KEY] = normalized
    else:
        merged.pop(NOTIFICATION_CHANNELS_KEY, None)
    return merged


def build_notification_delivery_supplement(channels: Iterable[str]) -> str:
    normalized = normalize_notification_channels(list(channels))
    if not normalized:
        return ""
    lines = [
        "【🚨 结果通知强制指令 (System Mandatory Command)】",
        "本次为 TaskCenter 自动化任务运行。任务数据分析或处理完成后，你【必须且必须】通过下发真正的 API 工具调用（Tool Call）将结果推送到以下指定渠道！",
        "",
        "⚠️ 极其重要的操作约束：",
        "1. 严禁仅在文本回答中口头说明“已发送”或仅输出文字总结！必须在系统里触发真正的工具函数调用 (Tool Call)。",
        "2. 必须按照以下指定渠道清单，触发对应的通知工具：",
    ]
    for channel in normalized:
        _tool, label, hint = CHANNEL_SPECS[channel]
        lines.append(f"   - {label}：{hint}")
    lines.append(
        "3. 必须在成功触发上述通知工具并收到工具返回结果后，再给出最终总结。\n"
        "4. 每个勾选渠道仅触发一次 API 调用，请勿漏发，也不要重复调用未列出的渠道。"
    )
    return "\n".join(lines)
