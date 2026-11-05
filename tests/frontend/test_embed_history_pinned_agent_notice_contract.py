from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
SIDEBAR = ROOT / "frontend/src/components/ChatHistorySidebar.vue"

pytestmark = pytest.mark.no_infrastructure


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_sidebar_renders_pinned_agent_notice_above_search():
    """锁定智能体后必须明说历史被限定了，否则用户会以为历史丢了。"""
    source = _source(SIDEBAR)
    body = source.split("<!-- 智能体锁定提示", 1)[1].split("<!-- Search Bar", 1)[0]

    assert "pinnedAgentName?: string;" in source
    assert 'v-if="pinnedAgentName"' in body
    assert "已锁定智能体" in body
    assert "仅显示该智能体的历史会话" in body
    # 提示条在搜索框之前
    assert source.index("已锁定智能体") < source.index("搜索历史记录...")


def test_sidebar_hides_agent_filter_and_agent_chip_when_pinned():
    """锁定后不允许再改智能体筛选：筛选面板里不出现该下拉，chip 也不给。

    筛选入口已从侧栏内联下拉搬到 ``ConversationFilterPanel``（所以旧的
    ``showAgentFilter`` prop 连同两处调用一起删了）；这里断言的是**行为**——
    锁定时面板用 ``hide-agent`` 关掉智能体选项，chips 也不渲染这一项。
    """
    source = _source(SIDEBAR)

    # 面板里关掉智能体选项：锁定智能体（或调用方整体关掉筛选）时都不给改
    assert ':hide-agent="Boolean(pinnedAgentName) || !showAgentFilter"' in source
    assert "if (f.agentId && !props.pinnedAgentName) {" in source


def test_embedchat_passes_pinned_agent_name_and_locks_filter():
    source = _source(EMBED_CHAT)

    assert ':pinned-agent-name="historyPinnedAgentName"' in source
    assert "const historyPinnedAgentName = computed(" in source
    assert "if (!pinnedAgentScope()) return \"\";" in source
    assert "const showAgentFilter = computed(() => !pinnedAgentScope());" in source
