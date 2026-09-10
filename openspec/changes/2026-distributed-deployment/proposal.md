# 提案：多 Pod / 多节点分布式部署改造（分阶段）

- 日期：2026-09-10
- 状态：Partial（已完成首批落地，后续延后）

## 背景

当前系统本质是**单节点设计**（`k8s_deploy/deployment.yaml` 默认 `replicas: 1`，`pvc.yaml` 为 `RWO` 单 PVC，
`app` 内存在多处进程内状态依赖）。`k8s_deploy/README.md` 的「多副本前置条件」表明确列出限制并声明
「多副本属于后续架构改造和验收事项，不是修改一个 replicas 数字即可完成的部署动作」。

本提案按**风险由低到高**分阶段推进，先做低风险高收益项，高风险项单独评估，避免一次性大改破坏现有单节点功能。

## 决策约束（来自评审结论）

- 数据源连接池（`pool_manager.py`）、向量索引（Redis/RediSearch）、会话分布式锁（Redis）等**已跨节点安全**，不在本提案改动范围。
- **已落地（本批交付）**：
  1. 给无锁的系统定时任务补 Redis 分布式锁（`_system_audit_log_maintenance_job`）；
  2. 审计日志队列 `asyncio.Queue` → Redis 共享 FIFO（保留无 Redis 回退、落库失败重试）。
- **暂缓 / 后续阶段**：部署清单 RWO → RWX（用户叫停，`pvc.yaml` 仅留迁移注释）、调度器选主、会话注册表去进程内、浏览器/沙箱去节点绑定——需专项设计后再动工。

## 目标环境

按 Kubernetes/K3s 多节点为默认目标评估；Docker Compose/裸机多实例的差异另标注。

## 验收方式

- 每阶段完成后由用户在控制台执行 `./dev.sh` 验证（Agent 不代跑）。
- 变更项补充/更新 pytest 契约测试，`pytest` 通过。
- 部署清单用 `kubectl kustomize k8s_deploy` 与 `kubectl apply --dry-run=client -k k8s_deploy` 预检。