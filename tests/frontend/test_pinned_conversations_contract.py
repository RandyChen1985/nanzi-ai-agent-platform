"""会话历史置顶的前端接线契约。

锁住四件事：

1. 侧栏按钮只 emit，不自己发请求；已置顶状态常显、置顶组不提供「删除整组」；
2. 聊天页与调试页都接线（同一个侧栏组件，漏一处就会出现点了没反应的按钮）；
3. 只走专用端点，绝不调用全量 `/api/portal/portal-prefs` PUT（会覆盖其它偏好）；
4. 搜索/筛选时不允许快照项凭空出现，身份切换必须清空上一位用户的置顶。
"""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

PIN_ENDPOINT = "/api/portal/portal-prefs/pinned-conversations"
PREFS_ENDPOINT = "/api/portal/portal-prefs"


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _block(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    return source[start:source.index(end_marker, start)]


def test_sidebar_button_only_emits_and_marks_pinned_state():
    source = _source("frontend/src/components/ChatHistorySidebar.vue")

    assert "@click.stop=\"emit('toggle-pin', item)\"" in source
    assert '(e: "toggle-pin", item: any): void;' in source
    # 已置顶实心常显；未置顶沿用 hover 显隐（移动端常显）
    assert "item.__pinned" in source
    assert "opacity-100 text-primary" in source
    assert "actionVisibilityClass" in source
    assert 'title="取消置顶"' in source or "'取消置顶'" in source
    # 置顶组不提供「删除整组」，避免一键清空置顶会话
    assert "group.id !== 'pinned'" in source


def test_both_pages_wire_the_shared_composable_and_event():
    embed = _source("frontend/src/views/EmbedChat.vue")
    debug = _source("frontend/src/views/AgentDebug.vue")

    for source, name in ((embed, "EmbedChat"), (debug, "AgentDebug")):
        assert "usePinnedConversations" in source, f"{name} 未接入置顶状态"
        assert '@toggle-pin="handleTogglePin"' in source, f"{name} 未绑定置顶事件"
        assert "splitHistoryByPin" in source, f"{name} 未按置顶拆分历史分组"
        assert "isDefaultHistoryFilters" in source, f"{name} 未判断默认视图"
        assert "void loadPinnedConversations()" in source, f"{name} 未加载置顶"


def test_embed_clears_pins_on_identity_change_and_cleans_up_deleted_sessions():
    source = _source("frontend/src/views/EmbedChat.vue")
    reset = _block(
        source,
        "const resetSessionForIdentityChange = (",
        "type RoutingPreference = {",
    )

    assert "pinnedConversations.value = [];" in reset
    assert "void removePinnedConversations([targetConvId]);" in source
    assert "void removePinnedConversations(convIds);" in source


def test_snapshot_pins_are_hidden_while_searching_or_filtering():
    for path in ("frontend/src/views/EmbedChat.vue", "frontend/src/views/AgentDebug.vue"):
        source = _source(path)
        assert "includeMissingPins:" in source
        assert "!historyKeyword.value.trim() && isDefaultHistoryFilters(historyFilters.value)" in source
        assert "pinnedGroup ? [pinnedGroup, ...dateGroups] : dateGroups" in source


def test_composable_only_talks_to_the_dedicated_pin_endpoint():
    source = _source("frontend/src/composables/chat/usePinnedConversations.ts")

    assert f'const PREFS_ENDPOINT = "{PREFS_ENDPOINT}";' in source
    assert f'const PIN_ENDPOINT = "{PIN_ENDPOINT}";' in source
    assert 'axios.put(\n      PIN_ENDPOINT,' in source or "axios.put(" in source
    # 全量偏好端点只能用于 GET，不能出现在任何 axios.put 调用里
    assert 'axios.put(\n      PREFS_ENDPOINT' not in source
    assert "MAX_PINNED_CONVERSATIONS = 20" in source
    # 乐观更新必须有回滚
    assert "pinned.value = previous" in source


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行分组纯函数行为测试")
def test_pin_grouping_behaviour_via_node():
    script = Path("tests/frontend/pinned_history_groups.mjs")
    assert script.exists(), "行为测试脚本缺失"

    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "all assertions passed" in proc.stdout
