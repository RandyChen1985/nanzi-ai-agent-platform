"""契约：定时任务可以指定「发到哪个群」。

背景：多群改造之后，个人中心里一个渠道可以有多个群。任务若只能发默认群，
「运维告警任务」和「日报任务」就会被塞进同一个群。

这里钉住四件事：
1. 任务能按渠道单独指定群，且不指定时明确表示「跟随个人中心的默认群」；
2. 只有钉钉 / 企微 / 飞书三个有「群」概念的渠道出现选择器（邮件、站内信没有群）；
3. 群选择随任务配置一起存取（notification_channel_groups），老任务没有这个键也不能崩；
4. 选了具体群时，界面上要说清「群失效会改发默认群」，而不是让用户以为一定发原群。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TASK_CENTER = (ROOT / "frontend/src/views/TaskCenter.vue").read_text(encoding="utf-8")
CHANNELS_MODULE = (ROOT / "app/services/task_notification_channels.py").read_text(
    encoding="utf-8"
)
DELIVERY = (ROOT / "app/services/task_notification_delivery.py").read_text(encoding="utf-8")


def test_task_center_collects_group_selection_into_task_config():
    assert "notificationChannelGroups" in TASK_CENTER
    assert "baseConfig.notification_channel_groups = groupPayload" in TASK_CENTER
    assert "cfg.notification_channel_groups" in TASK_CENTER


def test_only_group_capable_channels_get_a_selector():
    assert "const GROUP_CAPABLE_CHANNELS = ['dingtalk', 'wechat_work', 'feishu'] as const" in TASK_CENTER
    # 邮件发的是收件人列表、站内消息没有群，两者都不该出现群选择器
    assert "selectedGroupChannels" in TASK_CENTER
    assert "'email'" not in TASK_CENTER[
        TASK_CENTER.index("const GROUP_CAPABLE_CHANNELS"):TASK_CENTER.index("const GROUP_CAPABLE_CHANNELS") + 120
    ]


def test_default_group_option_is_explicit():
    """「不选」必须被写成「跟随个人中心的默认群」，不能是一个含义不明的空选项。"""
    assert "默认群（跟随个人中心设置）" in TASK_CENTER


def test_stale_group_is_visible_in_dropdown():
    """任务里存的群被删掉后，下拉里仍要显示出来并说明会改发默认群。"""
    assert "已失效，将改发默认群" in TASK_CENTER


def test_stale_group_falls_back_instead_of_dropping_the_message():
    """定时任务无人值守：群失效要降级发默认群，并留下可查的原因。"""
    assert "改发默认群" in DELIVERY
    assert "resolve_channel" in DELIVERY


def test_backend_key_does_not_break_legacy_list_channels():
    """存量任务的 notification_channels 是 List[str]，新键必须是独立的，不能改结构。"""
    assert 'NOTIFICATION_CHANNEL_GROUPS_KEY = "notification_channel_groups"' in CHANNELS_MODULE
    assert 'NOTIFICATION_CHANNELS_KEY = "notification_channels"' in CHANNELS_MODULE
