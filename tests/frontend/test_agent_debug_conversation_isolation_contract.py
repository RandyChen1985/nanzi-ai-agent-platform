"""AgentDebug（智能体调试页）会话指针的隔离契约。

## 背景

`AgentDebug.vue` 把「当前调试会话」存在 `localStorage["agent_debug_conv_id"]`，**键里没有
任何用户维度**，而且 `onMounted` 里是**同步**读取——`fetchCurrentUser()` 还没 await 就已经
把上一位使用者的会话 ID 塞进 `conversationId`，接着 `loadSessionHistory()` 去拉对方的会话。
换用户后打开调试页，就会看到（并可能继续写入）别人的会话。后端已按归属人过滤读、并在写侧
403，所以不会泄露内容，但页面表现为「莫名加载失败/发消息被拒」。

修复口径与 EmbedChat 一致：
1. 存储键带身份维度 `agent_debug_conv_id:u:<identity>`；没有用户维度的旧键**只清理、不读取**；
2. 身份未知（未登录 / 拉取失败）时既不读也不写，且**不覆盖**旧指针；
3. 采用任何已存会话 ID 之前先问 `/ownership`：只有 `adoptable` 才采用，`foreign` 清掉重开，
   `unknown`（网络/接口异常）这轮不采用但保留指针。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _source(path: str = "frontend/src/views/AgentDebug.vue") -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _block(source: str, marker: str, end: str = "\n};") -> str:
    start = source.index(marker)
    return source[start:source.index(end, start)]


def test_agent_debug_conversation_storage_is_scoped_by_identity():
    source = _source()
    assert 'const LEGACY_DEBUG_CONVERSATION_STORAGE_KEY = "agent_debug_conv_id";' in source
    assert 'const DEBUG_CONVERSATION_STORAGE_PREFIX = "agent_debug_conv_id:";' in source
    assert 'const USER_SCOPED_STORAGE_SEGMENT = "u";' in source

    key_fn = _block(source, "const conversationStorageKey = ")
    assert "currentIdentityKey()" in key_fn
    assert "USER_SCOPED_STORAGE_SEGMENT" in key_fn
    assert "encodeURIComponent(identity)" in key_fn
    # 身份未知 → 空键（既不读也不写），绝不退化成共享桶
    assert 'if (!identity) return "";' in key_fn


def test_agent_debug_identity_prefers_user_id_and_falls_back_to_user_name():
    source = _source()
    helper = _block(source, "const identityKeyFromUserData = ")
    assert "user_id" in helper
    assert "user_name" in helper
    assert "`id:${userId}`" in helper
    assert "`name:${userName}`" in helper


def test_agent_debug_never_reads_the_unscoped_legacy_key():
    source = _source()
    assert 'localStorage.getItem("agent_debug_conv_id")' not in source
    assert "localStorage.setItem(\"agent_debug_conv_id\"" not in source
    # 旧键只出现在「清理」路径里
    purge = _block(source, "const purgeLegacyConversationStorage = ")
    assert "removeItem(LEGACY_DEBUG_CONVERSATION_STORAGE_KEY)" in purge
    assert "getItem" not in purge
    # 带前缀但没有用户维度的历史键也一并清掉
    assert "scopedPrefix" in purge


def test_agent_debug_awaits_identity_before_reading_the_stored_conversation():
    source = _source()
    bootstrap = _block(source, "const bootstrapDebugConversation = ")
    assert "await fetchCurrentUser()" in bootstrap
    fetch_at = bootstrap.index("await fetchCurrentUser()")
    read_at = bootstrap.index("readStoredConversationId()")
    assert fetch_at < read_at, "必须在拿到身份之后才读会话指针"
    assert "purgeLegacyConversationStorage()" in bootstrap


def test_agent_debug_validates_ownership_before_adopting_a_stored_conversation():
    source = _source()
    check = _block(source, "const checkConversationAdoption = ")
    assert "/api/v1/chat/conversation/${encodeURIComponent(normalized)}/ownership" in check
    assert '"foreign"' in check
    assert '"adoptable"' in check

    bootstrap = _block(source, "const bootstrapDebugConversation = ")
    assert "await checkConversationAdoption(savedId)" in bootstrap
    adoption_at = bootstrap.index("await checkConversationAdoption(savedId)")
    adopt_at = bootstrap.index("conversationId.value = savedId")
    assert adoption_at < adopt_at


def test_agent_debug_discards_a_foreign_conversation_and_keeps_the_pointer_when_unknown():
    bootstrap = _block(_source(), "const bootstrapDebugConversation = ")
    unknown_branch = bootstrap.index('if (adoption === "unknown")')
    foreign_branch = bootstrap.index("clearStoredConversationId()")
    # 三分支穷尽：unknown 在前、foreign 在后
    assert unknown_branch < foreign_branch
    # unknown：只换内存里的会话，不覆盖存储里的指针
    assert "persist: false" in bootstrap[unknown_branch:foreign_branch]
    assert "clearStoredConversationId" not in bootstrap[unknown_branch:foreign_branch]
    # 身份拿不到时同样只换内存、不读不写
    no_identity = bootstrap.index("if (!identity)")
    assert "persist: false" in bootstrap[no_identity:unknown_branch]
    assert "clearStoredConversationId" not in bootstrap[no_identity:unknown_branch]


def test_agent_debug_persists_the_pointer_only_through_the_scoped_helper():
    source = _source()
    assert "persistConversationId(targetId);" in source
    write_fn = _block(source, "const persistConversationId = ")
    assert "conversationStorageKey()" in write_fn
    assert "localStorage.setItem(key, cid)" in write_fn
    # `generateNewConversation` 必须支持「只换内存、不落盘」
    generator = _block(source, "const generateNewConversation = ")
    assert "options: { persist?: boolean } = {}" in generator
    assert "options.persist !== false" in generator
    assert "persistConversationId(conversationId.value);" in generator
