"""契约：未落库的当前会话在历史侧栏里先露一条临时条目。

用户问的现象（原件）：「新会话我发了消息，是不是要等第一条 AI 回复完才能在历史里面看到？」

确实如此，而且不是数据丢了：侧栏读的是 `/api/v1/chat/history`，后端查
`ai_agent_execution_history`，**一行一个轮次、只在轮次收尾的 FinalizeStep 落库**；
用户消息本身在 ContextStep 就写进了 Redis 会话记忆，所以界面上看得见、历史列表里看不见。
方案 A：前端把「当前会话」当成一条 `is_pending` 条目临时插进侧栏，落库后由接口数据接管。

四个不能含糊的点：

1. 只在「历史已加载 + 默认筛选 + 无关键词 + 该 cid 还不存在」时插入——否则搜索时
   凭空冒出一条会让人以为它命中了关键词；
2. 临时条目点击必须短路：不能走会话归属校验（新会话在 DB 里没有行，校验结果不可靠，
   会弹「归属暂时无法确认」）；它本来就是当前会话，滚到底即可；
3. 临时条目没有 trace_id，置顶 / 导出 / 分享 / 回溯日志 / 删除这些操作全都无从谈起，
   必须隐藏，不能点了没反应或删出一片空白；
4. 状态点不能沿用「非 success 就是红点」的兜底，那会把一条正在生成的会话画成失败。
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
SIDEBAR = ROOT / "frontend/src/components/ChatHistorySidebar.vue"
GROUPS = ROOT / "frontend/src/composables/chat/useChatHistoryGroups.ts"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _block(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    return source[start : source.index(end_marker, start)]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行临时条目行为测试")
def test_pending_item_behaviour_via_node():
    script = Path("tests/frontend/pending_history_item.mjs")
    assert script.exists(), "行为测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "未落库会话临时历史条目测试全部通过" in proc.stdout


def test_pending_item_helpers_live_in_the_shared_history_groups_module():
    source = _source(GROUPS)
    assert "export const resolvePendingHistoryItem" in source
    assert "export const mergePendingHistoryItem" in source


def test_embed_chat_builds_the_pending_item_from_live_chat_state():
    source = _source(EMBED_CHAT)
    block = _block(source, "const pendingHistoryItem = computed(", "\n});")

    assert "resolvePendingHistoryItem({" in block
    assert "conversationId: conversationId.value" in block
    assert "history: historyList.value" in block
    # 历史必须已经成功拉过一次：否则首屏先闪一条假的，再被接口数据替换
    assert "historyLoaded: historyLoadedOnce.value" in block
    assert "keyword: historyKeyword.value" in block
    assert "isDefaultFilters: isDefaultHistoryFilters(historyFilters.value)" in block


def test_history_loaded_flag_is_set_only_after_a_successful_fetch():
    source = _source(EMBED_CHAT)
    assert "const historyLoadedOnce = ref(false);" in source
    body = _block(source, "const fetchHistory = async", "\n};")
    assignment = body.index("historyLoadedOnce.value = true;")
    # 必须落在 try 内的响应处理之后，失败路径不能把它置真
    assert assignment > body.index("const res = await axios.get(\"/api/v1/chat/history\"")


def test_grouped_history_is_built_from_the_merged_list():
    source = _source(EMBED_CHAT)
    merged = _block(source, "const historyListWithPending = computed(", "\n});")
    assert "mergePendingHistoryItem(aggregatedHistoryList.value, pendingHistoryItem.value)" in merged

    grouped = _block(source, "const groupedHistoryList = computed(", "\n});")
    assert "historyListWithPending.value" in grouped, "分组仍直接吃未合并的列表，临时条目不会出现"


def test_clicking_the_pending_item_skips_ownership_check_and_just_scrolls():
    source = _source(EMBED_CHAT)
    body = _block(source, "const handleHistoryClick = async", "\n};")
    short_circuit = body.index("item?.is_pending")
    assert short_circuit < body.index("checkConversationAdoption(target"), (
        "临时条目的短路必须在归属校验之前，否则会弹出「会话归属暂时无法确认」"
    )
    head = body[: body.index("checkConversationAdoption(target")]
    assert "scrollToBottom();" in head
    assert "showHistorySidebar.value = false;" in head, "移动端点完要收侧栏"


def test_sidebar_hides_unusable_actions_on_the_pending_item():
    source = _source(SIDEBAR)

    pin = _block(source, "<!-- Pin / Unpin", "<!-- Export Chat")
    assert 'v-if="!item.is_pending"' in pin, "临时条目的置顶按钮必须隐藏"

    export_block = _block(source, "<!-- Export Chat", "<!-- Share Chat")
    assert 'v-if="!item.is_pending"' in export_block

    share = _block(source, "<!-- Share Chat", "<!-- Open Logs Trace")
    assert "!item.is_pending" in share

    delete_block = _block(source, "<!-- Delete Single", "<!-- Query / Title")
    assert 'v-if="!item.is_pending"' in delete_block


def test_sidebar_pending_item_keeps_a_neutral_status_dot():
    source = _source(SIDEBAR)
    footer = _block(source, "<!-- 运行中转圈 / 疑似僵死琥珀静态点 / 否则沿用上一轮成功失败色 -->", "<!-- Inline Delete Confirmation Overlay -->")

    assert 'v-else-if="item.is_pending"' in footer, "临时条目没有独立的状态点分支，会掉进红点兜底"
    pending_branch = footer[footer.index('v-else-if="item.is_pending"') :]
    pending_branch = pending_branch[: pending_branch.index("</span>")]
    assert "bg-rose-500" not in pending_branch, "临时条目被画成失败红点"
    assert "bg-slate-400" in pending_branch or "bg-gray-400" in pending_branch


def test_sidebar_pending_item_shows_a_saving_label():
    source = _source(SIDEBAR)
    footer = _block(source, "<!-- 运行中转圈 / 疑似僵死琥珀静态点 / 否则沿用上一轮成功失败色 -->", "<!-- Inline Delete Confirmation Overlay -->")
    assert "待保存" in footer, "临时条目缺少「待保存」标签，用户不知道它还没进历史"

    # 标签必须挂在「进行中」之后的分支：正在跑时已经有「进行中」，两个标签会互相顶掉。
    match = re.search(r"<span[^>]*>\s*待保存\s*</span>", footer, re.S)
    assert match, "「待保存」不是一个独立的标签块"
    label_tag = match.group(0)
    assert "item.is_pending" in label_tag
    assert "v-else-if" in label_tag, "「待保存」没有排在「进行中」分支之后"


def test_bulk_group_delete_ignores_the_pending_item():
    """「清空此组」不能把临时条目算进删除名单。

    它在 DB 里没有行，batch-delete 删完它仍然在列表里（只要它还是当前会话），
    用户会以为删除失败；确认弹窗里的「这 N 个会话」也会多算一条。
    """
    source = _source(EMBED_CHAT)
    block = _block(source, "const handleDeleteGroup = (group: any)", "\n};")
    assert "is_pending" in block, "整组删除没有排除临时条目"
    assert "showDeleteGroupModal.value = true;" in block


def test_group_header_hides_clear_action_when_only_the_pending_item_remains():
    """组里只有一条临时条目时，「清空此组」点了不会有任何反应，必须隐藏。"""
    source = _source(SIDEBAR)
    assert "const hasDeletableItems = (group: any)" in source
    header = _block(source, "<!-- Delete Entire Group Action", "</button>")
    assert 'v-if="group.id !== \'pinned\' && hasDeletableItems(group)"' in header, (
        "组头「清空此组」仍对只有临时条目的分组可见"
    )
