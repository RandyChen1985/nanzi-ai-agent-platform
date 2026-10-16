"""Contract: 新建会话必须立刻解锁输入框。

用户实测：AI 还在回答时点「新会话」并确认，新会话的输入框仍然是锁定的。

根因是 `resetSession` 只清了 messages 并换了 conversation_id，没有停掉本地在途生成、
也没有复位运行态。三样残留会一起把输入框钉住：

1. 旧 SSE 流没断：`isProcessing` 一直为 true，回调还会继续往刚清空的 messages 里追加内容；
2. 运行态没复位：`remoteRunBusy` / `outputCompleted` 残留，输入框按「仍在生成」渲染；
3. 旧会话的 run-status 轮询没停：下一次 tick 会拿**旧** conversation_id 查到 active=true，
   把 `remoteRunActive` 重新翻回 true——即使复位过也会被再次锁上。

输入框锁定条件是 `isProcessing || remoteRunBusy || sendLocked`（EmbedChat 传
`:is-processing="isProcessing || remoteRunBusy"`、`:is-submitting="sendLocked"`），
所以前两样都必须清掉。口径与 `resetSessionForIdentityChange` 保持一致。
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend" / "src" / "views" / "EmbedChat.vue"
ENTRY = "resetConversationScopedState"


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def _body(source: str, signature: str) -> str:
    match = re.search(rf"{re.escape(signature)}\s*\([\s\S]*?\n\}};", source)
    assert match, f"未找到 {signature} 实现"
    return match.group(0)


def test_entry_point_stops_inflight_stream_and_resets_run_state():
    """三样残留都要清：本地流、isProcessing、旧会话轮询与运行态。

    这些复位原先在三条路径上各有一份手写副本（其中一份漏过游标与 stall 定时器），现在
    收敛成唯一入口；完整性另由 test_conversation_scoped_state_registry_contract.py 守。
    """
    body = _body(_source(), f"const {ENTRY} = ")

    assert "abortController.abort()" in body, "旧流没断，回调会写进新会话且 isProcessing 解不开"
    assert "isProcessing.value = false" in body, "isProcessing 残留会把输入框一直锁住"
    assert "stopRemoteRunPolling()" in body, "旧会话轮询没停，下一 tick 会把输入框重新锁上"
    assert "resetRunStatus()" in body, "outputCompleted / remoteRunBusy 残留会让锁判定失真"


def test_reset_session_goes_through_the_single_entry():
    """「新会话」确认后的 resetSession 是用户实测的入口。"""
    body = _body(_source(), "const resetSession = async")
    assert f"{ENTRY}()" in body, "resetSession 没停掉在途生成，新会话输入框仍会被锁"


def test_project_conversation_path_uses_the_same_reset():
    """/project 自己内联了 messages=[] + generateNewConversation()，同样要停流。"""
    project_case = _source().split('case "/project":')[1].split("case ")[0]
    assert f"{ENTRY}()" in project_case, "项目会话路径漏了停流，输入框会留锁"


def test_deleting_current_conversation_uses_the_same_reset():
    """生成中删掉当前会话，同样会把新会话的输入框留锁。"""
    delete_group = _source().split("const confirmDeleteGroup")[1][:1500]
    assert f"{ENTRY}()" in delete_group, "删除当前会话路径漏了停流，输入框会留锁"
