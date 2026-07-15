# 黄金报表订阅与站内通知 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为黄金报表增加所有者订阅、确定性定时执行、运行历史关联，以及可持久化的全局站内通知。

**Architecture:** 新增独立的报表订阅与站内通知数据模型；复用现有 APScheduler 实例但使用独立 job ID 和执行包装器。报表手动与定时执行共享同一执行核心，通知通过独立服务写库，并由 Dashboard 全局铃铛按需读取。

**Tech Stack:** FastAPI、SQLAlchemy AsyncSession、APScheduler、MySQL 8、Vue 3、TypeScript、Axios、Pytest。

---

### Task 1: 数据模型与迁移

**Files:**
- Modify: `app/models/saved_report.py`
- Create: `app/models/portal_notification.py`
- Create: `db-prod/V97-create-saved-report-subscriptions-and-inbox.sql`
- Test: `tests/test_saved_report_subscription_migration_contract.py`

- [ ] 编写失败测试，验证 V97 同时创建 `portal_saved_report_subscriptions`、`portal_notifications`，外键父子表排序规则一致，并包含中文字段注释。
- [ ] 运行 `venv/bin/python -m pytest tests/test_saved_report_subscription_migration_contract.py -q`，确认因 V97 不存在而失败。
- [ ] 添加 `PortalSavedReportSubscription`、`PortalNotification` 模型和 V97 SQL；订阅表以 `report_id` 唯一约束保证每报表一条订阅。
- [ ] 重跑迁移契约测试并确认通过。

### Task 2: 调度表达式与订阅服务

**Files:**
- Create: `app/services/saved_report_subscription_service.py`
- Modify: `app/services/ai/scheduler_service.py`
- Test: `tests/services/test_saved_report_subscription_service.py`

- [ ] 编写失败测试，定义 daily、weekly、monthly、cron 到五段 Cron 的转换、非法表达式拒绝和所有者校验。
- [ ] 运行定向测试，确认服务尚不存在而失败。
- [ ] 实现 `SavedReportSubscriptionService` 的创建/更新、暂停、恢复、删除、列表读取和下次运行时间同步。
- [ ] 在 `TaskSchedulerService.start()` 中加载 active 订阅；新增 `upsert_saved_report_subscription()`、`remove_saved_report_subscription()` 和 `get_saved_report_subscription_next_run_time()`。
- [ ] 重跑服务与现有 scheduler 定向测试。

### Task 3: 共享报表执行核心与定时包装器

**Files:**
- Modify: `app/api/portal/endpoints/saved_reports.py`
- Modify: `app/services/saved_report_subscription_service.py`
- Modify: `app/services/ai/scheduler_service.py`
- Test: `tests/api/portal/test_saved_reports.py`
- Test: `tests/services/test_saved_report_subscription_service.py`

- [ ] 编写失败测试，验证定时执行使用订阅用户身份、写入 `trigger_type=scheduled` 和 `task_id=subscription.id`，立即执行不发送通知。
- [ ] 从现有 endpoint 提取 `_execute_saved_report_core()`，参数明确包含 report、user_info、params、trigger_type、task_id 和 conversation_id。
- [ ] 保持手动 endpoint 行为兼容，并让调度包装器通过独立会话调用相同核心。
- [ ] 验证成功、权限失败、执行异常都会写运行历史并更新订阅指标。

### Task 4: 持久化站内通知服务与 API

**Files:**
- Create: `app/services/portal_notification_service.py`
- Create: `app/api/portal/endpoints/inbox.py`
- Modify: `app/api/portal/api.py`
- Test: `tests/api/portal/test_inbox.py`
- Test: `tests/services/test_saved_report_subscription_service.py`

- [ ] 编写失败测试，验证用户只能读取和标记自己的消息，未读计数准确，全部已读只影响当前用户。
- [ ] 实现通知创建、分页列表、未读计数、单条已读和全部已读服务。
- [ ] 挂载 `/api/portal/inbox` 路由。
- [ ] 在定时包装器中实现失败站内通知、可选成功通知，以及外部渠道低噪声策略；通知失败不得反向污染报表运行状态。
- [ ] 重跑 inbox、订阅与现有 notification 定向测试。

### Task 5: 报表订阅 API

**Files:**
- Modify: `app/api/portal/endpoints/saved_reports.py`
- Test: `tests/api/portal/test_saved_reports.py`

- [ ] 编写失败测试，覆盖读取、保存、暂停、恢复、立即执行和删除接口，以及共享用户 403。
- [ ] 添加请求/响应 Schema，限制 schedule_type、Cron、外部渠道和参数结构。
- [ ] 实现六个订阅接口并复用所有者权限校验。
- [ ] 重跑完整 saved report API 定向测试。

### Task 6: 报表详情订阅页签

**Files:**
- Modify: `frontend/src/components/chatbi/DatasetCapabilityMenu.vue`
- Test: `tests/frontend/test_saved_report_subscription_contract.py`

- [ ] 编写失败源码契约测试，定义所有者可见页签、频率字段、通知开关、暂停/恢复、立即执行和删除动作。
- [ ] 添加订阅状态、表单和 API 调用；共享用户不展示管理页签。
- [ ] 保存后刷新下一次运行时间；立即执行后刷新运行历史。
- [ ] 重跑前端源码契约测试。

### Task 7: Dashboard 全局通知铃铛

**Files:**
- Create: `frontend/src/components/PortalNotificationBell.vue`
- Modify: `frontend/src/views/Dashboard.vue`
- Test: `tests/frontend/test_portal_notification_bell_contract.py`

- [ ] 编写失败源码契约测试，定义未读接口、通知列表、单条已读、全部已读和资源跳转。
- [ ] 实现铃铛徽标和右侧通知抽屉，挂载到在线人数与退出按钮之间。
- [ ] 点击报表运行通知时携带 report/run 定位参数跳转数据门户。
- [ ] 重跑前端源码契约测试。

### Task 8: 验证与交付

**Files:**
- Modify: `tests/CHECKLIST.md`（仅在现有内容允许且不覆盖用户改动时补充手工验收项）

- [ ] 运行新增及相关后端定向测试。
- [ ] 运行 Python `py_compile` 和 `git diff --check`。
- [ ] 检查工作树，区分本功能、上一阶段运行历史和用户已有改动。
- [ ] 不执行数据库迁移、不运行 `./dev.sh`、不编译或重启前端、不暂存或提交代码。
- [ ] 交付 V97 执行顺序和成功/失败/权限隔离/未读状态的手工验收步骤。
