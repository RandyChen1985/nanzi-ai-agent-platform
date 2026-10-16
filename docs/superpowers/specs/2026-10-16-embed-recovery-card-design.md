# Embed 会话恢复提示从 toast 改为可关闭的状态卡片

- 日期：2026-10-16
- 涉及：`frontend/src/views/EmbedChat.vue`、`frontend/src/components/chat/ChatRecoveryCard.vue`（新增）、`frontend/src/utils/recoveryCard.ts`（新增）、`tests/frontend/test_recovery_card_contract.py`（新增）

## 背景

EmbedChat 是路由级页面，切到平台内其它页面再切回会卸载重建（`visibilitychange` 甚至
不触发），而服务端 producer 仍会把任务跑完并落库。为此已有三层恢复：

1. run-status 驱动的恢复器（每 3s 收敛最新历史）
2. 会话流事件续显（每 1s 按 `last_seq` 游标重放过程事件）
3. `sessionStorage` 流式快照（切走瞬间落盘，切回还原草稿）

恢复期间对用户的提示，目前**只有一个 toast**：

```
会话仍在处理中，已恢复实时同步
```

位置在 `scheduleStreamReplay()` 内，且该函数只在 `if (activeStreamDraft)` 时才被调用。

## 问题

用户实测反馈了两点：

1. **提示信息太少**。toast 一句话，装不下「后台在不在跑、跑到哪了、我还要不要刷新」。
   尤其「输出完成后会自动补齐，无需刷新」这个关键保证，现在完全没有传达。
2. **有一种场景完全静默**。快照不可用（草稿没还原成功）时 `activeStreamDraft` 为 null，
   `scheduleStreamReplay()` 不会启动，**连这条 toast 都不会出现**——后台照跑，界面不动，
   用户只能靠「内容突然变了」察觉；若迟迟不变，就只能手动刷新。

## 方案对比

| 方案 | 做法 | 评价 |
|---|---|---|
| **A. 独立组件 + 纯函数决策（选中）** | 新增 `ChatRecoveryCard.vue` + `planRecoveryCard()` 纯函数 | 决策可单测，不与 11900 行的 EmbedChat 继续缠绕 |
| B. 改造 `ChatTodoCard` 复用 | 给它加 mode | 组件职责从「任务清单」变成两件事，props 语义错位 |
| C. EmbedChat 内联模板 | 一个 ref + 一段模板 | 改动最少，但渲染决策没有测试落点——这部分逻辑正是前几轮反复出问题的地方 |

用户选择**方案 A**。

### 采纳方案 A 时确认的四个决策点

| 议题 | 决定 |
|---|---|
| 卡片展示哪些信息 | ① 后台状态 + 保证性说明 ② 当前执行阶段摘要 ③ 执行步数 / 已同步进度 |
| 输出完成后的退场 | 先变「已完成、内容已补齐」，短暂停留 3 秒后自动消失 |
| 用户手动关闭的生效范围 | 本轮内不再出现；下一轮恢复时照常弹出 |
| 草稿丢失时是否也显示 | **显示**（文案不同），补掉上述「完全静默」的场景 |

## 设计

### 形态

| 项 | 设计 |
|---|---|
| 位置 | 消息区顶部横幅区，与任务清单卡片（`activeTodoTimeline`）、沙箱降级横幅同区（`mx-3 mt-2`） |
| 视觉 | 沿用 `ChatTodoCard` 的容器样式：`rounded-2xl`、slate 配色、`shadow-xs`、深色模式、200ms 渐入渐出 |
| 结构 | 状态图标（转圈 / 对勾）+ 主行摘要 + 次行细节 + 右侧关闭按钮 |
| 交互 | 不折叠（两行放得下三块信息），`syncing` 阶段点 × 关闭 |

### 状态机

三个阶段：`hidden` / `syncing` / `done`。

**进入 `syncing` 的两个入口**：

| 场景 | 主行文案 |
|---|---|
| 有草稿、续显已接管 | 会话仍在后台处理中，已恢复实时同步 |
| 无草稿但后台仍在跑 | 正在同步后台进度，输出完成后会自动补齐 |

**退场**：

| 事件 | 行为 |
|---|---|
| 终态同步成功写入界面 | 转 `done`：「执行已完成，内容已补齐」→ 停留 3 秒 → `hidden` |
| 后台锁释放且无需收敛 | 直接 `hidden` |
| 用户点 × | 本轮内不再出现；下一轮提问（新 trace）自动复位 |
| 组件卸载 / 新一轮开始 | 清理停留定时器并复位 |

### 两种必须抑制的情况

实现时发现两个会误报的场景，均已用判据挡掉：

| 场景 | 为什么不能提示 | 判据 |
|---|---|---|
| 正常对话（流健康） | `runActive` 与在途草稿同时为真，但本端正在实时接收，不是恢复 | `streaming === true` → `hidden` |
| 正常对话结束（全程没离开页面） | 「终态已写入界面」同样成立，但用户没经历过等待，补一句是噪音 | `terminalApplied && !engaged` → `hidden` |

后者要求先记录「本轮是否真的展示过卡片」：`syncing` 置位，新一轮提问或会话重置清零。

### 数据来源

三块信息全部取自现有状态，**不新增接口、不新增后端字段**：

| 卡片信息 | 来源 |
|---|---|
| 后台状态 + 保证性说明 | 固定文案（`syncing` / `done` 两套） |
| 当前执行阶段摘要 | 草稿 `processTimeline` 最后一项的标题与状态 |
| 执行步数 / 已同步进度 | 步数 = `processTimeline.length`；已同步 = `lastAppliedSeq`（每轮 `_seq` 从 1 起计） |

### 接口

组件只渲染、不做决策，决策收敛到一个纯函数：

```ts
// frontend/src/utils/recoveryCard.ts
export interface RecoveryCardInput {
  runActive: boolean;
  hasDraft: boolean;
  draftStepCount: number;
  appliedSeq: number;
  turnDismissed: boolean;      // 本轮已被用户关闭
  terminalApplied: boolean;    // 终态已写入界面
  streaming: boolean;          // 本端 SSE 是否仍在收流（正常对话要抑制提示）
  engaged: boolean;            // 本轮是否真的展示过卡片（未展示则不收尾）
  stageLabel?: string;         // 执行阶段摘要（无则省略次行的该段）
}
export function planRecoveryCard(input: RecoveryCardInput): {
  phase: "hidden" | "syncing" | "done";
  message: string;
  detail?: string;
};
```

### 测试策略

`tests/frontend/test_recovery_card_contract.py`，沿用项目既有的「纯函数单测（真跑 TS）+
接线契约（读源码断言）」两段式：

- **纯函数**：无草稿也提示；已关闭则本轮 `hidden`；终态写入后转 `done`；`runActive=false`
  收起；`detail` 拼装（阶段 / 步数 / 已同步）
- **接线**：EmbedChat 用卡片替换该 toast（原 `showToast("会话仍在处理中…")` 消失）；
  `streamReplayNoticeShown` 的提示职责由卡片状态接管；诊断日志已清除

## 明确不做

- 不改造 `ChatTodoCard`（任务清单语义保持独立）
- 不动宿主 iframe 的「初始化智能对话中...」遮罩（属于 `Chat.vue` 的加载态）
- 不动其它 toast（沙箱降级、配额、快捷按钮补发等）
- 不改造 `AgentDebug.vue`（它没有续显恢复链路）
- 不新增后端接口 / 字段
- 顺手删除本轮排查期埋下的 `[Recovery]` / `[Replay]` 诊断日志
