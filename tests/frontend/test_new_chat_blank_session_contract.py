"""用户模式「新建任务」必须开空白会话的契约。

用户反馈：用户模式下点「新建任务」，仍然加载上一次历史会话。链路是
侧栏 → Dashboard `handleUserModeNewChat` → `/dashboard/chat?new=1` → Chat 挂 iframe
（`/embed/chat?new=1`）→ 挂件 `isForceNewSession` 跳过续接。

实际断在两处时序/兜底上，这里锁住：

1. 已经在聊天页时，Dashboard 必须**先**把地址栏换成 `new=1`，**再**重挂载；
   反过来时新挂载的 Chat 读到的还是上一条会话的 `conversation_id`，挂件据
   `requestedConversationId` 让位、续接历史。
2. 挂件初始化消息在 `new=1` 时不得下发 `conversation_id`（微任务窗口内的第二道闸）。
3. iframe 必须带 key，指令变化时整页重载，只改 src 不会重新初始化挂件。
4. 若 `new=1` 在挂载之后才落到路由上，Chat 要能自己补一次初始化。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
CHAT = ROOT / "frontend/src/views/Chat.vue"
DASHBOARD = ROOT / "frontend/src/views/Dashboard.vue"


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


def test_dashboard_replaces_query_before_remounting_chat() -> None:
    source = _read(DASHBOARD)
    assert 'router.replace({ path: "/dashboard/chat", query: nextQuery }).then(() => {' in source
    # 重挂载必须在地址栏更新之后，不能出现在 replace 之前
    replace_at = source.index('router.replace({ path: "/dashboard/chat", query: nextQuery }).then')
    token_at = source.index("pageReloadToken.value += 1")
    assert replace_at < token_at
    # 新建时必须清掉上一条会话的定位参数
    assert "delete nextQuery.conversation_id" in source
    assert "delete nextQuery.agent_id" in source


def test_chat_iframe_is_keyed_so_new_instructions_reload_the_widget() -> None:
    source = _read(CHAT)
    assert 'const iframeKey = ref(0)' in source
    assert ':key="iframeKey"' in source
    assert "iframeKey.value += 1" in source


def test_init_config_never_sends_conversation_id_for_new_session() -> None:
    source = _read(CHAT)
    block_at = source.index('conversation_id: String(route.query.new')
    window = source[block_at : block_at + 400]
    assert "=== '1'" in window
    assert "? null" in window


def test_chat_watches_new_query_as_a_fallback() -> None:
    source = _read(CHAT)
    watch_at = source.index("() => route.query.new")
    window = source[watch_at : watch_at + 260]
    assert "initChat()" in window
