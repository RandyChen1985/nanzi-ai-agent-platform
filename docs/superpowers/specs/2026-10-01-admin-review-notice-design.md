# 注册申请通知管理员（Admin Review Notice）设计

**日期：** 2026-10-01
**需求（用户原话）：** 「如果配置来全局邮件服务，有账号注册的时候，发送审核通知给 admin 的邮件」

---

## 1. 目标

自助注册成功后，向**所有填了邮箱的管理员账号**发送一封「有待审核的账号申请」通知邮件，让管理员不必反复手动刷「待审核」列表。

## 2. 范围

**做：**
- 仅在 `POST /api/portal/auth/register`（自助注册）成功落库后触发
- 收件人：`role='admin'` 且邮箱非空的管理员账号
- 正文含账号名、姓名、邮箱、备注（非空时）、申请时间、当前待审核总数
- 尽力而为：发信失败绝不影响注册结果

**明确不做（已与用户逐项确认）：**
- **不加链接**（用户选定）。理由见 §7.3
- 不新增配置项、不新增开关、**不新增数据库迁移**
- 不改前端（零前端改动）
- 不建审计表、不落库发送结果
- 不做聚合/合并通知（注册本身已有限流，见 §11.3）
- 不覆盖后台新建用户、第三方同步创建用户（它们不产生待审核账号，见 §3）
- 不落站内信（用户确认接受「无收件人时只记日志」）

## 3. 现状核实（证据，非推测）

| 事实 | 证据 |
|---|---|
| 待审核账号只由自助注册产生 | 全仓 `USER_STATUS_PENDING_REVIEW` **仅**在 `app/api/portal/endpoints/auth.py:298` 被赋值；`management.py` 只是引用它做筛选/校验，`user_sync_service.py` 无匹配 |
| 待审核计数已有唯一口径 | `app/api/portal/endpoints/management.py:315` 的 `GET /users/pending-count` 调用 `AuthService.count_pending_registrations(db=db)` |
| 审核结果邮件已有可对齐范式 | `management.py:_send_review_result_mail`：`load_global_smtp_settings()` → `run_in_threadpool(send_mail, ...)` → 返回 `(sent, error)` |
| 邮件正文构造函数的归属 | `app/services/email_delivery_service.py:386` 的 `build_review_mail(user_name, approved, sender_name)` |
| 该模块已有查库先例 | `resolve_self_email(cls, user_id, db)`（`:112`）会现查库 |
| 该模块已有「没给 session 就自建」的范式 | `load_user_smtp_settings`（`:247-283`）：`own_session = db is None` → `AsyncSessionLocal()` → `finally: await session.close()` |
| 平台时间格式化入口 | `format_platform_datetime(value, *, fmt="%Y-%m-%d %H:%M:%S")`（`app/services/platform_timezone.py:121`） |
| 注册请求的备注是可选、限 255 字 | `RegisterRequest.remark: Optional[str]`（`auth.py:119`） |
| 既有邮件不含链接是既定取舍 | `build_review_mail` 的注释：「不写登录地址：平台没有「站点基础地址」配置项，硬编码会写死部署形态」 |

## 4. 触发与时机

- 注册端点新增 `BackgroundTasks` 参数，在 `return` 之前 `background_tasks.add_task(...)`。
- 理由与邮件找回一致：`send_mail` 是**同步阻塞**且有 10 秒超时，放在响应前会把注册接口拖慢。
- **注册响应体逐字节不变**：是否发出通知属平台内部信息，不能回传给注册者（否则等于对外泄露「管理员邮箱是否配好」这类内部状态）。
  - 注意这与 `PATCH /users/{user_id}/status` 不同——那里的调用者本身就是管理员，回传 `email_sent`/`email_error` 对他是有效信息。

## 5. 收件人

```sql
WHERE role = 'admin' AND email IS NOT NULL AND TRIM(email) <> ''
```

- 一次查库取回全部，再经 `dedupe_recipient_emails()` **大小写不敏感去重**。
- 邮箱列可空且可能残留历史空串，因此必须同时判 `NULL` 与 `TRIM()<>''`：只判 `IS NOT NULL` 会让空串进入收件人列表；SMTP 端只要拒绝其中**一个**地址，**整封邮件都发不出去**，等于把「一个管理员没填好」升级成「所有人都收不到」。

**为什么去重不能交给数据库唯一索引（实测修正）**：实现本特性时尝试构造「两个仅大小写不同的管理员邮箱」作为测试数据，被 `uk_ai_agent_users_email` 直接拒绝，由此确认：

- MySQL 的唯一索引落在默认 `_ci` collation 上 → 大小写不敏感，那种数据在本库**造不出来**；
- PostgreSQL 的唯一索引是大小写**敏感**的，且**手工 SQL 或第三方同步可以绕过应用层的 `normalize_email` 归一化** —— 那两个地址在 PG 上是可以并存的。

这与 `AuthService.is_user_email_taken` 的处理完全同构（`auth_service.py:631` 的注释：「写入时已归一化，但手工 SQL 或第三方同步可能绕过应用层，且两个库的 collation 行为不同」）。因此去重留在应用层，并**抽成纯函数**以便真正被测试覆盖——放在查询里的话，在 MySQL 测试环境下它是不可达的死代码。
- **取不到任何收件人时**：记 `WARNING admin_review_notice_no_recipient` 后直接返回，不影响注册、不报错。
- **这是本方案已知且用户已确认接受的代价**：邮箱是上一个特性新增的**可选**字段，存量管理员很可能都没填，因此本功能可能开箱即静默不生效。日志会写明原因，便于排查。

## 6. 可用性

- **不新增开关**，只跟随全局邮件服务：`load_global_smtp_settings() is None` → 记 INFO 后不发。与用户原话「如果配置了全局邮件服务」一致。
- **不依赖 `download_url_prefix`**：因为不放链接（§7.3），所以不存在「前缀没配就发不出去」的问题。

## 7. 邮件内容

### 7.1 主题

`【{发件人名称}】有待审核的账号申请`

### 7.2 正文

```
您好，管理员：

平台有一条新的账号注册申请，需要审核：

账号名：zhangsan
用户姓名：张三
邮箱：zhangsan@corp.example.com
备注：市场部                    ← 仅非空时渲染
申请时间：2026-10-01 07:39:03   ← 平台时区
当前待审核：3 条

请到「用户管理 → 待审核」处理。

（本邮件由系统自动发送，请勿直接回复）
```

- **注册者未填邮箱时渲染「邮箱：未填写」**，而不是留一行空的 `邮箱：`：
  邮箱是选填字段，空值行会让管理员无法区分「注册者没填」与「系统取值失败」。
- **待审核总数复用 `AuthService.count_pending_registrations(db=db)`**，与 `GET /users/pending-count` 同一口径 —— 邮件里的数与页面角标绝不允许各算各的。
- 申请时间用 `format_platform_datetime()`，与平台时区配置一致。
- 备注为空/纯空白时**整行不渲染**，避免出现空的「备注：」。
- 备注是用户自填内容、最多 255 字；邮件是纯文本（`MIMEText` text/plain），无需 HTML 转义；超长按 255 字截断。
- 结尾沿用 `build_review_mail` 既有的「（本邮件由系统自动发送，请勿直接回复）」。

### 7.3 为什么不放链接（用户选定）

待审核页**无法用 URL 直达**：`frontend/src/views/Users.vue:2298` 的 `activeView` 是纯本地 `ref`，不接受 URL 参数，`/users?view=review` 会落在默认列表页。要直达就得改前端加 query 支持。用户权衡后选择**不放链接**，用文字指引「请到「用户管理 → 待审核」处理」。

附带好处：本特性因此不需要 `download_url_prefix`，也不需要新的「站点基础地址」配置，与 `build_review_mail` 的既有取舍保持一致。

## 8. 代码落点

全部放 `app/services/email_delivery_service.py`（正文构造函数已在此、查库先例已在此），新增三项：

| 新增 | 职责 | 可测性 |
|---|---|---|
| `resolve_admin_recipients(db) -> List[str]` | 收件人查询 + 去重 | 纯查询，可直接单测 |
| `build_admin_review_notice(...) -> Tuple[str, str]` | 正文构造，紧挨 `build_review_mail` | 纯函数，无 IO |
| `notify_admins_of_pending_registration(user_id, db=None) -> Tuple[bool, Optional[str]]` | 编排：查收件人 → 取全局设置 → 构造 → 发送 | 传 db 即可测，不传则自建 |

**不选「内联进注册端点」**：该端点已有七步校验顺序（开关 → 限流 → 格式 → 等保 → 积压上限 → 重名 → 落库），再混入查库+发信会把「校验顺序即错误优先级」这条主线淹没。

端点侧只加两行：

```python
background_tasks.add_task(
    EmailDeliveryService.notify_admins_of_pending_registration, user_id, None
)
```

## 9. 会话生命周期（本方案最容易踩的坑）

**绝不把请求作用域的 `db` session 传进后台任务**：响应返回后该 session 已关闭，会抛「session 已关闭」；若恰好落在另一个事件循环上，还会出现 `attached to a different loop`（本仓库今天就因连接池未重置踩过一次同类问题）。

采用该模块**已有**的范式（`load_user_smtp_settings:254-283`）：

```python
own_session = db is None
session = db
if own_session:
    from app.core.orm import AsyncSessionLocal
    session = AsyncSessionLocal()
try:
    ...
finally:
    if own_session and session is not None:
        await session.close()
```

- 端点传 `None` → 自建独立 session，彻底绕开生命周期问题。
- 自建 session 能读到刚提交的注册记录（注册在 `generate_api_key` 与 `set_user_password` 内已 commit），因此待审核计数天然包含这条新申请。
- 按 `user_id` 重查用户而不是传 ORM 对象，避免 detached instance。

## 10. 失败处理

- 编排内部 `try/except` **全捕获**：后台任务的异常不会被响应吃掉，但也无人接收，冒泡只会污染日志。
- 用户被删（查不到）→ 记 WARNING 后返回，不抛。
- SMTP 用 `run_in_threadpool` 包装（对齐 `_send_review_result_mail`），避免阻塞事件循环。
- 返回 `(sent, error)` 供测试与日志使用；**调用方不把它放进响应**。

## 11. 安全、隐私与滥用

1. **不回传发送结果**给注册者（§4）。
2. **正文不含任何凭据**：签名只接受展示用字段，从来源上保证不可能泄露密码/API Key。
3. **收件量天然有上限**：注册端点已有限流（账号名 3 次/小时、来源 5 次/小时、待审核积压上限 500），因此管理员收件量有界，不需要为本功能再加聚合或节流。这是刻意不做聚合的依据，不是遗漏。
4. 收件人是管理员自己的邮箱，属平台内部信息；正文包含注册者姓名/邮箱/备注——**这些内容注册者本就主动提交给平台，且管理员在「待审核」列表里能看到同样的字段**，因此不构成新的信息暴露面。
5. 不记录密码、不记录 token、不建审计表。

## 12. 测试计划（新增 `tests/test_admin_review_notice.py`）

后端行为用例：

1. 收件人筛选：多个管理员全部收到；无邮箱、纯空白邮箱、空串邮箱的管理员**被排除**
2. 非 admin 角色（普通用户/待审核账号）不计入收件人，即使有邮箱
3. 同一邮箱被两个管理员填写 → 去重后只出现一次
4. 一个可用收件人都没有 → 不发送、记 WARNING、**注册仍返回 200**
5. 全局邮件服务未启用 → 不发送、注册仍返回 200
6. **注册响应体在「邮件服务开启」与「未启用」两种情况下逐字节相同**（不得泄露内部通知行为）
7. 正文包含账号名、姓名、邮箱、备注、申请时间、待审核数
8. 备注为空/纯空白 → 不渲染「备注：」行
9. 待审核数来自 `count_pending_registrations`（与 `/users/pending-count` 同源），且**包含刚提交的这条申请**
10. `send_mail` 抛异常 → 注册仍返回 200，无 500
11. **端点机制契约**：注册端点必须把发信登记为 `background_tasks.add_task(...)` 而**不是** `await`
    - 为什么用源码契约而不是行为断言：TestClient 会等 `BackgroundTasks` 跑完才把响应交给测试，
      因此「快慢」在测试里观测不到，只能对机制本身断言（与邮件找回同理）

前端契约：**无**（零前端改动）。回归只需确认既有邮件/注册契约仍绿。

### 反向验证（每条守卫注入变异，必须观察到对应用例失败）

| 变异 | 必须失败的用例 |
|---|---|
| 去掉空/纯空白邮箱的排除条件 | 1 |
| 去掉收件人去重 | 3 |
| 收件人查询去掉 `role='admin'` 过滤 | 2 |
| 把发信改成同步 `await`（不再是 background task） | 端点机制契约（见下） |
| 后台任务改用请求作用域的 `db`（不自建 session） | 9 或 10（session 已关闭） |
| 备注为空时仍渲染「备注：」 | 8 |
| 把发信失败冒泡出去 | 10 |
| 统计待审核数时自己重算（改用另一个 where） | 9 |

> 「后台任务」这条与邮件找回同理：TestClient 会等 `BackgroundTasks` 跑完才把响应交给测试，**时序观测不到**，因此对机制本身做源码契约断言（端点必须 `add_task` 而非 `await`）。

## 13. 风险

| 风险 | 影响 | 处置 |
|---|---|---|
| 管理员普遍没填邮箱 → 功能静默失效 | 用户以为已通知，实际无人收到 | 记 WARNING 明示原因；本设计 §5 已向用户明示并获确认 |
| 每注册一人发一封，管理员收件箱噪音 | 管理员可能忽略 | 注册已限流（§11.3）；不引入聚合，避免复杂度 |
| 邮件里的待审核数与页面角标不一致 | 管理员困惑 | 强制复用同一计数函数（§7.2），并有测试钉住 |
| 后台任务里 session 生命周期错误 | 邮件随机丢失 | 采用模块既有 `own_session` 范式（§9），并有反向验证 |
| 收件人列表暴露给收件人彼此 | 管理员之间互相看到邮箱 | 属内部管理团队，用户已确认发「给所有管理员」；不引入 BCC 复杂度 |

## 14. 验收标准

1. 自助注册成功后，所有「admin 角色 + 有效邮箱」的账号收到通知邮件
2. 收件人做去重，且排除空/纯空白邮箱
3. 无可用收件人、邮件服务未启用、SMTP 失败三种情况下**注册接口行为完全不变**（仍 200，响应体逐字节相同）
4. 邮件正文含账号名、姓名、邮箱、备注、申请时间（平台时区）、当前待审核总数；备注为空时不出现空行
5. 待审核总数与 `GET /users/pending-count` 同源
6. 发信在响应之后进行（后台任务），不阻塞注册
7. 零迁移、零新增配置项、零前端改动
8. §12 的 10 项用例与全部反向验证通过；既有回归（`tests/api`+`tests/core`、`tests/frontend`、`vue-tsc`）不退化

## 15. 实施顺序

1. `build_admin_review_notice` + `resolve_admin_recipients`（纯函数 + 纯查询，先写测试）
2. `notify_admins_of_pending_registration`（编排 + `own_session` 范式）
3. 注册端点接上 `BackgroundTasks`
4. 补齐 §12 的 10 项用例与反向验证
5. 更新 `tests/CHECKLIST.md`

（本仓库禁止 Agent 自行 `git commit`，故各步骤以「运行验证」收尾，提交由用户明确指令触发。）
