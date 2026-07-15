# 黄金报表订阅、定时运行与站内通知设计

## 目标

让黄金报表从“用户手动运行并查看历史”升级为可持续监控资产：报表所有者配置订阅后，系统按计划以订阅创建人的身份执行报表，结果写入运行历史，并按通知策略生成可持久化的站内消息；用户可从全局通知铃铛查看未读消息并跳转回对应报表运行记录。

## 范围

本阶段实现：

- 每个黄金报表可创建一条所有者订阅。
- 支持每天、每周、每月和高级 Cron。
- 支持保存报表运行参数。
- 支持启用、暂停、更新、删除和立即执行。
- 定时执行写入 `portal_saved_report_runs`，其中 `trigger_type = scheduled`，`task_id` 保存订阅 ID。
- 新增持久化站内通知、未读数量、通知抽屉、单条已读、全部已读。
- 通知可跳转到黄金报表详情的指定运行记录。
- 失败始终生成站内通知；成功通知默认关闭，可由订阅开启。
- 外部通知复用个人中心已有钉钉、企业微信和邮件配置，并允许按订阅选择通道。

本阶段不实现：

- 指标阈值、同比、环比、智能异常检测。
- 多个接收人或群组订阅。
- 报表附件、Excel/PDF 导出和消息内嵌完整数据。
- 共享用户创建订阅。

## 核心决策

### 独立报表订阅，不复用 AgentScheduledTask 业务表

现有 `ai_agent_scheduled_tasks` 面向智能体对话，执行时会创建会话并调用 Agent。黄金报表是确定性 SQL 执行，不应额外消耗模型，也不应混入 Agent 任务指标。

实现上复用同一个 `TaskSchedulerService` 和 APScheduler 实例，新增独立任务 ID 命名空间 `saved_report_subscription_{id}`、独立加载和执行包装器，但订阅数据写入新的 `portal_saved_report_subscriptions` 表。

### 权限身份固定

- 只有报表所有者能创建、修改、暂停、立即执行或删除订阅。
- 调度运行使用订阅的 `user_id` 加载最新用户信息、角色和行权限。
- 不缓存管理员权限，不使用系统用户，不设置 `bypass_table_auth`。
- 报表所有权发生变化或报表被删除时，订阅停止并从调度器移除。
- 用户被禁用、报表不可访问或参数失效时，本次运行失败并产生站内通知。

## 数据模型

### portal_saved_report_subscriptions

- `id`: 订阅 ID。
- `report_id`: 黄金报表 ID，删除报表时级联删除。
- `user_id`: 订阅所有者，也是定时执行身份。
- `schedule_type`: `daily | weekly | monthly | cron`。
- `cron_expr`: 标准五段 Cron，最终调度表达式。
- `timezone`: 首期固定默认 `Asia/Shanghai`。
- `params`: 运行参数 JSON。
- `notify_on_success`: 成功时是否通知，默认否。
- `notify_on_failure`: 失败时是否同时发送外部通知，默认是；站内失败通知始终保留。
- `external_channels`: 外部渠道数组，仅允许 `dingtalk | wechat_work | email`。
- `status`: `active | paused | error`。
- `consecutive_failures`: 连续失败次数。
- `last_run_id`: 最近一次报表运行历史 ID。
- `last_run_at`、`next_run_at`、`last_error`。
- `created_at`、`updated_at`。

首期每个报表最多一条订阅，以 `report_id` 唯一约束保证。

### portal_notifications

- `id`: 消息 ID。
- `user_id`: 接收用户。
- `category`: 首期为 `saved_report`。
- `level`: `success | warning | error | info`。
- `title`、`content`: 消息摘要。
- `resource_type`: `saved_report_run`。
- `resource_id`: 运行历史 ID 字符串。
- `metadata`: `report_id`、`report_title`、`subscription_id`、`row_count`、`duration_ms` 等安全摘要。
- `read_at`: 为空表示未读。
- `created_at`。

通知不保存完整结果快照或 SQL，详情仍通过运行历史权限接口加载。

## 调度与执行流程

### 创建或更新订阅

1. API 验证当前用户是报表所有者。
2. 把每天、每周、每月选择转换为标准五段 Cron；高级 Cron 使用 APScheduler 校验。
3. 保存订阅并提交事务。
4. 调用调度服务 upsert `saved_report_subscription_{id}`。
5. 从调度器读取下次执行时间并回写/返回前端。

### 定时运行

1. APScheduler 调用顶层 `_saved_report_subscription_wrapper(subscription_id, is_manual=False)`。
2. 独立数据库会话重新加载订阅、报表和用户。
3. 校验订阅启用、报表所有权和用户状态。
4. 调用共享的黄金报表执行核心，传入 `trigger_type=scheduled` 和 `task_id=subscription.id`。
5. 执行核心沿用 SQL 校验、表权限和行权限，写入运行历史。
6. 回写订阅最近运行、连续失败和错误状态。
7. 根据通知策略写站内通知，并以低噪声策略发送外部失败通知。

### 立即执行

立即执行同样走订阅包装器并写入 `scheduled` 运行历史，用于验证订阅配置。它不生成订阅通知，也不改变 Cron 的下次运行时间，避免用户主动测试配置时收到重复消息。

## 通知策略

- 失败站内通知：每次失败都生成，保证站内审计完整。
- 成功站内通知：仅当 `notify_on_success = true`。
- 外部失败通知：第一次连续失败发送，之后每连续失败 3 次发送一次，避免刷屏。
- 外部成功通知：仅当成功通知开启且订阅选择了外部渠道。
- 未配置或未启用的外部渠道跳过，不影响任务和站内通知状态。
- 通知发送失败只记录日志，不把已经成功的报表运行改成失败。

## 后端接口

### 报表订阅

- `GET /api/portal/saved-reports/{report_id}/subscription`
- `PUT /api/portal/saved-reports/{report_id}/subscription`
- `POST /api/portal/saved-reports/{report_id}/subscription/run`
- `POST /api/portal/saved-reports/{report_id}/subscription/pause`
- `POST /api/portal/saved-reports/{report_id}/subscription/resume`
- `DELETE /api/portal/saved-reports/{report_id}/subscription`

### 站内通知

- `GET /api/portal/inbox?limit=20&offset=0&unread_only=false`
- `GET /api/portal/inbox/unread-count`
- `POST /api/portal/inbox/{notification_id}/read`
- `POST /api/portal/inbox/read-all`

所有接口只允许访问当前用户自己的通知。

## 前端体验

### 报表详情

在现有“报表信息 / 运行历史”旁增加“订阅设置”页签，仅所有者可见。页签包含：

- 开关状态和下次运行时间。
- 频率选择：每天、每周、每月、高级 Cron。
- 时间、星期、日期或 Cron 输入。
- 报表参数。
- 成功通知、失败通知和外部通道。
- 保存、暂停/恢复、立即执行、删除。

### 全局通知

在 `Dashboard.vue` 顶部在线人数与退出按钮之间增加通知铃铛：

- 未读数显示为红色徽标，超过 99 显示 `99+`。
- 页面进入时加载未读数；抽屉打开时加载通知列表。
- 抽屉项目展示等级、标题、摘要和时间。
- 点击项目先标记已读，再打开数据门户并定位报表详情与运行历史记录。
- 支持“全部已读”，空状态明确展示。
- 首期使用进入页面加载和操作后刷新，不做 WebSocket 实时推送。

## 错误与一致性

- 创建订阅必须先提交数据库，再注册调度器；注册失败时订阅标记为 `error` 并返回明确错误。
- 更新或暂停后调度器同步失败时保留数据库真实状态，记录错误供下一次服务启动重载。
- 服务启动时重新加载所有 active 报表订阅，自愈内存调度状态。
- 定时执行使用独立会话，失败记录和站内通知必须在抛出异常前提交。
- 删除订阅先停用调度任务，再删除数据库记录。

## 验收标准

- 所有者可创建并看到下一次运行时间，共享用户无法创建。
- 暂停后调度器不再触发，恢复后重新出现下一次运行时间。
- 立即执行和真实定时触发都生成带订阅 ID 的运行历史。
- 定时执行仍应用创建人的表权限和行权限。
- 失败产生持久化站内通知，刷新和重新登录后仍存在。
- 未读数、单条已读、全部已读正确。
- 通知点击能打开对应报表运行记录。
- 成功通知开关与外部通知低噪声策略符合设计。
- 相关后端测试、调度契约测试和前端源码契约测试通过。
