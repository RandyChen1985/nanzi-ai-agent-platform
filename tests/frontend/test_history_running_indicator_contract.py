"""契约：历史列表项上的「进行中 / 疑似僵死」标识。

用户诉求（原文）：「面板开着，正在进行中的会话，历史的这个列表上会有转圈进行中的效果吗」
——此前没有，列表项只有「上一轮成功/失败」的静态绿红点。

两个不能含糊的点：
① 疑似僵死（lane 锁已停止续约）**不能**和真正在跑的一样转圈，否则会让人以为一个已经死掉
   的会话还活着——那比不显示更糟；
② 当前会话的「进行中」不能只依赖轮询（浮层关闭时 15s 一次），否则用户自己刚发起的这一轮
   要等半分钟才在列表上亮起来。所以本地运行态必须并进去。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

SIDEBAR = ROOT / "frontend/src/components/ChatHistorySidebar.vue"
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"


def _sidebar() -> str:
    return SIDEBAR.read_text(encoding="utf-8")


def _embed() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def test_sidebar_accepts_running_and_stale_conversation_ids():
    source = _sidebar()
    assert "runningConversationIds?: string[];" in source
    assert "staleConversationIds?: string[];" in source
    # 默认值必须给，否则父组件不传时 `.includes` 会炸。
    assert "runningConversationIds: () => []," in source
    assert "staleConversationIds: () => []," in source


def test_running_item_gets_a_spinner_but_stale_one_does_not():
    source = _sidebar()
    # 三分支：运行中 → 转圈；疑似僵死 → 琥珀静态点；否则沿用成功/失败色。
    assert re.search(
        r'v-if="isItemRunning\(item\) && !isItemStale\(item\)"[\s\S]{0,200}?animate-spin',
        source,
    ), "运行中的列表项没有转圈标识"
    assert re.search(
        r'v-else-if="isItemStale\(item\)"[\s\S]{0,200}?bg-amber-500',
        source,
    ), "疑似僵死没有独立的琥珀标识"
    # 僵死分支绝不能带转圈动画。
    stale_branch = source[source.index('v-else-if="isItemStale(item)"') :]
    stale_branch = stale_branch[: stale_branch.index("</span>")]
    assert "animate-spin" not in stale_branch, "疑似僵死也在转圈，会误导为仍在运行"


def test_running_item_gets_a_text_label_beside_the_active_tag():
    source = _sidebar()
    # 三元顺序是「是僵死吗」在前：僵死 → 疑似僵死，否则 → 进行中。
    assert "{{ isItemStale(item) ? '疑似僵死' : '进行中' }}" in source, "缺少「进行中」文字标签"
    # 标签要和「当前会话」并排，不能互相顶掉。
    assert re.search(
        r"<div class=\"flex items-center gap-1\">[\s\S]{0,600}?进行中[\s\S]{0,600}?当前会话",
        source,
    ), "「进行中」与「当前会话」标签没有并排渲染"


def test_embed_chat_passes_the_ids_from_the_active_runs_poll():
    source = _embed()
    assert ':running-conversation-ids="runningConversationIds"' in source
    assert ':stale-conversation-ids="staleRunConversationIds"' in source
    assert 'item.state === "active"' in source
    assert 'item.state === "stale"' in source


def test_current_conversation_is_marked_running_without_waiting_for_the_poll():
    """本地运行态要并进「进行中」，否则自己刚发起的一轮要等下一轮轮询才亮。"""
    source = _embed()
    start = source.index("const runningConversationIds = computed(")
    body = source[start : source.index("\n});", start)]
    assert "if (conversationId.value && remoteRunActive.value) ids.add(conversationId.value);" in body


def test_mobile_reuses_the_subtitle_entry_instead_of_a_separate_button():
    """移动端不再单独占一个 44px 按钮。

    原设计把入口放在「更多」之前，但它和「更多」都是 44px 热区，16px 图标被两圈 padding
    撑出约 36px 视觉空白，看起来「离得远」。现在并入副标题行，与桌面共用同一个入口。
    """
    source = _embed()
    ref = source.index('ref="activeRunsRef"')
    entry_tag = source[source.rindex("<div", 0, ref) : source.index(">", ref)]
    assert "!isMobile" not in entry_tag, "副标题行入口仍在排除移动端"
    hits = source.count('@click="toggleActiveRuns"')
    assert hits == 1, f"除副标题行外还有第二处触发入口（{hits} 处）"


def test_mobile_entry_reuses_the_same_open_state_and_modal_popover():
    """复用同一个 showActiveRuns：否则点图标与点菜单项会各开一层浮层。"""
    source = _embed()
    start = source.index('ref="activeRunsRef"')
    assert "showActiveRuns" in source[start : start + 2000]
    assert re.search(r'v-if="isMobile"[\s\S]{0,600}?variant="modal"', source), "移动端浮层不是 modal 变体"
    assert re.search(r'v-if="isMobile"[\s\S]{0,400}?:open="showActiveRuns"', source), "移动端浮层没有接同一个 open 状态"


def test_mobile_entry_keeps_a_count_badge():
    """角标随入口一起并进副标题行，数量提示不能丢。"""
    source = _embed()
    start = source.index('ref="activeRunsRef"')
    block = source[start : source.index("</button>", start)]  # 角标在按钮内
    assert 'v-if="activeRunsCount"' in block
    assert "activeRunsStaleCount ? 'bg-amber-500' : 'bg-primary'" in block
