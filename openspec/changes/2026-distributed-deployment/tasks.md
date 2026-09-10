# 任务清单：分布式部署改造

## 已完成
- [x] 首轮审核：识别进程内状态依赖、RWO 单 PVC、调度器无选主、沙箱/浏览器节点绑定（详见会话）

## 本次阶段（低风险）
- [x] P1：`_system_audit_log_maintenance_job` 补 Redis 分布式锁（`lock:system_audit_log_maintenance:<minute>`，NX+EX，本调度分钟内仅一节点执行）
- [x] R1：审计日志 `asyncio.Queue` → Redis 共享队列（FIFO，RPUSH 入队 + BLPOP 出队；Redis 不可用/配置关闭时回退进程内队列；落库失败按 `_retry_count<5` 重新入队、超上限丢弃）
- [x] R1 测试：`tests/services/test_audit_redis_queue.py`（Redis 路由、内存回退、BLPOP 解码/脏数据跳过、失败重试与超上限丢弃）——10 项通过

## 后续阶段（待专项设计）
- [ ] R3：调度器 Leader Election（含非 leader 写操作路由）
- [ ] R4：会话运行注册表去进程内 → Redis（owner node + 取消事件）
- [ ] P2/P6：浏览器 / 沙箱去节点绑定；RWO→RWX（PVC，用户暂缓）
- [ ] 文案校验：`kubectl kustomize k8s_deploy` + `kubectl apply --dry-run=client -k k8s_deploy`

## 验证备注
- Agent 不代跑 `./dev.sh`，服务启停由用户在控制台执行。