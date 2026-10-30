"""契约：一轮结束（含恢复收敛）后必须把时间线里仍挂起的步骤落定。

背景（真实反馈）：会话恢复、终态收敛之后，恢复卡片消失了、输入框也解锁了，但思考卡片
里那一步仍是 pending —— 秒表继续走、转圈与「仍在处理中…」一直挂在那儿（文案规则见
processTimeline 的 `resolvePendingHint` / `PENDING_HINT_LABEL_AFTER_MS`），用户以为 AI 还在干活。

秒表由 `resolveLiveTimerLogId` 驱动：它深度优先找**最后一条 pending 的 log 项**。
所以只要没人把 pending 落定，秒表就永远不会停。

正常流结束时有统一收尾（把 `status === "pending"` 归位为 success），但恢复/终态收敛
这条路径没有走那套。这里锁定「落定」纯函数的行为，以及每条终态收敛路径都接上了它。
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
TIMELINE_TS = "frontend/src/utils/processTimeline.ts"
pytestmark = pytest.mark.no_infrastructure


def _run(items):
    """把真实 TS 源码转译后在 node 里执行，返回落定前后的秒表状态与落定结果。"""
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(TIMELINE_TS)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
const ex = moduleRef.exports;
const items = {json.dumps(items, ensure_ascii=False)};
const before = ex.resolveLiveTimerLogId(items);
const settled = ex.settlePendingTimelineSteps(items);
const after = ex.resolveLiveTimerLogId(items);
process.stdout.write(JSON.stringify({{ before, settled, after, items }}));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def _log(log_id, status="pending", **extra):
    item = {"kind": "log", "id": log_id, "title": f"步骤{log_id}", "details": "", "status": status}
    item.update(extra)
    return item


def test_pending_step_is_settled_and_its_timer_stops():
    """闭环：落定前秒表在跑，落定后必须停下——这正是用户看到的那两个症状。"""
    result = _run([_log(1, started_at=1000)])
    assert result["before"] == "1"
    assert result["after"] is None
    assert result["items"][0]["status"] == "success"
    assert result["settled"] == 1


def test_nested_steps_are_settled_recursively():
    """时间线是树：子步骤挂着不动，秒表照样停不下来。"""
    items = [_log(1, children=[_log(2, children=[_log(3)])])]
    result = _run(items)
    assert result["settled"] == 3
    assert result["items"][0]["children"][0]["children"][0]["status"] == "success"
    assert result["after"] is None


@pytest.mark.parametrize("category", ["permission", "external"])
def test_waiting_on_user_or_external_stays_pending(category):
    """等用户确认 / 等外部执行不是「机器在跑」，不能被落定成成功。"""
    result = _run([_log(1, category=category, started_at=1000)])
    assert result["items"][0]["status"] == "pending"
    assert result["settled"] == 0


def test_already_finished_steps_are_untouched():
    result = _run(
        [_log(1, status="success"), _log(2, status="error"), _log(3, status="warning")]
    )
    assert [item["status"] for item in result["items"]] == ["success", "error", "warning"]
    assert result["settled"] == 0


def test_pending_narration_is_no_longer_pending():
    """旁白项的 pending 驱动「思考中」的表现，同样要落定。"""
    items = [
        {
            "kind": "text",
            "id": "t1",
            "textKind": "narration",
            "content": "正在整理…",
            "pending": True,
        }
    ]
    result = _run(items)
    assert result["items"][0]["pending"] is False
    assert result["settled"] == 1


def test_todo_items_are_left_alone():
    """待办是任务级语义，不随一轮结束被改写。"""
    items = [
        {
            "kind": "todo",
            "id": "todo1",
            "title": "任务",
            "todos": [{"text": "子任务", "status": "in_progress"}],
            "counts": {"pending": 0, "in_progress": 1, "completed": 0},
        }
    ]
    result = _run(items)
    assert result["items"][0]["todos"][0]["status"] == "in_progress"
    assert result["settled"] == 0


def test_empty_or_missing_timeline_is_safe():
    assert _run([])["settled"] == 0
    assert _run(None)["settled"] == 0


def test_every_terminal_convergence_settles_the_timeline():
    """接线契约：每条终态收敛路径都要收尾，漏一条秒表就会一直跑。

    历史收敛的多个分支（reload / update|fill|append / 已同步）现在统一走
    `finishRecoveredTurn()`：它同时落定时间线、置完成态并清草稿/快照。这样新增
    收敛分支时不会再出现「只置了完成态、忘了 settle」的漏收尾。
    """
    source = EMBED_CHAT.read_text(encoding="utf-8")
    start = source.index("const finishRecoveredTurn = () => {")
    body = source[start:source.index("\n};", start)]
    assert "settleRecoveredTimeline();" in body
    assert "recoveryCardTerminalApplied.value = true;" in body

    sync_start = source.index("const syncLatestSessionHistory = async")
    sync_body = source[sync_start:source.index("\n  };", sync_start)]
    assert sync_body.count("finishRecoveredTurn();") >= 3, "所有历史收敛分支都必须走统一收尾"
    assert "recoveryCardTerminalApplied.value = true;" not in sync_body

    # SSE 自然结束路径仍要自行收尾（此时没有历史收敛分支可走）。
    assert "settleRecoveredTimeline();" in source
    assert "recoveryCardTerminalApplied.value = true;" in source


def _settle_helper_body() -> str:
    """取出顶层收尾函数的函数体（到第一个顶层 `};` 为止）。"""
    source = EMBED_CHAT.read_text(encoding="utf-8")
    start = source.index("const settleRecoveredTimeline = () => {")
    end = source.index("\n};", start)
    return source[start:end]


def test_settle_helper_does_not_touch_function_local_agent_msg():
    """回归护栏（真实反馈）：气泡尾部多出「[错误: agentMsg is not defined]」。

    收尾函数定义在 setup 顶层，而 `agentMsg` 是 sendMessageInternal 内的局部 ref：
    顶层作用域引用它必然抛 ReferenceError，被该函数的 catch 捕获后拼进回答正文，
    于是每一轮正常结束都「多一行报错」；且异常发生在 try 内，紧随其后的
    `clearStreamReplayTimer()` 被整段跳过（续显轮询定时器残留）。
    """
    body = _settle_helper_body()
    assert "agentMsg.value" not in body
    assert "currentStreamAgentMessage()" in body


def test_settle_helper_resolves_target_from_shared_scope():
    """收尾目标必须由顶层可解析的来源决定：在途草稿优先，否则取消息列表末尾的助手消息。

    两处「先清草稿再收尾」的终态收敛路径（正常收流结束、历史补齐落地）说明不能只看草稿。
    """
    source = EMBED_CHAT.read_text(encoding="utf-8")
    assert "const currentStreamAgentMessage = (): Message | null" in source
    resolver = source[
        source.index("const currentStreamAgentMessage = (): Message | null"):
        source.index("\n};", source.index("const currentStreamAgentMessage = (): Message | null"))
    ]
    assert "activeStreamDraft" in resolver
    # 解析细节收在解析函数里：收尾函数只消费结果，避免又出现作用域外引用。
    assert "activeStreamDraft" not in _settle_helper_body()
