# 用户邮箱字段与全平台邮件服务 设计文档

- 日期：2026-10-01
- 状态：设计待确认
- 关联：`2026-09-30-user-self-registration-design.md`（账号自助注册与审核，本设计是其自然延续：审核动作需要一条把结果告知用户的通道）

## 1. 背景与目标

上一轮做完了「账号自助注册 → 管理员审核」，但审核结果只能靠用户自己反复试登录来感知。本设计补齐两件事：

1. **用户资料增加邮箱字段**，注册、管理员增改、个人中心自助编辑三处都支持，并做**邮箱重名检测**。
2. **全平台邮件发送服务**：系统配置新增「邮件服务」分组（开关默认关闭、支持测试发送），使得**审核通过/禁用后可以给用户发邮件**。

现状的关键事实（已逐一核实）：

- `ai_agent_users` **没有 email 列**，全仓用户相关的模型、schema、SSO 同步里都没有邮箱概念。
- 平台**没有任何全局邮件配置**。SMTP 完全是**用户级**的：每用户一行 `user_notification_configs`（`channel_type='email'`），配置塞在 `config_json` 这个 **TEXT(JSON)** 字段里，`smtp_password` **明文存储**（仅有 API 出参脱敏）。
- 系统配置里 `is_secret=1` 的条目**一条都没有**（迁移建了列、后端实现了脱敏，但从未被真实使用过）。本设计会是第一个使用者。
- `/me` 返回的用户信息**不是直接读 ORM 对象**，而是 `require_api_key` 注入的 dict，该 dict 在 `AuthService` 里有 **5 处**几乎重复的构造点。
- 前端用户相关的**共享类型只有一个**：`frontend/src/api/portal.ts` 的 `User` 接口（只有 `user_name`/`real_name`）。

## 2. 范围

### 做

1. `ai_agent_users.email` 列（nullable、归一化小写、全局唯一索引），双库迁移。
2. 注册表单增加选填邮箱；管理员创建/编辑用户支持邮箱；三处统一走同一套「格式 + 唯一性」校验。
3. 系统配置新增 `email` 分组「邮件服务」，放在「其他参数」之后；8 个配置项，总开关默认**关闭**；支持**测试发送**。
4. 新增 SMTP 分层解析：`global`（全局）与 `custom`（用户自定义）两种来源，**显式选择、默认 global**。
5. 审核通过/禁用后给用户发邮件（**强制走全局 SMTP**）；不阻塞审核动作；响应回传发送结果；提供**重发**入口。
6. 个人中心「基本信息」支持自助编辑邮箱（**不需要密码确认**）。
7. 个人中心「消息通知 · 邮件通知 (SMTP)」支持选择全局或自定义 SMTP；收件人新增「同时发送到我的邮箱」复选框，**默认勾选**。
8. 邮箱参与第三方用户同步（字段映射、落库、抽屉 UI、映射配置）。
9. 用户列表显示邮箱并支持按邮箱搜索。

### 不做（本轮明确排除）

| 项 | 原因 |
|---|---|
| 邮箱验证（验证码/验证链接） | 用户已决策：邮箱选填，不做验证。**已知后果：写错的邮箱无人发现，通知会静默丢失** |
| 密码找回 / 邮箱改密 | 未确认平台是否已有找回功能；本轮不接入邮箱通道，需单独设计（涉及一次性令牌、防重放、限流） |
| 给**旧邮箱**发「邮箱已变更」通知 | 等保视角应该做，但会引入「用户信息泄露给已废弃地址」的新风险面；列入后续 |
| 邮箱变更的独立审计表 | 平台没有通用的用户变更审计表；本轮只打结构化日志。要合规审计需另立设计 |
| 用户批量导入/导出邮箱 | 平台当前无用户导出功能 |
| 邮件模板引擎 / HTML 模板 | 两封固定文案邮件，用 f-string 拼纯文本即可，不引入模板依赖 |
| 存量 3400+ 用户的邮箱回填 | 只能靠管理员逐个补录或 SSO 同步；不做批量脚本 |

## 3. 数据模型

### 3.1 `ai_agent_users.email`

```python
email = Column(String(254), nullable=True, index=True,
               comment='邮箱（小写归一化存储，全局唯一；NULL 表示未填写）')
```

- 放在 `real_name` 之后（MySQL 迁移用 `AFTER real_name`）。
- 长度 254：RFC 5321 对邮箱地址路径的上限。
- **NULL 而非空串**：MySQL 与 PostgreSQL 的唯一索引都允许多个 NULL，所以「大量用户不填邮箱」不会被唯一约束拦住；空串则会互相冲突。

### 3.2 迁移（双库，两套不可混用）

| 文件 | 内容 |
|---|---|
| `db-prod/V163-add-user-email.sql` | `ALTER TABLE ai_agent_users ADD COLUMN email ... AFTER real_name` + `CREATE UNIQUE INDEX uk_ai_agent_users_email` |
| `db-prod-pg/V64-add-user-email.sql` | `ADD COLUMN IF NOT EXISTS` + `CREATE UNIQUE INDEX IF NOT EXISTS` |
| `db-prod/V164-add-email-service-configs.sql` | `INSERT IGNORE` 8 条 `category='email'` 配置种子，无 DDL |
| `db-prod-pg/V65-add-email-service-configs.sql` | `INSERT ... ON CONFLICT (key) DO NOTHING`，同上 |

- **MySQL 侧不加 `IF NOT EXISTS`**：MySQL 8.0 不支持 `ADD COLUMN IF NOT EXISTS`（那是 MariaDB 扩展）。沿用仓库现有约定——`db-prod/apply_sql.py` 会识别并吞掉 `ERROR 1050/1060/1061`（已存在），见 `db-prod/README.md:72`。因此重复执行安全。
- **PG 侧用原生 `IF NOT EXISTS`**，真正幂等，不依赖错误码吞掉。参照 `db-prod-pg/V9-add_ai_model_token_limits.sql`。
- 唯一索引安全性：存量 email 全为 NULL，不存在重复值，可直接建索引。

### 3.3 归一化与唯一性

**归一化**（唯一入口 `normalize_email(raw) -> Optional[str]`）：

1. `str(raw).strip()`
2. 空串 → `None`（存 NULL）
3. `.lower()`
4. 格式校验：非空时必须是 `本地部分@域名`，域名含至少一个 `.`，总长 ≤ 254。用宽松正则，**不做** RFC 5322 全量校验（过度严格会误伤合法地址）

**格式规则**：`^[^@\s]+@[^@\s]+\.[^@\s]+$`，且不含空白字符。

**唯一性**（`AuthService.is_user_email_taken(email, exclude_user_id=None, db=None) -> bool`）：

- 与 `is_user_name_taken` 同构，**覆盖全部状态**（`status ∈ {0,1,2}`）：禁用/待审核账号的邮箱同样占用，与账号名的既定语义一致。
- 比较用 `func.lower(User.email) == normalized`：虽然写入时已归一化为小写，但用 `func.lower()` 可以防住绕过应用层写入的行（手工 SQL、SSO 同步的历史数据），且与 `is_user_name_taken` 处理跨库 collation 差异的既有做法一致。
- `exclude_user_id` 用于「编辑自己」的场景，避免把自己判成重复。
- 调用点共 4 处：注册（不排除）、管理员创建（不排除）、管理员编辑（排除自己）、个人中心自助编辑（排除自己）。

**并发抢占**：应用层校验之后、落库之前仍有竞态，靠唯一索引兜底，捕 `IntegrityError` 转 400 统一文案「该邮箱已被其他账号使用」。

> ⚠️ 注册路径**已经**有一个 `IntegrityError` 处理分支（用于账号名冲突）。必须按异常文本里的**索引名**区分是 `uk_ai_agent_users_email` 还是账号名唯一键，否则会把邮箱冲突误报成「账号名已被占用」。这是本设计里最容易写错的一处，会专门写用例覆盖。

## 4. 全局邮件服务配置（系统配置 · 邮件服务分组）

### 4.1 配置项

`category = 'email'`，`description` 用于界面说明，全部为字符串存储（与现有配置一致）。

| key | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `email_service_enabled` | 布尔 | `false` | 总开关，**默认关闭** |
| `email_smtp_host` | 文本 | `""` | SMTP 服务器地址 |
| `email_smtp_port` | 整数 | `465` | 端口 |
| `email_smtp_security` | 枚举 `ssl`/`starttls`/`none` | `ssl` | 加密方式 |
| `email_smtp_user` | 文本 | `""` | 登录账号，**允许为空**（免认证的内部中继） |
| `email_smtp_password` | **密钥**（`is_secret=1`） | `""` | 登录密码/授权码 |
| `email_from_address` | 文本 | `""` | 发件人地址，空则回退 `email_smtp_user` |
| `email_sender_name` | 文本 | `NanZi AI Agent` | 发件人显示名，同时用作邮件主题前缀 |

**`email_smtp_security` 是顺手修掉的既有缺陷**：现有实现（`notification_service.py:564-566`、`notification_tools.py:157-165`）只靠端口猜加密方式（465→`SMTP_SSL`，否则 `SMTP` + `starttls()`），且 `starttls()` 失败被 `except: pass` **静默吞掉** → 587 端口存在明文降级风险。显式枚举后：`ssl` 用 `SMTP_SSL`，`starttls` 必须先 `ehlo()` 再 `starttls()` 且**失败即报错、不降级**，`none` 用明文 `SMTP`。

> **为什么必须有 `none`**：内网平台非常常见的部署是「免认证的内部 SMTP 中继」（如 `smtp.internal:25`）。只提供 `ssl`/`starttls` 会把这类部署直接堵死——而它恰恰是企业内网最典型的邮件出口。因此 `none` 必须存在，但要在界面上明确标注为**不安全**；同时 `email_smtp_user` 允许为空，以支持免认证中继。`none` + 非空密码的组合等于把凭证明文发到网络上，这一点由界面警示与文档说明约束，不做硬性禁止（否则无法覆盖「内网中继但仍需认证」的形态）。

### 4.2 密钥字段（第一个 `is_secret=1` 使用者）

- 后端 `get_all_configs_grouped()` 对 `is_secret` 条目脱敏为 `前3位 + "****" + 后4位`（`config_service.py:391-395`），`is_secret` 字段本身也返回给前端。
- 前端据 `item.is_secret` 渲染带「显示/隐藏」切换的密码框（`SystemConfig.vue:4697`）。
- **安全性已确认**：`saveConfigs` 只提交 `item.value !== originalConfigs[key]` 的项（`SystemConfig.vue:1320`），而 `originalConfigs` 与输入框初值都是脱敏串，两者相等 → **脱敏值不会被回写覆盖真实密码**。管理员只有在真的改了密码框内容时才会提交。
- 这条链路此前从未被真实使用过，会补一个契约测试钉住「脱敏值不参与提交」的行为。

### 4.3 后端校验（`ConfigService.validate_config_update`）

| key | 规则 |
|---|---|
| `email_service_enabled` | 必须 ∈ `true/false/1/0/yes/no/on/off`（复用现有 `_BOOLEAN_LITERALS`） |
| `email_smtp_port` | 整数且 `1 ≤ port ≤ 65535` |
| `email_smtp_security` | 必须 ∈ `ssl`/`starttls`/`none` |
| `email_from_address` | 空 或 合法邮箱格式 |
| `email_smtp_host` / `email_smtp_user` / `email_sender_name` | 无强校验（允许为空以便分步填写） |

**刻意不做「开启总开关时必须填全」的强校验**：管理员常需先填各项再开启，且允许保存半成品；配置不完整时发送方在运行期返回「配置不完整」的明确错误，由「测试发送」按钮引导补齐。

### 4.4 测试发送

- `POST /api/portal/system/configs/email/test`，权限 `element:system:config_save`（与保存配置一致）
- 请求体 `{to?: string}`：为空时发给 `email_from_address`（再回退 `email_smtp_user`）
- **用当前库里已保存的配置发送**，不是前端表单里未保存的值——因为请求体不含密码（前端拿到的是脱敏值），无法用未保存的密码发信。界面文案要写清「测试使用已保存的配置，请先保存」
- 返回 `200 {ok: bool, message: string}`。失败时把 SMTP 的原始错误文本回传便于排查，**但必须先过滤掉密码字样**，避免错误信息里带上认证串

## 5. SMTP 分层解析

新增 `app/services/email_delivery_service.py`。

```python
@dataclass
class SmtpSettings:
    host: str; port: int; user: str; password: str
    security: str          # 'ssl' | 'starttls'
    from_address: str; sender_name: str
    timeout: int = 10

def load_global_smtp_settings() -> Optional[SmtpSettings]   # host 缺失 → None（user/password 允许同时为空，支持免认证中继）
def load_user_smtp_settings(user_id) -> Optional[SmtpSettings]
def resolve_smtp_settings(user_id, scope: Literal['user','global']) -> Optional[SmtpSettings]
def send_mail(settings, to: list[str], subject: str, body: str) -> tuple[bool, str]
```

**「配置不完整」的判定**：只以 `host` 是否为空为准。`user`/`password` 允许同时为空（内部中继免认证）；若只填了其中一个则视为不完整并给出明确错误——半套认证凭据几乎总是配置失误，早报错比默默用匿名发送好。

### 5.1 来源选择语义

用户级的 `user_notification_configs.config_json` 新增 `smtp_source` 字段：

- `'global'`（**默认**）→ 用全局配置
- `'custom'` → 用该用户自己填的 host/port/user/password

**为什么用显式选择而不是「配了就用个人的」隐式兜底**：隐式兜底的失败模式是静默的——用户以为在用全局，实际在用自己很久以前填错的旧配置，邮件发不出去且界面上没有任何提示。显式选择才能在「消息通知」界面直接标出「当前生效：全局邮件服务」，让状态可解释。

### 5.2 存量兼容（零迁移）

`config_json` 是 TEXT(JSON)，无法用 SQL 简单判断嵌套键，因此**不改历史数据**，改为在读取时自适应：

```
smtp_source 缺失时：
  若 smtp_host 非空 → 按 'custom' 处理（行为与升级前完全一致）
  否则             → 按 'global' 处理
```

自适应、可逆、不需要迁移脚本，也不会因为 JSON 里多一个键而影响任何现存读取逻辑。

### 5.3 调用方如何选择 scope

**由触发方决定**，而不是由用户配置决定：

| 触发方 | scope | 理由 |
|---|---|---|
| 审核结果通知（系统 → 用户） | **强制 `global`** | 用户已确认。这是系统通知，若走用户个人配置，用户把个人 SMTP 关掉或填错就永远收不到审核结果——而这恰恰是他能否登录的前提 |
| 用户自己的任务/Agent 通知（用户 → 用户） | `user`（按 `smtp_source` 解析） | 这是用户自己的通知渠道，个人中心给了「全局或自定义」的选择权 |
| 测试发送（管理员） | `global` | 测的就是全局配置 |

## 6. 生命周期邮件（审核结果通知）

### 6.1 触发

在 `PATCH /api/portal/management/users/{user_id}/status` 内，**仅当该用户原本 `status == 2`（待审核）**时发信：

- 原 `2` → 新 `1`：发「账号已启用」
- 原 `2` → 新 `0`：发「账号已禁用」

**这个条件同时解决了「审核」与「日常启停」的区分**：管理员事后对一个已启用账号做启停（`1→0`、`0→1`）时原状态不是 `2`，不会发信。不需要新增字段或额外的动作类型参数。

### 6.2 不阻塞主操作

审核是主操作，发信是尽力而为。以下情况**都不影响审核成功**：

- 用户没填邮箱
- 全局邮件服务未开启 / 配置不完整
- SMTP 连接、认证、投递失败

`PATCH /api/portal/management/users/{user_id}/status` 的响应**扩展**（保留原有字段，只新增）：

```json
{ "...原有字段": "...", "email_sent": true, "email_error": null }
```

前端据此让 toast 区分「已启用，通知邮件已发送」/「已启用，但通知邮件发送失败（原因）」，并在失败时展示**重发**入口。管理员因此知道要不要手工通知。

### 6.3 重发

- `POST /api/portal/management/users/{user_id}/notify-review-mail`，权限 `element:user:edit`
- 请求体 `{approved: bool}` —— **显式传事件**而不是从当前状态推断：重发往往发生在用户已被改成其他状态之后，从状态推断会发错文案
- 返回同样的 `{ok, message}`
- 仅允许对 `status ∈ {0,1}` 的用户重发（对仍待审核的用户无意义 → 400）

### 6.4 发送方式与模板

- **同步发送 + 10 秒超时**。审核是低频人工操作，同步发送才能把结果回传给管理员；`BackgroundTasks` 拿不到结果。
- 纯文本正文 + f-string，不引入模板引擎。主题前缀用 `email_sender_name`。
- 两封邮件都包含账号名，**不包含**用户的其他信息；**不包含密码**（注册时用户自设，平台也不该假装能告诉他）。
- 正文不写登录 URL：平台当前没有「站点基础地址」配置项，硬编码会写死部署形态。

## 7. 个人中心

### 7.1 基本信息 —— 自助编辑邮箱

- `PATCH /api/portal/auth/me/profile`，**只允许改 `email`**（不允许改 `real_name`/`role`/`status`/`remark`），最小权限
- 该端点在 `auth.router` 内（该 router 是唯一没有整组 `require_api_key` 的），因此**必须显式声明** `Depends(require_api_key)`
- **不需要密码确认**（用户已决策）：email 不是登录因子，改了也能再改；而要求密码确认会让纯 API Key 登录的账号直接无解
- 唯一性校验排除自己
- **审计**：平台没有通用的用户变更审计表，本轮打一条结构化 `logger.info`（含 user_name、旧值、新值）。真正的合规审计需另立设计
- 允许**清空邮箱**（传 `null` 或空串 → 存 NULL）

### 7.2 消息通知 · 邮件通知 (SMTP) 改造

现有表单（`NotificationConfigs.vue:309-397`）保持结构，增加：

1. **邮件服务来源**单选：`使用全局邮件服务（推荐）` / `使用自定义 SMTP`，存 `smtp_source`
2. 选「全局」时**隐藏** host/port/user/password，改为展示全局配置概览（host + 发件人，**不含密码**）；全局未启用或未配置时给明确提示与「去系统配置」引导
3. 选「自定义」时展示原有 SMTP 字段
4. 收件人区新增复选框 **`☑ 同时发送到我的邮箱（xxx@yyy）`，默认勾选**

**第 4 条的建模要点**：存 **`include_self: true`** 这个布尔标志，而**不是**把邮箱字符串写进 `recipients`。写快照会导致用户之后改了邮箱、通知仍旧发往废弃地址。运行时再拼接：

```
to = dedupe(([user.email] if include_self and user.email else []) + parse_email_recipients(recipients))
```

- `include_self` 缺失时默认 **`true`**（新老用户一致），靠**去重**避免存量用户 `recipients` 里已经写了自己而收两封。
- 用户没填邮箱时复选框禁用 + 提示「请先在基本信息中填写邮箱」。
- **兼容既有 `or [smtp_user]` 兜底**：现有实现当 `recipients` 为空时会回退发给 `smtp_user`（即发信账号自己，`notification_service.py:558`）。这是既有的错误投递行为。处理方式：`to` 为空时，`smtp_source == 'custom'` 保留该回退（行为不变），`smtp_source == 'global'` 视为「未配置收件人」返回失败而不是发给发件账号。

## 8. 后端接口清单

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| `POST` | `/api/portal/auth/register` | 公开 | 新增可选 `email` 字段，格式 + 唯一校验 |
| `PATCH` | `/api/portal/auth/me/profile` | 登录（`require_api_key`） | 自助改邮箱，仅 email |
| `GET` | `/api/portal/auth/me` | 登录 | 返回新增 `email` |
| `POST` | `/api/portal/management/users` | `element:user:edit` | 新增可选 `email`，格式 + 唯一校验 |
| `PUT` | `/api/portal/management/users/{user_id}` | `element:user:edit` | 同上，唯一性排除自己 |
| `GET` | `/api/portal/management/users` | `menu:system:users` | 返回 `email`，搜索覆盖 email |
| `PATCH` | `/api/portal/management/users/{user_id}/status` | `element:user:edit` | 审核时发信；响应新增 `email_sent`/`email_error` |
| `POST` | `/api/portal/management/users/{user_id}/notify-review-mail` | `element:user:edit` | 重发审核结果邮件 |
| `POST` | `/api/portal/system/configs/email/test` | `element:system:config_save` | 测试发送 |
| `GET`/`PUT` | `/api/portal/system/configs` | `menu:system:config` / `element:system:config_save` | 新分组自动生效，无需改端点 |

## 9. 前端改动清单

| 文件 | 改动 |
|---|---|
| `frontend/src/views/Login.vue` | 注册表单新增可选邮箱输入（移动端需同样 `text-base`/`py-3` 适配）；payload |
| `frontend/src/views/Users.vue` | 创建/编辑表单新增邮箱；列表新增邮箱列；搜索提示补充邮箱；审核结果 toast 区分发信结果；失败时「重发通知邮件」按钮 |
| `frontend/src/views/PersonalCenter.vue` | 「基本信息」新增邮箱展示与编辑（行内编辑或小弹窗） |
| `frontend/src/components/personal/NotificationConfigs.vue` | 来源单选、全局概览、`include_self` 复选框 |
| `frontend/src/views/SystemConfig.vue` | `orderedCategories` 顺序数组末尾加 `'email'`（:789）；`getCategoryLabel`(:1406) / `CATEGORY_ICONS`(:1431) / `getGroupSubtitle`(:869) 三个 map 各加一项；**布尔型 key 硬编码数组**（:5122）加 `email_service_enabled`；**下拉/整数型 key 硬编码清单**加 `email_smtp_security`/`email_smtp_port`；「邮件服务」分组内放测试发送按钮 |
| `frontend/src/components/ThirdPartyUserSyncDrawer.vue` | `FIELDS` 增加 email 映射 |
| `frontend/src/api/portal.ts` | `User` 接口增加 `email`；通知配置类型增加 `smtp_source`/`include_self` |

> 前端的分组排序、布尔/整数/下拉渲染方式都是**硬编码 key 清单**（不是按类型自动推断），所以每个新配置项都必须登记到对应清单，否则会退化成普通文本框。这是本设计里最容易漏改的地方。

## 10. 错误处理与边界

| 场景 | 行为 |
|---|---|
| 邮箱格式非法 | 400「邮箱格式不正确」 |
| 邮箱已被占用（注册/管理员创建） | 400「该邮箱已被其他账号使用」 |
| 邮箱已被占用（编辑自己以外的账号） | 400「该邮箱已被其他账号使用」 |
| 并发抢占邮箱（唯一索引冲突） | 捕 `IntegrityError` → 400 同上，**不得误报为账号名冲突** |
| 全局邮件服务关闭/配置不全 | 审核仍成功，`email_sent=false` + 明确原因 |
| 用户无邮箱 | 审核仍成功，`email_sent=false`，原因「该用户未填写邮箱」 |
| SMTP 发送失败 | 审核仍成功，`email_sent=false` + SMTP 原始错误（已过滤密码） |
| 测试发送时未保存配置 | 用库中已保存值；界面明示「请先保存」 |
| 对 `status=2` 用户重发 | 400「该用户仍在待审核状态」 |
| 自助编辑试图改其他字段 | 请求模型只接受 `email`，其他字段被忽略 |

## 11. 测试计划

**后端（`tests/test_user_email_and_mail_service.py`，新增）**

- 纯单元（`no_infrastructure`）：`normalize_email` 的归一化与格式边界（大小写、前后空白、空串→NULL、无 `@`、无 `.`、超长、含空白）；`_resolve_smtp_source` 的存量自适应（缺 `smtp_source` 且 `smtp_host` 非空 → `custom`；否则 → `global`）
- 注册：带合法邮箱成功并可登录前查库；非法格式 400；重复邮箱 400；大小写不同视为重复；不填邮箱仍可注册
- 管理员创建/编辑：邮箱写入；编辑自己保留原邮箱不算重复；占用他人邮箱 400
- 自助编辑：改自己的邮箱成功；占用他人邮箱 400；清空邮箱成功；请求里的其他字段被忽略
- 唯一索引冲突：直接并发/构造 `IntegrityError`，断言返回的是**邮箱**文案而不是账号名文案
- 审核发信：全局关闭 → 不发送但审核成功；无邮箱 → 不发送；mock SMTP 成功 → `email_sent=true`；mock 失败 → `email_sent=false` + 原因；原状态非 2 的启停**不触发**发信
- 重发：成功；对 `status=2` 返回 400
- 测试发送端点：权限校验；配置不全时的错误信息；**错误文本不含密码**

**迁移契约（`tests/test_user_email_migration_contract.py`，新增）**：4 个文件存在、双库各自包含列与唯一索引、配置种子含 8 个 key 且 `category='email'`、`email_smtp_password` 的 `is_secret` 为真、MySQL 用 `INSERT IGNORE`/PG 用 `ON CONFLICT DO NOTHING`、PG 用 `IF NOT EXISTS`

**前端契约（`tests/frontend/test_user_email_contract.py`，新增）**：注册表单含邮箱输入且移动端类名不退化；`Users.vue` 表单与列表含邮箱；`PersonalCenter.vue` 含邮箱编辑；`NotificationConfigs.vue` 含来源单选与 `include_self` 且默认勾选；`SystemConfig.vue` 的 `orderedCategories` 里 `'email'` 在 `'other'` 之后、三个 map 与布尔/下拉硬编码清单都已登记

**回归**：`tests/test_user_registration.py`、`tests/frontend`、`tests/api`、`tests/core` 全绿；`vue-tsc` 错误集合与基线逐条一致

## 12. 完整影响面清单

| 位置 | 改动 |
|---|---|
| `app/models/user.py` | `email` 列 |
| `app/services/auth_service.py` | `normalize_email`；`is_user_email_taken`；**5 处**用户 dict 构造点（`:172/:708/:905/:1035/:1205`）各加 `email` |
| `app/api/portal/endpoints/auth.py` | `RegisterRequest.email`；`register` 校验与落库；`/me` 返回 email；新增 `PATCH /me/profile` |
| `app/api/portal/endpoints/management.py` | `CreateUserRequest.email`/`UpdateUserRequest.email`；创建/编辑校验与落库；列表序列化（`:404`）与搜索（`:380`）；审核发信与 `email_sent` 回传；重发端点 |
| `app/services/config_service.py` | 8 个新 key 的 `validate_config_update` 分支 |
| `app/services/email_delivery_service.py` | **新增**：SMTP 设置加载、分层解析、发送、两封审核邮件文案 |
| `app/services/notification_service.py` | `send_email` 改用分层解析 + `include_self` 收件人拼接；`DEFAULT_CONFIGS['email']` 增加 `smtp_source`/`include_self` |
| `app/services/ai/tools/notification_tools.py` | 同上的加密方式显式化（消除 STARTTLS 静默降级） |
| `app/services/user_sync_service.py` | 字段映射与落库（`:150/:283/:351/:361`） |
| `app/schemas/user_sync.py` | `ThirdPartyUserSyncFieldMap.email` |
| `frontend/src/api/portal.ts` | `User.email`；通知配置类型 |
| 前端 5 个视图/组件 | 见第 9 节 |
| 迁移 4 个文件 | 见 3.2 |
| `tests/CHECKLIST.md` | 按仓库规范同步 |

## 13. 未决与已知边界

- **邮箱未验证**：用户已决策不做验证，所以写错的邮箱会让审核通知静默丢失，且用户对此无感知。对冲：管理员端 toast 明确显示发信失败原因并提供重发，避免出现「平台以为发了、用户没收到、双方都不知道」的静默失败。
- **全局 SMTP 密码仍是明文落库**：`system_configs.value` 是普通文本列，`is_secret` 只作用于 API 出参脱敏，**不加密存储**。这一点与现有用户级 SMTP 配置一致，本轮不引入加密（需要引入密钥管理，属独立议题）。相比现状的改进是：密码从「每用户一份」收敛为「全局一份」，暴露面显著变小。
- **`email_smtp_security = none` + 非空密码 = 凭证明文上网**：不硬性禁止（内网中继仍可能要求认证），靠界面警示与文档约束。若部署在内网且中继免认证，正确做法是 `none` + 同时留空 user/password。
- **邮件发送超时 10 秒**：SMTP 不可达时审核接口会多等最多 10 秒。已接受，因为审核是低频人工操作且需要回传结果。
- **无审计表**：邮箱变更只有日志。合规审计需另立设计。
- **存量用户无邮箱**：3400+ 用户 email 为 NULL，审核邮件对他们不生效，需要管理员补录或等 SSO 同步。
- **SSO 同步覆盖手工修改**：若第三方同步开启 email 映射，用户手工在个人中心改的邮箱会在下次同步时被覆盖。这是与 `real_name` 一致的既有语义，不做特殊处理，但需在同步抽屉的说明里提示。
- **个人中心自助改邮箱不要求密码**：账号被短暂接管时可被改走邮箱。因 email 不是登录因子，风险可接受；若将来邮箱被用作密码找回通道，**必须**重新评估并加上密码确认。
