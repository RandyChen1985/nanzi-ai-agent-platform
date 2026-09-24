# 时间线「单卡实时秒表」设计

## 背景

用户反馈执行时间线里一条 Bash 工具调用跑了 29 秒，但界面上除了右上角整轮计时器
在动，那一行本身只有一个「进行中」呼吸点，看不到自己跑了多久。用户无法判断
「这条是在跑，还是已经卡住」。

排查结论：AgentScope 的工具调用在 `TOOL_CALL_START`（pending 卡）与
`TOOL_RESULT_END`（完成卡）之间**不产生任何中间事件**，因此「执行中」的进度只能
由前端本地推断。而前端其实已经有现成零件却没有接上：

1. `isLiveThoughtStepTimer`（`frontend/src/utils/agentscopeSseHandlers.ts:356`）
   早就写好了「只让最后一条挂起步骤走秒表」的正确语义，**但没有任何组件引用它**
   （死代码，仅被一处契约测试锁存在性）。
2. 时间线组件 `tickNow` 的 500ms interval **只在工作区预热期间运行**
   （`ChatExecutionTimeline.vue:803-819`）。
3. 更关键：秒表需要 `started_at` 作基准，而**两套 store 不一致**——
   `msg.logs` 的 pending 卡创建时有 `started_at = Date.now()`
   （`EmbedChat.vue:8366`），但时间线 UI 渲染的 `msg.processTimeline` 没有：
   `syncProcessTimelineLog` 只透传 SSE chunk 的 `data.started_at`
   （工具起始 chunk 不带该字段），`processTimeline.ts` 创建分支因此写入 undefined。

## 目标与边界

目标：让「当前正在执行的那一步」在时间线上显示实时递增的耗时；步骤完成后数字
立即冻结为最终耗时；等待用户操作（权限确认、外部执行）期间不计时。

范围内：
- 为 `processTimeline` 的 pending log 补齐 `started_at` 基准。
- 新增纯函数判定「当前该走秒表的那一条」。
- 组件接线：泛化 ticker、把实时值接入已有的耗时渲染函数。

范围外（明确不做）：
- 不改后端，不新增 SSE 事件类型。
- 不做工具输出的实时滚动（需要新增流式执行链路，是独立特性）。
- 不改 `msg.logs` 的既有逻辑，不改完成时「前端按 `started_at` 反算耗时」的现状。
- 不给「深度思考」文本行加秒表（那是 `pending: boolean` 的另一套形态）。

## 方案

### 1. 基准：pending log 创建时写入 started_at

`frontend/src/utils/processTimeline.ts` 的 `upsertTimelineLog` 创建分支：

```ts
started_at: data.started_at ?? (data.status === "pending" ? Date.now() : undefined),
```

与 `EmbedChat.vue:8366` 对 `msg.logs` 的做法对齐。对 EmbedChat 与 AgentDebug
两条路径同时生效；已有 item 的 `started_at` 不会被空值覆盖（更新分支本就只在
`data.started_at !== undefined` 时写入）。

### 2. 判定：纯函数 `resolveLiveTimerLogId`

放在 `processTimeline.ts`（只有 type-only import，可直接被仓库既有的 node
transpile 测试 harness 加载）：

```ts
export const NON_LIVE_TIMER_CATEGORIES: ReadonlySet<string> = new Set(["permission", "external"]);

export function resolveLiveTimerLogId(items: ProcessTimelineItem[] | undefined): string | null;
```

规则（深度优先按视觉顺序）：
1. 找出**最后一条** `status === "pending"` 的 log（含嵌套 children）。
2. 不存在 → `null`。
3. 它的 `category` 命中 `NON_LIVE_TIMER_CATEGORIES` → `null`
   （正在等用户点确认，不是机器在跑，不该走秒表）。
4. 它没有有限数值的 `started_at` → `null`（无基准，算不出耗时）。
5. 否则返回 `String(item.id)`。

`NON_LIVE_TIMER_CATEGORIES` 由 `agentscopeSseHandlers.ts` 改为从本模块导入，
避免两处各定义一份。`isLiveThoughtStepTimer` 保留原处不动（既有契约测试锁着它），
本次实际生效的是新函数。

### 3. 渲染：组件接线

`frontend/src/components/chat/ChatExecutionTimeline.vue`：

- `liveTimerLogId` computed，依赖 `tickNow` 保持每 500ms 重新计算。
- ticker 泛化：新增 `needsLiveTick = isWorkspacePrewarming || liveTimerLogId !== null`，
  由它驱动同一个 500ms interval；预热逻辑（`prewarmStartedAtMs`）拆分到独立的
  watch 中保持原语义。
- `formatTimelineDuration(item)` 扩展：命中 live 的那一条返回实时耗时，否则维持
  现状（`execution_time_ms`，route group 前缀「总计」）。这样 5 处模板
  （170 / 254 / 362 / 453 / 527 行）**一行都不用改**。
- 实时值同样走 `formatDuration`（<1000ms → `Nms`，否则 `x.x s`），保证冻结瞬间
  数字口径与显示不跳变。

完成后 `execution_time_ms` 一旦写入即优先，数字冻结；`finalizePendingStreamLogs`
/ `finalizeAllPendingStreamLogs` 的既有收尾语义不变。

## 测试

沿仓库既有前端测试风格（`tests/frontend/`，`pytest --confcutdir=tests/frontend`）：

- **行为**（新增 `tests/frontend/test_timeline_live_step_timer_behavior.py`，用
  既有 node transpile harness 直接跑 `processTimeline.ts`）：
  - 最后一条 pending 命中；全完成返回 null；pending 但无 `started_at` 返回 null
  - 最后一条 pending 是 `permission` / `external` → null
  - 嵌套 children 中的 pending 也能命中
  - 纯 `text`（reasoning/narration）pending 不会成为秒表项
  - `upsertTimelineLog` 创建 pending log 时写入 `started_at`；创建非 pending log 不写
- **契约**（扩展 `tests/frontend/test_dataset_menu_loading_contract.py::
  test_thought_step_timer_contract`）：组件源码必须 import 并使用
  `resolveLiveTimerLogId`，存在 `liveTimerLogId` / `needsLiveTick`，且 ticker 仍依赖
  `tickNow` —— 把「函数定义了但没人用」这个漏洞锁死。
- **类型**：`vue-tsc --noEmit` 不得新增错误。

## 风险

- 实时值基于客户端 `Date.now()`，与后端写在标题里的 `(NNNNms)` 会有小幅差异。
  这是**现状**（右侧数字本来就由前端按 `started_at` 反算），本次不引入第二个口径。
- ticker 频率 500ms，只重算一个 computed，且仅在存在 live 项或预热时运行；
  完成或中断后自动停表并清理 interval。
- 唯一可见行为变化：原来执行期间那一行不显示任何耗时，现在会看到秒数递增。
