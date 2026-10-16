# EmbedChat 顶部「进行中的会话」入口与强制终止

- 日期：2026-10-16
- 涉及：`app/api/v1/endpoints/chat.py`、`app/services/ai/runtime/session_run_lane.py`、`frontend/src/utils/cancelConversationRun.ts`、`frontend/src/composables/chat/useActiveRuns.ts`（新增）、`frontend/src/components/chat/ActiveRunsPopover.vue`（新增）、`frontend/src/views/EmbedChat.vue`、`tests/frontend/test_active_runs_contract.py`（新增）、`tests/CHECKLIST.md`

## 背景

一个会话同一时刻只允许一个 run，由 Redis lane 锁 `nanzi:conv_run:{uid}:{cid}`
强制串行（TTL 默认 600s，持有期间每 `ttl/3` 自动续约）。

「停止」目前只有两个入口：

1. 输入框的停止按钮——显示条件是 `isProcessing || remoteRunBusy`；
2. 排队等待条目里的「终止」——只在消息撞上会话锁、进入 followup 等待时才出现。

而 `remoteRunBusy = active && !outputCompleted`，即**输入框释放不等于会话空闲**。
恢复卡片（`ChatRecoveryCard.vue`）是唯一提示「后台还在跑」的地方，但它只有一个
「关闭提示」按钮，没有任何终止操作。

## 问题

四个已核实的错位路径，共同结果是「会话还在跑，但界面上找不到停止按钮」：

1. **输出完成但锁未释放**。`markOutputCompleted()` 只解锁输入，`remoteRunActive`
   保持锁真相（这是有意设计）。此时服务端仍在 FinalizeStep 落库/摘要，停止按钮消失。
2. **run-status 轮询失败即清零停摆**。`useConversationRunStatus.ts` 的 `catch` 分支把
   `remoteRunActive`、`remoteRunBusy` 一起置 false 并 `stopPolling()`，一次网络抖动就被
   判成空闲，且不再自动纠正。
3. **历史同步重试耗尽**。`syncLatestSessionHistory` 在重试耗尽时会
   `markOutputCompleted()`，而 `outputCompleted=true` 会持续压制 `remoteRunBusy`，
   复位条件只有「轮询到 `active=false`」。只要锁一直 active，输入框就永久自由，
   而恢复卡片还在转——用户看得见「后台在跑」，却没有按钮能停。
4. **切换会话路径缺 `resetRunStatus`**。`handleHistoryClick` 只调用
   `refreshCurrentRunStatus()`；上一轮的 `outputCompleted=true` 会残留到目标会话，
   导致从历史侧栏或本面板点进一个正在运行的会话时 `remoteRunBusy=false`，
   没有「正在处理」提示、没有停止按钮。

用户诉求：在 EmbedChat 顶部加一个「进行中的会话」入口，能看、能进、能停，
兜住僵死锁与上述状态错位。

## 方案对比

| 方案 | 做法 | 评价 |
|---|---|---|
| **A. 后端只读接口 + 前端映射标题（选中）** | 新增 `GET /api/v1/chat/active-runs`，`SCAN nanzi:conv_run:{uid}:*` 返回锁列表；标题用前端已有的会话历史列表映射 | 跨标签/跨设备全局可见（这是「防僵死」的前提）；后端零 DB 查询；僵死判定口径集中在后端一处 |
| B. 后端连标题一起返回 | 同 A，额外按会话查历史表取标题 | 标题覆盖更全，但每个活跃会话多一次查询，且与前端列表口径可能不一致 |
| C. 前端聚合，不新增接口 | 遍历前端已知会话逐个调 `run-status` | **不可行**：只能覆盖本设备记录过的会话，别的标签/设备正在跑的会话根本枚举不到，正是要解决的场景 |

用户选择**方案 A**。

### 采纳方案 A 时确认的五个决策点

| 议题 | 决定 |
|---|---|
| 列表范围 | 只列运行中的会话，并区分「正常活跃」与「疑似僵死」 |
| 终止语义 | 统一走现有 `POST /api/v1/chat/cancel`，不新增「仅释放锁」入口 |
| 面板形态 | 顶部栏常驻按钮 + 下拉浮层（参考后台任务面板的折叠样式） |
| 条目信息 | 会话标题 + 状态（active / stale） + 锁剩余 TTL，不做「已运行时长」 |
| `resetRunStatus` 修复位置 | 补在公共切换路径 `handleHistoryClick`，所有切会话入口一起修好 |

## 设计

### 后端：接口契约

**`GET /api/v1/chat/active-runs`**，权限与同一文件里的 `run-status` 一致：
`require_api_key` + `_require_chat_user_id`。

> 路径不能写成 `/conversation/active-runs`——会被已有的
> `/conversation/{conversation_id}` 抢先匹配。`/chat/active-runs` 与 `/history`、
> `/active`、`/cancel` 同级，同属静态段，无路由冲突。

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "ttl_seconds": 600,
    "stale_before_ttl_seconds": 300,
    "items": [
      { "conversation_id": "abc", "trace_id": "uuid 或 null", "ttl_seconds": 512, "state": "active" },
      { "conversation_id": "xyz", "trace_id": null, "ttl_seconds": 87, "state": "stale" }
    ]
  }
}
```

- `ttl_seconds`：当前生效的 lane 锁 TTL 配置值；
- `stale_before_ttl_seconds`：`max(1, int(ttl_seconds * 0.5))`，判定阈值随配置变化，
  前端不自己算；
- `state`：`active` / `stale`，判定口径只存在于后端一处。

### 后端：只读扫描实现

在 `ConversationRunLane` 上新增只读方法 `list_active(user_id)`，端点只做鉴权与封装。

- 用 `scan_iter(match=f"nanzi:conv_run:{glob_escape(uid)}:*", count=200)` 枚举，
  **不使用 `KEYS`**；uid 里的 glob 元字符（`*` `?` `[` `]` `\`）必须先转义，
  否则 SCAN 模式会扩散到别人的会话键（列出他人会话 ID = 信息泄漏）；
- 逐个 key 读取 `GET`（trace token）与 `TTL`。lane 锁数量与「用户并发会话数」同量级
  （个位数），无需 pipeline 优化，也让测试能直接复用既有的 `FakeRedis` 夹具；
- key 解析：`remainder = key[len("nanzi:conv_run:"):]`，再 `partition(":")` 得到
  `(uid, conversation_id)`，并**逐条比对 uid 段**——`match` 只是粗筛，不能当作身份
  边界。key 里的 cid 是 acquire 时经 `:`→`_` 归一化后的形式；当前会话 ID 一律是
  UUID（`createConversationId = createUuid`，不含 `:`），因此解析值可直接作为
  `conversation_id` 返回给前端用于切换会话，无需反向还原；
- **`state` 判定**：lane 锁的续约由 `renew_lock_during_hold` 以 `ttl/3` 为间隔执行，
  所以正常活跃的锁，其剩余 TTL 一定落在 `(2/3·ttl, ttl]`。剩余 `< ttl/2` 说明续约
  已连续错过（进程崩溃、pod 被杀、任务卡死），判为 `stale`。
  **阈值刻意不取 `2/3·ttl`**：正常下限与它零距离，流式输出/工具密集期的一次事件循环
  调度延迟（`sleep` 醒来 → `pexpire` 之间）就足以把正在跑的会话判成 `stale`；而用户对
  `stale` 条目点「强制释放」走的是 cancel 接口，存在 handle 时 `request_stop` 会
  `task.cancel()` —— 会**误杀正在正常运行的 run**。取 `ttl/2` 等于留出半个续约周期的
  容错（默认 600s 时余量 100s）；
- 边界处理：
  - `TTL = -1`（key 存在但无过期，属异常）→ 保守判 `active`；
  - `TTL = -2` 或 key 已消失 → 跳过该条；
  - `agent_session_run_lock_enabled` 关闭 / Redis 不可用 / `user_id` 缺失 → 返回空列表；
- 该方法**不写不删任何 key**，纯只读。

### 前端：文件职责

| 文件 | 职责 |
|---|---|
| `composables/chat/useActiveRuns.ts`（新增） | 状态与轮询：`items / count / staleCount / loading / refresh()`；fetcher 依赖注入，便于契约测试 |
| `components/chat/ActiveRunsPopover.vue`（新增） | **纯渲染**浮层：props 进、emits 出（`select` / `terminate` / `refresh` / `close`），自身不发请求 |
| `views/EmbedChat.vue` | 接线：顶部栏按钮与角标、标题映射、点击与终止的动作 |
| `utils/cancelConversationRun.ts` | 新增 `cancelConversationRunDetailed()` 返回结构化结果；原 `cancelConversationRun()` 委托它并继续返回 `boolean`，既有调用点不变 |

### 前端：轮询策略

- 面板打开时每 3s 刷新；面板关闭时每 15s 低频刷新，只为更新角标数字；
- `document.visibilityState === 'hidden'` 时暂停（复用现有可见性处理模式），
  切回可见时立即刷新一次；
- 同一时刻只允许一个请求在飞（单飞去重）；
- **请求失败保留上一次的数据**，仅在浮层内显示「刷新失败，正在重试」，
  绝不清零角标——`useConversationRunStatus.ts` 的 `catch` 清零正是问题 2 的成因，
  新代码不重犯。

### 前端：入口与浮层

- 位置：顶部栏（`h-12`）左侧标题区与右侧按钮组之间，即用户标注的位置；
- 按钮常驻，带数字角标；存在 `stale` 条目时角标转琥珀色；
- 移动端收进现有的「更多」弹层（`showHeaderMore`），与设置/帮助一致；
- 浮层为锚定下拉（参考后台任务面板的分组折叠样式）：
  分组标题显示条数，条目为「状态点 + 会话标题 + 剩余 TTL + 终止按钮」；
- 标题映射：前端用已有的 `aggregatedHistoryList` 建 `conversation_id → query/summary`
  索引；映射不到（会话不在当前分页窗口内）则显示 conversation_id 短码；
- 打开浮层时立即刷新一次（不等下一个轮询周期），浮层内提供手动刷新按钮；
- 终止成功后该条从列表移除，**浮层保持打开**，便于继续处理其他会话；
- 空态：「当前没有进行中的会话」。

### 交互：点击条目进入会话

点击条目 = 进入该会话，复用 `handleHistoryClick({ conversation_id })`，它已包含
归属校验、旧会话后台 finalize、清空消息列表、拉取历史、移动端自动收起。

但必须先让运行态从锁真相重新初始化，否则问题 4 会让停止按钮不出现：

```
resetRunStatus()            // 清掉上一轮残留的 outputCompleted
handleHistoryClick({ conversation_id })   // 切换会话并加载历史
refreshCurrentRunStatus()   // 目标是 active 时自动开始轮询 → 恢复卡片 + 停止按钮就位
```

`resetRunStatus()` 补在 `handleHistoryClick` 内部（切换成功后、加载历史前），
使历史侧栏、本面板、自动加载三条入口同时受益。

进入会话后浮层关闭（移动端必须收起；桌面端同样关闭，避免遮挡会话内容，
需要再看列表时重新点开即可）。

### 交互：终止

| 终止对象 | 走法 |
|---|---|
| 当前打开的会话 | 复用 `stopGeneration({ notifySuccess: true })`，保证 `isProcessing`、消息标记、恢复卡片一起收敛，而不是只发一个 HTTP 请求 |
| 其他会话 | 调 `cancelConversationRunDetailed(cid, { traceId, headers })` |

其他会话的文案按后端返回区分：

- `run_cancelled = true` → 「已终止运行并释放锁」；
- `run_cancelled = false` 且 `success = true` → 「已强制释放（该会话任务未在运行）」；
- `success = false` → 「释放失败，请重试」（warning）。

终止成功后把该条从本地列表移除并立即刷新一次。

统一走后端 cancel 的理由：该接口在有运行 handle 时真取消任务（并停同会话画布进程、
强释放两把锁），handle 已不在时退化为只强释放锁——正好覆盖僵死与正常两种场景，
不需要第二个「仅释放锁」按钮，也避免误用导致新旧 run 并发写同一会话。

## 明确不做

- 不做「清空」按钮：图 1 的语义是清空已完成任务列表，这里删不掉锁，语义不成立；
- 不显示「已运行时长」：lane 锁只存 TTL、没有起始时间，要做需新增 `started_at`
  或查审计表；
- 不做「仅释放锁」独立操作：统一走 cancel 接口（见上）；
- 不做管理员看全部用户的会话：只列当前身份自己的；
- 不重构 `outputCompleted` 为会话维度标记：本次只在切换路径补 `resetRunStatus`
  消除残留，重构留作后续单独评估。

## 测试策略

- **后端单测**：`list_active` 的 key 解析（含 bytes key）；`state` 三态判定（`active` /
  `stale` / `TTL=-1` 保守 active）；**续约抖动不误报**（剩余 TTL 低于 `2/3·ttl` 但高于
  阈值时仍判 `active`）；`TTL=-2` 跳过；锁功能关闭返回空；非本人 uid 的 key 不返回
  （精确集合断言）；`_glob_escape` 元字符转义。
- **前端契约测试** `tests/frontend/test_active_runs_contract.py`（沿用现有契约测试写法）：
  - 接口路径为 `/api/v1/chat/active-runs`，且不是 `/conversation/active-runs`；
  - `list_active` 实现体内不得出现 `delete` / `await redis.set(` / `force_release`；
  - 终止其他会话复用 `cancelConversationRunDetailed`，当前会话走 `stopGeneration`；
  - 轮询失败分支保留旧数据、不置零（断言源码中不含该 catch 清零写法）；
  - `stop()` 真的停表（`stopped` 标志 + `schedule()` 内检查）、面板重开恢复轮询；
  - 移动端**必须真的有 modal 变体的浮层实例**（只有入口按钮会出现「点了没反应」）；
  - `visibilitychange` 隐藏时暂停、可见时恢复；
  - `responded` 字段区分「请求异常」与「后端明确答复无锁可释放」；
  - `handleHistoryClick` 内调用 `resetRunStatus`（锁死问题 4 的修复）。
- **类型检查**：`vue-tsc --noEmit`。
- 按项目约定在 `tests/CHECKLIST.md` 登记本次改动。

## 风险

- **SCAN 开销**：lane 锁数量与「并发运行的会话数」同量级（每用户个位数），
  `count=200` 的 SCAN 成本可忽略；仍坚持不用 `KEYS`。
- **`stale` 误判的代价（设计期曾低估，实施后已修正）**：进程被调度暂停、pod 被 SIGSTOP
  时续约可能延迟，会把真在跑的会话判成 `stale`。而「强制释放」**并不是**幂等安全操作
  ——后端 cancel 接口在存在 handle 时会 `task.cancel()` 硬取消该 run。因此阈值从
  `2/3·ttl` 放宽到 `ttl/2`（留半个续约周期容错），文案统一用「疑似」/「强制释放」。
  残余风险：续约延迟超过 `ttl/2`（默认 600s 配置下为 300s）的极端场景仍会误报，此时
  用户点终止等于主动取消该会话——与点「停止按钮」的后果一致，可接受。
- **请求量**：关闭时 15s、隐藏时暂停，单用户后台请求量与现有 run-status 轮询同量级。
- **cid 归一化的隐式约束**：lane key 用 `cid.replace(":", "_")` 拼接，当前 cid 均为
  UUID 故无影响。若将来引入含 `:` 的会话 ID，从 key 反解会失真（`partition` 会把多出的
  段并进 cid）——届时应改为把原始 cid 写进锁的 value（目前 value 是 trace token）。

## 实施后审查修复（2026-10-16）

首版实现完成后经独立审查，确认并修复 4 处缺陷（均先写失败测试再改实现）：

| 级别 | 缺陷 | 后果 | 修复 |
|---|---|---|---|
| P1 | `stale` 阈值取 `2/3·ttl`，与正常续约下限零距离 | 事件循环抖动即误报「疑似僵死」；用户点「强制释放」会把**正在正常运行**的 run 硬取消 | 阈值改为 `max(1, int(ttl*0.5))`，留半个续约周期容错 |
| P2 | `stop()` 只 `clearTimeout` | 若此刻有请求在飞，其 `finally` 会重新 `schedule()`，页面 hidden 后仍每 15s 请求 | 增加 `stopped` 标志，`schedule()` 内检查；`start()` / `setOpen(true)` 复位 |
| P3 | 兼容入口 `cancelConversationRun` 收紧为要求 `success === true` | 会话恰好自然结束时后端返回 `success=false`（无 handle、无锁可删），停止按钮弹「服务端未确认释放结果」假警告 | 结果新增 `responded`；入口按 `responded` 判定，只有拿不到响应（真异常）才算失败 |
| P5 | SCAN `match` 未转义 uid，且未二次校验 uid 段 | uid 含 glob 元字符（`*?[]\`）时模式扩散，可能列出他人会话 ID（信息泄漏） | 新增 `_glob_escape`；改用 `partition(":")` 并逐条比对 uid |

审查同时指出的两项「缺口」（`list_active` 只读断言、`/active-runs` 路由断言、CHECKLIST
未登记）经核对为**基于中间快照的误判**——这些在审查进行时已被补齐；其余缺口（移动端浮层
实例、hidden 暂停）确认存在并已补断言。
