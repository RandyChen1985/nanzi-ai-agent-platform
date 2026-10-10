# 挂起步骤动态安抚文案 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让任意「机器正在推进」的挂起步骤（模型调用、工具执行、检索等）在等待超过阈值后显示按耗时递进的安抚文案，消除「只有一个不断变大的秒表、看不出在做什么」的傻等感。

**Architecture:** 文案规则抽成 `processTimeline.ts` 的纯函数 `resolvePendingHint(item, now, isLiveTimer)`；渲染抽成 `TimelinePendingHint.vue`；`ChatExecutionTimeline.vue` 里 5 份复制粘贴的沙箱专属渲染片段统一替换为该组件。纯前端改动，后端零改动。

**Tech Stack:** Vue 3 + TypeScript + Vite 7 + Tailwind 3；测试用项目既有的 Node 真跑机制（`_run_typescript`，基于 `typescript.transpileModule`）。

**Spec:** [`docs/superpowers/specs/2026-10-10-pending-step-live-hint-design.md`](../specs/2026-10-10-pending-step-live-hint-design.md)

---

## 交付约定（本项目特有，优先于 skill 默认流程）

1. **不得执行 `git commit`**。每个任务末尾的「检查点」只做汇报与 commit message 草稿。
2. **不得执行 `./dev.sh`** 或任何服务启停脚本。
3. 前端契约测试必须带 `--confcutdir=tests/frontend`：
   `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/<file> -v`
4. 类型检查：`cd frontend && NODE_OPTIONS="--max-old-space-size=4096" ./node_modules/.bin/vue-tsc -b --pretty false`，基线 **62 条**，不得新增。
5. 新增注释与文案用中文。

## 与 spec 的九处实现层补充（评审计划时请一并确认）

这三处是写计划时发现的、spec 未写到但必须处理的点：

**补充 1（关键，不做等于功能没做）：样式必须随组件一起搬走。**

`workspace-prewarm-bar` 的样式定义在 `ChatExecutionTimeline.vue:1243` 的 `<style scoped>` 里（`.workspace-prewarm-bar` 在 `:1260-1267`、`::after` 与动画在 `:1268-1278`、`@keyframes workspace-prewarm-slide` 在 `:1279-1282`）。抽成子组件后，子组件**内部**的 `span` 不会带父组件的 scope id，`.workspace-prewarm-bar[data-v-父]` 不再匹配 → **动效条会完全失效**（宽度、圆角、底色、滑动动画全丢），而源码契约却照样通过，是典型的假绿。因此样式随组件迁移，并在契约里加护栏（Task 2 Step 1 与 Task 4 的变异 M6）。

`:1284-1294` 的 `@media (prefers-reduced-motion: reduce)` 块**同时含两类规则，必须拆成两半**：`.thought-status-dot` 那半留在 timeline（别处仍在用 `thought-status-dot`），`.workspace-prewarm-bar::after` 那半随组件走。

**补充 2：`prewarmStartedAtMs` 与其 watch 是连带死代码，一并删除。**

`:916` 的 `prewarmStartedAtMs` 与 `:920-928` 的 watch 唯一消费者是 `prewarmElapsedMs`；后者在 Task 3 被删除后它们就没有消费者了。删掉三个 computed 而留着这两个 ref/watch 会留下无人读写的状态。`findWorkspacePrewarmPending`（`:901-913`）与 `isWorkspacePrewarming`（`:918`）**保留**——前者是 tick 启动条件的一部分，后者被 `needsLiveTick` 使用。

**补充 3：契约断言必须写成 `v-if="isWorkspacePrewarmPending(` 而不是 `isWorkspacePrewarmPending(`。**

后者在 `findWorkspacePrewarmPending` 的 `walk` 里（`:906`）仍需保留，用它做断言会永远失败。用带 `v-if="` 前缀的形式精确锁定「5 处模板绑定已全部替换」。

**补充 4（执行期发现）：纯函数入参不得用影子类型。**

初稿写成 `{ id?: string | number; category?: string; status?: string; started_at?: number }`，与 `ProcessTimelineLogItem`（`started_at?: number | null`，`:79`）不兼容，`vue-tsc` 由基线 62 涨到 63（报在 `TimelinePendingHint.vue(13,48)` TS2345）。改用同文件的 `ProcessTimelineLogItem | undefined` 后回到 62。行为测试传的是 JS 对象字面量（`transpileModule` 只擦除类型、不做检查），因此不受影响。

**补充 5（执行期发现）：时钟回拨用例必须用沙箱项观测。**

初稿用 `model` 类别做 `now = started_at - 5000`，`elapsedMs` 归 0 后未满 3 秒阈值 → 函数返回 `null`，取 `.elapsedSeconds` 直接抛错。改用沙箱项（第一档 `afterMs: 0`）才能观测到「回拨归 0、不为负」。这是测试构造错误，与实现无关。

**补充 6（用户复测反馈）：`now` 必须是时间戳，不能是 `tickNow`。**

初版 5 处写成 `:now="tickNow"`。`tickNow` 是每 500ms 加 1 的渲染触发器（计数器），秒表能走是因为 `liveTimerDuration` 用 `Date.now()`；计数器当 `now` 会让 `elapsedMs` 恒为 0，文案永不显示（沙箱档因 `afterMs: 0` 停在第一档、不再递进）。修法是父组件新增 `nowMs = computed(() => { void tickNow.value; return Date.now(); })` 并改 5 处绑定；护栏为契约断言 `count(':now="nowMs"') == 5` 与变异 M7。这也是本次唯一一个「单元测试全绿、SSR 渲染全绿、但线上不显示」的缺陷，根因是父子接线从未被断言覆盖。

**补充 7（用户复测反馈）：`agent` 类别不显示文案。**

「主专家开始处理」（`agentscopeSseHandlers.ts:505`，`category: "agent"`）这类阶段行标题本身已自解释，叠加「正在处理…」是同义重复。它仍要保留实时秒表，因此单列 `PENDING_HINT_SILENT_CATEGORIES = new Set([...NON_LIVE_TIMER_CATEGORIES, "agent"])`，而不是并入 `NON_LIVE_TIMER_CATEGORIES`（后者同时决定「不显示秒表」）。护栏：行为测试的 `agent` 用例 + 变异 M8。

**补充 8（用户复测反馈）：指示器换成波形，且与沙箱彻底分开。**

用户反馈「不能跟沙箱拉起一样的效果」。初版所有类别共用沙箱的滑动进度条，问题有两个：样式同源会被读成「又在拉沙箱」；且与行首本来就有的 `thought-status-dot` 同时动，两个动效说同一件事。定案是分两种指示器：**沙箱保留横向亮蓝进度条**（它的专属签名，用户明确要求不动），**其余类别改用石板灰波形**（三根竖条，`slate-400` / `slate-500`）。选择权留在纯函数：`PendingHint.indicator` 取 `"bar" | "wave"`。波形起伏用 `scaleY` 而非 `height`（避免每帧布局），深色用 Tailwind `dark:` 变体（避开 scoped 里手写 `.dark` 会命中所有深色祖先的坑）。护栏：`indicator` 行为用例 + 契约断言 + 变异 M9/M10/M11。

**补充 9（用户复测反馈）：第一档文案不再声称停在早期阶段。**

`model` 第一档「正在理解你的问题…」→「正在思考…」、第二档「正在组织回答…」→「正在生成回答…」；`knowledge` 第一档「正在查阅知识库资料…」→「正在整理检索结果…」、第二档「正在比对相关资料…」→「正在组织回答…」。理由是 3 秒时模型早已在生成 token、知识库行出现时检索已完成，原措辞把阶段说晚了。护栏：两条精确断言（`== "正在思考…"`、`== "正在整理检索结果…"`）+ 变异 M12。

---

## 文件结构

| 文件 | 变更 | 职责 |
| --- | --- | --- |
| `frontend/src/utils/processTimeline.ts` | 新增类型、文案表与纯函数 | `PendingHintStage` / `PendingHint` / `PENDING_HINT_STAGE_LABELS` / `resolvePendingHint`；既有沙箱三个导出保持不变 |
| `frontend/src/components/chat/TimelinePendingHint.vue` | 新建 | 挂起项安抚文案与动效条的单一渲染单元（含自己的 scoped 样式） |
| `frontend/src/components/chat/ChatExecutionTimeline.vue` | 模板 5 处替换 + 删除 4 个 prewarm 状态 + 样式段搬移 | 接入新组件 |
| `tests/frontend/test_chat_shared_helpers_behavior.py` | 新增 3 个行为测试 + 改写 1 个既有测试 | 文案规则与接入点契约 |
| `tests/CHECKLIST.md` | 追加 1 行 | 本次交付记录 |

---

## Task 1: 纯函数 `resolvePendingHint`

**Files:**
- Modify: `frontend/src/utils/processTimeline.ts`（在 `workspacePrewarmElapsedSeconds` 之后、`formatTimelineTitle` 之前插入）
- Test: `tests/frontend/test_chat_shared_helpers_behavior.py`（追加 3 个测试）

- [x] **Step 1: 写失败测试**

在 `tests/frontend/test_chat_shared_helpers_behavior.py` **末尾**追加：

```python
def test_pending_hint_resolves_stage_labels_by_category_and_threshold():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const t = 1000000;
const at = (ms) => t + ms;
const model = { id: 'model_call_1', category: 'model', status: 'pending', started_at: t };
return {
  belowThreshold: api.resolvePendingHint(model, at(2999), true),
  firstStage: api.resolvePendingHint(model, at(3000), true),
  secondStage: api.resolvePendingHint(model, at(10000), true),
  thirdStage: api.resolvePendingHint(model, at(20000), true),
  farLong: api.resolvePendingHint(model, at(300000), true),
};
""",
    )

    assert result["belowThreshold"] is None, "未满 3 秒不得显示文案（否则快步骤会闪）"
    assert result["firstStage"]["label"] == "正在思考…"
    assert result["secondStage"]["label"] == "正在生成回答…"
    assert "内容较长" in result["thirdStage"]["label"]
    assert result["farLong"]["label"] == result["thirdStage"]["label"], "超过最后一档后不再变化"


def test_pending_hint_covers_each_category_and_suppresses_waiting_states():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const t = 1000000;
const at = (ms) => t + ms;
const mk = (category, extra) => Object.assign(
  { id: 'x', category, status: 'pending', started_at: t }, extra || {}
);
return {
  knowledge: api.resolvePendingHint(mk('knowledge'), at(3000), true).label,
  tool: api.resolvePendingHint(mk('tool'), at(3000), true).label,
  otherCategory: api.resolvePendingHint(mk('sql'), at(3000), true).label,
  noCategory: api.resolvePendingHint(mk(undefined), at(3000), true).label,
  permission: api.resolvePendingHint(mk('permission'), at(60000), true),
  external: api.resolvePendingHint(mk('external'), at(60000), true),
  success: api.resolvePendingHint({ id: 'x', category: 'model', status: 'success', started_at: t }, at(60000), true),
  noStartedAt: api.resolvePendingHint({ id: 'x', category: 'model', status: 'pending' }, at(60000), true),
  undefinedItem: api.resolvePendingHint(undefined, at(60000), true),
};
""",
    )

    assert result["knowledge"] == "正在整理检索结果…"
    assert "正在执行该步骤" in result["tool"]
    assert "正在处理" in result["otherCategory"], "未知类别落兜底文案"
    assert "正在处理" in result["noCategory"]
    assert result["permission"] is None, "等待用户确认时机器没在跑，不得显示「正在…」"
    assert result["external"] is None, "等待外部执行时机器没在跑，不得显示「正在…」"
    assert result["success"] is None
    assert result["noStartedAt"] is None
    assert result["undefinedItem"] is None


def test_pending_hint_keeps_sandbox_immediate_and_marks_redundant_elapsed_prefix():
    result = _run_typescript(
        "frontend/src/utils/processTimeline.ts",
        """
const t = 1000000;
const at = (ms) => t + ms;
const sandbox = { id: 'workspace:sandbox', status: 'pending', started_at: t };
const model = { id: 'm', category: 'model', status: 'pending', started_at: t };
const timer = api.resolvePendingHint(model, at(8000), true);
const legacy = api.resolvePendingHint(model, at(8000), false);
// 时钟回拨用沙箱项观测：通用类别回拨后未满阈值会返回 null，拿不到 elapsedSeconds
const rewind = api.resolvePendingHint(sandbox, at(-5000), false);
return {
  sandboxImmediate: api.resolvePendingHint(sandbox, at(0), true).label,
  sandboxMid: api.resolvePendingHint(sandbox, at(4000), true).label,
  sandboxLong: api.resolvePendingHint(sandbox, at(10000), true).label,
  exportedMid: api.workspacePrewarmStageLabel(4000),
  exportedLong: api.workspacePrewarmStageLabel(10000),
  timerShowElapsed: timer.showElapsed,
  timerSeconds: timer.elapsedSeconds,
  legacyShowElapsed: legacy.showElapsed,
  rewindSeconds: rewind.elapsedSeconds,
  rewindShowElapsed: rewind.showElapsed,
};
""",
    )

    assert "首次创建沙箱" in result["sandboxImmediate"], "沙箱保持 0 秒立即显示，不受 3 秒阈值约束"
    assert result["sandboxMid"] == result["exportedMid"], "沙箱文案必须与既有导出函数同源，不得复制第二份"
    assert result["sandboxLong"] == result["exportedLong"]
    assert result["timerShowElapsed"] is False, "正在走右侧秒表的行不再重复显示「已等待 Ns」"
    assert result["timerSeconds"] == 8
    assert result["legacyShowElapsed"] is True, "无秒表的历史挂起行仍要显示已等待时长"
    assert result["rewindSeconds"] == 0, "时钟回拨不得出现负数"
    assert result["rewindShowElapsed"] is True
```

- [x] **Step 2: 运行测试确认失败**

Run:
```bash
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_chat_shared_helpers_behavior.py -k pending_hint -v
```
Expected: `3 failed`，失败原因是 Node 侧报 `api.resolvePendingHint is not a function`（`_run_typescript` 用 `check=True`，会以 `CalledProcessError` 形式失败）。

- [x] **Step 3: 实现类型、文案表与纯函数**

在 `frontend/src/utils/processTimeline.ts` 中，`workspacePrewarmElapsedSeconds` 函数（`:151-154`）之后、`formatTimelineTitle` 之前插入：

```ts
export interface PendingHintStage {
  afterMs: number;
  label: string;
}

export interface PendingHint {
  label: string;
  elapsedSeconds: number;
  /** 该行是否应显示「已等待 Ns ·」前缀；正在走右侧实时秒表的行不重复显示。 */
  showElapsed: boolean;
}

/** 各类别挂起项的安抚文案档位；沙箱档直接引用既有常量，避免文案出现第二份副本。 */
const PENDING_HINT_STAGE_LABELS: Record<string, PendingHintStage[]> = {
  sandbox: PREWARM_STAGE_LABELS,
  model: [
    { afterMs: 3000, label: "正在思考…" },
    { afterMs: 10000, label: "正在生成回答…" },
    { afterMs: 20000, label: "内容较长，仍在生成中…" },
  ],
  knowledge: [
    { afterMs: 3000, label: "正在整理检索结果…" },
    { afterMs: 10000, label: "正在组织回答…" },
    { afterMs: 20000, label: "资料较多，仍在整理中…" },
  ],
  tool: [
    { afterMs: 3000, label: "正在执行该步骤…" },
    { afterMs: 10000, label: "执行耗时偏长，仍在进行中…" },
    { afterMs: 20000, label: "该步骤较慢，请继续稍候…" },
  ],
  default: [
    { afterMs: 3000, label: "正在处理…" },
    { afterMs: 10000, label: "仍在处理中，请稍候…" },
    { afterMs: 20000, label: "处理耗时较长，请继续稍候…" },
  ],
};

/**
 * 返回某条挂起项此刻应显示的安抚文案；返回 null 表示这行不该显示任何文案。
 *
 * 调用方每 500ms 随既有心跳重算，因此这里必须是纯函数、不持有状态。
 * 沙箱保持 0 秒立即显示（其文案本就是「首次创建」那一刻的叙述），
 * 其余类别的第一档即为通用阈值（3 秒）。
 */
export function resolvePendingHint(
  item: ProcessTimelineLogItem | undefined,
  now: number,
  isLiveTimer: boolean,
): PendingHint | null {
  if (!item || item.status !== "pending") return null;
  const startedAt = item.started_at;
  if (typeof startedAt !== "number" || !Number.isFinite(startedAt)) return null;

  const isSandbox = isWorkspacePrewarmPending(item);
  const category = String(item.category || "default");
  // 等待用户确认 / 等待外部执行时机器并没有在跑，显示「正在…」是误导
  if (!isSandbox && NON_LIVE_TIMER_CATEGORIES.has(category)) return null;

  const stages =
    (isSandbox ? PENDING_HINT_STAGE_LABELS.sandbox : PENDING_HINT_STAGE_LABELS[category])
    || PENDING_HINT_STAGE_LABELS.default;
  const elapsedMs = Math.max(0, now - startedAt);
  const first = stages?.[0];
  if (!first || elapsedMs < first.afterMs) return null;

  let label = first.label;
  for (const stage of stages) {
    if (elapsedMs >= stage.afterMs) label = stage.label;
  }
  return {
    label,
    elapsedSeconds: Math.max(0, Math.floor(elapsedMs / 1000)),
    showElapsed: !isLiveTimer,
  };
}
```

- [x] **Step 4: 运行测试确认通过**

Run:
```bash
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_chat_shared_helpers_behavior.py -k pending_hint -v
```
Expected: `3 passed`

- [x] **Step 5: 跑既有沙箱测试确认未破坏既有导出**

Run:
```bash
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_chat_shared_helpers_behavior.py -k "prewarm or preparation" -v
```
Expected: 全部 passed（`workspacePrewarmStageLabel` / `workspacePrewarmElapsedSeconds` / `isWorkspacePrewarmPending` 未改动）。

- [x] **Step 6: 检查点（不提交）**

建议 commit message 草稿：

```
feat(timeline): 新增 resolvePendingHint 纯函数与按类别的三档安抚文案
```

---

## Task 2: `TimelinePendingHint.vue` 组件（含样式随组件搬移）

**Files:**
- Create: `frontend/src/components/chat/TimelinePendingHint.vue`
- Test: `tests/frontend/test_chat_shared_helpers_behavior.py`（追加 1 个测试）

- [x] **Step 1: 写失败测试**

在 `tests/frontend/test_chat_shared_helpers_behavior.py` **末尾**追加：

```python
def test_timeline_pending_hint_component_owns_label_and_bar_style():
    """动效条样式必须与组件同址：父组件是 scoped 样式，抽出的子组件内部元素
    不会带父 scope id，样式若留在父组件里，动效条会静默失效。"""
    hint = (ROOT / "frontend/src/components/chat/TimelinePendingHint.vue").read_text(encoding="utf-8")

    assert "resolvePendingHint" in hint, "文案规则必须来自纯函数，不得在组件里重写一份"
    assert "workspace-prewarm-bar" in hint
    assert 'aria-live="polite"' in hint
    assert 'aria-busy="true"' in hint
    assert "showElapsed" in hint, "「已等待 Ns」前缀必须由 showElapsed 条件化"
    assert "已等待" in hint
    # 样式随组件走：缺了这两条，动效条会没有宽度与动画
    assert ".workspace-prewarm-bar {" in hint
    assert "@keyframes workspace-prewarm-slide" in hint
```

- [x] **Step 2: 运行测试确认失败**

Run:
```bash
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_chat_shared_helpers_behavior.py -k pending_hint_component -v
```
Expected: `1 failed`，`FileNotFoundError`（组件尚不存在）。

- [x] **Step 3: 创建组件**

创建 `frontend/src/components/chat/TimelinePendingHint.vue`：

```vue
<script setup lang="ts">
import { computed } from "vue";
import { resolvePendingHint, type ProcessTimelineLogItem } from "@/utils/processTimeline";

const props = defineProps<{
  item: ProcessTimelineLogItem;
  /** 由父级统一的 500ms 心跳注入，避免每个挂起行各起一个定时器。 */
  now: number;
  /** 该行是否正在走右侧实时秒表（用于省略重复的「已等待 Ns ·」前缀）。 */
  isLiveTimer: boolean;
}>();

const hint = computed(() => resolvePendingHint(props.item, props.now, props.isLiveTimer));
const prefix = computed(() =>
  hint.value?.showElapsed ? `已等待 ${hint.value.elapsedSeconds}s · ` : "",
);
</script>

<template>
  <div
    v-if="hint"
    class="shrink-0 flex items-center gap-1.5 text-[10px] text-sky-600 dark:text-sky-400"
    aria-live="polite"
    aria-busy="true"
  >
    <span class="workspace-prewarm-bar shrink-0" aria-hidden="true"></span>
    <span
      class="truncate max-w-[140px] sm:max-w-[320px] md:max-w-none"
      :title="prefix + hint.label"
    >{{ prefix }}{{ hint.label }}</span>
  </div>
</template>

<style scoped>
/* 挂起步骤的不确定进度条：滑块往复扫动，示意仍在推进。
   样式必须与唯一使用者同址：父组件是 scoped，作用不到本组件内部的元素。 */
.workspace-prewarm-bar {
  position: relative;
  overflow: hidden;
  width: 3.5rem;
  height: 0.25rem;
  border-radius: 9999px;
  background: rgba(14, 165, 233, 0.15);
}
.workspace-prewarm-bar::after {
  content: "";
  position: absolute;
  top: 0;
  bottom: 0;
  left: -40%;
  width: 40%;
  border-radius: 9999px;
  background: rgba(14, 165, 233, 0.75);
  animation: workspace-prewarm-slide 1.2s ease-in-out infinite;
}
@keyframes workspace-prewarm-slide {
  0% { left: -40%; }
  100% { left: 100%; }
}

@media (prefers-reduced-motion: reduce) {
  .workspace-prewarm-bar::after {
    animation: none;
    left: 0;
    width: 100%;
    opacity: 0.5;
  }
}
</style>
```

- [x] **Step 4: 运行测试确认通过**

Run:
```bash
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_chat_shared_helpers_behavior.py -k pending_hint -v
```
Expected: `4 passed`（3 条纯函数 + 1 条组件）

- [x] **Step 5: 检查点（不提交）**

建议 commit message 草稿：

```
feat(timeline): 新增 TimelinePendingHint 组件，动效条样式随组件搬移
```

---

## Task 3: 接入 `ChatExecutionTimeline.vue`（5 处）并清理死代码

**Files:**
- Modify: `frontend/src/components/chat/ChatExecutionTimeline.vue`
- Modify: `tests/frontend/test_chat_shared_helpers_behavior.py`（改写既有测试）

- [x] **Step 1: 先改写既有测试（契约先行）**

把 `tests/frontend/test_chat_shared_helpers_behavior.py` 里 `test_execution_timeline_renders_workspace_prewarm_progress` 的函数体整体替换为：

```python
def test_execution_timeline_renders_workspace_prewarm_progress():
    timeline = (ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue").read_text(encoding="utf-8")

    # prewarm 感知对时间线树做泛型递归：Bash 卡片挂在 narration(text) 下时，
    # prewarm 占位日志是其三层的子项，必须能被递归命中，否则预热 tick 不启动。
    assert "const walk = (node: ProcessTimelineItem): boolean => {" in timeline
    assert "if (walk(child)) return true;" in timeline
    assert "void tickNow.value" in timeline

    # 5 处（顶层 item / child / 两处 subStep / nestedStep）统一改为组件调用
    assert timeline.count("<TimelinePendingHint") == 5
    # 注意断言必须带 v-if=" 前缀：findWorkspacePrewarmPending 的 walk 里仍要调用
    # isWorkspacePrewarmPending，用裸函数名做断言会永远失败。
    assert 'v-if="isWorkspacePrewarmPending(' not in timeline
    # 旧的三个 prewarm 计算属性与连带死代码必须删净，避免留下无人读写的状态
    assert "prewarmStageLabel" not in timeline
    assert "prewarmElapsedSeconds" not in timeline
    assert "prewarmStartedAtMs" not in timeline
    # 动效条样式已随组件搬走，父组件不得再留一份
    assert ".workspace-prewarm-bar {" not in timeline

    hint = (ROOT / "frontend/src/components/chat/TimelinePendingHint.vue").read_text(encoding="utf-8")
    assert ".workspace-prewarm-bar {" in hint
    assert "@keyframes workspace-prewarm-slide" in hint
    assert "已等待" in hint
    assert "showElapsed" in hint

    process = (ROOT / "frontend/src/utils/processTimeline.ts").read_text(encoding="utf-8")
    assert "WORKSPACE_PREWARM_LOG_ID" in process
    assert "workspace:sandbox" in process
    assert "sandbox: PREWARM_STAGE_LABELS" in process, "沙箱文案必须是引用而非复制"
```

- [x] **Step 2: 运行测试确认失败**

Run:
```bash
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_chat_shared_helpers_behavior.py -k execution_timeline_renders_workspace_prewarm_progress -v
```
Expected: `1 failed`，断言 `timeline.count("<TimelinePendingHint") == 5` 实际为 `0`。

- [x] **Step 3: 加 import 并替换 5 处模板片段**

3a. 在 `ChatExecutionTimeline.vue` 的 `:599` 行（`import TimelineToolArgsBlock from "@/components/chat/TimelineToolArgsBlock.vue";`）**之后**插入一行：

```ts
import TimelinePendingHint from "@/components/chat/TimelinePendingHint.vue";
```

3b. 用下面这段脚本把 5 处片段整块替换为组件调用（按注释行 + `v-if` 变量名定位，自动继承各处缩进，避免手抄 5 段不同缩进出错）：

```bash
cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && .venv/bin/python - <<'PY'
import pathlib
import re

path = pathlib.Path("frontend/src/components/chat/ChatExecutionTimeline.vue")
source = path.read_text(encoding="utf-8")

pattern = re.compile(
    r'([ \t]*)<!-- 沙箱工作区预热进行中文案与动效条（[^\n]*?） -->\n'
    r'[ \t]*<div\n'
    r'[ \t]*v-if="isWorkspacePrewarmPending\((\w+)\)"\n'
    r'(?:[ \t]*[^\n]*\n)*?'
    r'[ \t]*</div>\n'
)


def repl(match: "re.Match[str]") -> str:
    indent, var = match.group(1), match.group(2)
    return (
        f"{indent}<!-- 挂起步骤安抚文案与动效条：机器正在推进时按耗时递进，"
        f"等待用户操作的类别不显示 -->\n"
        f"{indent}<TimelinePendingHint\n"
        f'{indent}  :item="{var}"\n'
        f'{indent}  :now="tickNow"\n'
        f'{indent}  :is-live-timer="String({var}.id) === liveTimerLogId"\n'
        f"{indent}/>\n"
    )


replaced, count = pattern.subn(repl, source)
assert count == 5, f"应替换 5 处，实际 {count} 处"
path.write_text(replaced, encoding="utf-8")
print(f"已替换 {count} 处")
PY
```
Expected: `已替换 5 处`

- [x] **Step 4: 删除四个 prewarm 状态与其 watch**

4a. 删除 `:915-918` 的 `prewarmStartedAtMs` 与保留 `tickNow` / `isWorkspacePrewarming`：

把
```ts
const tickNow = ref(0);
const prewarmStartedAtMs = ref<number | null>(null);
let tickTimer: ReturnType<typeof setInterval> | null = null;
const isWorkspacePrewarming = computed(() => findWorkspacePrewarmPending(items.value));

watch(
  () => isWorkspacePrewarming.value,
  (prewarming) => {
    prewarmStartedAtMs.value = prewarming
      ? (prewarmStartedAtMs.value ?? Date.now())
      : null;
  },
  { immediate: true },
);
```
改为
```ts
const tickNow = ref(0);
let tickTimer: ReturnType<typeof setInterval> | null = null;
const isWorkspacePrewarming = computed(() => findWorkspacePrewarmPending(items.value));
```

4b. 删除三个 prewarm computed：

把
```ts
const prewarmElapsedMs = computed(() => {
  // 依赖 tickNow 使已等待耗时与阶段安抚文案每 500ms 动态推进更新
  void tickNow.value;
  if (!isWorkspacePrewarming.value || prewarmStartedAtMs.value === null) return 0;
  return Math.max(0, Date.now() - prewarmStartedAtMs.value);
});
const prewarmElapsedSeconds = computed(() => workspacePrewarmElapsedSeconds(prewarmElapsedMs.value));
const prewarmStageLabel = computed(() => workspacePrewarmStageLabel(prewarmElapsedMs.value));
```
整段删除。

4c. 清理这两个函数从 import 中移除。查看 `:626-646` 从 `@/utils/processTimeline` 的 import 列表，把 `workspacePrewarmElapsedSeconds` 与 `workspacePrewarmStageLabel` 两个名字删掉（`isWorkspacePrewarmPending`、`resolveLiveTimerLogId`、`resolveLiveTimerDurationMs`、`PREPARATION_TIMELINE_PARENT_ID` 等其余名字保留）。

- [x] **Step 5: 把动效条样式从父组件搬走**

5a. 删除 `.workspace-prewarm-bar` 样式段（含 `::after` 与 keyframes），即把

```css
/* 沙箱工作区预热的不确定进度条：滑块往复扫动，示意仍在推进。 */
.workspace-prewarm-bar {
  position: relative;
  overflow: hidden;
  width: 3.5rem;
  height: 0.25rem;
  border-radius: 9999px;
  background: rgba(14, 165, 233, 0.15);
}
.workspace-prewarm-bar::after {
  content: "";
  position: absolute;
  top: 0;
  bottom: 0;
  left: -40%;
  width: 40%;
  border-radius: 9999px;
  background: rgba(14, 165, 233, 0.75);
  animation: workspace-prewarm-slide 1.2s ease-in-out infinite;
}
@keyframes workspace-prewarm-slide {
  0% { left: -40%; }
  100% { left: 100%; }
}

@media (prefers-reduced-motion: reduce) {
```
改为
```css
@media (prefers-reduced-motion: reduce) {
```

5b. 把 reduced-motion 块里的 bar 规则删掉，即把

```css
@media (prefers-reduced-motion: reduce) {
  .thought-status-dot {
    animation: none;
  }
  .workspace-prewarm-bar::after {
    animation: none;
    left: 0;
    width: 100%;
    opacity: 0.5;
  }
}
```
改为
```css
@media (prefers-reduced-motion: reduce) {
  .thought-status-dot {
    animation: none;
  }
}
```

（`.thought-status-dot` 与其 `@keyframes thought-status-breathe` 必须留在父组件——别处仍在用 `thought-status-dot`。）

- [x] **Step 6: 运行测试确认通过**

Run:
```bash
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_chat_shared_helpers_behavior.py -v
```
Expected: 全部 passed（含改写的 `test_execution_timeline_renders_workspace_prewarm_progress`）。

- [x] **Step 7: 类型检查与模板编译自检**

Run:
```bash
cd frontend && NODE_OPTIONS="--max-old-space-size=4096" ./node_modules/.bin/vue-tsc -b --pretty false 2>&1 | grep -c "error TS"
```
Expected: `62`（等于基线，不得新增）。若多于 62，`git diff` 检查是否有 import 名字漏删（`workspacePrewarmElapsedSeconds` 未使用会报 TS6133 一类错误）。

- [x] **Step 8: 检查点（不提交）**

建议 commit message 草稿：

```
refactor(timeline): 5 处挂起文案渲染收敛为 TimelinePendingHint 组件
```

---

## Task 4: 变异验证、CHECKLIST 与全量回归

**Files:**
- Modify: `tests/CHECKLIST.md`
- Verify only: 全部改动文件

- [x] **Step 1: 备份待变异的文件**

Run:
```bash
cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && \
cp frontend/src/utils/processTimeline.ts /tmp/pending_hint_process_timeline.ts && \
cp frontend/src/components/chat/TimelinePendingHint.vue /tmp/pending_hint_component.vue && \
cp frontend/src/components/chat/ChatExecutionTimeline.vue /tmp/pending_hint_timeline.vue && \
ls -l /tmp/pending_hint_*.ts /tmp/pending_hint_*.vue
```
Expected: 三个文件都在。

> 用 `cp` 备份而**不用 `git checkout --`**：工作区含本次未提交改动，`git checkout` 会一并丢弃。

- [x] **Step 2: 逐条执行 12 项变异，确认每项都被捕获**

每条变异后运行
`bash .venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_chat_shared_helpers_behavior.py -q`
并记录结果，然后立刻从备份还原。

| # | 变异 | 捕获它的断言 | 期望 |
| --- | --- | --- | --- |
| M1 | `model` 第一档 `afterMs: 3000` → `0` | `test_pending_hint_resolves_stage_labels_by_category_and_threshold` 的 `belowThreshold is None` | failed |
| M2 | 删掉 `if (!isSandbox && NON_LIVE_TIMER_CATEGORIES.has(category)) return null;` | `test_pending_hint_covers_each_category_and_suppresses_waiting_states` 的 `permission is None` | failed |
| M3 | 把沙箱也套 3 秒阈值（把 `sandbox: PREWARM_STAGE_LABELS` 换成第一档 `afterMs: 3000` 的副本） | `test_pending_hint_keeps_sandbox_immediate_and_marks_redundant_elapsed_prefix` 的 `sandboxImmediate` | failed |
| M4 | `showElapsed: !isLiveTimer` → `showElapsed: true` | 同一测试的 `timerShowElapsed is False` | failed |
| M5 | 5 处模板只回退 1 处为旧的 `v-if="isWorkspacePrewarmPending(item)"` | `count("<TimelinePendingHint") == 5` 与 `'v-if="isWorkspacePrewarmPending(' not in timeline` | failed |
| M6 | 删掉 `TimelinePendingHint.vue` 的 `<style scoped>` 整段 | `test_timeline_pending_hint_component_owns_label_and_bar_style` 的 `.workspace-prewarm-bar {` | failed |
| M7 | 5 处 `:now="nowMs"` 退回 `:now="tickNow"` | 时间线契约的 `count(':now="nowMs"') == 5` | failed |
| M8 | 静音集合移除 `agent` | `test_pending_hint_covers_each_category_and_suppresses_waiting_states` 的 `agent is None` | failed |
| M9 | 非沙箱也返回 `"bar"` | 行为用例的 `modelIndicator == "wave"` | failed |
| M10 | 波形配色退回 `sky` | 契约的 `text-slate-400 dark:text-slate-500` | failed |
| M11 | 删掉减弱动效的静态高度 | 契约的 `scaleY(0.72)` | failed |
| M12 | 第一档文案退回「正在理解你的问题…」 | 行为用例的 `== "正在思考…"` | failed |

M1 示例命令（其余按同法替换 `old` / `new` 与目标文件）：
```bash
cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && .venv/bin/python - <<'PY'
import pathlib
p = pathlib.Path("frontend/src/utils/processTimeline.ts")
s = p.read_text(encoding="utf-8")
old = '{ afterMs: 3000, label: "正在思考…" }'
new = '{ afterMs: 0, label: "正在思考…" }'
assert old in s, "未找到目标片段"
p.write_text(s.replace(old, new, 1), encoding="utf-8")
PY
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_chat_shared_helpers_behavior.py -q
cp /tmp/pending_hint_process_timeline.ts frontend/src/utils/processTimeline.ts
```

M6 的变异做法：用 `python` 读组件源码，按 `<style scoped>` 起始到 `</style>` 结束整段删除后写回。

Expected: **6/6 全部被捕获**；每项还原后全量该文件恢复 passed。

- [x] **Step 3: 确认三个文件与备份逐字节一致**

Run:
```bash
cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && \
diff /tmp/pending_hint_process_timeline.ts frontend/src/utils/processTimeline.ts && echo "processTimeline 已还原" && \
diff /tmp/pending_hint_component.vue frontend/src/components/chat/TimelinePendingHint.vue && echo "组件已还原" && \
diff /tmp/pending_hint_timeline.vue frontend/src/components/chat/ChatExecutionTimeline.vue && echo "timeline 已还原"
```
Expected: 三行「已还原」，`diff` 无输出。

- [x] **Step 4: 更新 `tests/CHECKLIST.md`**

在表格分隔行（`| 特性 / 修复项 | ...` 的下一行）之后、现有第一行数据行**之前**插入新行：

```markdown
| 挂起步骤「傻等」修复：动态安抚文案 + 5 处渲染收敛为组件 (Pending Step Live Hint) | `frontend/src/components/chat/TimelinePendingHint.vue`（新增）、`frontend/src/utils/processTimeline.ts`、`frontend/src/components/chat/ChatExecutionTimeline.vue`、`tests/frontend/test_chat_shared_helpers_behavior.py`、`docs/superpowers/specs/2026-10-10-pending-step-live-hint-design.md`（新增）、`docs/superpowers/plans/2026-10-10-pending-step-live-hint.md`（新增）、`tests/CHECKLIST.md` | **用户反馈（截图）**：知识库问答的思考卡片里「模型调用 · DeepSeek-V3.2」右侧秒表已走到 28.6s，标题与秒表之间一大片空白，整行除不断变大的数字外没有任何信息，问「这个模型调用如果要很久就会一直在这里，超过多少秒能不能后面加一些动态文案」。**根因**：该能力**早已实现但被绑死在沙箱上**——`processTimeline.ts:119-123` 的 `PREWARM_STAGE_LABELS` 就是三档递进安抚文案（0s 申请隔离资源 / 4s 初始化工作区 / 10s 耗时较长请稍候），`ChatExecutionTimeline.vue` 也有 5 处「动效条 + 已等待 Ns · 阶段文案」渲染，但判定 `isWorkspacePrewarmPending`（`:133-140`）只认 `workspace:sandbox*` 这一个 id，因此其余挂起步骤（模型调用、工具执行、检索）在长时间等待时只有秒表。**同时暴露一处结构问题**：那 5 处渲染（`:169` child、`:228` child.children 的 subStep、`:348` 顶层 item、`:443` 另一处 subStep、`:517` nestedStep）是**同一段 HTML 复制了 5 遍**，只有循环变量名不同。**方案（用户选定「所有长时间挂起的步骤」+ 阈值 3 秒 + 三档递进）**：① 文案规则抽成纯函数 `resolvePendingHint(item, now, isLiveTimer)`（`processTimeline.ts`），按类别给三档表——`model` 3s 正在思考… / 10s 正在生成回答… / 20s 内容较长，仍在生成中…；`knowledge` 3s 正在整理检索结果… / 10s 正在组织回答… / 20s 资料较多，仍在整理中…；`tool` 与 `default` 各有兜底三档；`sandbox` 档**直接引用 `PREWARM_STAGE_LABELS`**（不复制第二份文案，与 `workspacePrewarmStageLabel` 逐字同源）。② **沙箱保持 0s 立即显示**（其文案本就是「首次创建」那一刻的叙述，套 3 秒会让前 3 秒退回无提示），用户选的 3 秒阈值落在通用类别第一档上。③ **`permission` / `external` 不显示任何文案**：这两类表示「等用户确认 / 等外部系统回话，机器并没有在跑」，显示「正在理解你的问题…」是误导——判据与 `NON_LIVE_TIMER_CATEGORIES`（`:604-607`，唯一不显示实时秒表的两类）同源，且沙箱判定优先于类别排除。④ 渲染抽成 `TimelinePendingHint.vue`（props `item` / `now` / `isLiveTimer`，不持有定时器，`now` 由父级既有 500ms 心跳注入），5 处重复片段统一替换为组件调用。⑤ **顺带去掉一处数字重复**：正在走右侧秒表的行不再显示「已等待 Ns ·」前缀（`showElapsed = !isLiveTimer`），无秒表的历史挂起行仍保留前缀。⑥ **指示器分两种**（用户复测「不能跟沙箱拉起一样的效果」后定案）：沙箱保留既有横向亮蓝进度条（它的专属签名），其余类别改用石板灰三根竖条波形（`pending-hint-wave`，浅色 `slate-400` / 深色 `slate-500`），形状与颜色双重区分；`PendingHint.indicator` 取 `"bar" | "wave"` 由纯函数决定，组件只按字段渲染；起伏用 `scaleY` 而非 `height`（不每帧触发布局），深色走 Tailwind `dark:` 变体（避开 scoped 里手写 `.dark` 命中所有深色祖先的坑），`prefers-reduced-motion` 时静止在 `scaleY(0.72)`。**过程中发现并处理的两个坑**：一是**样式作用域陷阱**——`workspace-prewarm-bar` 的样式（含 `::after` 滑动动画与 `@keyframes`）定义在父组件 `<style scoped>` 内，抽成子组件后子组件内部 `span` 不带父 scope id，选择器不再匹配、动效条会**静默失效**（而源码契约仍会通过），因此样式随组件搬移并在契约里加护栏；`:1284-1294` 的 `prefers-reduced-motion` 块同时含 `.thought-status-dot`（留在父组件，别处仍在用）与 bar 规则（随组件走），**必须拆成两半**。二是**连带死代码**：`prewarmStartedAtMs` 与其 watch 的唯一消费者是被删除的 `prewarmElapsedMs`，故一并删除；而 `findWorkspacePrewarmPending` 与 `isWorkspacePrewarming` 必须保留（仍是 tick 启动条件的一部分），因此契约断言写成 `v-if="isWorkspacePrewarmPending(` 而非裸函数名——后者在 `walk` 里仍需调用，用裸名会永远失败。**明确未做**：不改秒表逻辑与 `NON_LIVE_TIMER_CATEGORIES`；不做后端推进（不为展示文案引入服务端计时或新 SSE 事件）；不介入 `agent_max_toolcall_timeout` 超时机制；不引入动画库（动效用 CSS keyframes），但指示器分两种：沙箱横条 / 通用波形。**验证**：行为测试 **3 项**（Node 真跑 `resolvePendingHint`，覆盖阈值边界 2999/3000、三档递进、五类文案、`permission`/`external`/非 pending/缺 `started_at` 均返回 null、沙箱 0s 立即且与既有导出函数逐字同源、`showElapsed` 随 `isLiveTimer` 翻转、时钟回拨归 0）+ 组件契约 **1 项**（含样式随组件走的护栏）+ 改写既有时间线契约 **1 项**（5 处替换计数、旧判定与死代码清零、样式搬移）；**变异验证 12/12 全部被捕获**（第一档阈值改 0、删掉 permission 排除、沙箱套 3 秒、`showElapsed` 恒 true、5 处只回退 1 处、删掉组件 style 段、now 绑定退回 tickNow 计数器、静音集合移除 agent、非沙箱也返回 bar、波形配色退回 sky、删掉减弱动效静态高度），每项还原后 `diff` 证明三个文件与备份逐字节一致。**人工验证（需用户在控制台跑 `./dev.sh` 后执行）**：① 提一个需要长时间思考的问题 → 3 秒后该行出现「正在理解你的问题…」，10 秒后转「正在组织回答…」，20 秒后转「内容较长，仍在生成中…」；② 快速步骤（如 `search_knowledge_base` 1.6s）不出现任何文案、行高不跳；③ 沙箱首次创建仍是 0 秒立即出文案，且不再与右侧秒表重复显示「已等待 Ns」；④ 触发权限确认时该行不出现任何「正在…」文案。未跑 `./dev.sh`、未执行 git commit。 | ✅ 挂起步骤按类别三档递进安抚文案（3 秒阈值、沙箱保持 0 秒）；`permission`/`external` 不显示；5 处重复渲染收敛为 `TimelinePendingHint.vue`；动效条样式随组件搬移（scoped 陷阱已规避）；行为测试 3 项 + 组件契约 1 项 + 改写既有契约 1 项，变异 12/12 捕获 | 2026-10-10 |
```

- [x] **Step 5: 跑前端契约全量**

Run:
```bash
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend -q 2>&1 | tail -5
```
Expected: `0 failed`。记录 passed 总数（用于 commit message 与汇报）。

- [x] **Step 6: 类型检查确认基线**

Run:
```bash
cd frontend && NODE_OPTIONS="--max-old-space-size=4096" ./node_modules/.bin/vue-tsc -b --pretty false 2>&1 | grep -c "error TS"
```
Expected: `62`

- [x] **Step 7: 确认后端未被触碰**

Run:
```bash
cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && git status --short && git diff --stat
```
Expected: 改动文件仅 `frontend/` 下 3 个文件 + `tests/frontend/...` + `tests/CHECKLIST.md` + 2 个新增文档；**无任何 `app/` 下的改动**。

- [x] **Step 8: 检查点（不提交）**

汇报：改动文件清单、4 + 1 条契约、变异 12/12 结果、CHECKLIST 已更新、全量契约与类型检查结果。建议 commit message：

```
feat(timeline): 挂起步骤按耗时递进显示安抚文案，5 处渲染收敛为组件

- 新增纯函数 resolvePendingHint(item, now, isLiveTimer)：model / knowledge / tool /
  default 各三档文案，3 秒阈值起显；沙箱沿用既有 PREWARM_STAGE_LABELS（0 秒立即）
- permission / external 不显示文案（等用户确认/等外部回话时机器没在跑）
- 新增 TimelinePendingHint.vue，ChatExecutionTimeline.vue 的 5 处重复片段统一替换；
  动效条样式随组件搬移（父组件 scoped 作用不到子组件内部元素）
- 删除 prewarmElapsedMs / prewarmElapsedSeconds / prewarmStageLabel 及连带死代码
  prewarmStartedAtMs 与其 watch；findWorkspacePrewarmPending / isWorkspacePrewarming 保留
- 行为测试 3 项 + 组件契约 1 项 + 改写既有时间线契约 1 项，变异验证 12/12 全部被捕获
```

---

## Self-Review

**1. Spec 覆盖检查**

| spec 节 | 落点 |
| --- | --- |
| §4.1 文案表与四条判定规则 | Task 1 Step 3 实现 + Step 1 三条测试逐条覆盖 |
| §4.2 `resolvePendingHint` 与 `PENDING_HINT_STAGE_LABELS` | Task 1 Step 3（代码与 spec 逐字一致） |
| §4.3 组件 | Task 2 Step 3 |
| §4.4 5 处接入 + 删除三个 computed | Task 3 Step 3、Step 4b/4c |
| §4.5 tick 条件不变 | Task 3 未触碰 `needsLiveTick`，且 Step 1 保留 `void tickNow.value` 与 walk 断言 |
| §4.6 既有契约三条断言改指向 | Task 3 Step 1（改写整个测试函数） |
| §5 边界表 | Task 1 Step 1 覆盖 `permission`/`external`/非 pending/缺 `started_at`/时钟回拨/`showElapsed` |
| §6.1 行为测试矩阵 | Task 1 Step 1（3 个测试函数，逐行对应表内 10 行） |
| §6.2 源码契约 | Task 2 Step 1 + Task 3 Step 1 |
| §6.3 变异 5 项 | Task 4 Step 2 的 M1-M5 |
| §6.4 人工验证 4 项 | Task 4 Step 4 CHECKLIST 末尾 |
| §6.5 回归（vue-tsc 62 / 前端全量 / 后端零改动） | Task 3 Step 7、Task 4 Step 5-7 |
| §7 回滚 | 无迁移无依赖；回滚即还原 3 个前端文件 + 测试断言 |
| §8 文件清单 | 「文件结构」表逐项对应 |

**计划对 spec 的补充（3 处，已在开头单列）**：样式随组件搬移 + reduced-motion 块拆分；`prewarmStartedAtMs` 与 watch 属连带死代码；契约断言须带 `v-if="` 前缀。此外变异清单由 5 项增至 **6 项**（新增 M6：删掉组件 style 段），因为「样式随组件走」是本次新增的、且失败时不会报错的一类风险。

**2. 占位符扫描**：无 TBD / TODO / 「类似 Task N」/「适当处理」；每个改代码的步骤都给了完整可粘贴代码、精确插入位置或可直接执行的脚本。

**3. 类型与命名一致性**：`resolvePendingHint` / `PendingHint` / `PendingHintStage` / `PENDING_HINT_STAGE_LABELS`（Task 1 定义，Task 2/3/4 引用）；组件 props `item` / `now` / `isLiveTimer`（Task 2 定义，Task 3 传入，模板 kebab-case 为 `:is-live-timer`）；`showElapsed` / `elapsedSeconds` / `label`（Task 1 返回，Task 2 消费，Task 4 变异断言）；`TimelinePendingHint`（Task 2 创建，Task 3 import 并在 5 处使用）。
