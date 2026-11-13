"""契约：通知渠道卡片的布局密度（可折叠 + 群卡片一行两个）。

诉求原文：「消息通道，不同的渠道，比如钉钉，飞书，企微，他们支持折叠」
以及「一行可以显示两个」。两者是同一个问题：多群之后页面纵向过长，
要滚很久才能看全每个渠道配了几个群、可不可用、哪条是默认。

这里钉住四件事：
1. 四张卡片（钉钉/企微/飞书/邮件）都能收起，默认收起；
2. 收起的是内容区，且必须用 ``v-show`` —— 用 ``v-if`` 会销毁子组件，
   折叠一下就把用户没保存的编辑丢掉；
3. 折叠状态记进 localStorage，用户常看的那张不用每次重新点开；
4. 收起后「有改动未保存」仍然看得见，否则会被彻底忘掉。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIGS = (ROOT / "frontend/src/components/personal/NotificationConfigs.vue").read_text(
    encoding="utf-8"
)
GROUPS = (
    ROOT / "frontend/src/components/personal/NotificationChannelGroups.vue"
).read_text(encoding="utf-8")


def test_all_four_channel_cards_are_collapsible():
    for channel in ("dingtalk", "wechat_work", "feishu", "email"):
        assert f"toggleChannelCard('{channel}')" in CONFIGS, channel
        assert f"isCardExpanded('{channel}')" in CONFIGS, channel


def test_cards_default_to_collapsed():
    """默认收起，进页面先看四个渠道的状态行；展开是有意动作。"""
    assert "expandedCards.value[channel] === true" in CONFIGS


def test_collapse_uses_v_show_not_v_if():
    """折叠必须用 v-show：v-if 会销毁子组件，连同未保存的编辑一起丢掉。"""
    assert 'v-show="isCardExpanded(' in CONFIGS
    # 内容区里不应出现「折叠导致的 v-if 卸载」
    assert "v-if=\"isCardExpanded(" not in CONFIGS


def test_expanded_state_is_remembered():
    assert "notification-channel-cards-expanded" in CONFIGS
    assert "localStorage.setItem" in CONFIGS


def test_unsaved_marker_visible_when_collapsed():
    """折叠后头部是唯一的提醒位。"""
    assert "channelDirty" in CONFIGS
    assert "未保存" in CONFIGS
    # 子组件要把 dirty 上报给父组件，否则头部无从得知
    assert "emit('dirty'" in GROUPS


def test_group_cards_flow_two_per_row():
    """群卡片横向排两列。

    群卡片是「并排对比」的内容（谁默认、谁停用、webhook 填没填），
    纵向堆叠会让每个渠道白白多出半屏滚动。
    """
    assert "lg:grid-cols-2" in GROUPS
    # 窄屏必须还能退回单列，否则手机上群卡片会被压成一半宽
    assert "grid-cols-1" in GROUPS


def test_header_shows_enabled_slash_total():
    """卡片头部要显示「启用数/总数」，不能只显示总数。

    只写「2 个群」时，「两个群都配好了」和「两个群里有一个停用了」长得一模一样，
    而后者正是消息发不出去、用户又看不出问题的状态。
    """
    for channel in ("dingtalk", "wechat_work", "feishu"):
        assert "groupCounts.%s.enabled }}/{{ groupCounts.%s.total }} 个群" % (
            channel,
            channel,
        ) in CONFIGS, channel
        # 徽标显隐仍按总数判断：0 个群时不该出现「已启用·可用」的徽标
        assert 'v-if="groupCounts.%s.total > 0"' % channel in CONFIGS


def test_counts_update_live_not_only_on_load():
    """改开关/删群也要立刻反映，只在加载时上报一次会显示错数字。"""
    assert "deep: true, immediate: true" in GROUPS
    assert "emit('changed'" in GROUPS
