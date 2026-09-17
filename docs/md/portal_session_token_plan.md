# B 层改造方案：浏览器不再持有长期 API Key

> 状态：**P0 / P1 / P2 已完成**（`PORTAL_SESSION_TOKEN_ENABLED` 默认 true，浏览器不再持有真实 API Key，前端凭据通道已收敛至 HttpOnly Cookie）；**P3 已按需求取消**——登录响应体保留 `api_key` 属有意设计，理由见 §11
> 目标读者：后端 + 前端负责人
> 前置：A 层加固已落地（Cookie Secure、user_info 去 api_key、安全响应头、登录锁定、CORS 告警）
> 实际实现与本文最初设计的差异见文末「P0 实现记录」

## 1. 目标与非目标

**目标**

1. 浏览器（localStorage / cookie / 请求头）中不再出现真实 API Key
2. 真实 API Key 退回到"仅用于外部系统集成调用"的角色
3. 登出能真正吊销会话，而不是只删 cookie
4. 会话可有过期与滑动续期，泄露后有明确的时间上界

**非目标**

- 不改动 `/embed/*` 的 ticket → session token 流程（已闭环，无问题）
- 不改动外部集成方的 `X-API-Key` 调用方式
- 不引入 JWT（见 §3 设计取舍）
- 不做"所有会话集中管理 / 踢下线"这类运营功能（可作为后续独立迭代）

## 2. 现状调研结论（均已核对源码）

| # | 事实 | 位置 |
|---|---|---|
| 1 | `require_api_key` 按 `X-API-Key` → `Authorization: Bearer` → cookie `admin_token` 三路取凭据，并把凭据原文写进 `user_info["api_key"]` | `app/core/dependencies.py:14-54`（第 42 行） |
| 2 | `verify_api_key` 先查 Redis hash `auth:api_key:{sha256}`，未命中再查 DB | `app/services/auth_service.py:158-216` |
| 3 | **`verify_api_key` 已内建 session 概念**：对 `session_type == "embed"` 做 24h 滑动续期 | `app/services/auth_service.py:177-182` |
| 4 | 下游消费 `user_info["api_key"]` 共 6 处，最终都汇入 `AgentContext.api_key` | `context_manager.py:224,332`、`executors/base.py:131,171`、`resume.py:530` |
| 5 | **下游用途是"代表用户回调平台自身"**：`sql_query_execution_service` 直接把该值交给 `AuthService.verify_api_key()` | `app/services/sql_query_execution_service.py:409-411` |
| 6 | `workspace.py:1295` 的 `api_key` 来自系统配置 `sandbox_e2b_api_key`，**与用户凭据无关**（易误判，特此澄清） | `app/services/ai/runtime/agentscope/workspace.py:1273` |
| 7 | 前端从 localStorage 注入 `X-API-Key`；`withCredentials: true` 已开启；已有 `Authorization: Bearer` 分支 | `frontend/src/utils/axios.ts:15,27-41`、`frontend/src/main.ts:15` |
| 8 | cookie `admin_token` 下发 5 处，均为 `HttpOnly + SameSite=Lax + max_age=86400` | `app/api/portal/endpoints/auth.py:62,118,174,247` 等 |
| 9 | 前端 20+ 处读取 `localStorage.api_key` | `utils/axios.ts`、`main.ts`、`api/metadata.ts`、`Roles.vue`（9 处）、`Playground.vue` 等 |

**结论**：第 3 条和第 5 条决定了本方案的可行性——embed 已经趟通了"把短令牌当作一种特殊 api_key 走同一条校验链"这条路，而下游拿到凭据后调用的又是平台自己的 `verify_api_key`。因此**新令牌天然被现有链路接受，不需要改动下游任何一处**。

## 3. 设计

### 3.1 令牌形态

```
sess_<secrets.token_urlsafe(32)>     # 约 43 字符载荷，总长 ~48
```

用固定前缀而不是"靠 Redis 是否存在来判断"，原因：`verify_api_key` 的分支判断必须是常数开销，且日志脱敏、代码可读性都更好。

**为什么不用 JWT**：JWT 的卖点是免存储校验，但代价是**无法即时吊销**。本方案的核心诉求恰恰是"登出即可吊销、泄露可止损"，用不透明令牌 + Redis 查表更贴合，也更简单（无密钥轮换、无算法混淆坑）。

### 3.2 存储

```
portal:session:{sha256(token)}   hash   -> user_data(与 verify_api_key 返回结构一致)
                                        + session_type=portal
                                        + created_at
                                 TTL    86400，活跃调用滑动续期
portal:user_sessions:{user_id}   set    -> token_hash 集合（用于"列出/吊销我的全部会话"，可选）
```

复用与 `verify_api_key` **完全相同**的 user_data 结构，这样下游拿到的 `user_info` 字段不会有任何缺失。

### 3.3 校验入口（唯一的后端改动点）

在 `verify_api_key` 最前面加一个分支：

```python
if api_key.startswith("sess_"):
    return await AuthService._verify_portal_session(api_key)
    # 命中 -> 返回 user_data 并滑动续期
    # 未命中 -> 直接返回 None，不 fallthrough 到 DB
```

**真 key 路径一行不动**，老会话与新会话天然并存。这是本方案风险可控的根本原因。

### 3.4 登录 / 登出

- 5 处 `set_cookie` 的 `value` 从 `api_key` 换成 `session_token`
- `logout` 端点真正删除 Redis 会话键（当前只删 cookie，key 依然永久有效）
- 响应体是否继续返回 `api_key` 字段：见 §4 分阶段

## 4. 兼容性与灰度（重点）

### 4.1 三重平滑保障

1. **老会话不需要失效**：`verify_api_key` 同时接受真 key 与 session token，已登录用户 cookie 里还是真 key，刷新照常工作 → **不需要强制全员重登**
2. **外部集成不受影响**：`X-API-Key` 真 key 路径原样保留
3. **EmbedChat 不受影响**：独立链路，完全不动

### 4.2 分阶段推进

| 阶段 | 内容 | 影响面 |
|---|---|---|
| P0 | 实现 + 配置开关 `PORTAL_SESSION_TOKEN_ENABLED`（默认 **false**）→ 部署 | 行为零变化 |
| P1 | 打开开关，只影响**新登录**的会话 | 老用户无感 |
| P2 | 前端移除 `X-API-Key` 注入（靠 cookie 自动携带） | 老会话 cookie 里仍是真 key，仍工作 |
| P3 | 前端停止写 `localStorage.api_key`；响应体移除 `api_key` 字段 | 用户**下次登录**后浏览器中彻底无真 key |

P1 与 P2 之间必须留观察窗口：这是唯一会出现"新旧凭据形态混合"的时期。

### 4.3 回滚

- **后端**：关掉开关 → 新登录回到真 key，无需回滚代码
- **前端**：P3 之前 `localStorage.api_key` 仍有值，恢复注入逻辑即可
- 由于真 key 路径全程未改动，回滚不需要数据迁移

## 5. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| **Redis 从"缓存"变成"会话强依赖"** | 现在重启不掉线；改后 Redis 数据丢失 = 全员掉线 | 确认 Redis 持久化（AOF/RDB）配置；`auth:api_key` 现有缓存丢失只是性能损失，改造后性质变了，**必须在上线前确认** |
| `ctx.api_key` 语义由真 key 变为会话令牌 | 长生命周期异步任务在会话过期后回调可能 401 | 滑动续期；异步任务改为按 `user_id` 而非凭据传递身份 |
| 前端 20+ 处读取点遗漏 | 某页面/某功能调不通 | 逐处盘点 + 加"禁止前端读取 api_key"的契约测试兜底 |
| 会话无法按设备独立吊销 | 用户想"踢掉某台设备"做不到 | 可选项：维护 `portal:user_sessions:{user_id}` 索引 |
| 会话令牌被日志打印 | 泄露 | 日志统一脱敏：仅记录前缀 + 前 8 位 |

## 6. 改动清单与工作量

**后端（约 1 天）**

| 文件 | 改动 | 量级 |
|---|---|---|
| `app/services/auth_service.py` | 新增签发 / 校验 / 吊销 / 滑动续期 4 个方法 | +80 行 |
| `app/api/portal/endpoints/auth.py` | 5 处 `set_cookie` 换值 + logout 真正吊销 | ~15 行 |
| `app/core/config.py` | 新增开关 | +2 行 |
| `app/core/dependencies.py` | **无改动**（第 42 行天然适配） | 0 |

**前端（约 1 天）**

| 文件 | 改动 |
|---|---|
| `frontend/src/utils/userSession.ts` | 不再写 `localStorage.api_key` |
| `frontend/src/utils/axios.ts`、`main.ts` | 移除 `X-API-Key` 注入，保留 embed 的 `Authorization` 分支 |
| 20+ 处 `localStorage.getItem('api_key')` | 逐处改为依赖 cookie 自动携带 |
| `frontend/src/views/Login.vue` | 不再 `persistApiKey` |

**测试（约 0.5 天）**：签发/校验/吊销/过期/滑动续期单测；"真 key 仍可用"与"伪造 `sess_` 前缀无效"的兼容契约；前端"不再写 api_key"契约。

**联调与灰度（约 1 天）**：按 §4.2 逐阶段观察。总计约 **3.5 天**。

## 7. 验收标准

1. 打开开关并重新登录后，`localStorage` 中不存在真实 key（`localStorage.api_key` 为空）
2. F12 网络面板中，任意 API 请求**不再出现 `X-API-Key`**，只携带会话 cookie
3. 登出后，原会话令牌立即失效（手动重放该 cookie 应得到 401）
4. 外部集成用真实 key 调 `/api/v1/*` 仍然正常
5. `/embed/*` 嵌入流程完全不受影响
6. 关闭开关后，系统回到改造前行为

## 8. 建议的下一步

先只做 **P0 + 后端单测**（可独立评审、行为零变化、随时可弃），确认 Redis 持久化现状后再决定是否推进 P1。前端改造只有在前端负责人确认 20+ 处读取点的盘点结果后才开始。

---

## 9. P0 实现记录（已完成）

### 9.1 实现比原设计更简单

原设计打算新建 `portal:session:*` 键空间，并在 `verify_api_key` 里加 `sess_` 前缀分支。实际实现发现：**复用现有的 `auth:api_key:{sha256(token)}` 键空间即可，`verify_api_key` 无需任何分支**。

原因：`register_online_state()` 本来就是"认证缓存预热"，写入的正是 `verify_api_key` 读取的键。因此签发时把令牌写进同一个键，现有校验链自动接受它——`dependencies.py` 与 AI 运行时下游**一处未改**。

`verify_api_key` 仅有的一处改动：把滑动续期条件从 `session_type == "embed"` 扩展为 `in ("embed", "portal")`（两者 TTL 均为 86400）。

### 9.2 顺带修掉的一个原有盲点

`logout` 原来**只从 `X-API-Key` 请求头取凭据**：

```python
api_key: Optional[str] = Header(None, alias="X-API-Key")
if api_key:
    await AuthService.expire_api_key(api_key)
```

浏览器实际是凭 cookie 认证、并不发送该请求头，所以**登出从未真正吊销过服务端会话**，只是本地删掉了 cookie。P0 一并修正为"优先取请求头，其次取 `admin_token` cookie"，并区分会话令牌（`revoke_portal_session`）与真实 Key（`expire_api_key`）。

### 9.3 两个设计决策

- **重置 API Key 不吊销会话**：`reset_my_api_key` 的响应体仍返回真实新 Key（用户需要它去配置外部集成），但 cookie 换成会话令牌。重置不等同于登出；泄露的 Key 已立即失效，而浏览器本地令牌并未泄露，保留当前会话是合理体验。
- **5 处下发放统一走 `_issue_admin_token_cookie`**：避免遗漏任何一条登录路径，并有契约测试锁定该数量。

### 9.4 测试抓到的一个真实缺陷

新增的"开关关闭时行为不变"测试首次真正执行了 helper，立刻暴露出 `register_online_state(resolved, user)` 的 `NameError`（重命名 `resolved` → `credential` 时漏改一处）。此前 11 个测试全绿，是因为没有任何用例走到那一行——这也印证了"开关关闭路径"必须被显式覆盖。

### 9.5 文件清单（P0）

| 文件 | 改动 |
|---|---|
| `app/services/auth_service.py` | 会话常量、`_session_cache_key`、`_user_model_to_data`、`create_portal_session`、`revoke_portal_session`、滑动续期分支 |
| `app/api/portal/endpoints/auth.py` | `_issue_admin_token_cookie` 统一入口 + 5 处接入 + logout 吊销 |
| `app/core/config.py` | `PORTAL_SESSION_TOKEN_ENABLED`（默认 true） |
| `tests/services/test_portal_session_token.py` | 13 项测试（签发/校验/吊销/滑动/兼容真 key/开关行为/logout 契约） |

### 9.6 Redis 依赖（P1 已按默认开启推进）

`PORTAL_SESSION_TOKEN_ENABLED` 现已**默认开启**。这使 Redis 从"缓存"变为会话的**硬依赖**：
数据丢失不再是缓存失效，而是全员掉线。

仓库内没有 docker-compose 定义，k8s 配置指向外部 `redis.example.internal`，即 Redis 由运维侧
独立提供，其 AOF/RDB 配置无法在本地核实。**请运维确认 Redis 已开启持久化**；若出现异常，
将 `PORTAL_SESSION_TOKEN_ENABLED=false` 即可立即回退到旧行为（cookie 直存真实 API Key），
已签发的令牌也随之失效，用户需重新登录。

---

## 10. P2 进展记录（凭据通道收敛）

### 10.1 目标

把浏览器里的凭据从"JS 可读的 localStorage"收敛到"JS 不可读的 HttpOnly Cookie"，
使 XSS 无法窃取会话凭据。P1 已保证**浏览器里不再有真实 API Key**，P2 进一步消除
"令牌副本可被脚本读取"这一残余面。

### 10.2 改造顺序：先移除读取，再停止写入

这个顺序把风险降到最低——在读取点全部移除前，`localStorage.api_key` 仍照常写入，
任何一步都可独立回滚。

1. **移除全局注入** ✓：`main.ts`、`utils/axios.ts` 不再从 localStorage 取 api_key 注入 `X-API-Key`
2. **移除页面内手动注入**（27 处 / 19 个文件）：同源 Cookie 已承担该职责
3. **停止写入** `localStorage.api_key`（`Login.vue`）：此时已无读取方，可以安全移除
4. **解除登录态对 localStorage 的依赖** ✓：`Dashboard.vue` / `NoPermission.vue` / `Playground.vue`
   不再以"本地有没有凭据"判断是否登录，改以后端 `/api/me` 响应为准

### 10.3 保留显式凭据传递的三处（Cookie 无法替代）

| 位置 | 为什么不能改用 Cookie | 处理方式 |
|---|---|---|
| `WidgetDebugger.vue` | 生成给**第三方宿主**的接入代码，凭据必须显式交付 | 改为向后端索取**真实 API Key** |
| `Chat.vue` | `postMessage` 向同源 iframe 传初始化配置 | 初始化配置照常下发；token 仅在本地仍有值时附带，嵌入页有 cookie-only 兜底 |
| `Playground.vue` | Scalar 外部 SDK 自行发起请求，不受本项目拦截器控制 | token 置空，依赖同源 Cookie |

### 10.4 P1 引出的一个功能回归（已修）

`WidgetDebugger` 原先用 `localStorage.api_key` 作为接入代码里的凭据。P1 之后那里是
24 小时会话令牌，写进第三方接入代码会导致**用户一登出，集成立即失效**。

已改为调用 `GET /api/portal/management/api-key/{user_id}` 取真实 API Key。该接口
在读取自己的 Key 时不需要额外权限（`management.py` 中 `user_id == current_user_id`
时跳过 `element:user:view_key` 校验），因此普通用户可用。

### 10.5 嵌入场景保持独立

`EmbedChat.vue` 继续管理自己的凭据，门户侧改造不介入该路径。它本身已有完整的
cookie-only 兜底分支（`/api/portal/auth/user_apikey` + `credentials:'include'`），
并在校验成功后主动清理 `localStorage.api_key` / `yovole_token`。

## 11. P3 决策记录：登录响应体保留 `api_key`（按需求取消）

原计划在 P3 移除 `POST /auth/login`、`/auth/sso/login`、`/auth/login/2fa` 响应体里的
`api_key` 字段。**已确认取消**：返回凭据供调用方做鉴权判断是业务需要的行为，不属于缺陷，
故保留不动。`POST /auth/api-key/reset` 返回真实新 Key 的设计同样保留（重置是显式的低频
操作，用户需要它去配置外部集成）。

排查过程中确认的事实，供后续改动参考：

- `require_api_key`（`app/core/dependencies.py`）会把**本次请求使用的凭据**写入
  `user_info["api_key"]`，供下游（SQL 执行服务、AI 运行时等）内部使用。因此
  **凡是用 `**user` 整包展开的响应，都会把调用方凭据原样回显**。
- 全仓 `**user` 展开共 4 处：`auth.py` 的 3 处（SSO 登录 / 登录 / 2FA，即上述有意保留的
  三处）与 `management.py:73`。后者是 `for user in sso_users` 的循环变量，**不是**依赖注入
  的字典，无凭据回显问题。
- `GET /auth/me` 的入参虽同为 `Depends(require_api_key)`，但返回体是**逐字段显式取值**
  （`user.get("user_id")` 等），未整包展开，因此不携带凭据。**这个安全性来自写法而非结构**：
  若将来把它改成 `**user`，凭据会随之泄露，改动时需留意。

前端侧维持现状：不写入、不读取 `localStorage.api_key`，认证依赖同源 HttpOnly Cookie。
后端照常返回该字段，两者不冲突。

