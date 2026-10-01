# 忘记密码（邮件找回）设计文档

- 日期：2026-10-01
- 状态：设计待确认
- 关联：`2026-10-01-user-email-and-mail-service-design.md`（用户邮箱字段与全平台邮件服务，本设计是其直接延续：邮件通道既已具备，才有条件用邮件找回密码）
- 说明：本特性**零数据库迁移**（token 存 Redis，不新增表、不新增列、不新增配置项）

## 1. 背景与目标

用户诉求（原文）：「我们加个忘记密码的功能，通过邮件找回密码，生成临时的有效期的 url 地址，来修改密码，同时做好风控，避免重复操作找回密码，地址的 url 前缀也可以读 `download_url_prefix` 的值，系统配置中的 `download_url_prefix` 也加一下说明，可能忘记密码这块 url 地址也会用到」。

拆成四件事：

1. **未登录可用的找回入口**：输入邮箱 → 收到一封含**带有效期的重置链接**的邮件 → 打开链接设置新密码。
2. **风控**：同邮箱 60 秒冷却 + 每小时 3 次；同来源地址每小时 5 次；被限流时静默。
3. **链接前缀复用 `download_url_prefix`**，不新增配置项。
4. **补 `download_url_prefix` 的说明**，写明它同时用于重置链接。

现状的关键事实（已逐一核实）：

- 上一轮已完成用户邮箱字段（`ai_agent_users.email`，全局唯一索引 `uk_ai_agent_users_email`）与全局邮件服务（`EmailDeliveryService`，`email_service_enabled` 开关默认关闭）。
- `EmailDeliveryService.load_global_smtp_settings()` **已经在内部做完开关与完整性判定**：开关关闭或配置不完整一律返回 `None`。所以「邮件服务是否可用」是一次调用即可判定的。
- 平台已有**唯一免鉴权**的路由组就是 `auth`（`/api/portal/auth/*`），本设计的两个新端点都属于它；其余路由组都挂了 `require_api_key`。
- Redis **已经是**平台运行依赖，且代码里已有**一次性票据**的成熟范式：`app/services/embed_service.py` 用 `GET → 校验 → GETDEL → 再比对身份` 核销票据，并留有踩坑注释（直接 GETDEL 会在「校验未通过」时把票烧掉）。
- `app/core/redis.py` 提供两个客户端（`decode_responses` 分别为 True/False），现有调用方按需兼容 `bytes`/`str`。
- `AuthService` 已有 `revoke_sessions_for_user(user_id)`：登录签发的会话在 Redis 里以 `auth:api_key:{hash}` 存储，并以集合索引到用户；该方法删除索引内的全部会话键。**长期 API Key 不在该索引内**，因此不会被动到。
- `AuthService.resolve_rate_limit_source(request)` 与 `get_redis()` 的 fail-open 范式已在注册/登录限流中使用，来源可信度判定（反代异机时不误伤全平台）可直接复用。
- `PUT /api/portal/auth/password`（登录态改密）**目前不吊销任何会话** —— 已确认本次顺手修掉。
- `get_download_url_prefix()` 读取系统配置并回退 `settings.APP_PUBLIC_URL`，且已做 `strip()` + 去尾斜杠。
- 系统配置页的**搜索只匹配数据库的 `item.description`**，不匹配代码里的 `configShortDescriptions`。
- 迁移编号现状：MySQL 到 V164，PG 到 V65（本设计不使用新编号）。

## 2. 范围

**做**：

- `POST /api/portal/auth/password-reset/request`、`POST /api/portal/auth/password-reset/confirm`
- `/auth/config/public` 增加 `password_reset_available`
- Redis 存重置 token（30 分钟 TTL、单次使用、新申请作废旧链接）
- 登录页「忘记密码？」入口 + 公开路由 `/reset-password` 重置页
- `download_url_prefix` 的三处说明更新 + 配置搜索覆盖代码短说明
- `PUT /auth/password` 改密后吊销该用户全部会话（含对应前端处理）

**不做（YAGNI）**：

- 不加独立开关（已定：只看全局邮件服务 `email_service_enabled`）
- 不新增表、不新增列、不新增配置项、不新增迁移
- 不改用户名校验口径、不做重置历史审计表、不做邮件模板引擎
- 不自动轮换用户的长期 API Key（见 §7.2）
- 不在重置成功后自动登录
- 不发「密码已变更」的通知邮件给原邮箱
- 不改动 SSO 登录路径

## 3. 已锁定的决策

| # | 决策 | 取值 |
|---|---|---|
| 1 | 防枚举 | 无论邮箱是否存在、账号是否可用、是否被限流，**响应体完全一致** |
| 2 | 有效期与使用 | 30 分钟有效；用一次即失效；**再次申请会作废旧链接** |
| 3 | 限流口径 | 同邮箱 60 秒冷却 + 3 次/小时；同来源地址 5 次/小时 |
| 4 | 独立开关 | 不加；`password_reset_available` 完全跟随全局邮件服务可用性 |
| 5 | token 存储 | Redis 单键（方案 A），零迁移 |
| 6 | Redis 降级 | 限流 fail-open（沿用现状）；**发信与核销 fail-closed**，统一 503 |
| 7 | 状态过滤 | 仅 `status=1`（启用）账号真发信；待审核/已禁用静默 |
| 8 | 改密后会话 | 重置成功吊销该用户全部会话；`PUT /auth/password` 一并修 |
| 9 | 链接前缀 | 复用 `get_download_url_prefix()`，不新增配置 |

## 4. 接口契约

### 4.1 `POST /api/portal/auth/password-reset/request`

请求：`{"email": "<string>"}`（`email` 必填、非空、去首尾空白）

处理顺序（顺序即错误优先级，与注册端点同风格）：

1. **邮件服务可用性**：`EmailDeliveryService.load_global_smtp_settings()` 为 `None` → `503`，`detail = "邮件找回功能未开启，请联系管理员"`。该判定与提交的邮箱完全无关，**不构成枚举泄露**。
2. **对外地址可用性**：`get_download_url_prefix()` 为空串 → `503`，`detail = "平台未配置对外访问地址，请先在系统配置中填写 download_url_prefix"`。理由见 §5：空前缀会把链接拼成相对地址，邮件里根本点不开 —— 与其发一封废邮件，不如明确报出配置问题（同样与邮箱无关，不构成枚举泄露）。
3. **Redis 可用性**：`get_redis()` 返回 `None` 或抛错 → `503`，`detail = "密码重置服务暂时不可用，请稍后重试"`。理由：发信必须 fail-closed —— 不能发一封之后无法核销的链接。
4. **限流**（见 §6）：任一维度触顶 → **静默**返回与成功完全相同的 200 响应，不发信、不报错、记 `logger.info`。
5. **邮箱格式**：复用 `AuthService.normalize_email(raw, strict=True)` —— 平台既有唯一入口，**不要再写第三份正则**（`config_service` 里那份重复是既有问题，不在本次范围）。格式非法 → `400`，`detail = "邮箱格式不正确"`。
   这不破坏一致性契约：格式是否合法**只取决于输入本身**，与邮箱是否已注册无关，因此不产生任何枚举信息（见 §6.1 的边界说明）。
   > 次序后果：若一次请求同时命中限流又是非法格式，按第 4 步先返回一致响应体。这与注册端点既有的不变式一致（「限流先于格式校验，避免绕过计数」），不是矛盾。
6. **查库**：`func.lower(User.email) == normalized_email`。
7. **发信**：命中且 `status == 1` 时，通过 FastAPI `BackgroundTasks` **在响应之后再发**（见 §6.3）。
8. 返回。

成功/静默响应（**恒定**）：

```json
{"status": "success", "message": "如果该邮箱已注册，我们已发送重置邮件"}
```

> `status` 字段沿用平台其它端点的 `"success"` 约定，便于前端统一处理。

### 4.2 `POST /api/portal/auth/password-reset/confirm`

请求：`{"token": "<string>", "password": "<string>"}`

处理顺序（**副作用顺序经过刻意安排：所有校验都必须在核销之前完成**）：

1. **解析 token**：`uid, sep, nonce = token.partition(".")`；`uid` 必须是纯数字且 `sep` 存在，否则 → 统一错误。
2. **读 Redis**：`GET auth:pwdreset:{uid}`。不存在（过期/已用/被新申请覆盖）→ 统一错误。
3. **比对哈希**：`hmac.compare_digest(sha256(nonce).hexdigest(), stored)` 不匹配 → 统一错误。
4. **状态复核**：查用户；不存在或 `status != 1`（待审核/已禁用）→ 统一错误。
5. **密码复杂度**：`AuthService.validate_password_complexity(password, username=user_name)`（需要 `user_name`，所以必须排在第 4 步之后）→ 失败时返回**具体原因**（见下）。
6. **核销**：`GETDEL auth:pwdreset:{uid}`，返回 `None`（并发下已被别人用掉）或内容与第 2 步读到的**不一致**（同一 key 被换进了另一张票）→ 统一错误。此处沿用 `embed_service` 的四步范式。
7. **改密**：`set_user_password`（沿用现有 72 字节截断行为，与 `PUT /auth/password` 保持一致）。
8. **吊销会话**：`revoke_sessions_for_user(user_id)`。
9. **审计**：结构化 `logger.info`（`user_id`、`user_name`、事件类型；**不记录 token 与密码**）。
10. 返回 `200 {"status": "success", "message": "密码重置成功，请使用新密码登录"}`。**不签发任何会话**。

> ⚠️ **第 1—5 步全部无副作用，核销只在第 6 步发生**。这是从 `embed_service` 的踩坑注释里学到的同构问题：若先核销再校验，用户只是「密码设得不够强」或「账号刚好被管理员禁用」就会白白失去这条链接，必须重新走一遍邮件流程。第 4、5 步的失败都发生在核销之前，token 仍然可用。

统一错误（第 1、2、3、4、6 步）：`400`，`detail = "链接无效或已过期，请重新申请"`。五处必须**字面一致**，避免把「过期」「已用」「被新申请覆盖」「账号被禁用」区分出来。

第 5 步的密码复杂度错误**可以具体**（返回真实原因）：用户此时已证明持有邮箱与有效 token，不存在枚举面，具体提示对体验更重要。

本端点**不做限流**（有意识的决定，非遗漏）：token 由 32 字节随机 `nonce` 构成，枚举不可行；限流反而会给正常用户因输错密码而被锁的风险。仅通过统一错误文案 + 审计日志观测异常。

### 4.3 `GET /api/portal/auth/config/public`

新增字段：

```json
{"password_reset_available": "<bool>"}
```

计算方式（两个条件同时满足）：

```python
mail_ok = (await EmailDeliveryService.load_global_smtp_settings()) is not None
prefix_ok = bool(await get_download_url_prefix())
password_reset_available = mail_ok and prefix_ok
```

把「对外地址是否配置」也纳入该标志：配置缺失时登录页直接不显示入口，好过让用户填完邮箱才拿到 503。

## 5. Token 设计

- 明文 token（只出现在邮件链接里）：`f"{user_id}.{nonce}"`，`nonce = secrets.token_urlsafe(32)`
- Redis 键：`auth:pwdreset:{user_id}`
- Redis 值：`sha256(nonce).hexdigest()` —— **存哈希而非明文**：Redis 快照、`MONITOR`、慢日志泄漏时明文不可直接使用
- TTL：`1800` 秒（30 分钟）
- **发新即覆盖**：签发时直接 `SET key value EX 1800`（覆盖写）。因为每个用户只有一个键，「再次申请作废旧链接」是**原子的**，不需要读旧值、不存在并发窗口，也不存在「两个链接同时有效」的可能。
- **单次使用**：核销用 `GETDEL`（原子取删），两个并发请求只有一个能拿到值。
- **登录态不参与**：token 与用户当前是否登录无关；重置页是公开页。

链接构造：

```python
prefix = await get_download_url_prefix()          # 已 strip + 去尾斜杠
link = f"{prefix}/reset-password?token={token}"
```

留空的 `download_url_prefix` 会回退到 `settings.APP_PUBLIC_URL`。

**前缀为空的处理与下载链接不同**：`get_download_url_prefix()` 依次回退「系统配置 → `APP_PUBLIC_URL`」，两者都没有时返回空串。下载链接即使退化成相对地址也仍然可用（用户本来就在 Web 应用里点），**但邮件里的相对地址点不开**。因此本特性把「前缀为空」视为**功能不可用**：登录页不显示入口（§4.3）、端点直接 503（§4.1 第 2 步），绝不发出一封带废链接的邮件。

## 6. 防枚举与风控

### 6.1 一致性契约（防枚举的核心）

下列五种情况必须返回**字面完全相同**的 200 响应体：

1. 邮箱不存在
2. 邮箱存在但账号处于待审核（`status=2`）
3. 邮箱存在但账号已禁用（`status=0`）
4. 邮箱存在、账号启用，且确实发了信
5. **被限流**（冷却窗口内、邮箱配额触顶、来源配额触顶）

第 5 项尤其关键：若返回「操作过于频繁」，攻击者就能用它反推「这个邮箱刚刚被谁申请过」，从而确认邮箱已注册 —— 这正是本设计要封掉的枚举面。用户侧的 60 秒冷却提示由**前端本地实现**（按钮倒计时），不依赖服务端报错。

**一致性契约的边界**（避免把下列情况误判为违约）：

- **格式非法 → 400**：只取决于输入本身，与是否已注册无关
- **邮件服务未开启 / 对外地址未配置 / Redis 不可用 → 503**：只取决于平台级配置与基础设施状态，与提交的邮箱无关，且对任何邮箱都一致
- 只要「响应差异与提交的邮箱是否已注册相关」就不允许存在；「响应差异与邮箱无关」是允许且必要的（否则用户无法得知平台没开这个功能）

### 6.2 限流实现

三个 Redis 键：

| 维度 | 键 | 语义 |
|---|---|---|
| 同邮箱冷却 | `auth:pwdreset:cd:email:{sha256(lower(email))}` | `SET NX EX 60` |
| 同邮箱配额 | `auth:pwdreset:cnt:email:{sha256(lower(email))}` | `INCR` + 首次 `EXPIRE 3600`，上限 3 |
| 同来源配额 | `auth:pwdreset:cnt:ip:{source}` | `INCR` + 首次 `EXPIRE 3600`，上限 5 |

- 键名用 **`sha256(lower(email))` 而不是明文邮箱**：Redis 键名会出现在监控、慢日志与 `KEYS` 输出里，不应把用户邮箱写成键。
- `source` 取自 `AuthService.resolve_rate_limit_source(request)`，与注册限流**共用同一套可信度判定**：反代在异机/异 Pod 时判定失效则跳过该维度，避免「全平台每小时只能找回 5 次」。
- 冷却键**仅在通过全部限流后设置**，避免一次被拒的请求也占用冷却窗口。
- 内部命令异常（Redis 可用但单条命令失败）→ 告警并跳过该维度（fail-open，沿用现状）；Redis 整体不可用则在 §4.1 第 2 步就已 503。

### 6.3 耗时一致性

SMTP 是**同步阻塞**发送（`send_mail`，10 秒超时）。若在请求内发信，「发了信」与「没发信」的响应耗时差异可达数秒 —— 这本身就是一个可远程利用的枚举预言机，会让 §6.1 的字面一致性失效。

因此本端点用 FastAPI `BackgroundTasks` **在响应返回之后再发信**：

- 所有分支的响应耗时一致（不受 SMTP 影响）
- 发送失败只进日志（`logger.warning`），不影响响应 —— 反枚举要求响应不可能报告真实发送结果
- 与「审核结果通知」的同步发送不同（那里要给管理员回执 `email_sent`），本处**不需要回执**

## 7. 安全联动

### 7.1 会话吊销

重置成功后调用 `AuthService.revoke_sessions_for_user(user_id)`，使用户在所有设备上被强制重新登录。这是「密码找回」成立的前提：否则窃取了会话的人在你重置密码后依然畅通。

### 7.2 长期 API Key 不自动轮换

`revoke_sessions_for_user` 只清理**登录签发的会话**（索引集合内的键），不会动长期 API Key。这是刻意的：

- 长期 API Key 是用户显式部署到外部集成里的凭证，自动轮换会静默打断那些集成
- 它有自己的轮换入口（`POST /auth/api-key/reset`，个人中心）
- 邮件正文与重置成功页会提示：「如果你怀疑账号已被他人使用，请到个人中心重置 API Key」

### 7.3 2FA 不受影响

重置只修改密码；开启了两步验证的用户登录时仍须通过 2FA，不存在「用邮箱绕过 2FA」的路径。

### 7.4 审计

结构化 `logger.info`，与「个人中心自助改邮箱」的现有口径一致（平台不为这类自助操作建审计表）。记录：`user_id`、`user_name`、发生时间、来源。**不记录** token、密码或密码哈希。

## 8. 邮件内容

- 收件人：提交的邮箱（即账号邮箱，唯一索引保证至多一个用户）
- 发件：**全局 SMTP**（`scope="global"`），与「审核结果通知」同一分层 —— 找回密码必须在用户自己 SMTP 配错时仍然可用
- 主题：`AI 智能体平台 - 密码重置`
- 正文（纯文本）：重置链接、**30 分钟内有效**、**只能使用一次**、「如果不是你本人操作，请忽略本邮件，你的密码不会被修改」、以及 §7.2 的 API Key 提示
- 链接不隐藏、不做短链（用户在纯文本邮件里要能看清并复制）

## 9. 前端

### 9.1 登录页

`Login.vue` 在密码登录区增加「忘记密码？」入口，**仅当 `password_reset_available` 为真时渲染**（数据来自已有的 `/auth/config/public` 调用，与 `registrationEnabled` 同一处赋值）。

### 9.2 重置页

新增公开路由 `/reset-password`（`meta: { public: true, title: '重置密码' }`）与 `frontend/src/views/ResetPassword.vue`，**同一页两种模式**：

- **无 `token` 查询参数** → 「输入邮箱」表单。提交后切换到「已发送」态，展示后端返回的**统一文案**，并开始 60 秒本地倒计时禁用按钮。
- **有 `token` 查询参数** → 「设置新密码」表单（新密码 + 确认密码）。客户端做复杂度与一致性预校验，但**以服务端为准**；成功后展示成功提示，引导回 `/login`。
- token 无效/过期/已用 → 展示后端统一文案，并提供「重新申请」入口回到邮箱模式。

### 9.3 登录态改密的联动（对应 §2 的既有缺陷修复）

`PUT /auth/password` 改为改密后吊销全部会话，意味着**当前设备也会被踢出**。因此个人中心改密成功的处理必须同步调整：清掉本地凭据（token/会话状态）并跳转登录页，同时提示「密码已修改，请重新登录」。若不做这一步，用户会在改密后被一连串 401 弄得莫名其妙。

## 10. `download_url_prefix` 的说明更新（纯代码，零迁移）

新文案要点：这个前缀不只用于生成文件下载链接，**也用于忘记密码的重置链接**；只填协议 + 域名 + 必要的反代前缀，**不要**填写 API 路径、`/reset-password` 路径或 token；留空时回退 `APP_PUBLIC_URL` 或相对地址。

三处更新：

1. `frontend/src/views/SystemConfig.vue` 的 `configShortDescriptions.download_url_prefix`（左侧短说明）
2. 同文件 `item.key === 'download_url_prefix'` 的自定义渲染块（💡 示例框）：在现有下载地址示例旁补一条重置链接示例
3. `frontend/src/components/system/DeploymentChecklist.vue` 中该配置项的说明（它同样是「这个配置干什么用」的文案，一并写明）

外加一处**让说明可被搜到**：

4. `SystemConfig.vue` 的配置搜索目前只匹配 `item.description`（数据库字段）。因为本设计不写迁移、不改数据库里的 description，需把搜索条件扩展为**同时匹配 `configShortDescriptions[item.key]`**，否则用户在系统配置里搜「找回密码」将搜不到本项 —— 而这正是「不知道这个前缀还被重置链接用着」时最自然的发现路径。

> 数据库里 `download_url_prefix.description` 的旧文案保持不变（仍是准确的，只是没提重置链接）。若后续希望数据库文案也同步，补一份 `UPDATE` 迁移即可，属独立小改动。

## 11. 测试计划

### 11.1 后端行为测试（新建 `tests/test_password_reset.py`）

反枚举一致性：

1. 邮箱不存在 / 待审核 / 已禁用 / 启用命中 → 四者 `status_code` 与响应体**逐字节相同**
2. 冷却窗口内重复提交 → 响应体与成功路径相同，且**没有第二次发信**
3. 邮箱配额触顶（第 4 次）、来源配额触顶（第 6 次）→ 同样返回一致响应体，且不发信
4. 邮箱格式非法 → 400（**不是**静默 200），且与邮箱是否注册无关

Token 生命周期：

5. token 单次使用：第二次 confirm → 400 统一文案
6. 并发 confirm（同一 token 两个请求）→ 恰好一个成功（`GETDEL` 原子性）
7. 再次申请 → **旧 token 失效**，新 token 可用（覆盖写生效）
8. TTL：签发时 `EX` 为 1800；键过期后 confirm → 400
9. token 篡改：改 `uid`、改 `nonce`、去掉分隔符 → 均 400 统一文案
10. Redis 值里存的是哈希，**不是明文 nonce**
11. 密码不合法 → 400 具体原因，**且 token 未被核销**（随后用合法密码仍能成功）—— 这条专门守住 §4.2「所有校验都在核销之前」的顺序

降级与开关：

12. Redis 不可用（`get_redis` 返回 `None`）→ 503，且**不发信**
13. 邮件服务未开启 → 503，且**不发信**
14. `download_url_prefix` 与 `APP_PUBLIC_URL` 均为空 → 503，且**不发信**；同时 `/auth/config/public` 的 `password_reset_available` 为 `false`

副作用：

15. 成功后旧密码不能再登录 / 新密码可登录，且 `password_hash` 发生变化
16. 成功后 `revoke_sessions_for_user` 被调用（或断言会话索引键被删）
17. 审计日志被写入（`caplog`），且**日志里不含 token 与密码**
18. 邮件正文包含由 `download_url_prefix` 拼出的链接与「30 分钟」
19. `PUT /auth/password` 改密后同样吊销会话（§7.1 的既有缺陷修复）

### 11.2 前端契约测试（新建 `tests/frontend/test_password_reset_contract.py`）

20. `Login.vue` 入口由 `passwordResetAvailable` 控制（且取自 `/auth/config/public`）
21. 路由 `/reset-password` 存在且 `meta.public === true`
22. 统一文案字符串与后端一致（防两处漂移）
23. 无 token → 渲染邮箱表单；有 token → 渲染新密码表单
24. 改密成功后清理本地凭据并跳转 `/login`
25. `SystemConfig.vue` 的说明含「重置链接」，且搜索条件包含 `configShortDescriptions`

### 11.3 反向验证

上述每条守卫都用「注入变异 → 对应用例必须失败 → 还原后通过」验证一遍，重点：把统一文案改成区分文案、把 `GETDEL` 换成 `GET`、把复杂度校验挪到核销之后、去掉覆盖写、把后台发信改回同步发信、去掉会话吊销、去掉对外地址为空的判定、把格式非法改成静默 200。

### 11.4 回归基线

- 后端 `tests/api` + `tests/core` 与基线逐项一致
- `tests/frontend` 全绿
- `vue-tsc --noEmit` 错误数与基线一致（改动前先记录基线）

## 12. 风险与已知取舍

| 风险 | 说明与取舍 |
|---|---|
| Redis 重启 | 在途重置链接全部失效，用户重新申请即可。30 分钟窗口内影响面小，可接受 |
| 用户填错邮箱看不到提示 | 这是防枚举的**必然代价**（决策 1）。缓解：统一文案里说明「如果你没有收到邮件，请检查邮箱地址是否正确」 |
| 后台发信失败不可见 | 用户视角只能靠「没收到邮件」感知；受风控限制，重试成本低。日志侧有告警 |
| 邮箱被攻破即等于账号被攻破 | 邮件找回的固有性质，非本设计引入；2FA 仍可兜住登录 |
| 邮件进垃圾箱 | 由发件域名信誉决定，非本设计可控；统一文案已提示「检查垃圾邮件」 |
| 一致性只做到「响应体 + 耗时」 | 攻击者仍可通过「有没有收到邮件」判定，但那需要控制目标邮箱，等价于已经拿到邮箱控制权，不构成额外枚举面 |

## 13. 验收标准

1. 全局邮件服务未开启时：登录页**没有**「忘记密码？」入口；直接调接口得 503，且不发信
2. `download_url_prefix` 与 `APP_PUBLIC_URL` 都为空时：同样**没有**入口，接口 503，且不发信（不发出带废链接的邮件）
3. 已注册的启用账号能收到邮件，链接形如 `{download_url_prefix}/reset-password?token=...`，正文含 30 分钟与一次性说明
4. 该链接能成功改密，且**用一次即失效**；重复使用得统一错误文案
5. 改密成功后原会话全部失效（其它设备被登出），新密码可登录
6. 再次申请后**旧链接失效**、新链接可用
7. 提交任意的、不存在的、待审核的、已禁用的邮箱，响应与成功路径**完全一致**；格式非法则明确 400
8. 60 秒内连点、或超过每小时配额，**不产生额外邮件**，且响应与成功路径一致
9. 密码设得不够强时给出具体原因，且**该链接仍可再次使用**（没被烧掉）
10. 系统配置页中搜「找回密码」能定位到 `download_url_prefix`，并以新说明展示
11. **零数据库迁移**：`db-prod/` 与 `db-prod-pg/` 不新增文件
12. 登录态改密后当前设备同样需要重新登录，且前端有明确提示

## 14. 实施顺序建议

1. `AuthService`：token 签发/核销 + 限流（纯逻辑，可先单测）
2. `EmailDeliveryService`：重置邮件正文构造
3. 两个端点 + `/auth/config/public` 字段
4. `PUT /auth/password` 吊销会话 + 前端联动
5. 前端 `ResetPassword.vue` + 路由 + 登录页入口
6. `download_url_prefix` 三处说明 + 搜索扩展
7. 测试与反向验证
8. 更新 `tests/CHECKLIST.md`
