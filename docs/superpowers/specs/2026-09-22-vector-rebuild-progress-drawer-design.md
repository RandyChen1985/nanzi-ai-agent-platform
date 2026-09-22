# 向量重构进度抽屉（本地向量 + 记忆向量）

**日期：** 2026-09-22
**状态：** 已批准，实施中
**背景：** 用户在系统诊断点「重构本地向量数据」后，只能在后台终端看日志；在记忆工作台点「检查/创建索引」更是毫无反馈（该问题的根因已单独修复：`ensure_index` 原先只判索引存在、不校验 DIM）。需求是把两处重构都做成「元数据同步 RAGFlow」那样的右侧抽屉，实时显示进度与详细日志。

## 现状约束（决定了设计的形状）

1. 现有可复用模式：`MetadataSyncLogService`（Redis Hash 存任务状态 + Redis Stream 存事件 + TTL 1800s）+ SSE 端点 + `MetadataDatasets.vue` 内联右侧抽屉。
2. **真正耗时的再向量化工作目前全部脱离了请求链路**：`MetadataIndexService.sync_local_redis_vector()` 与 `ExampleIndexService.sync_all_examples()` 内部都是 `asyncio.create_task(_run_sync())` 甩出去就返回。因此「重构」编排既不知道进度、也不知道何时结束——**必须先让这条链路可 await、可回调**，否则抽屉只能是假的。
3. `rebuild_local_vector_indexes()` 还被启动流程 `maybe_rebuild_local_vectors_on_startup()` 复用，启动路径不能变异步任务。
4. 记忆侧数据当前为 `memory:summary:*`（session / consolidated / daily 混合），embedding 字段是二进制，必须用 `get_redis_binary()` 读取。

## 一、通用任务日志通道

新增 `app/services/task_log_service.py`：

- `TaskLogService`，Redis 键可通过类属性参数化（默认 `task_log:task:{task_id}` / `task_log:events:{task_id}`），`TASK_TTL_SECONDS = 1800`，`STREAM_MAXLEN = 2000`，`TERMINAL_EVENTS = {completed, failed}`。
- 方法：`create_task(scope, **meta)`、`publish(task_id, event=..., stage=..., message=..., progress=..., done=..., total=..., error_detail=..., failed_items=...)`、`read_events(task_id, after_id)`、`read_new_events(task_id, after_id, block_ms, count)`、`get_task(task_id)`、`is_terminal(event)`。
- `publish` 校验：终态事件的 `stage` 必须等于 `event`；`event` 仅允许 `started` / `progress` / `completed` / `failed`。
- `MetadataSyncLogService` 收敛为它的薄封装：继承 `TaskLogService` 并覆盖键模板与 `dataset_id` 语义，**对外 API 与 Redis 键格式保持不变**，既有 30 项 SSE / 同步契约测试是护栏。

## 二、冻结契约

### SSE 事件 JSON（每行一个 `data:`，中文不转义）

```json
{
  "task_id": "vrb_xxx",
  "scope": "local_vector_rebuild",
  "event": "started | progress | completed | failed",
  "stage": "lock | drop | index | count | metadata | examples | scan | reembed | summary",
  "stage_label": "重建元数据向量",
  "message": "数据集【销售】完成：表 3 + 指标 1",
  "progress": 42,
  "done": 12,
  "total": 90,
  "elapsed_ms": 12345,
  "error_detail": null,
  "failed_items": [{"name": "数据集【X】", "error": "..."}]
}
```

`progress` / `done` / `total` / `error_detail` / `failed_items` 允许缺省或为 `null`；`failed_items` 仅出现在 `summary` / `failed` 事件。

### 端点

| 方法 | 路径 | 响应 |
|---|---|---|
| POST | `/api/portal/system/redis/rebuild-vectors` | `200 {"task_id", "message"}`；已有任务在跑 → `409 {"detail"}` |
| GET | `/api/portal/system/redis/rebuild-vectors/{task_id}/events` | SSE |
| POST | `/api/portal/memory/vectors/rebuild` | `200 {"task_id", "message"}`；已有任务在跑 → `409 {"detail"}` |
| GET | `/api/portal/memory/vectors/rebuild/{task_id}/events` | SSE |
| POST | `/api/portal/memory/index/rebuild` | `200 {"status":"success","data":{...既有 ok/action/index_dim/configured_dim/message..., "task_id"}}` |
| GET | `/api/portal/memory/index/rebuild/{task_id}/events` | SSE |

SSE 一律「先回放 `after_id=0-0` 的历史事件，再阻塞等待新事件」，因此在极快任务上打开抽屉仍能看到完整日志。

### 前端组件契约

`frontend/src/components/common/TaskProgressDrawer.vue`

```ts
props: {
  open: boolean
  title: string
  subtitle?: string
  streamUrl: string                                   // 为空则不连接
  meta?: Array<{ label: string; value: string }>      // 顶部附加信息行
}
emits: { close: []; finished: [{ ok: boolean; message: string }] }
```

职责：连接 SSE、渲染状态/阶段/进度条/`done/total`/耗时、逐条时间线（失败标红 + `error_detail`）、结束汇总；**关闭抽屉不取消任务**（底部注明）。复用 `@/utils/chartRenderer` 的 `createSseLineParser`。

## 三、本地向量重构任务化

新增任务编排 `start_local_vector_rebuild(trigger)`（在 `local_vector_rebuild.py`），阶段与日志：

1. `lock`：`SET NX` 抢 `nanzi:lock:rebuild_local_vectors`；抢不到返回 `None`（端点转 409）。
2. `drop`：`FT.DROPINDEX ... DD` 删 metadata / example 索引，逐条日志。
3. `index`：重建两个索引，日志写明 DIM（维度变更时写 `1024 → 1536`）。
4. `count`：统计启用数据集 / 表 / 指标 / 已批案例总数 → 设定 `total`。
5. `metadata`：逐数据集一行（名称、表数 + 指标数、耗时），`done++`。
6. `examples`：逐案例一行。
7. `summary`：`成功 x / 失败 y`，附 `failed_items`；终态 `completed`（全失败时 `failed`）。

同步函数改造（**默认值保持现有行为，零回归**）：

- `MetadataIndexService.sync_local_redis_vector(dataset_id, *, progress_cb=None, wait=False)`：`wait=True` 时 `await _run_sync()` 并返回 `bool`，不再 `create_task`；`progress_cb` 在每张表 / 指标 embedding 完成后回调。
- `MetadataIndexService.sync_all_datasets(*, progress_cb=None, wait=False)`：透传。
- `ExampleIndexService.sync_all_examples(*, progress_cb=None, wait=False)`：同上，逐案例回调。

启动路径 `maybe_rebuild_local_vectors_on_startup()` 继续调用原函数（不传新参数），行为不变。

## 四、记忆向量重构

- `MemoryIndexService.reembed_all(progress_cb=None) -> dict`：SCAN `memory:summary:*`；binary client 读 HASH；按 `summary_type` 取待嵌入文本（session / consolidated 用 `summary`；daily 与 `daily_summary_service` 生成时的文本口径一致）；逐条 `EmbeddingClient.embed_text` → 写回 `embedding` 与 `embedding_missing=0`，其它字段与 TTL 不变。无文本计入 `skipped`，单条失败记 `error_detail` 并继续。
- 阶段：`index`（复用 `ensure_index(force=True)`）→ `scan`（统计 `total`）→ `reembed`（逐条一行）→ `summary`。
- 锁：`nanzi:lock:rebuild_memory_vectors`。
- `POST /memory/index/rebuild`（「检查/创建索引」，秒级）也创建一个任务并立即完成，事件包含维度检查 / 是否重建 / 结果；响应体保留既有字段并追加 `task_id`。

## 五、前端接入

- `SystemConfig.vue`：确认后 `POST` 拿 `task_id` → 打开抽屉连 SSE；原 `appendLog` 日志控制台保留（不再是本次操作的主反馈）。
- `MemoryManagement.vue`：`检查/创建索引` 与新增 `重构记忆向量` 两个按钮共用抽屉。
- 元数据同步 RAGFlow 的既有内联抽屉**本次不迁移**，避免一次性扩大回归面。

## 六、测试

- 后端：`TaskLogService` 单测（发布 / 回放 / 终态 / 非法事件）；编排单测（阶段顺序、`done/total`、单条失败不中断、汇总与 `failed_items`）；`reembed_all` 单测（binary 读取、跳过无文本、写回 embedding、失败继续）；**护栏**：`sync_*` 不传参数时仍走 `create_task`（行为与改造前一致）。
- 前端契约：抽屉组件存在、两处按钮接线与 SSE URL 正确、关闭不取消任务。
- 全量回归与 `HEAD` 基线逐条比对零新增失败；`vue-tsc --noEmit` 零报错。

## 七、风险与不做的事

- **真实成本**：全量重算会真实调用 Embedding API（当前 90 个数据集 + 92 条记忆，表/指标量级更大），耗时长且消耗额度——确认弹窗必须写明。
- `asyncio.create_task` 是进程内的，SSE 必须连到发起任务的同一进程；与现有元数据同步同一假设。多 worker 下彻底解决需要 Redis Stream 跨进程消费，属更大改造，**不在本次范围**。
- 不做任务历史列表 / 断点续跑 / 取消正在运行的任务；关闭抽屉不取消（沿用既有语义）。

## 八、实施记录（与设计的差异）

1. **`stage_label` 由后端统一给**：`TaskLogService.STAGE_LABELS` 覆盖两个 scope 的全部阶段键（含元数据同步的 `loading` / `knowledge_base` / `parsing`），前端只做 `stage_label || stage` 回退，不必内置映射表。
2. **一个任务只能有一个终态事件**（实施中发现的关键约束）：前端抽屉读到第一个 `completed` / `failed` 就停止订阅并 emit `finished`。因此 `_ensure_index_with_events()` 新增 `emit_terminal` 参数——作为「检查/创建索引」独立任务时为 `True`，作为记忆重构任务的一个阶段时为 `False`（中途只发 `progress`，终态留给整条链路）。测试断言「整条任务的终态事件恰好 1 个」。
3. **记忆重构索引阶段不复用 `MemoryIndexService.rebuild_index()`**：它会把 ensure 结果转成一句人话（`ensure_message()`，两处共用），但需要按阶段插事件，故编排侧直接调用 `ensure_index(force=True)` + `last_ensure_result()`。`rebuild_index()` 保留（其单测仍覆盖）。
4. **响应包装差异**：`/memory/vectors/rebuild` 与 `/memory/index/rebuild` 按记忆模块既有风格包在 `data` 里（`{"status": "...", "data": {...}}`）；`/system/redis/rebuild-vectors` 不包装（`{"status", "task_id", "message"}`）。`/memory/index/rebuild` 在 `ok=false` 时 `status` 为 `error`（与改动前一致）。
5. **`reembed_all` 增加一次 `scan` 回调**：编排需要先拿到 `total` 才能算出百分比，故 SCAN 完成后立即回调 `{"phase": "scan", "total": N}`。
6. **旧函数 `rebuild_local_vector_indexes()` 保留**：启动路径 `maybe_rebuild_local_vectors_on_startup()`（`drop_indexes=False, acquire_lock=True`）仍走它，手工端点改走任务化路径。
7. **锁加固（实施后复查发现的三处真实缺陷）**：新增 `app/services/task_lock.py` 的 `TaskLock`（value=持有者标识的 compare-and-delete + `hold()` 上下文管理器自动续期），修掉：
   - **长任务中途掉锁**：修复前的 `SET NX EX 1800` 在任务跑超过 30 分钟时自动过期，第二次点击不再被拦、两个重构并发跑；且 `TaskLogService.publish` 原先**只在终态事件**刷新任务 Hash 的 TTL，过期后 `get_task` 返回 None → `publish` 因「任务不存在」静默失败、前端抽屉从此断流。现改为：锁按 `renew_interval`（60s）持续续期，`publish` 每次事件都刷新任务 Hash TTL。
   - **抢到锁之后建任务失败 → 锁泄漏 30 分钟**：`start_*_rebuild()` 现在在 `create_task`/`asyncio.create_task` 失败时立刻 `release()` 再抛。
   - **启动锁会挡住手动重构**：启动路径故意把锁留到 TTL 到期（它无法感知 fire-and-forget 同步的结束时间），而它原先与手动重构**共用** `nanzi:lock:rebuild_local_vectors`，导致每次重启后 30 分钟内点「重构本地向量数据」都会 409 且提示一个 `task_id=startup` 的假任务。现拆成 `nanzi:lock:startup_local_vectors`（保留到 TTL，语义不变）与 `nanzi:lock:rebuild_local_vectors`（任务结束即释放，409 提示改用 `describe_lock_owner` 如实描述持有者）。
8. **手动任务锁改用短 TTL（180s）+ 60s 续期**：因为有续期心跳，进程活着时锁不会掉；进程一旦崩溃/被 `--reload` 重启，用户最多等 3 分钟即可重试，而不是干等 30 分钟。启动锁保持 1800s（它守护的是无法感知结束时间的 fire-and-forget 同步）。
9. **任务记录必须原子设置 TTL**：`create_task()` 的 `hset` 与 `expire` 原先分两个 await 提交，进程若在两者之间被杀（开发环境 `--reload` 很常见）会留下**永久没有 TTL** 的任务 Hash —— 现场实测抓到一个 `TTL=-1` 的残留（它的 events stream 也不存在，正是「创建后被就地杀掉」的指纹）。现改为同一个 `pipeline()` 原子提交，并用真实 Redis 断言 `create_task` 后 TTL=1800。
10. **事故复盘（用户实测反馈）**：用户遇到「锁住了、无法重新重构」。排查 Redis 实况发现三把锁都有 TTL，其中两把是残留：`nanzi:lock:rebuild_local_vectors='startup'`（旧版本启动路径留下的，正是挡手的那个）与 `nanzi:lock:rebuild_memory_vectors=<已完成任务的 task_id>`（进程在其发布 `completed` 之后、执行 `finally` 释放之前被打断）。另有两个 `status=running` 的重构任务**一条事件都没有**，是 `--reload` 就地杀掉的僵尸。结论：TTL 兜底有效，但「共用锁键 + 长 TTL + 无续期」的组合会让一次进程重启把用户挡 30 分钟，故有上述 7/8 两项改造。
