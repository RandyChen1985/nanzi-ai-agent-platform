from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"

pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def test_history_fetch_waits_until_pinned_agent_resolves_to_db_id():
    """深链 `?agent_id=<slug>` 不能直接拿去过滤历史。

    历史表存的是解析后的数据库 id，用 slug 过滤必然为空；侧栏会先闪一次
    「暂无会话历史」，看起来像历史丢了。
    """
    source = _source()
    fetch_body = source.split("const fetchHistory = async (isLoadMore = false) => {", 1)[1][:2000]

    assert "if (urlPinnedAgentKey.value && !pinnedAgentId.value) return;" in fetch_body
    # 守卫必须在真正发请求之前。
    assert fetch_body.index("if (urlPinnedAgentKey.value && !pinnedAgentId.value) return;") < fetch_body.index(
        'axios.get("/api/v1/chat/history"'
    )


def test_init_chat_backfills_history_sidebar_after_auth_and_agent_lock():
    """首屏自动展开的侧栏会早发一次请求（认证未就绪 / agent 未解析），必须补拉。"""
    source = _source()
    init_body = source.split("const initChat = async", 1)[1].split("// History State", 1)[0]

    assert "if (showHistorySidebar.value) {" in init_body
    assert "void fetchHistory();" in init_body
    # 补拉必须发生在智能体锁定/解析之后，否则仍会带 slug。
    assert init_body.index("resolveUrlPinnedAgent()") < init_body.index("void fetchHistory();")
