"""契约：个人中心「消息通知」开关必须说真话。

背景（用户实测反馈）：把开关打开后，即使下面什么都没填、或保存其实失败了，
界面也一律显示成「已启用」，让人以为配置已经完成——转头去任务里却勾不上该渠道。

这里钉住三件事：
1. 开关只表达「启用意图」，可用性另用徽标表达（取自后端就绪接口，与发信同源）；
2. 开关切换保存失败必须把开关拨回去，不能让界面与库里的状态脱节；
3. 必填缺失时置灰「保存配置」，并且**可见地**给出原因，而不是把按钮静静置灰。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = (ROOT / "frontend/src/components/personal/NotificationConfigs.vue").read_text(
    encoding="utf-8"
)
CHANNELS = ("dingtalk", "wechat_work", "feishu", "email")
# 三个群渠道的表单已下沉到子组件：多群之后「发到哪个群」的逻辑完全共用，
# 表单没必要在父组件里重复三遍。邮件不是群语义，仍留在父组件。
GROUP_CHANNELS = ("dingtalk", "wechat_work", "feishu")
CHANNEL_GROUPS = (ROOT / "frontend/src/components/personal/NotificationChannelGroups.vue").read_text(
    encoding="utf-8"
)


def test_availability_comes_from_backend_readiness():
    """可用性必须取自 /notifications/readiness，不得由开关状态倒推。"""
    assert "axios.get('/api/portal/notifications/readiness')" in COMPONENT
    assert "channelReadiness" in COMPONENT


def test_unknown_availability_is_not_rendered_as_unavailable():
    """读不到就绪状态时按「未知」处理：中性徽标，不断言不可用。"""
    catch_idx = COMPONENT.index("Failed to load notification channel readiness")
    window = COMPONENT[catch_idx : catch_idx + 200]
    assert "channelReadiness.value = {}" in window, "请求失败必须清空而不是伪造不可用"


def test_badge_distinguishes_enabled_from_usable():
    for label in ("已启用 · 可用", "已启用 · 当前不可用"):
        assert label in COMPONENT, f"缺少状态文案：{label}"
    for channel in CHANNELS:
        assert f"channelBadge('{channel}')" in COMPONENT, f"{channel} 卡片缺少状态徽标"


def test_badge_only_shows_for_configured_channels():
    """徽标只在渠道有配置时出现，没配置时不必多一个「未配置」标签。

    多群之后「渠道是否启用」不再是一个开关，而是「有没有群 / 群是否启用」，
    所以群渠道改看群数量；邮件仍是单套配置，继续看 is_enabled。
    """
    for channel in GROUP_CHANNELS:
        # 计数从「一个数字」变成 {enabled, total}（头部要显示 x/y），
        # 显隐判断跟着走 total：0 个群时不该冒出「已启用·可用」的徽标
        assert f'v-if="groupCounts.{channel}.total > 0"' in COMPONENT, channel
    assert 'v-if="configs.email.is_enabled"' in COMPONENT


def test_toggle_failure_rolls_the_switch_back():
    """保存失败必须回滚开关：否则界面显示已开启、库里没存上。"""
    idx = COMPONENT.index("const onToggleChannel = async")
    window = COMPONENT[idx : idx + 700]
    assert "if (!ok)" in window, "必须判断保存结果"
    assert "is_enabled = !next" in window, "保存失败必须把开关拨回原状态"


def test_save_config_reports_success():
    """saveConfig 必须把成功/失败作为返回值交给调用方判断。"""
    idx = COMPONENT.index("const saveConfig = async")
    window = COMPONENT[idx : idx + 900]
    assert "Promise<boolean>" in window
    assert "return true" in window and "return false" in window


def test_save_button_is_gated_by_required_fields():
    for channel in GROUP_CHANNELS:
        assert 'channel-type="' + channel + '"' in COMPONENT, f"{channel} 未接入多群组件"
    # 群渠道的必填校验在子组件里：缺 Webhook 就不该允许保存
    assert "saveBlockedReason" in CHANNEL_GROUPS
    # 断言「缺 Webhook」这个原因本身，不绑定具体句式（原句「还没有填写 Webhook
    # 地址后再保存」有语病，已改成「请先为「X」填写 Webhook 地址再保存」）
    assert "填写 Webhook 地址再保存" in CHANNEL_GROUPS
    assert ':disabled="saving || !dirty || !!saveBlockedReason"' in CHANNEL_GROUPS
    # 邮件仍走父组件的单套配置校验
    assert "channelSaveDisabled('email')" in COMPONENT


def test_blocked_save_shows_a_visible_reason():
    """置灰必须附带可见原因——只把按钮变灰是另一种「界面说谎」。"""
    # 群渠道：原因既进 title 也渲染成一行文字，两者都出现才算「可见」
    assert CHANNEL_GROUPS.count("saveBlockedReason") >= 2, (
        "多群组件必须把置灰原因渲染到界面上，而不只是用在 disabled 上"
    )
    assert 'v-if="saveBlockedReason"' in CHANNEL_GROUPS
    # 邮件：仍由父组件渲染
    assert COMPONENT.count("emailSaveBlockedReason") >= 2


def test_email_save_block_only_for_custom_source():
    """全局来源即使平台未启用也允许保存：那是管理员侧的事，不该锁死用户。"""
    idx = COMPONENT.index("const emailSaveBlockedReason = computed")
    window = COMPONENT[idx : idx + 500]
    assert "=== 'custom'" in window and "customSmtpConfigured" in window, (
        "邮件的保存校验只应作用于自定义来源"
    )


def test_group_list_reads_is_enabled_and_tolerates_legacy_key():
    """「读不到启用状态」绝不能等同于「已停用」。

    后端曾经在群列表响应里返回 ``enabled``，而前端读 ``is_enabled``：结果是整列
    显示成「已停用」，用户点一次保存就把停用真写回库里 —— 一次保存废掉全部群，
    而且没有任何报错。现在两边统一为 ``is_enabled``，并保留对旧键名的兜底读取，
    覆盖前后端新旧版本同时存在的部署窗口期。
    """
    assert "item.is_enabled" in CHANNEL_GROUPS
    assert ".enabled" in CHANNEL_GROUPS, "必须保留旧键名兜底，否则部署窗口期会误判为停用"
