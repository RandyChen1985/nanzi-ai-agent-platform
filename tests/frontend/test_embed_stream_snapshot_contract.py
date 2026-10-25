"""契约：EmbedChat 流式快照（切页面后还原已输出的思考与正文）。

覆盖 `buildEmbedStreamSnapshot` / `isEmbedStreamSnapshotUsable` 纯函数行为
（node 转译真实 TS 源码执行）：

- 快照必须能承载「已输出的思考与正文」，并带上 trace_id 供运行恢复器接管；
- 必须对体积做硬约束（sessionStorage 只有 ~5MB，且思考/工具日志可以很大）；
- 必须有 TTL 与会话归属校验，避免把过期或别的会话的草稿注入当前界面。
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

MODULE_PATH = "frontend/src/utils/embedStreamSnapshot.ts"
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"


def _embed_source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def _call(expression: str):
    """在 node 里执行转译后的真实 TS 源码；表达式块必须自带 return。"""
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(MODULE_PATH)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
const api = moduleRef.exports;
const result = await (async () => {{ {expression} }})();
process.stdout.write(JSON.stringify(result === undefined ? null : result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def test_build_keeps_body_reasoning_and_trace_id():
    snapshot = _call(
        """
return api.buildEmbedStreamSnapshot({
  conversationId: 'c1',
  traceId: 't1',
  user: { content: '帮我查一下', timestamp: '2026-09-23T11:00:00' },
  draft: { content: '正在查询', reasoningContent: '先确认表结构', agentName: 'sys_main' },
  now: 1000,
})
"""
    )
    assert snapshot["conversationId"] == "c1"
    assert snapshot["traceId"] == "t1"
    assert snapshot["user"]["content"] == "帮我查一下"
    assert snapshot["draft"]["content"] == "正在查询"
    assert snapshot["draft"]["reasoningContent"] == "先确认表结构"
    assert snapshot["draft"]["agentName"] == "sys_main"
    assert snapshot["savedAt"] == 1000


def test_build_persists_the_resume_owned_flag():
    """恢复流写出的正文必须把 `resumeOwned` 一起落盘。

    刷新是恢复流程的核心场景：标记只存在内存里时，重启后首拉会按「只有挂起前事件」
    的日志重建，把快照里恢复流刚写出的正文换回旧内容。
    """
    owned = _call(
        """
return api.buildEmbedStreamSnapshot({
  conversationId: 'c1',
  traceId: 't1',
  resumeOwned: true,
  draft: { content: '确认后继续输出的正文' },
  now: 1000,
})
"""
    )
    assert owned["resumeOwned"] is True
    plain = _call(
        """
return api.buildEmbedStreamSnapshot({
  conversationId: 'c1',
  traceId: 't1',
  draft: { content: '普通实时流正文' },
  now: 1000,
})
"""
    )
    assert "resumeOwned" not in plain


def test_build_returns_null_without_any_visible_content():
    assert _call(
        """
return api.buildEmbedStreamSnapshot({
  conversationId: 'c1',
  draft: { content: '', reasoningContent: '', processTimeline: [] },
  now: 1000,
})
"""
    ) is None


def test_build_returns_null_without_conversation_id():
    assert _call(
        """
return api.buildEmbedStreamSnapshot({
  conversationId: '',
  draft: { content: '有内容但没有会话归属' },
  now: 1000,
})
"""
    ) is None


def test_build_truncates_oversized_body_and_reasoning():
    snapshot = _call(
        """
const snapshot = api.buildEmbedStreamSnapshot({
  conversationId: 'c1',
  draft: { content: 'A'.repeat(90000), reasoningContent: 'B'.repeat(90000) },
  now: 1000,
});
return {
  contentLength: snapshot.draft.content.length,
  reasoningLength: snapshot.draft.reasoningContent.length,
  totalBytes: JSON.stringify(snapshot).length,
};
"""
    )
    assert snapshot["contentLength"] <= 20000
    assert snapshot["reasoningLength"] <= 20000
    assert snapshot["totalBytes"] <= 400000


def test_build_caps_timeline_items_and_recurses_into_children():
    snapshot = _call(
        """
const timeline = Array.from({ length: 400 }, (_, index) => ({
  kind: 'log',
  id: index,
  title: 'tool',
  details: 'D'.repeat(5000),
  status: 'success',
  children: Array.from({ length: 10 }, (_, child) => ({
    kind: 'log', id: `${index}-${child}`, title: 'nested', details: 'E'.repeat(5000), status: 'success',
    children: [{ kind: 'log', id: `${index}-${child}-deep`, title: 'too-deep', details: 'F'.repeat(5000), status: 'success' }],
  })),
}));
const snapshot = api.buildEmbedStreamSnapshot({
  conversationId: 'c1',
  draft: { content: '正文', processTimeline: timeline },
  now: 1000,
});
return {
  items: snapshot.draft.processTimeline.length,
  serializedBytes: JSON.stringify(snapshot).length,
};
"""
    )
    assert snapshot["items"] <= 120
    assert snapshot["serializedBytes"] <= 400000


def test_build_drops_timeline_before_exceeding_storage_budget():
    """极端体积下必须降级（先丢时间线），而不是写入一个撑爆 sessionStorage 的快照。"""
    result = _call(
        """
const timeline = Array.from({ length: 120 }, (_, index) => ({
  kind: 'text', id: index, textKind: 'reasoning', content: 'G'.repeat(8000), pending: false,
}));
const snapshot = api.buildEmbedStreamSnapshot({
  conversationId: 'c1',
  draft: { content: '正文', processTimeline: timeline },
  now: 1000,
});
return {
  bytes: JSON.stringify(snapshot).length,
  timelineItems: snapshot.draft.processTimeline.length,
  content: snapshot.draft.content,
};
"""
    )
    assert result["bytes"] <= 400000
    assert result["content"] == "正文"


def test_snapshot_usable_flags():
    result = _call(
        """
const snapshot = { version: 1, conversationId: 'c1', savedAt: 1000, draft: { content: '正文' } };
return {
  fresh: api.isEmbedStreamSnapshotUsable(snapshot, 'c1', 1000 + api.SNAPSHOT_TTL_MS - 1),
  expired: api.isEmbedStreamSnapshotUsable(snapshot, 'c1', 1000 + api.SNAPSHOT_TTL_MS + 1),
  otherConversation: api.isEmbedStreamSnapshotUsable(snapshot, 'c2', 1500),
  emptyDraft: api.isEmbedStreamSnapshotUsable(
    { version: 1, conversationId: 'c1', savedAt: 1000, draft: { content: '', processTimeline: [] } },
    'c1',
    1500,
  ),
  nullSnapshot: api.isEmbedStreamSnapshotUsable(null, 'c1', 1500),
};
"""
    )
    assert result["fresh"] is True
    assert result["expired"] is False
    assert result["otherConversation"] is False
    assert result["emptyDraft"] is False
    assert result["nullSnapshot"] is False


def test_embed_chat_wires_snapshot_storage_helpers():
    source = _embed_source()
    assert 'from "@/utils/embedStreamSnapshot"' in source
    assert "const streamSnapshotStorageKey = (" in source
    assert "const persistStreamSnapshot = (" in source
    assert "const maybePersistStreamSnapshot = (" in source
    assert "const restoreEmbedStreamSnapshot = async (" in source
    assert "const clearStreamSnapshot = (" in source


def test_embed_chat_persists_stream_draft_on_throttle_and_force_on_unmount():
    source = _embed_source()
    # 流式过程中节流写入（正文/思考每次刷新都可能触发）
    assert "maybePersistStreamSnapshot();" in source
    # 卸载前强制写入一次，保证重建后能还原已输出的思考
    assert "maybePersistStreamSnapshot(true);" in source


def test_embed_chat_restores_snapshot_after_history_load_and_clears_on_terminal():
    source = _embed_source()
    assert "restoreEmbedStreamSnapshot(initGeneration)" in source
    assert "clearStreamSnapshot(conversationId.value);" in source


def test_build_keeps_replay_cursor():
    snapshot = _call(
        """
return api.buildEmbedStreamSnapshot({
  conversationId: 'c1',
  traceId: 't1',
  lastSeq: 42,
  draft: { content: '正文' },
  now: 1000,
})
"""
    )
    assert snapshot["lastSeq"] == 42


def test_build_keeps_pending_interaction_state():
    """待确认交互态必须能随快照还原。

    事件日志被 LTRIM/TTL 截断时，它是刷新后待确认卡片的唯一来源；缺了它，后台任务
    会一直挂到确认超时。这些状态是 upsert 终态，重放回退风险由后续事件覆盖。
    """
    snapshot = _call(
        """
return api.buildEmbedStreamSnapshot({
  conversationId: 'c1',
  traceId: 't1',
  user: { content: '帮我执行' },
  draft: {
    content: '准备调用工具',
    pendingPermission: {
      permission_request_id: 'p1', title: '工具调用确认', status: 'pending',
      tool_call: { id: 'call_1', name: 'bash', arguments: '{}' },
    },
    userQuestion: { question_id: 'q1', question: '选哪个', status: 'pending' },
    businessConfirmation: { confirmation_id: 'c1', status: 'pending' },
  },
  now: 1000,
})
"""
    )
    assert snapshot["draft"]["pendingPermission"]["permission_request_id"] == "p1"
    assert snapshot["draft"]["pendingPermission"]["status"] == "pending"
    assert snapshot["draft"]["userQuestion"]["question_id"] == "q1"
    assert snapshot["draft"]["businessConfirmation"]["confirmation_id"] == "c1"


def test_build_keeps_a_user_only_snapshot_before_first_visible_output():
    """首片正文/时间线到达前刷新时，至少要把本轮提问留在界面上。"""
    snapshot = _call(
        """
return api.buildEmbedStreamSnapshot({
  conversationId: 'c1',
  user: { content: '我刚发出的提问' },
  draft: { content: '' },
  now: 1000,
})
"""
    )
    assert snapshot is not None
    assert snapshot["user"]["content"] == "我刚发出的提问"
    assert snapshot["draft"]["content"] == ""


def test_user_only_snapshot_is_usable():
    result = _call(
        """
const snapshot = { version: 1, conversationId: 'c1', savedAt: 1000, user: { content: '提问' }, draft: { content: '' } };
return api.isEmbedStreamSnapshotUsable(snapshot, 'c1', 1500);
"""
    )
    assert result is True


def test_build_trims_oversized_tool_call_but_keeps_the_card():
    """工具参数可能很大：超限时丢参数、保留卡片，不能整张卡片一起丢。"""
    result = _call(
        """
const snapshot = api.buildEmbedStreamSnapshot({
  conversationId: 'c1',
  draft: {
    content: '准备执行',
    pendingPermission: {
      permission_request_id: 'p1', title: '工具调用确认', status: 'pending',
      tool_call: { id: 'call_1', name: 'bash', arguments: 'X'.repeat(40000) },
    },
  },
  now: 1000,
});
return {
  requestId: snapshot.draft.pendingPermission.permission_request_id,
  hasToolCall: Object.prototype.hasOwnProperty.call(snapshot.draft.pendingPermission, 'tool_call'),
  bytes: JSON.stringify(snapshot).length,
};
"""
    )
    assert result["requestId"] == "p1"
    assert result["hasToolCall"] is False
    assert result["bytes"] <= 400000


def test_embed_chat_restores_interaction_state_from_snapshot():
    source = _embed_source()
    assert "hasRestorableDraft" in source
    assert "pendingPermission:" in source
    assert "pendingExternalExecution:" in source
    assert "userQuestion:" in source
    assert "businessConfirmation:" in source
    # 只还原了提问时不能伪造一条空助手气泡：交给恢复器补建草稿。
    assert "只还原了提问：先让用户看见本轮问题，助手草稿由恢复器补建" in source


def test_strip_client_stream_error_suffix_only_removes_the_transient_tail():
    """本端读流失败的错误只提示当前界面，不得写进恢复快照。

    它是浏览器传输层失败（network error / Failed to fetch），不代表服务端那轮失败；
    快照带上它，刷新后就会先闪出一条过时错误，再被落库终态覆盖（用户实测）。
    """
    result = _call(
        """
const suffix = '\\n[错误: network error]';
return {
  stripped: api.stripClientStreamErrorSuffix('完整回答' + suffix, suffix),
  noSuffix: api.stripClientStreamErrorSuffix('完整回答', ''),
  mismatch: api.stripClientStreamErrorSuffix('完整回答\\n[错误: other]', suffix),
  serverErrorKept: api.stripClientStreamErrorSuffix('完整回答\\n\\n> ❌ **处理未完成**: 服务端错误', suffix),
};
"""
    )
    assert result == {
        "stripped": "完整回答",
        "noSuffix": "完整回答",
        "mismatch": "完整回答\n[错误: other]",
        "serverErrorKept": "完整回答\n\n> ❌ **处理未完成**: 服务端错误",
    }


def test_embed_chat_does_not_persist_a_transient_client_transport_error():
    source = _embed_source()
    assert "stripClientStreamErrorSuffix(draft.content, draft.clientTransportErrorSuffix)" in source
    assert "agentMsg.value.clientTransportErrorSuffix = transportErrorSuffix;" in source
    assert "clientTransportErrorSuffix?: string;" in source
