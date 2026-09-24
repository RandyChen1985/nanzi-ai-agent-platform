"""时间线「单卡实时秒表」的纯函数行为契约。

秒表基准与「该给哪一条走秒表」的判定必须可单测：此前
`isLiveThoughtStepTimer` 写好了却从未被组件引用，正是因为没有行为测试兜住。
这里直接用仓库既有的 node transpile harness 跑 `processTimeline.ts`。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

MODULE = "frontend/src/utils/processTimeline.ts"


def _run_typescript(expression: str):
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(MODULE)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
const api = moduleRef.exports;
const result = await (async () => {{ {expression} }})();
process.stdout.write(JSON.stringify(result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_live_timer_picks_the_last_pending_log():
    """同时存在历史挂起与当前挂起时，只有最后一条走秒表。"""
    assert _run_typescript(
        """
const items = [
  { kind: "log", id: "t1", title: "工具完成: A", details: "", status: "success", execution_time_ms: 100, started_at: 1 },
  { kind: "log", id: "t2", title: "调用工具: B", details: "", status: "pending", category: "tool", started_at: Date.now() - 2500 },
];
return typeof api.resolveLiveTimerLogId === "function" ? api.resolveLiveTimerLogId(items) : "MISSING_RESOLVER";
"""
    ) == "t2"


def test_live_timer_returns_null_when_nothing_pending():
    assert _run_typescript(
        """
const items = [
  { kind: "log", id: "t1", title: "工具完成: A", details: "", status: "success", started_at: 1 },
];
return typeof api.resolveLiveTimerLogId === "function" ? api.resolveLiveTimerLogId(items) : "MISSING_RESOLVER";
"""
    ) is None


def test_live_timer_ignores_pending_permission_or_external_waits():
    """等待用户确认/外部执行时机器没在跑，不该继续走秒表。"""
    assert _run_typescript(
        """
const items = [
  { kind: "log", id: "t1", title: "调用工具: Bash", details: "", status: "pending", category: "tool", started_at: Date.now() - 5000 },
  { kind: "log", id: "p1", title: "等待授权", details: "", status: "pending", category: "permission", started_at: Date.now() - 100 },
];
return typeof api.resolveLiveTimerLogId === "function" ? api.resolveLiveTimerLogId(items) : "MISSING_RESOLVER";
"""
    ) is None


def test_live_timer_finds_pending_log_inside_children():
    """委派子智能体的步骤嵌在 children 里，也要能找到。"""
    assert _run_typescript(
        """
const items = [
  { kind: "log", id: "parent", title: "委派智能体", details: "", status: "pending", category: "agent", started_at: Date.now() - 900,
    children: [{ kind: "log", id: "child", title: "调用工具: Read", details: "", status: "pending", category: "tool", started_at: Date.now() - 300 }] },
];
return typeof api.resolveLiveTimerLogId === "function" ? api.resolveLiveTimerLogId(items) : "MISSING_RESOLVER";
"""
    ) == "child"


def test_live_timer_requires_a_numeric_started_at():
    """没有基准就算不出耗时，宁可不动也不显示假数字。"""
    assert _run_typescript(
        """
const items = [
  { kind: "log", id: "t1", title: "调用工具: Bash", details: "", status: "pending", category: "tool" },
];
return typeof api.resolveLiveTimerLogId === "function" ? api.resolveLiveTimerLogId(items) : "MISSING_RESOLVER";
"""
    ) is None


def test_live_timer_ignores_pure_text_pending_items():
    """深度思考/过程叙述是 text 项，不属于本次范围。"""
    assert _run_typescript(
        """
const items = [
  { kind: "text", id: "r1", textKind: "reasoning", content: "思考中", pending: true, started_at: Date.now() - 400 },
];
return typeof api.resolveLiveTimerLogId === "function" ? api.resolveLiveTimerLogId(items) : "MISSING_RESOLVER";
"""
    ) is None


def test_live_timer_duration_counts_elapsed_for_the_live_item():
    assert _run_typescript(
        """
const item = { kind: "log", id: "t2", title: "调用工具: Bash", details: "", status: "pending", category: "tool", started_at: 1000 };
return typeof api.resolveLiveTimerDurationMs === "function"
  ? api.resolveLiveTimerDurationMs(item, "t2", 3500)
  : "MISSING_DURATION";
"""
    ) == 2500


def test_live_timer_duration_frozen_once_finished():
    """完成后由 execution_time_ms 接管，实时值必须让位，否则数字会一直涨。"""
    assert _run_typescript(
        """
const item = { kind: "log", id: "t2", title: "工具完成: Bash", details: "", status: "success", category: "tool",
  started_at: 1000, execution_time_ms: 9166 };
return typeof api.resolveLiveTimerDurationMs === "function"
  ? api.resolveLiveTimerDurationMs(item, "t2", 99999)
  : "MISSING_DURATION";
"""
    ) is None


def test_live_timer_duration_only_for_the_live_item():
    """历史遗留的 pending 即使带 started_at 也不计时，避免多个秒表同时跑。"""
    assert _run_typescript(
        """
const item = { kind: "log", id: "t1", title: "调用工具: Read", details: "", status: "pending", category: "tool", started_at: 1000 };
return typeof api.resolveLiveTimerDurationMs === "function"
  ? api.resolveLiveTimerDurationMs(item, "t2", 3500)
  : "MISSING_DURATION";
"""
    ) is None


def test_live_timer_duration_never_goes_negative():
    """客户端时钟回拨时不能显示负数或 0ms。"""
    assert _run_typescript(
        """
const item = { kind: "log", id: "t2", title: "调用工具: Bash", details: "", status: "pending", category: "tool", started_at: 5000 };
return typeof api.resolveLiveTimerDurationMs === "function"
  ? api.resolveLiveTimerDurationMs(item, "t2", 4200)
  : "MISSING_DURATION";
"""
    ) == 1


def test_upsert_sets_started_at_for_pending_logs_only():
    result = _run_typescript(
        """
const target = {};
api.upsertTimelineLog(target, { id: "t1", title: "调用工具: Bash", status: "pending", category: "tool" });
api.upsertTimelineLog(target, { id: "t2", title: "工具完成: Bash", status: "success", category: "tool" });
return target.processTimeline.map(item => ({
  id: item.id,
  hasStartedAt: typeof item.started_at === "number" && item.started_at > 0,
}));
"""
    )
    assert result == [
        {"id": "t1", "hasStartedAt": True},
        {"id": "t2", "hasStartedAt": False},
    ]


def test_upsert_does_not_overwrite_started_at_with_empty_update():
    """完成事件不带 started_at，不能把已记录的基准抹掉（否则实时值与冻结耗时都会失真）。"""
    result = _run_typescript(
        """
const target = {};
api.upsertTimelineLog(target, { id: "t1", title: "调用工具: Bash", status: "pending", category: "tool" });
const first = target.processTimeline[0].started_at;
api.upsertTimelineLog(target, { id: "t1", title: "工具完成: Bash", status: "success" });
return { first: first ?? null, second: target.processTimeline[0].started_at ?? null, status: target.processTimeline[0].status };
"""
    )
    assert isinstance(result["first"], (int, float)) and result["first"] > 0
    assert result["second"] == result["first"]
    assert result["status"] == "success"


def test_live_timer_survives_real_turn_merge_pipeline():
    """复现真实一轮的完整链路（对照界面截图）：组件先 merge，再从合并结果判定。

    真实时序里 processTimeline 与 msg.logs 两套 store 各持一份同 id 的工具卡，
    组件用 mergeTimelineLogs 合并后才渲染。此前只单测了纯函数，
    没覆盖「合并 + 分组之后仍能定位到正在跑的那一条」这条真实路径。
    步骤总数同时用作场景校验：与截图一致的 13 步。
    """
    result = _run_typescript(
        """
const now = Date.now();
const prep = {
  kind: "log", id: "preparation:auth_context_capability", title: "鉴权及上下文与能力准备",
  details: "", status: "success", execution_time_ms: 127,
  children: Array.from({ length: 7 }, (_, i) => ({
    kind: "log", id: "prep_" + i, title: "准备 " + i, details: "", status: "success", execution_time_ms: 10,
  })),
};
const timeline = [
  prep,
  { kind: "log", id: "tool_availability", title: "工具可用性检查", details: "ok", status: "success" },
  { kind: "log", id: "session_state", title: "会话状态已更新", details: "ok", status: "success" },
  { kind: "log", id: "main_agent", title: "主专家开始处理", details: "", status: "pending", category: "agent", started_at: now - 9000 },
  { kind: "log", id: "model_call_1", title: "模型调用: DeepSeek-V4-Flash", details: "", status: "success", execution_time_ms: 3300 },
  { kind: "log", id: "call_bash", title: "调用工具: Bash", details: "", status: "pending", category: "tool", started_at: now - 5000 },
];
// msg.logs 是另一套 store：同 id、同 pending、同样带基准
const logs = [
  { id: "prep_0", title: "准备 0", details: "", status: "success" },
  { id: "main_agent", title: "主专家开始处理", details: "", status: "pending", category: "agent", started_at: now - 9000 },
  { id: "model_call_1", title: "模型调用: DeepSeek-V4-Flash", details: "", status: "success", execution_time_ms: 3300 },
  { id: "call_bash", title: "调用工具: Bash", details: "", status: "pending", category: "tool", started_at: now - 5000 },
];
const merged = api.mergeTimelineLogs(timeline, logs);
const items = api.groupRouteTimelineItems(merged, undefined);
const liveId = api.resolveLiveTimerLogId(items);
const stack = [...items];
let liveItem = null;
while (stack.length) {
  const item = stack.shift();
  if (item.kind === "log" && String(item.id) === String(liveId)) liveItem = item;
  if (item.children) stack.push(...item.children);
}
const liveMs = liveItem ? api.resolveLiveTimerDurationMs(liveItem, liveId, now) : null;
return {
  liveId,
  startedAtIsFinite: liveItem ? Number.isFinite(liveItem.started_at) : false,
  liveMsAround5s: typeof liveMs === "number" && liveMs >= 4000 && liveMs < 6000,
  stepCount: api.countTimelineSteps(items),
};
"""
    )
    assert result["liveId"] == "call_bash"
    assert result["startedAtIsFinite"] is True
    assert result["liveMsAround5s"] is True
    # 场景自校验：与截图一致的 13 步，证明没有隐藏的靠后项抢走「最后一条挂起项」
    assert result["stepCount"] == 13

