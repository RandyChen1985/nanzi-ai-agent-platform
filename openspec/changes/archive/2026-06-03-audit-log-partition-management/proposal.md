## Why

随着智能体交互频次的大幅增加以及 ChatBI 场景下大体积工具执行记录的写入，底层的审计访问日志表 `ai_agent_access_logs` 和步骤级执行追踪表 `ai_agent_execution_traces` 数据量急剧膨胀。直接在单表中存储与通过 `DELETE` 清理历史冷数据存在明显的性能瓶颈，极易引发大事务锁表或数据库 I/O 阻塞。为了在长周期运维中平滑系统负载，需要将日志表升级为 MySQL 原生的 Range 按月分区表，并配合定时任务自动扩容与 Drop 回收，同时在系统管理端为管理员（Admin）提供可视化的“日志管理”与一键手动清理功能。

## What Changes

- **系统配置管理界面新增 Tab**：仅对 `admin` 角色用户开放“日志管理”功能模块。
- **日志保留天数设置**：支持管理员配置日志保留期限（对应参数 `audit_log_retention_days`），并能够保存应用。
- **分区监控可视化**：能够动态展示 `ai_agent_access_logs` 与 `ai_agent_execution_traces` 两张表的物理分区健康状态，包含每个分区的名称、截止范围和估算数据行数。
- **手动一键清理**：支持管理员点击按钮，手动对过期分区执行秒级 Drop 回收；对未完成分区改造的环境，采用微批量 DELETE 降级清理。
- **分区运维自动化（Scheduler）**：后端调度器每日自动运行分区维护，预先为未来月份建好分区防止写入溢出，同时 Drop 过期的历史月分区。
- **数据库主键与分区 DDL**：在生产和开发库中将上述两张表的 `id` 主键变更为 `(id, created_at)` 联合主键，并初始按月配置 Range 分区。

## Capabilities

### New Capabilities
- `audit-log-partition-management`: 包含数据库日志表的分区设计、后台分区自动扩容与 Drop 清理维护的 Scheduler 任务、前台管理员日志生命周期参数配置以及分区状态监控的可视化能力。

### Modified Capabilities
<!-- 无 -->

## Impact

- **数据库**：`ai_agent_access_logs` 和 `ai_agent_execution_traces` 的主键定义和表结构发生变更，需要执行一次 DDL 升级。
- **后端 API 与服务**：在 `ConfigService` 中增加对 `audit_log_retention_days` 的配置项支持；增加日志管理相关的 API（获取分区信息、手动清理触发等）；在定时任务调度模块中加入每日分区自动维护任务。
- **前端配置页**：修改 [SystemConfig.vue](file:///Users/chenxiaolong/workspace/yovole-nanzi-ai-agent-platform/frontend/src/views/SystemConfig.vue) 组件以支持“日志管理” Tab 的展示、权限控制、状态请求及交互逻辑。
