# Todo 清单终态语义（settled）设计

## 背景

`todo_write` 的实时状态依赖模型主动更新，后端仅在 `execution_status == "success"`
时补全未完成项（见 `2026-08-22-todo-finalization-design.md`）。实际使用中出现两个
问题：

1. 一轮已经结束（停止、取消、异常、进程重启、或历史遗留快照），但清单仍停留在
   `pending` / `in_progress`，`ChatTodoCard` 继续显示「进行中」。
2. `activeTodoTimeline` 只判断「消息里有没有 todo」，不判断轮次归属与终态，导致
   **任意历史轮次**的清单都会常驻输入框上方，刷新页面后依旧显示。

根因是状态机与展示层之间缺少「本轮已终结」这一信号：后端刻意不把未执行步骤伪装成
`completed`（正确），但前端没有任何依据区分「真的在跑」和「早就结束了」（问题）。

## 目标

- 已终结轮次的清单不再出现在输入框上方的 banner 中；历史消息内的时间线仍可查看。
- 历史时间线里的卡片显示「已结束 / 已中断」，不再显示「进行中」。
- 不新增 Todo 状态枚举，不修改模型的入参协议，不改变 `success` 补全语义。

## 非目标

- 不引入持久化的任务管理系统、重试、任务 ID。
- 不把 `awaiting_*` 暂停态当作终结：恢复后仍要继续展示「进行中」。
- 不改变 `settlePendingTimelineSteps` 对 todo 项「不改 status」的既有契约。

## 方案

### A. 展示层轮次收敛（前端）

`activeTodoTimeline` 从「全量扫描最后一条含 todo 的消息」收紧为：

1. 只取**最后一条 agent 时间线消息**（含在途草稿 `activeStreamDraft.msg`）；
2. 该消息必须处于**活跃或挂起**状态才展示：
   - 活跃：`isProcessing || remoteRunActive || remoteRunBusy`
   - 挂起：消息上存在 `pendingPermission` / `pendingExternalExecution` /
     `businessConfirmation` / `userQuestion`
3. 其余情况（包括刷新后 `isProcessing=false` 且 run-status 为 inactive）一律不展示。

历史消息内的时间线不受影响，`ChatExecutionTimeline` 仍然渲染 todo 项。

### B. 终态标记落库（后端 + 类型）

新增展示字段（不改变 `todo.status`）：

```json
{ "type": "todo_update", "todos": [...], "counts": {...}, "settled": true, "settledStatus": "cancelled" }
```

- `settled`：本轮清单已终结，不会再更新；
- `settledStatus`：终结原因，用于文案（`success` / `cancelled` / `error` / `interrupted` / `rejected` 等）。

后端规则（`settle_todo_items`）：

| execution_status | 行为 |
| --- | --- |
| `success` | 沿用 `complete_todo_items` 全标 `completed`，并补 `settled=true`；清单本来就全完成时也发一次终态事件（内容不变） |
| `awaiting_permission` / `awaiting_external_execution` / `awaiting_user` / `interrupted` | 不动，等待恢复 |
| 其他终态（`cancelled` / `error` / `rejected` / …） | 保持原 `status`，补 `settled=true` + `settledStatus` |

时间线快照与 `process_timeline` 持久化使用同一份数据；`process_timeline` 是 JSON 列，
新增字段不需要数据库迁移。

### 数据流

```text
模型调用 todo_write ──► todo_update（无 settled）──► 前端实时渲染「进行中」
        │
        └─ 本轮终结
             ├─ success   ──► 全标 completed + settled=true ──► 卡片折叠为「已完成」
             ├─ 其他终态   ──► 保持 status + settled=true + settledStatus ──► 卡片「已结束 · N 项未完成」
             └─ awaiting_* ──► 不发 settled ──► 恢复后继续
```

为什么全完成也要发事件：前端两条渲染通道都以「事件里的 `settled`」为准。若不发，
实时界面在 `run_status` 之后既不满足 banner 条件（本轮已结束）、也不满足时间线归档
条件（`settled` 未到），清单会先消失、刷新读库后才重新出现（AgentDebug 没有收尾期
历史回灌，会一直看不到）。

前端 `ChatTodoCard`：

- `settled=true`：标题显示「任务清单 · 已结束」/「已中断」，灰色、默认折叠、
  不再渲染旋转图标与「进行中」标签；
- `archived=true`（归档到执行过程）：不提供关闭按钮、不沿用 banner 的「已关闭」记忆，
  未终结时只显示「N 项未完成」，不谎称进行中；
- `settled` 缺失（旧快照）：由 A 的规则保证不会常驻 banner，在时间线里按归档视图展示。

### 归档与 banner 的分工

清单在任一时刻只渲染一处：

- banner（输入框上方）：`resolveBannerTodoTimeline` 只认最后一条助手消息 / 在途草稿，
  且要求「本轮在跑」或「挂着等待用户操作的卡片」；
- 时间线（执行过程）：`ChatExecutionTimeline` 通过 `hide-todo` 接收「这一条正由 banner
  展示」，此时时间线内过滤清单；其余情况（已终结、旧快照、提问轮）一律归档展示。

不把归档条件绑死在 `settled` 上，是为了兜住三类没有终态标记的清单：旧数据（迁移前
的历史轮次）、`awaiting_user` 轮（用户回答会开新 trace，本轮不再有收尾机会）、
以及确认卡 TTL 过期后不再有挂起卡的消息——这些轮次的清单否则会既不在 banner 也不在
时间线，等于永久丢失。

`timelineHasPending` 对 `settled=true` 的 todo 不再计入 pending，避免思考卡
`is-thinking` 与 `AgentDebug` 展开判断被历史清单卡住。

## 测试

- 后端：非 success 终态产生带 `settled` 的事件且 `status` 不变；`awaiting_*` 不产生；
  `_apply_todo_update` 折叠时保留字段；`success` 全标 completed 且带 `settled`；
  清单本来就全完成时也必须返回带 `settled` 的事件。
- 前端契约：`upsertTimelineTodo` / `hydrateHistoryProcessTimeline` 保留 `settled`；
  `timelineHasPending` 忽略 settled；归档视图保留未终结清单、`hideTodo` 时才过滤；
  多条 `todo` 项只保留最后一份；`countTimelineSteps` 不计清单；`resolveTimelineCurrentStep`
  跳过 settled；已提交的确认/提问卡不再撑住 banner；`ChatTodoCard` 的 `archived` 契约；
  两处 `activeTodoTimeline` 只取最后一条 agent 消息。
- 类型检查：`vue-tsc --noEmit`。
