"""EmbedChat「隐藏聊天头像」开关的契约测试。

背景：头像本身 32px（``w-8``），与气泡之间还有 ``space-x-2`` 的 8px，左右各占 40px。
把它藏掉之后这 80px 应当还给消息内容——但**不能只加一个 v-if**：用户消息的操作行有
``pr-10``、AI 名字胶囊有 ``ml-1``，这两处都是为了跟头像边缘对齐才存在的，头像一消失
它们就变成莫名其妙的缩进。气泡的 ``max-w-[85%]/[90%]`` 也要同步放开，否则只是"少了头像、宽度没变"。

这组用例盯的就是这件事：藏头像、收间距、放宽度三步必须成套出现。
"""

from __future__ import annotations

from pathlib import Path

import pytest

CHAT = Path("frontend/src/views/EmbedChat.vue")
SETTINGS = Path("frontend/src/components/embed/ChatSettings.vue")

pytestmark = pytest.mark.no_infrastructure


def _chat() -> str:
    return CHAT.read_text(encoding="utf-8")


def _settings() -> str:
    return SETTINGS.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 设置面板


def test_settings_panel_exposes_the_switch():
    settings = _settings()

    assert "隐藏聊天头像" in settings
    assert 'Switch :modelValue="!!config.hideChatAvatar"' in settings
    assert '@update:modelValue="handleSetHideChatAvatar"' in settings
    # 说明文案要写清收益，否则用户不知道这个开关省的是什么
    assert "40px" in settings


def test_switch_sits_above_message_border():
    """按产品要求放在「隐藏 AI 消息外框」上方：两者都是"砍占位、把空间还给内容"。"""

    settings = _settings()
    # 用 ``>文案<`` 定位到 h5 标题本身：新开关上方的注释里也提到了「隐藏 AI 消息外框」，
    # 直接 index(文案) 会命中那条注释，把顺序判断弄反。
    avatar_pos = settings.index(">隐藏聊天头像<")
    border_pos = settings.index(">隐藏 AI 消息外框<")

    assert avatar_pos < border_pos


def test_toggle_writes_its_own_local_storage_key():
    settings = _settings()

    assert 'localStorage.setItem("yovole_hide_chat_avatar"' in settings
    # 不能复用 hideMessageBorder 的 key，否则两个开关会互相覆盖
    assert 'localStorage.setItem("user_has_custom_border_preference"' not in settings[
        settings.index("handleSetHideChatAvatar") : settings.index("handleSetMessageBorder")
    ]


# ---------------------------------------------------------------- 配置持久化


def test_config_has_an_explicit_default():
    chat = _chat()

    assert "hideChatAvatar: false," in chat
    # 默认必须是"显示头像"：老用户没动过开关，界面不该突然变样
    assert "hideChatAvatar: true" not in chat


def test_state_round_trips_through_local_storage():
    chat = _chat()

    # 读写走按实例分桶的 helper：`instance_id` 不同的挂件互不覆盖
    assert 'writeConfigValue("yovole_hide_chat_avatar", config.hideChatAvatar ? "1" : "0")' in chat
    assert 'readConfigValue("yovole_hide_chat_avatar")' in chat
    assert 'config.hideChatAvatar = savedHideChatAvatar === "1"' in chat


# ---------------------------------------------------------------- 渲染


def test_both_avatars_are_conditionally_rendered():
    chat = _chat()

    # 用户头像 + AI 头像，各一处
    assert chat.count('v-if="!config.hideChatAvatar"') == 2


def test_spacers_collapse_with_the_avatars():
    """三处为对齐头像而存在的间距，必须跟着头像一起收掉。"""

    chat = _chat()

    # 1) 两个消息行的 space-x-2
    assert chat.count("{ 'space-x-2': !config.hideChatAvatar }") == 2
    # 2) 用户消息操作行的 pr-10（40px = 头像宽度）
    assert "config.hideChatAvatar ? 'pr-0' : 'pr-10'" in chat
    # 3) AI 名字胶囊的 ml-1（4px，与头像左边缘对齐）
    assert "config.hideChatAvatar ? 'ml-0' : 'ml-1'" in chat


def test_bubbles_widen_when_avatars_are_hidden():
    chat = _chat()

    # 用户气泡：85% -> 94%
    assert "config.hideChatAvatar ? 'max-w-[94%]' : 'max-w-[85%]'" in chat
    # AI 气泡列：90% -> 满宽
    assert "config.hideChatAvatar ? 'max-w-full' : 'max-w-[90%]'" in chat


def test_edit_mode_width_tracks_the_bubble():
    """点开编辑时宽度不能跳一下。

    正常气泡默认 85%、编辑态默认 90%（编辑框本来就该宽一点，这是原有设计）；
    藏头像后气泡变 94%，编辑态必须跟着到 94%，否则一进编辑就缩一格。
    """

    chat = _chat()

    assert "config.hideChatAvatar ? 'max-w-[94%]' : 'max-w-[90%]'" in chat


def test_hidden_mode_does_not_reuse_the_border_switch():
    """两个开关互不牵连：只藏头像时消息外框该怎样还怎样。"""

    chat = _chat()

    assert "config.hideChatAvatar" in chat
    assert "'message-borderless': config.hideMessageBorder" in chat
    # 头像隐藏不应顺手改消息外框的判定
    assert "hideMessageBorder: config.hideChatAvatar" not in chat
