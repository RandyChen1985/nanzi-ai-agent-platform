# 时间线单卡实时秒表 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让时间线上正在执行的那一步显示实时递增耗时，完成后冻结，等待用户操作时不计时。

**Architecture:** 纯前端三处改动：`processTimeline.ts` 为 pending log 补 `started_at` 基准并新增纯函数 `resolveLiveTimerLogId`；`ChatExecutionTimeline.vue` 把 ticker 泛化为「有 live 项或预热时运行」，并把实时值接入既有的 `formatTimelineDuration`，使 5 处模板零改动。

**Tech Stack:** Vue 3 + TypeScript（`vue-tsc --noEmit`）、pytest 前端契约与 node transpile 行为测试。

---

## 文件结构

- Modify: `frontend/src/utils/processTimeline.ts`：创建 pending log 时写 `started_at`；新增 `NON_LIVE_TIMER_CATEGORIES` 与 `resolveLiveTimerLogId`。
- Modify: `frontend/src/utils/agentscopeSseHandlers.ts`：改为从 `processTimeline` 导入 `NON_LIVE_TIMER_CATEGORIES`（删除本地重复定义）。
- Modify: `frontend/src/components/chat/ChatExecutionTimeline.vue`：ticker 泛化 + `liveTimerLogId` + `formatTimelineDuration` 接入实时值。
- Create: `tests/frontend/test_timeline_live_step_timer_behavior.py`：纯函数与 `upsertTimelineLog` 的行为测试。
- Modify: `tests/frontend/test_dataset_menu_loading_contract.py`：扩展 `test_thought_step_timer_contract` 锁接线。
- Modify: `tests/CHECKLIST.md`：登记交付记录。

---

## Task 1: 用失败测试锁定纯函数行为

**Files:**

- Create: `tests/frontend/test_timeline_live_step_timer_behavior.py`

- [ ] **Step 1: 写测试文件**

```python
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
    assert _run_typescript(
        """
const items = [
  { kind: "log", id: "t1", title: "工具完成: A", details: "", status: "success", execution_time_ms: 100, started_at: 1 },
  { kind: "log", id: "t2", title: "调用工具: B", details: "", status: "pending", category: "tool", started_at: Date.now() - 2500 },
];
return api.resolveLiveTimerLogId(items);
"""
    ) == "t2"


def test_live_timer_returns_null_when_nothing_pending():
    assert _run_typescript(
        """
const items = [
  { kind: "log", id: "t1", title: "工具完成: A", details: "", status: "success", started_at: 1 },
];
return api.resolveLiveTimerLogId(items);
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
return api.resolveLiveTimerLogId(items);
"""
    ) is None


def test_live_timer_finds_pending_log_inside_children():
    assert _run_typescript(
        """
const items = [
  { kind: "log", id: "parent", title: "委派智能体", details: "", status: "pending", category: "agent", started_at: Date.now() - 900,
    children: [{ kind: "log", id: "child", title: "调用工具: Read", details: "", status: "pending", category: "tool", started_at: Date.now() - 300 }] },
];
return api.resolveLiveTimerLogId(items);
"""
    ) == "child"


def test_live_timer_requires_a_numeric_started_at():
    assert _run_typescript(
        """
const items = [
  { kind: "log", id: "t1", title: "调用工具: Bash", details: "", status: "pending", category: "tool" },
];
return api.resolveLiveTimerLogId(items);
"""
    ) is None


def test_live_timer_ignores_pure_text_pending_items():
    assert _run_typescript(
        """
const items = [
  { kind: "text", id: "r1", textKind: "reasoning", content: "思考中", pending: true, started_at: Date.now() - 400 },
];
return api.resolveLiveTimerLogId(items);
"""
    ) is None


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
    """完成事件不带 started_at，不能把已记录的基准抹掉（否则秒表与冻结耗时都会失真）。"""
    result = _run_typescript(
        """
const target = {};
api.upsertTimelineLog(target, { id: "t1", title: "调用工具: Bash", status: "pending", category: "tool" });
const first = target.processTimeline[0].started_at;
api.upsertTimelineLog(target, { id: "t1", title: "工具完成: Bash", status: "success" });
return { first, second: target.processTimeline[0].started_at, status: target.processTimeline[0].status };
"""
    )
    assert result["second"] == result["first"]
    assert result["status"] == "success"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_timeline_live_step_timer_behavior.py -v`

Expected: `resolveLiveTimerLogId` 相关用例 FAIL（`api.resolveLiveTimerLogId is not a function`）；
最后一条 `upsert_sets_started_at_for_pending_logs_only` 的 pending 断言也 FAIL。

---

## Task 2: 实现纯函数与基准写入

**Files:**

- Modify: `frontend/src/utils/processTimeline.ts`

- [ ] **Step 1: 创建分支写入 started_at**

把 `upsertTimelineLog` 创建分支中的：

```ts
    execution_time_ms: data.execution_time_ms,
    started_at: data.started_at,
```

改为：

```ts
    execution_time_ms: data.execution_time_ms,
    // pending 卡必须在创建时刻留下基准，否则「执行中实时秒表」无从计算：
    // msg.logs 侧已在 EmbedChat 里这么做，processTimeline 侧此前一直缺失。
    started_at: data.started_at ?? (data.status === "pending" ? Date.now() : undefined),
```

- [ ] **Step 2: 新增常量与纯函数**

在 `upsertTimelineLog` 之前（`isReasoningContentExpanded` 之后）新增：

```ts
/** 等待用户操作的挂起类别：机器没在跑，不该展示实时秒表。 */
export const NON_LIVE_TIMER_CATEGORIES: ReadonlySet<string> = new Set([
  "permission",
  "external",
]);

function isEligibleLiveTimerLog(item: ProcessTimelineLogItem): boolean {
  if (item.status !== "pending") return false;
  if (item.category && NON_LIVE_TIMER_CATEGORIES.has(item.category)) return false;
  return typeof item.started_at === "number" && Number.isFinite(item.started_at);
}

/**
 * 返回当前应当在时间线上走实时秒表的那条 log id（深度优先、按视觉顺序取最后一条挂起项）。
 *
 * 只有最后一条挂起项走秒表：历史遗留的 pending 若也计时，会出现多个秒表同时跑。
 * 最后一条挂起项是「等待用户确认/外部执行」时返回 null —— 那时机器并没有在执行。
 */
export function resolveLiveTimerLogId(
  items: ProcessTimelineItem[] | undefined,
): string | null {
  let lastPending: ProcessTimelineLogItem | null = null;

  const walk = (list: ProcessTimelineItem[] | undefined) => {
    for (const item of list || []) {
      if (!item) continue;
      if (item.kind === "log") {
        if (item.status === "pending") lastPending = item;
      }
      walk(item.children as ProcessTimelineItem[] | undefined);
    }
  };

  walk(items);

  if (!lastPending) return null;
  if (!isEligibleLiveTimerLog(lastPending)) return null;
  return String(lastPending.id);
}
```

- [ ] **Step 3: 运行确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_timeline_live_step_timer_behavior.py -v`

Expected: 8 passed。

---

## Task 3: 复用常量，消除重复定义

**Files:**

- Modify: `frontend/src/utils/agentscopeSseHandlers.ts`

- [ ] **Step 1: 改为导入共享常量**

删除该文件中的：

```ts
const NON_LIVE_TIMER_CATEGORIES = new Set(["permission", "external"]);
```

并在文件顶部的 `./processTimeline` 导入块中加入 `NON_LIVE_TIMER_CATEGORIES`
（保持现有导入项与字母序风格）。

- [ ] **Step 2: 运行既有回归确认未破坏**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_dataset_menu_loading_contract.py -v`

Expected: 全部 PASS（`isLiveThoughtStepTimer` 仍存在于该文件，契约不变）。

---

## Task 4: 组件接线并锁死

**Files:**

- Modify: `frontend/src/components/chat/ChatExecutionTimeline.vue`
- Modify: `tests/frontend/test_dataset_menu_loading_contract.py`

- [ ] **Step 1: 先扩展契约测试（RED）**

把 `test_thought_step_timer_contract` 改为：

```python
def test_thought_step_timer_contract():
    handlers = _source("frontend/src/utils/agentscopeSseHandlers.ts")
    assert "finalizePendingStreamLogs" in handlers
    assert "isLiveThoughtStepTimer" in handlers
    assert "findPendingAgentReplyLog" in handlers
    embed = _source("frontend/src/views/EmbedChat.vue")
    timeline = _source("frontend/src/components/chat/ChatExecutionTimeline.vue")
    assert "finalizeAllPendingStreamLogs(agentMsg.value)" in embed
    assert "function formatDuration(duration?: number | null)" in timeline
    # 单卡实时秒表必须真的接线：此前 isLiveThoughtStepTimer 只被定义、无人引用，
    # 导致长耗时工具调用在执行期间那一行完全不显示耗时。
    assert "resolveLiveTimerLogId" in timeline
    assert "liveTimerLogId" in timeline
    assert "needsLiveTick" in timeline
    assert "tickNow.value" in timeline
```

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_dataset_menu_loading_contract.py -k thought_step_timer -v`

Expected: FAIL（组件里还没有 `resolveLiveTimerLogId` / `needsLiveTick`）。

- [ ] **Step 2: 导入纯函数**

在 `ChatExecutionTimeline.vue` 的 `@/utils/processTimeline` 导入块中，`mergeTimelineLogs` 之后加入：

```ts
  resolveLiveTimerLogId,
```

- [ ] **Step 3: 拆分预热 watch 并泛化 ticker**

把原来的：

```ts
watch(
  () => isWorkspacePrewarming.value,
  (prewarming) => {
    if (prewarming) {
      if (prewarmStartedAtMs.value === null) prewarmStartedAtMs.value = Date.now();
      if (!tickTimer) {
        tickTimer = setInterval(() => { tickNow.value += 1; }, 500);
      }
    } else if (tickTimer) {
      clearInterval(tickTimer);
      tickTimer = null;
      prewarmStartedAtMs.value = null;
    }
  },
  { immediate: true },
);
```

替换为：

```ts
watch(
  () => isWorkspacePrewarming.value,
  (prewarming) => {
    prewarmStartedAtMs.value = prewarming
      ? (prewarmStartedAtMs.value ?? Date.now())
      : null;
  },
  { immediate: true },
);

/** 当前该走实时秒表的那条步骤：只有最后一条挂起项、且不是等待用户操作。 */
const liveTimerLogId = computed(() => {
  void tickNow.value;
  return resolveLiveTimerLogId(items.value);
});

/** 预热文案与单卡秒表共用同一个 500ms ticker；都不需要时自动停表。 */
const needsLiveTick = computed(
  () => isWorkspacePrewarming.value || liveTimerLogId.value !== null,
);

watch(
  needsLiveTick,
  (needs) => {
    if (needs) {
      if (!tickTimer) {
        tickTimer = setInterval(() => { tickNow.value += 1; }, 500);
      }
    } else if (tickTimer) {
      clearInterval(tickTimer);
      tickTimer = null;
    }
  },
  { immediate: true },
);
```

注意：`liveTimerLogId` / `needsLiveTick` 必须定义在 `items` 之后（`items` 已在
约 687 行声明），且 `prewarmElapsedMs` 仍依赖 `tickNow`，行为不变。

- [ ] **Step 4: 把实时值接入耗时渲染**

把：

```ts
function formatTimelineDuration(item: ProcessTimelineLogItem): string {
  const duration = formatDuration(item.execution_time_ms);
  return isRouteGroup(item) && duration ? `总计 ${duration}` : duration;
}
```

改为：

```ts
/** 命中实时秒表的那一条返回已执行时长；其余维持冻结耗时。 */
function liveTimerDuration(item: ProcessTimelineLogItem): string {
  if (item.status !== "pending") return "";
  if (String(item.id) !== liveTimerLogId.value) return "";
  const startedAt = item.started_at;
  if (typeof startedAt !== "number" || !Number.isFinite(startedAt)) return "";
  return formatDuration(Math.max(1, Date.now() - startedAt));
}

function formatTimelineDuration(item: ProcessTimelineLogItem): string {
  const live = liveTimerDuration(item);
  if (live) return live;
  const duration = formatDuration(item.execution_time_ms);
  return isRouteGroup(item) && duration ? `总计 ${duration}` : duration;
}
```

5 处模板（170 / 254 / 362 / 453 / 527 行）调用 `formatTimelineDuration`，因此
无需改动即自动获得实时秒表；完成后 `execution_time_ms` 优先，数字冻结。

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_dataset_menu_loading_contract.py -k thought_step_timer -v`

Expected: PASS。

---

## Task 5: 全量回归与记录

**Files:**

- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 前端契约与行为测试全量**

Run: `pytest --confcutdir=tests/frontend tests/frontend -q`

Expected: 全绿（基线为 1219 passed 量级；本任务新增 8 条行为用例）。

- [ ] **Step 2: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`

Expected: 退出码 0 且无输出（若基线本有错误，则错误集合与改动前逐条一致、零新增）。

- [ ] **Step 3: 登记 CHECKLIST**

在 `tests/CHECKLIST.md` 表格首行插入本次交付记录（沿用现有列：特性 / 涉及核心文件 /
方案设计与测试闭环说明 / 验收状态 / 交付日期），说明：`isLiveThoughtStepTimer`
此前是无人引用的死代码、`processTimeline` 缺 `started_at` 基准、三处改动、8 条行为
测试先 RED 后 GREEN、`vue-tsc` 结果。

- [ ] **Step 4: 确认改动范围**

Run: `git status --short`

Expected: 仅 3 个前端源文件、2 个测试文件与 `tests/CHECKLIST.md`（以及本 spec/plan
文档）。**不执行 git commit**（由用户决定）。

---

## Self-Review

- **Spec 覆盖**：目标 1（基准）→ Task 2 Step 1；目标 2（判定）→ Task 2 Step 2；
  目标 3（渲染）→ Task 4；范围外的三项均未出现在任何任务中。
- **占位符**：无 TBD/TODO；每个改动步骤都给出完整替换代码。
- **命名一致性**：`NON_LIVE_TIMER_CATEGORIES`、`resolveLiveTimerLogId`、
  `liveTimerLogId`、`needsLiveTick`、`liveTimerDuration`、`formatTimelineDuration`
  在测试断言与实现中完全一致。
- **顺序依赖**：`liveTimerLogId` 依赖 `items`（约 687 行）与 `tickNow`（797 行），
  实现时须放在这两者之后；计划已在 Task 4 Step 3 注明。
