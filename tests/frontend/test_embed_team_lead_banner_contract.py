"""EmbedChat：选中的智能体是团主专家时，输入框上方要有可关闭的团队说明条。

为什么需要：团队身份在页头只有一个 👥 TEAM 小徽标，进对话后很容易忘。用户以为自己
在跟单个专家聊天，看到「已转元数据专家」会困惑。所以补一条常驻说明，并且：

- 关闭状态按**智能体**记（换一个团要重新提示），不是按浏览器会话记；
- 只在 web 端显示：移动端输入区本就窄，横幅会把消息区挤掉；
- `team_member_count` 的 `null`（未配置调度范围）与 `0`（显式清空）文案必须分开，
  混成一个数会把「谁都能调」说成「一个成员都没有」。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
BANNER = ROOT / "frontend/src/components/chat/TeamLeadBanner.vue"
EMBED = ROOT / "frontend/src/views/EmbedChat.vue"


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


def test_banner_component_exists_with_team_identity():
    banner = _read(BANNER)
    # 标题 = 团名 + 团队模式；团名缺失时兜底成「专家团」
    assert "· 团队模式" in banner
    assert "'专家团'" in banner
    # 说明里要交代「谁在干活」和「怎么指定某位」
    assert "主专家会按分工调度" in banner
    assert "在输入框里 @ 他" in banner


def test_member_count_null_and_zero_are_not_the_same_copy():
    """null = 范围未限定，0 = 明确没有子专家——这两句话不能共用一个分支。"""
    banner = _read(BANNER)
    assert "调度范围还没限定，可从全部可委派专家里选" in banner
    assert "这个团还没有子专家，主专家会自己处理全部问题" in banner
    # 不许把未知成员数渲染成「0 位」
    assert "0 位" not in banner


def test_embed_mounts_banner_and_remembers_dismiss_per_agent():
    embed = _read(EMBED)
    assert 'import TeamLeadBanner from "@/components/chat/TeamLeadBanner.vue"' in embed
    assert "showTeamLeadBanner" in embed
    # 数据来源：允许列表里的智能体对象，团身份挂在智能体上
    assert "is_expert_team_lead === true" in embed
    assert "team_name" in embed
    assert "team_member_count" in embed
    # 按智能体记关闭状态
    assert "teamLeadBannerDismissedAgentId" in embed
    assert "@dismiss=\"teamLeadBannerDismissedAgentId = teamLeadAgentId\"" in embed


def test_banner_is_desktop_only():
    """移动端不显示：输入区本来就窄，横幅会把消息区挤掉。"""
    embed = _read(EMBED)
    assert 'v-if="showTeamLeadBanner && !isMobile"' in embed


def test_banner_sits_above_composer_with_scope_banner():
    """挂在 ChatInput 的 #banner 插槽里（与知识库范围条、沙箱提示同一位置）。"""
    embed = _read(EMBED)
    slot = embed.split("<template #banner>", 1)[1]
    banner_zone = slot.split("<ChatInput", 1)[0] if "<ChatInput" in slot else slot
    assert "TeamLeadBanner" in banner_zone
    # 与现有提示条同级：外层用 mx-3 mt-2，保持一致的上下间距
    assert 'class="mx-3 mt-2"' in embed
