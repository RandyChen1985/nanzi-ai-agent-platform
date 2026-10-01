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


def test_badge_only_shows_for_enabled_channels():
    """徽标只在渠道已启用时出现，未启用时不必多一个「未启用」标签。"""
    for channel in CHANNELS:
        assert f'v-if="configs.{channel}.is_enabled"' in COMPONENT, channel


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
    for channel in CHANNELS:
        assert f"channelSaveDisabled('{channel}')" in COMPONENT, f"{channel} 保存按钮未接入校验"
    assert "请先填写 Webhook 地址后再保存。" in COMPONENT


def test_blocked_save_shows_a_visible_reason():
    """置灰必须附带可见原因——只把按钮变灰是另一种「界面说谎」。"""
    for channel in CHANNELS:
        marker = "emailSaveBlockedReason" if channel == "email" else f"channelSaveBlockedReason('{channel}')"
        # 每张卡片的提示行都要真的渲染这个原因，而不只是用在 disabled 上
        assert COMPONENT.count(marker) >= 2, (
            f"{channel} 卡片必须把置灰原因渲染到界面上（{marker} 只出现一次说明它只用在 disabled 上）"
        )


def test_email_save_block_only_for_custom_source():
    """全局来源即使平台未启用也允许保存：那是管理员侧的事，不该锁死用户。"""
    idx = COMPONENT.index("const emailSaveBlockedReason = computed")
    window = COMPONENT[idx : idx + 500]
    assert "=== 'custom'" in window and "customSmtpConfigured" in window, (
        "邮件的保存校验只应作用于自定义来源"
    )
