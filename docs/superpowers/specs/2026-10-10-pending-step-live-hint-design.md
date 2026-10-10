# 挂起步骤动态安抚文案设计

## 1. 问题背景

用户在知识库问答里观察到：思考卡片的「模型调用 · DeepSeek-V3.2」一项右侧秒表已经走到 `28.6s`，标题与秒表之间是一大片空白，整行除了那个不断变大的数字之外没有任何信息。用户的原话是「这个模型调用，如果要很久，就会一直在这里，如果超过多少秒，能不能后面加一些动态文案，让用户感受好一些」。

### 现状：能力已有，但被绑死在沙箱上

秒表本身是活的（`ChatExecutionTimeline.vue` 的 500ms `tickNow` 心跳驱动），它能回答「等了多久」，但回答不了「在等什么」与「是否还在推进」。后者其实**早已实现**，只是限定在沙箱预热这一种场景：

- `frontend/src/utils/processTimeline.ts:119-123` 的 `PREWARM_STAGE_LABELS` 是三档递进安抚文案：
  - `0ms` 首次创建沙箱，正在申请隔离资源配置…
  - `4000ms` 正在初始化沙箱工作区（拉取镜像与启动运行环境）…
  - `10000ms` 创建工作区耗时较长，请稍候（最长约 60 秒）…
- `ChatExecutionTimeline.vue` 有 5 处渲染「动效条 + 已等待 Ns · 阶段文案」：`:169`（`child`）、`:228`（`child.children` 的 `subStep`）、`:348`（**顶层 `item`**，截图红框所在）、`:443`（另一处 `subStep`）、`:517`（`nestedStep`）。
- 但 5 处的判定都是 `isWorkspacePrewarmPending(item)`（`processTimeline.ts:133-140`），它只认 `id === "workspace:sandbox"` 或 `id.startsWith("workspace:sandbox:")`。

因此除了沙箱，其余任何挂起步骤（模型调用、工具执行、检索）在长时间等待时都只有秒表。

### 一处结构性问题

上述 5 处渲染是**同一段 HTML 复制了 5 遍**，只有循环变量名不同，每份都各带一个沙箱判定。若按最小改动把新判定加到 5 处，就要维护 5 份副本；且文案规则埋在模板里无法单测。本次一并收敛。

## 2. 目标与非目标

### 目标

- 任意**机器正在推进**的挂起步骤，等待超过阈值后在该行显示按耗时递进的安抚文案，位置与该行既有沙箱文案一致（标题与右侧秒表之间）；
- 文案按类别区分，措辞贴合该步骤在做什么；
- 消除 5 份重复渲染，文案规则抽成可单测的纯函数；
- 既有沙箱预热行为不回退。

### 非目标

- **不改秒表逻辑**（`resolveLiveTimerLogId` / `resolveLiveTimerDurationMs` 与 `NON_LIVE_TIMER_CATEGORIES` 判定保持原样）；
- **不做后端推进**：不为展示文案引入服务端计时或新 SSE 事件；
- **不改挂起超时机制**：`agent_max_toolcall_timeout`（默认 180s）仍负责把长时间无响应的挂起项标为失败，本设计不介入、不改变文案与超时的关系；
- **不改文案之外的行内元素**（图标、标题、错误态、展开箭头、工具参数块）；
- **不引入动画体系**：动效一律用 CSS keyframes，不引入动画库；但沙箱与通用步骤**各用一种指示器**（见 §4.3.1）——沙箱保留既有滑动进度条，通用步骤改用波形。

## 3. 方案选择

| 方案 | 做法 | 结论 |
| --- | --- | --- |
| **1（选用）** | 新建 `TimelinePendingHint.vue` 组件 + `processTimeline.ts` 纯函数 `resolvePendingHint`；5 处重复片段替换为组件调用 | 一处机制、消除 5 份重复、文案逻辑可用 Node 真跑测试 |
| 2 | 保留复制粘贴，仅把 5 处的 `isWorkspacePrewarmPending(x)` 换成 `resolvePendingHint(x)` | 改动点看着更少，但 5 份重复继续存在、文案规则埋在模板里不可单测，下次再改仍要动 5 处 |
| 3 | 服务端定时推进文案（更新 details 或发新事件） | 为纯展示逻辑引入服务端计时与额外 SSE 事件，与前端 500ms 心跳重复，改动面最大而效果相同 |

本设计取方案 1。

## 4. 详细设计

### 4.1 文案表与判定规则

每类别一张档位表，按耗时向后取最后一档（与既有沙箱实现同构）：

| 类别 | 第一档 | 第二档 | 第三档 |
| --- | --- | --- | --- |
| `sandbox`（沙箱预热） | **0s** 首次创建沙箱，正在申请隔离资源配置… | **4s** 正在初始化沙箱工作区（拉取镜像与启动运行环境）… | **10s** 创建工作区耗时较长，请稍候（最长约 60 秒）… |
| `model`（模型调用） | 3s 正在思考… | 10s 正在生成回答… | 20s 生成耗时较长，仍在继续… |
| `knowledge`（知识库检索/组织回答） | 3s 正在整理检索结果… | 10s 正在组织回答… | 20s 整理耗时较长，仍在继续… |
> **文案修订（用户复测反馈）**：第一档原为「正在理解你的问题…」、知识库第一档原为「正在查阅知识库资料…」。用户指出 3 秒时模型**早就在生成 token**，说「理解」等于把阶段说晚了；知识库那行出现时**检索已经完成**（标题即「检索完成，正在组织回答」），说「正在查阅」同样落后。故模型调用改为 3s「正在思考…」/ 10s「正在生成回答…」——「思考」不会落后于任何实际阶段（出 reasoning 是思考，出正文也是思考）；知识库改为 3s「正在整理检索结果…」/ 10s「正在组织回答…」。工具、兜底与沙箱三档经复核不存在同类问题，不动。

> **第三档修订（用户复测反馈）**：`model` 20s「内容较长，仍在生成中…」→「生成耗时较长，仍在继续…」，`knowledge` 20s「资料较多，仍在整理中…」→「整理耗时较长，仍在继续…」，`tool` 20s「该步骤较慢，请继续稍候…」→「仍未收到该步骤的返回，请继续稍候…」。前两处原措辞替用户**猜原因**（慢未必因为内容长/资料多，也可能是推理久或上游排队），猜错反而让人以为「快好了」；第三档改为只陈述事实。第三处的第二、三档原本基本同义，改后补上「一直没收到返回」这条新信息。另：`tool` 第二档由「执行耗时偏长，仍在进行中…」改为「执行耗时偏长，仍在处理…」，与第三档的措辞拉开层次。`default` 与沙箱三档不变。

| `tool`（工具执行） | 3s 正在执行该步骤… | 10s 执行耗时偏长，仍在处理… | 20s 仍未收到该步骤的返回，请继续稍候… |
| `default`（其他类别兜底） | 3s 正在处理… | 10s 仍在处理中，请稍候… | 20s 处理耗时较长，请继续稍候… |

四条判定规则：

1. **沙箱保持 0s 立即显示**，不受通用阈值约束：它的文案本就是「首次创建」那一刻的叙述，套上 5 秒会让前 5 秒退回无提示状态。用户选定的 3 秒阈值体现为通用类别第一档的 `afterMs`，机制上不写死全局常量。
2. **`permission` / `external` / `agent` 不显示任何文案**：前两类表示「等用户确认 / 等外部系统回话，机器并没有在跑」，显示任何「正在…」类文案都是在误导，判据与 `NON_LIVE_TIMER_CATEGORIES`（唯一不显示实时秒表的两类）同源；`agent` 类（「主专家开始处理」等阶段行，`category: "agent"`）标题本身已自解释，再补一句「正在处理…」是同义重复——它**保留**实时秒表，故单列 `PENDING_HINT_SILENT_CATEGORIES = new Set([...NON_LIVE_TIMER_CATEGORIES, "agent"])` 而不并入 `NON_LIVE_TIMER_CATEGORIES`（后者同时决定「不显示秒表」）。
3. **只认 `status === "pending"` 且 `started_at` 为有限数**，与 `isEligibleLiveTimerLog`（`processTimeline.ts:609-613`）同源。
4. **沙箱判定优先于类别排除**：沙箱行按 id 识别（`isWorkspacePrewarmPending`），先于 `NON_LIVE_TIMER_CATEGORIES` 判断，避免沙箱行因类别取值而误被排除。

### 4.2 纯函数 `resolvePendingHint`

在 `processTimeline.ts` 中新增（`PREWARM_STAGE_LABELS` 之后、`formatTimelineTitle` 之前）：

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
    { afterMs: 20000, label: "生成耗时较长，仍在继续…" },
  ],
  knowledge: [
    { afterMs: 3000, label: "正在整理检索结果…" },
    { afterMs: 10000, label: "正在组织回答…" },
    { afterMs: 20000, label: "整理耗时较长，仍在继续…" },
  ],
  tool: [
    { afterMs: 3000, label: "正在执行该步骤…" },
    { afterMs: 10000, label: "执行耗时偏长，仍在处理…" },
    { afterMs: 20000, label: "仍未收到该步骤的返回，请继续稍候…" },
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
  // permission / external 机器没在跑，agent 行标题已自解释
  if (!isSandbox && PENDING_HINT_SILENT_CATEGORIES.has(category)) return null;

  const stages = (isSandbox ? PENDING_HINT_STAGE_LABELS.sandbox : PENDING_HINT_STAGE_LABELS[category])
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
    indicator: isSandbox ? "bar" : "wave",
  };
}
```

要点：

- **`PENDING_HINT_STAGE_LABELS.sandbox` 直接引用 `PREWARM_STAGE_LABELS`**，沙箱文案只有一份来源。三个既有导出原样保留：`isWorkspacePrewarmPending` 被新机制复用；`workspacePrewarmStageLabel` / `workspacePrewarmElapsedSeconds` 改后已不被渲染层调用，但它们是既有契约（`test_chat_shared_helpers_behavior.py:1465-1471`）覆盖的公开取用口，故不删除、不改签名。
- **时钟回拨兜底**：`elapsedMs` 下限 0，`elapsedSeconds` 不为负，沿用既有 `workspacePrewarmElapsedSeconds` 的做法。
- **`showElapsed`**：正在走右侧实时秒表的行不再重复显示「已等待 Ns ·」（同一数字出现两遍）；没有秒表的历史残留 pending 行仍保留前缀，否则它无法体现已等待时长。

### 4.3 组件 `TimelinePendingHint.vue`

新建 `frontend/src/components/chat/TimelinePendingHint.vue`，只做渲染、不持有定时器：

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
    <span
      v-if="hint.indicator === 'bar'"
      class="workspace-prewarm-bar shrink-0"
      aria-hidden="true"
    ></span>
    <span
      v-else
      class="pending-hint-wave shrink-0 text-slate-400 dark:text-slate-500"
      aria-hidden="true"
    >
      <i></i><i></i><i></i>
    </span>
    <span
      class="truncate max-w-[140px] sm:max-w-[320px] md:max-w-none"
      :title="prefix + hint.label"
    >{{ prefix }}{{ hint.label }}</span>
  </div>
</template>
```

文案与可访问性属性沿用既有沙箱渲染（`text-sky-600 dark:text-sky-400`、`aria-live="polite"`、`aria-busy="true"`、三档 `max-w` 截断）；**指示器不再与沙箱共用**，见下。

### 4.3.1 两种指示器：沙箱横条 vs 通用波形

初版让所有类别共用沙箱的滑动进度条，实际用起来暴露两个问题：一是会被读成「又在拉沙箱」（同源样式导致语义混淆），二是它与行首本来就有的 `thought-status-dot`（1.6s 呼吸）同时在动，两个动效说同一件事。因此按类别分成两种指示器：

| 类别 | 指示器 | 颜色 | 理由 |
| --- | --- | --- | --- |
| 沙箱（`isWorkspacePrewarmPending`） | 既有横向进度条 `workspace-prewarm-bar` | 亮蓝 `rgba(14,165,233,.75)` | 「正在创建工作区」的专属签名，保留不动 |
| 其余类别 | 三根竖条波形 `pending-hint-wave` | 浅色 `slate-400` / 深色 `slate-500` | 与横条在**形状与颜色**上双重区分；中性色不与行首蓝点争「状态」语义 |

选择权留在纯函数：`PendingHint.indicator` 取 `"bar" | "wave"`，组件只按字段渲染，不在组件里重写业务判断。

三个实现约束：

- **起伏用 `transform: scaleY()` 而不是 `height`**：`height` 动画每帧触发布局，`scaleY` 只走合成层。（既有沙箱进度条动的是 `left`，同属可优化项，但它是既有实现，本轮不动。）
- **深色适配用 Tailwind 的 `dark:` 变体**（`text-slate-400 dark:text-slate-500`），不在 scoped 样式里手写 `.dark` 选择器——后者会命中所有带 `.dark` 的祖先（同类坑见 `GeneratingWalker.vue` 的注释）。波形取色用 `currentColor`，颜色只在这一处 `text-*` 上定义。
- `prefers-reduced-motion: reduce` 时波形静止在中等高度（`scaleY(0.72)`），否则会停在最矮的基线帧上。

### 4.4 `ChatExecutionTimeline.vue` 接入

5 处（`:169`、`:228`、`:348`、`:443`、`:517`）的既有片段：

```vue
<!-- 沙箱工作区预热进行中文案与动效条（同行右侧对齐） -->
<div v-if="isWorkspacePrewarmPending(child)" ...> ... </div>
```

统一替换为一行组件调用（变量名按各处的循环变量取 `child` / `subStep` / `item` / `nestedStep`）：

```vue
<TimelinePendingHint
  :item="item"
  :now="tickNow"
  :is-live-timer="String(item.id) === liveTimerLogId"
/>
```

同时删除 `prewarmElapsedMs` / `prewarmElapsedSeconds` / `prewarmStageLabel` 三个 computed（`:964-971`）——它们的职责已由纯函数接管。`isWorkspacePrewarming` 保留：它仍是 tick 启动条件的一部分。

### 4.5 tick 条件保持不变

`needsLiveTick`（`:939-941`）现为 `isWorkspacePrewarming.value || liveTimerLogId.value !== null`，**不改**。理由：

- 模型调用等待时它就是最后一条挂起项，`liveTimerLogId` 非空，500ms 心跳已在跑，文案随之推进；
- `permission` / `external` 挂起时不显示文案（规则 2），无需为它们新增触发源；
- 唯一会引出触发源缺口的组合是「某行显示文案、但它不是最后一条挂起项，而最后一条又是 `permission` / `external`」——此时秒表停摆、心跳不跑，前面那行的文案会停在上一次计算值。该组合在真实链路中不成立：模型调用开始时 `handleModelCallEvent` 会先 `finalizePendingStreamLogs(msg, "model")` 收尾上一条 `model` 挂起项，工具项在完成时也会收尾，因此挂起项不会堆叠成「前面还有一条正在跑的 model、后面跟着一条等待用户操作的项」。这是本次不提前引入 `hasPendingHintItem` 触发源的依据（YAGNI）：将来真出现该组合再加，成本是给 `needsLiveTick` 增加一个遍历 `items` 的条件。

### 4.6 既有契约的同步修改

`tests/frontend/test_chat_shared_helpers_behavior.py:1502-1504` 现在断言 `ChatExecutionTimeline.vue` 模板中必须出现 `prewarmStageLabel`、`prewarmElapsedSeconds`、`已等待 {{ prewarmElapsedSeconds }}s` 三个字面量。渲染搬进新组件后这三条必然失败——属于**断言位置变了、锁定的能力没变**，因此把断言目标改为 `TimelinePendingHint.vue`（并保持对「已等待 Ns」前缀与阶段文案绑定的覆盖），而不是删除它们。

### 4.7 数据流

```
SSE log 事件（携带 category / status / started_at）
        │
        ▼
EmbedChat 写入 msg.logs → processTimeline 组装 items
        │
        ▼
ChatExecutionTimeline 渲染每一行
        │  tickNow 每 500ms +1（仅当 liveTimerLogId 非空或沙箱预热中）
        ▼
TimelinePendingHint(item, now, isLiveTimer)
        │
        ▼
resolvePendingHint（纯函数）── null ──▶ 不渲染
                            └ 命中 ──▶ 指示器（沙箱横条 / 通用波形）+ 阶段文案
```

### 4.8 实施中发现并修复的接线缺陷：`now` 必须是时间戳

初版把 5 处接成 `:now="tickNow"`。`tickNow` 是 `ref(0)` 每 500ms **加 1** 的**渲染触发器（计数器）**，不是时间戳——父组件里秒表能正常走，是因为 `liveTimerDuration` 用的是 `Date.now()`（`ChatExecutionTimeline.vue:1106`）。计数器当 `now` 传入后，子组件里 `elapsedMs = Math.max(0, 计数器 − 时间戳) = 0`，**永远小于第一档阈值，文案从不显示**；沙箱档又因 `afterMs: 0` 只会停在第一档、不再递进。

修法：父组件新增 `nowMs = computed(() => { void tickNow.value; return Date.now(); })`，与既有 `liveTimerLogId` 借用 `tickNow` 建立重算依赖的写法同构，5 处改为 `:now="nowMs"`。

**为什么原有测试没抓到**：行为测试直接以时间戳调用纯函数（逻辑正确），组件 SSR 验证也由手工传入时间戳（绕过接线），父子之间的这条数据流没有任何断言覆盖。补的护栏是契约断言 `count(':now="nowMs"') == 5` 且 `':now="tickNow"' not in timeline`，以及变异 M7（把 `nowMs` 退回 `tickNow`）必须被捕获。

## 5. 边界与已知限制

| 场景 | 行为 |
| --- | --- |
| 步骤在 3 秒内完成 | `resolvePendingHint` 返回 null，**没有任何文案出现**，行高不跳 |
| 历史残留的 pending（非最后一条，无秒表） | 显示「已等待 Ns · 阶段文案」；秒表行则只显示阶段文案 |
| 时钟回拨（`now < started_at`） | 已等待归 0，不出现负数 |
| 缺 `started_at` 的挂起项 | 不显示文案（无计时基准） |
| `permission` / `external` | 不显示文案（与不显示秒表同源） |
| 多条挂起项、且最后一条是 `permission` / `external` | 秒表停摆、500ms 心跳不跑，前面行的文案会停在上次计算值（不随时间递进）。真实链路中该组合不成立，理由见 4.5 |
| 文案出现挤压标题 | 标题为 `min-w-0 flex-1 truncate`、文案为 `shrink-0`，标题可能被截断；与既有沙箱行完全同构，非新增行为 |
| 文案与超时机制的关系 | 文案与 `agent_max_toolcall_timeout` 无关；超时后该行转为失败态，文案随之消失（状态不再是 pending） |
| 沙箱 | 保持 0s 立即出现与三档文案；唯一变化是不再与右侧秒表重复显示「已等待 Ns」 |

## 6. 测试策略

### 6.1 行为测试（Node 真跑）

复用 `tests/frontend/test_chat_shared_helpers_behavior.py` 既有的 `_run_typescript`（Node + `typescript.transpileModule`）基础设施，调用 `api.resolvePendingHint`。表内 `t` 为 `started_at`：

| 输入 | 期望 |
| --- | --- |
| `model` pending，`now = t+2999` | `null`（未满 5s） |
| `model` pending，`now = t+3000` / `t+10000` / `t+20000` | 「正在思考…」/「正在生成回答…」/「生成耗时较长，仍在继续…」 |
| `knowledge` / `tool` / `default` pending，`now = t+3000` | 各自第一档文案 |
| 沙箱 id pending，`now = t+0` | 「首次创建沙箱，正在申请隔离资源配置…」 |
| 沙箱 id pending，`now = t+4000` / `t+10000` | 与 `workspacePrewarmStageLabel(4000)` / `(10000)` **逐字相同** |
| `permission` / `external` pending，`now = t+60000` | `null` |
| `agent` pending，`now = t+60000` | `null`（「主专家开始处理」等自解释行不再叠加「正在…」） |
| `status: "success"` | `null` |
| 缺 `started_at` | `null` |
| `now < started_at`（`isLiveTimer = false`） | `elapsedSeconds === 0` 且 `showElapsed === true`，不为负 |
| `isLiveTimer` 为 `true` / `false` | `showElapsed` 为 `false` / `true` |

### 6.2 源码契约

- `TimelinePendingHint.vue` 含 `workspace-prewarm-bar`、`aria-live="polite"`、文案绑定与 `showElapsed` 条件化的前缀；
- `TimelinePendingHint.vue` 含 `pending-hint-wave`、`@keyframes pending-hint-wave`、`scaleY`、`text-slate-400 dark:text-slate-500`、`scaleY(0.72)`，以及指示器分支 `hint.indicator === 'bar'`（专抓「通用步骤又用回沙箱横条」与「波形撞沙箱蓝」）；
- `ChatExecutionTimeline.vue` 中 `TimelinePendingHint` 出现 **5 次**，且模板中**不再出现** `isWorkspacePrewarmPending(`（专抓「5 处只换 4 处」）；
- `PENDING_HINT_STAGE_LABELS` 的 sandbox 档引用 `PREWARM_STAGE_LABELS` 而非复制文案；
- 改写既有 `:1502-1504` 三条断言，目标指向新组件。

### 6.3 变异验证（14 项，项目事实门槛）

1. 通用类别第一档 `3000` → `0`（快步骤也闪文案）→ 应被「未满 3s 返回 null」捕获；
2. 从排除逻辑中删掉 `NON_LIVE_TIMER_CATEGORIES` 判断 → 应被 `permission` 用例捕获；
3. 沙箱也套 3s 阈值 → 应被「沙箱 `t+0` 即出文案」捕获；
4. `showElapsed` 恒为 `true` → 应被 `isLiveTimer` 用例捕获；
5. 5 处渲染点只替换 4 处 → 应被「模板中不再出现 `isWorkspacePrewarmPending(`」捕获；
6. 删掉 `TimelinePendingHint.vue` 的 `<style scoped>` 整段 → 应被 `.workspace-prewarm-bar {` 断言捕获；
7. 把 5 处 `:now="nowMs"` 退回 `:now="tickNow"`（时间戳换成计数器）→ 应被 `count(':now="nowMs"') == 5` 捕获；
8. 从静音集合里移除 `agent` → 应被 `agent` 用例捕获；
9. 让非沙箱也返回 `"bar"` → 应被 `modelIndicator == "wave"` 捕获；
10. 波形配色退回 `sky` → 应被 `text-slate-400 dark:text-slate-500` 断言捕获；
11. 删掉减弱动效时的静态高度 → 应被 `scaleY(0.72)` 断言捕获；
12. 第一档文案退回「正在理解你的问题…」→ 应被 `== "正在思考…"` 精确断言捕获；
13. 第三档又替用户猜原因（「内容较长」）→ 应被 `== "生成耗时较长，仍在继续…"` 精确断言捕获；
14. `tool` 第二档退回「仍在进行中…」→ 应被 `== "执行耗时偏长，仍在处理…"` 精确断言捕获。

### 6.4 人工验证（跑 `./dev.sh` 后）

1. 提一个需要长时间思考的问题：3 秒后该行出现「正在思考…」，10 秒后转「正在生成回答…」，20 秒后转「生成耗时较长，仍在继续…」；
2. 快速步骤（如 `search_knowledge_base` 1.6s）不出现任何文案，行高不跳；
3. 沙箱首次创建仍是 0 秒立即出文案，且不再与右侧秒表重复显示「已等待 Ns」；
4. 触发权限确认时该行不出现任何「正在…」文案。

### 6.5 回归

- `vue-tsc -b` 保持 62 条基线；
- `pytest --confcutdir=tests/frontend tests/frontend` 全量 0 failed；
- 本次**纯前端改动**，后端文件零改动。

## 7. 兼容性与回滚

- 无表结构变更、无迁移、无新增依赖、无后端改动、无 SSE 协议变更；
- 纯前端渲染层改动，对历史会话同样生效（文案按 `started_at` 与类别实时计算，不依赖落库字段）；
- 回滚：还原 `processTimeline.ts`、`ChatExecutionTimeline.vue`，删除 `TimelinePendingHint.vue`，并把 `test_chat_shared_helpers_behavior.py` 的三条断言还原指向即可，无数据残留。

## 8. 涉及文件清单

| 文件 | 变更 |
| --- | --- |
| `frontend/src/components/chat/TimelinePendingHint.vue`（新增） | 挂起项安抚文案与指示器（沙箱横条 / 通用波形）的单一渲染单元 |
| `frontend/src/utils/processTimeline.ts` | 新增 `PendingHintStage` / `PendingHint` / `PENDING_HINT_STAGE_LABELS` / `resolvePendingHint`；既有沙箱三个导出保持不变 |
| `frontend/src/components/chat/ChatExecutionTimeline.vue` | 5 处重复片段替换为组件调用；删除三个 prewarm computed |
| `tests/frontend/test_chat_shared_helpers_behavior.py` | 新增 `resolvePendingHint` 行为断言；三条模板断言改指向新组件 |
| `tests/CHECKLIST.md` | 新增本次交付记录 |
