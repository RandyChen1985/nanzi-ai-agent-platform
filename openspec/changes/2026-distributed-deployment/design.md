# 设计：分布式部署改造分阶段设计

## 阶段划分与风险

| 阶段 | 内容 | 风险 | 状态 |
|---|---|---|---|
| P1 | 系统定时任务补 Redis 分布式锁 | 低 | ✅ 已实现 |
| R1（原 P3） | 审计日志 `asyncio.Queue` → Redis 队列 | 中 | ✅ 已实现 |
| P2 | 部署清单 RWO → RWX（含文档） | 低 | ⏸ 用户叫停，仅留注释 |
| R3（原 P4） | 调度器 Leader Election | 中·高 | ⏳ 后续 |
| R4（原 P5） | 会话注册表去进程内 | 高 | ⏳ 后续 |
| P6 | 浏览器/沙箱去节点绑定 | 高 | ⏳ 后续 |

---

## P1：系统定时任务补 Redis 分布式锁（✅ 已实现）

### 现状盘点（已核对）
`scheduler_service.py` 注册的系统级任务：
- `_system_audit_log_maintenance_job` —— **无锁** ❌（需补）
- `_system_memory_consolidation_job` —— ✅ 已有锁（`lock:system_memory_consolidation:...`）
- `_system_knowledge_metrics_sync_job` —— ✅ 已有锁
- `_system_third_party_user_sync_job` —— ✅ 已有锁
- `_system_scheduler_reconcile_job` —— 对账任务（为同步非调度节点写操作，专用于多节点，本身不需要互斥锁）

### 改动
给 `_system_audit_log_maintenance_job` 增加与其他系统任务一致的 Redis 分布式锁（分钟级 key + `nx=True` + TTL），
避免多节点重复执行日志分区扩容/清理。

现状锁的写法统一为：
```python
lock_key = f"lock:{job}:{datetime.now().strftime('%Y%m%d%H%M')}"
if not await redis.redis_client.set(lock_key, "locked", ex=TTL, nx=True):
    logger.warning("... skipped: lock already acquired by another node.")
    return
```

### 验收
- `pytest` 通过
- 多节点同时触发时同一分钟内只有一次真正执行

---

## P2：部署清单 RWO → RWX（⏸ 用户叫停，仅留注释，未改 accessModes）

### 现状（已核对）
- `pvc.yaml`：`accessModes: [ReadWriteOnce]`，注释「多副本必须改为集群可用的 RWX 共享存储」
- `deployment.yaml`：`replicas: 1` + 注释「RWO PVC 与单副本数据目录配套；升级时先停旧 Pod，再挂载新 Pod」+ `Recreate` 策略
- `k8s_deploy/README.md`「多副本前置条件」表 + 第 1007/1261 行

### 落地情况（如实记录）
用户在本批明确叫停 PVC 改动，**未修改 `accessModes`**，仅在 `pvc.yaml` / `deployment.yaml` / `README.md` 标注多副本迁移指引：
- RWX 依赖集群提供支持的 StorageClass（如 NFS/Longhorn RWX/云厂商）；RWO 仍是当前默认、单节点基线。
- 多副本需：改 `accessModes` → RWX + 显式 `storageClassName`，并把 `deployment.yaml` 的 `Recreate` 改为 `RollingUpdate`、提升 `replicas`；同时先完成调度选主、会话/浏览器进程内状态迁移，否则直接扩容会暴露真实缺陷。

> 说明：RWX 是存储/运维侧能力，代码无法凭空保证；后续如要扩容，需目标集群具备支持 RWX 的 StorageClass，并同步完成 R3/R4/P6。

---

## 审计队列改 Redis（✅ 已实现，原 P3 → 重新编号 R1）

### 现状（已核对）
- `audit_service.py`：`_queue = asyncio.Queue()` 进程内队列，`_worker_loop` 批量落库。
- 落库失败**无重试**（源码注释自认），节点崩溃丢队列。

### 实际实现（已交付）
- `enqueue_log` 改为：Redis **`RPUSH`** 到 `audit:log_queue`（FIFO）；Redis 不可用/配置关闭时回退**进程内队列**（保持单机可用）。
- `_worker_loop` 改为：Redis **`BLPOP`**（队头阻塞取，带 1s 超时），配合 `RPUSH` 实现「多生产者 / 多消费者」，崩溃不丢、可多节点消费。
- 落库失败时把 log 放回队列（带递增 `_retry_count`，达到 `_MAX_RETRIES=5` 才丢弃，防止死循环）；requeue 走 Redis，Redis 再不可用回退进程内队列。
- 新增开关 `AUDIT_USE_REDIS_QUEUE`（`app/core/config.py`，默认 `True`），关闭即回退单机原行为。
- 兼容：保留 `_queue` 作为降级路径，改造不破坏现有 middleware 调用链。
- 专项测试：`tests/services/test_audit_redis_queue.py`（no_infrastructure + monkeypatch，不打真实 Redis/DB）。

### 风险与取舍
- 审计落库现在多了一层 Redis 中转依赖；Redis 出问题时会回退进程内队列（不丢，但退回单机模式）。
- 多节点共享同一 `audit:log_queue` 由各节点 worker 竞争消费，顺序由 BLPOP 保证队头先取。

---

## R3：调度器选主（Leader Election，后续阶段执行，原 P4）

### 现状（已核对）
- `config.py`：`TASK_SCHEDULER_ENABLED`，注释「仅建议在一个节点开启」。
- `main.py`：`if settings.TASK_SCHEDULER_ENABLED: await scheduler_service.start()`。
- `scheduler_service`：AsyncIOScheduler 单例；大量接口（`upsert_task`、`apply_platform_timezone_change`、`get_next_run_time`）操作**本进程** `self._scheduler`。

### 关键依赖（导致该阶段不是纯低风险）
- 非 leader 节点 `self._scheduler is None`，`upsert_task` 会直接 warning 跳过 → **leader 与非 leader 的写操作必须路由/转发**（现存 `system_scheduler_reconcile` 对账任务部分缓解读端一致，但写端未解决）。
- 需 Leader Election：Redis `SETNX` 续租 + 主备探活；token 随机、TTL 续租、优雅释放。
- 需处理：leader 变化时本地 scheduler 的 start/stop 切换。

### 建议
> 该阶段需独立设计（含 leader 写操作路由），不并入本次"低风险"交付。若只求**短期多副本可用**，
> 可优先依赖 **会话亲和性（sessionAffinity: ClientIP + Ingress sticky）** 作为兜底，把选主延后。

---

## R4/P6：会话注册表 / 浏览器 / 沙箱去进程内（后续阶段）

不在本批交付范围。涉及进程内状态全面迁移到 Redis 或独立执行器，需单独设计。

> 专项结论（会话注册表，经源码核对）：
> - 会话**并发互斥**已由 `session_run_lane` 的 Redis `SET nx` 分布式锁覆盖，跨节点不会同一会话并发跑——这半边已安全，无需改。
> - 残留的进程内部分（`conversation_run_registry._runs` 存 `asyncio.Task` / 子进程 / `cancel_event`）**天然进程局部**，把它塞进 Redis 无实际意义（asyncio.Task 不可跨进程）。
> - 真实缺口是「跨节点取消信令投递」：任务跑在 Pod A、用户在 Pod B 点取消时，需要把取消信号投递到 A 上触发 `handle.request_stop()`。候选方案为 **Redis Pub/Sub 事件总线**（B publish、A 订阅后 set 本地 `cancel_event`，复用现有 `run_handle.cancelled` 轮询机制），成本低、无新依赖。是否实现取决于多副本上线时间表。