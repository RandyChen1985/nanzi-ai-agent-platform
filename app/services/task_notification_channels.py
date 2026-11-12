"""TaskCenter 结果通知渠道：与 UI / create_recurring_task / 调度提示词补充同源。"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

NOTIFICATION_CHANNELS_KEY = "notification_channels"
# 「渠道 -> 群 ID」映射，键与 notification_channels 对应。
# 存成独立键而不是把 notification_channels 改成对象列表：存量任务的
# notification_channels 全是 List[str]，改结构会让所有老任务读不出渠道。
NOTIFICATION_CHANNEL_GROUPS_KEY = "notification_channel_groups"

# 能指定群的渠道。站内消息没有「群」的概念，邮件发的是收件人列表而非群，
# 两者都不参与群选择。
GROUP_CAPABLE_CHANNELS = frozenset({"dingtalk", "feishu", "wechat_work"})

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
    "feishu": (
        "send_feishu_message",
        "飞书",
        "调用 send_feishu_message(title, content)；凭据来自个人中心→消息通知，勿再索要 webhook",
    ),
    "email": (
        "send_email",
        "邮件",
        "调用 send_email(to_email, subject, content)；SMTP 与收件人来自个人中心→消息通知，勿再索要服务器配置。用户未指定收件人时 to_email 留空即可发给其配置的收件人，不要自行编造地址；定时通知不带附件，**不要传 attachments**",
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


def normalize_channel_groups(raw: Any) -> Dict[str, int]:
    """归一「渠道 -> 群 ID」映射。

    只留下能指定群的渠道与正整数 ID。任务配置是用户可改的 JSON，
    脏值（字符串、0、负数、别的渠道）一律丢弃，不做猜测性修正。
    """
    if not isinstance(raw, dict):
        return {}
    result: Dict[str, int] = {}
    for key, value in raw.items():
        channel = str(key or "").strip().lower()
        if channel not in GROUP_CAPABLE_CHANNELS:
            continue
        if isinstance(value, bool) or value is None:
            continue
        try:
            group_id = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if group_id > 0:
            result[channel] = group_id
    return result


def channel_groups_from_task_config(config: Optional[Dict[str, Any]]) -> Dict[str, int]:
    if not isinstance(config, dict):
        return {}
    return normalize_channel_groups(config.get(NOTIFICATION_CHANNEL_GROUPS_KEY))


def merge_notification_channel_groups_into_config(
    config: Optional[Dict[str, Any]],
    groups: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    merged = dict(config or {})
    normalized = normalize_channel_groups(groups)
    if normalized:
        merged[NOTIFICATION_CHANNEL_GROUPS_KEY] = normalized
    else:
        merged.pop(NOTIFICATION_CHANNEL_GROUPS_KEY, None)
    return merged


def build_notification_delivery_supplement(
    channels: Iterable[str],
    channel_groups: Optional[Dict[str, int]] = None,
) -> str:
    normalized = normalize_notification_channels(list(channels))
    if not normalized:
        return ""
    labels = [CHANNEL_SPECS[channel][1] for channel in normalized]
    channel_list = "、".join(labels)
    groups = normalize_channel_groups(channel_groups)
    target_line = "；".join(
        f"{CHANNEL_SPECS[channel][1]}群 id={groups[channel]}"
        for channel in normalized
        if channel in groups
    )
    lines = [
        "【结果通知说明】",
            f"任务结束后将由 TaskCenter 统一投递结果到已勾选渠道（{channel_list}）。",
            "你无需、也不应调用 send_portal_notification / send_dingtalk_message / send_wechat_work_message / send_feishu_message / send_email 等通知工具。",
            "请把完整分析结论和关键数据解读都写在最终回复正文中；系统只会投递该最终正文。",
            "最终可见正文只能是交付给用户的业务结论；禁止夹带中间思考、执行规划或自我独白"
            "（如 “I'll / Let me / 我先 / 让我…”），否则可能随通知正文发送给用户。",
            "严禁在分析未完成、仅输出“查询成功/让我再补充…”等半截话术时结束本轮。",
            "禁止在回复正文中提及本说明、TaskCenter、通知工具名或“将由系统统一投递”等元话术；正文只写给终端用户看的业务结论。",
    ]
    if target_line:
        # 调度侧会按这条投递；万一本轮仍调用了通知工具，也必须发到同一个群，
        # 否则同一个任务会因为「谁发的」不同而落到不同的群。
        lines.append(f"本任务指定的发送目标是：{target_line}；投递由系统完成，无需你指定群。")
    return "\n".join(lines)
