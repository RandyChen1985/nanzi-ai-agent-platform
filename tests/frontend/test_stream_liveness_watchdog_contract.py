"""契约：主流在途时的存活性看门狗（空闲超时 + run-status 心跳）。

背景：用户实测「执行过程耗时不再增长、输入框小人动画停、页面点不动，刷新后数据
全都有」。这是前端主线程/读流停摆——`reader.read()` 没有空闲超时，而恢复器被
`streamInFlight` 闸门挡住，界面只能靠刷新绕过。看门狗负责在停摆（或解冻）后
主动 abort 本地流，让 `finally` 走 run-status 核验 + 历史收敛。

覆盖三层：
1. `decideStreamAbort` 纯函数（node 转译真实 TS 源码执行，非复刻）；
2. `EmbedChat.vue` 的接线（每帧续命、到点 abort、收尾清定时器）；
3. AbortError 不得把看门狗中止误标成「用户终止」。
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
LIVENESS_MODULE = "frontend/src/utils/streamLiveness.ts"
pytestmark = pytest.mark.no_infrastructure


def _decide(**overrides):
    payload = {
        "streamInFlight": True,
        "pendingConfirmation": False,
        "idleMs": 0,
        "remoteRunActive": True,
    }
    payload.update(overrides)
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(LIVENESS_MODULE)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
const result = moduleRef.exports.decideStreamAbort(
  {json.dumps(payload, ensure_ascii=False)},
);
process.stdout.write(JSON.stringify(result === null ? null : result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def _embed_source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


# --- 纯函数行为 ---

def test_keeps_stream_when_frames_are_arriving():
    """正常收帧（含每秒 keepalive）时不得中止。"""
    assert _decide(idleMs=1000) is None


def test_aborts_only_after_the_idle_timeout():
    """阈值边界：14999ms 不动，15000ms 起中止。"""
    assert _decide(idleMs=14999) is None
    assert _decide(idleMs=15000) == "idle"
    assert _decide(idleMs=60000) == "idle"


def test_aborts_when_backend_run_is_already_finished():
    """run-status 心跳发现后端已收尾：流却还在途，直接中止交给收敛。"""
    assert _decide(remoteRunActive=False, idleMs=1000) == "run-finished"


def test_idle_wins_over_run_finished():
    """两个条件同时成立时优先报「空闲」，诊断时能看出连接先死。"""
    assert _decide(remoteRunActive=False, idleMs=20000) == "idle"


def test_pending_confirmation_never_aborts():
    """等用户确认/外部执行时后端确实在等，不得因为静默而中止本轮。"""
    assert _decide(pendingConfirmation=True, idleMs=10**9, remoteRunActive=True) is None
    assert _decide(pendingConfirmation=True, idleMs=10**9, remoteRunActive=False) is None


def test_never_aborts_when_no_stream_is_in_flight():
    assert _decide(streamInFlight=False, idleMs=10**9, remoteRunActive=False) is None


# --- 接线契约 ---

def test_embed_chat_wires_the_watchdog_into_the_stream_loop():
    source = _embed_source()
    assert "decideStreamAbort" in source
    assert "startStreamLivenessWatch" in source
    assert "stopStreamLivenessWatch" in source
    # 每收到一帧都要续命，否则正常长任务会被误杀。
    assert "streamLastFrameAt = Date.now()" in source
    # 到点必须真的 abort 本地流，而不是只打日志。
    assert "abortController?.abort()" in source
    # 流期间低频探测 run-status，用于发现「后端已结束、流还在途」。
    assert "STREAM_RUN_STATUS_PROBE_INTERVAL_MS" in source


def test_watchdog_is_cleared_in_finally():
    source = _embed_source()
    # 主流 finally 紧邻 BOTTOM_THRESHOLD_PX 定义之前，取最后一个 `} finally {` 更稳。
    tail = source.split("const BOTTOM_THRESHOLD_PX", 1)[0]
    finally_block = tail.rsplit("} finally {", 1)[1]
    assert "stopStreamLivenessWatch()" in finally_block


def test_watchdog_abort_is_not_reported_as_user_stop():
    """看门狗中止要能被识别出来，不能追加「[用户终止]」误导用户。"""
    source = _embed_source()
    abort_block = source.split('if (e.name === "AbortError") {', 1)[1].split("} else if", 1)[0]
    assert "streamAbortReason" in abort_block
    # 「[用户终止]」必须落在 streamAbortReason 判断之后的 else 分支。
    assert abort_block.index("streamAbortReason") < abort_block.index("[用户终止]")
