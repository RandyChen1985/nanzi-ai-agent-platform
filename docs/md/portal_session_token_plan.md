# B 层改造方案：浏览器不再持有长期 API Key

> 状态：**待评审，尚未动代码**
> 目标读者：后端 + 前端负责人
> 前置：A 层加固已落地（Cookie Secure、user_info 去 api_key、安全响应头、登录锁定、CORS 告警）

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
