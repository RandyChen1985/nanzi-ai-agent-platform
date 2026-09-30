# 账号自主注册申请与管理员审核：设计文档

- 状态：📝 待评审（2026-09-30）
- 创建时间：2026-09-30
- 类型：功能新增（登录页 + 系统配置 + 用户管理）
- 涉及迁移：MySQL `V162` / PostgreSQL `V63`（仅配置种子，无 DDL）

## 1. 背景与目标

平台当前所有账号都由管理员在「用户管理」页手工创建，或从 SSO / 第三方数据源同步。外部想接入平台的同事没有任何自助入口，只能线下找人开账号。

**目标**：在登录页提供一个可开关的「申请账号」入口，申请人自助提交账号名、用户姓名、密码与备注；提交后账号进入**待审核**状态且**不能登录**；管理员在用户管理页看到待审核数量提示，在「待审核」页签里逐条审核为**启用**或**禁用**。

**关键约束（来自需求）**：

1. 注册功能由系统配置控制，位于「系统配置 › 常规设置」分组，**默认关闭**。
2. 密码必须符合现有等保复杂度规范。
3. 待审核账号不允许登录。
4. 审核结果只有两种：启用 或 禁用；**禁用为永久禁用，账号名唯一且永久占用**，不释放、不可重新申请。
5. 注册时做账号重名检测并给出明确提示。
6. 不改动数据库表结构（不新增列、不 `ALTER TABLE`）。

## 2. 范围

### 做

1. **配置开关**：新增 `system_configs` 配置项 `user_registration_enabled`（`general` 分组，默认 `false`），在系统配置页以布尔开关呈现。
2. **公开配置下发**：`/api/portal/auth/config/public` 返回该开关，登录页未登录即可读取。
3. **注册接口**：`POST /api/portal/auth/register`，无需鉴权，受开关保护，含格式/等保/重名校验与 Redis 限流。
3.1 **账号名可用性预检**：`GET /api/portal/auth/register/available`，注册表单**边输边查**，无需等到提交才发现重名。
   为对冲「用户名枚举」风险设三重限制：仅当注册开关开启时才存在（关闭返回 403）；按 IP 独立限流且阈值更严
   （60 次/小时）；只回 `available / reason / message`，不透露占用者的 id、姓名、状态。命中限流或网络异常时
   前端**静默降级**为「提交时校验」，不阻断注册。与注册提交共用 `AuthService.is_user_name_taken`，
   确保预检结论与提交结论口径完全一致。
3.2 **来源地址可信度判定**（`AuthService.resolve_rate_limit_source`）：只在能确信地址标识真实来源时才按来源限流。
   详见 4.5 —— 这是「限流不会静默变成平台级封禁」的关键。
3.3 **待审核积压上限**（`AuthService.REGISTER_PENDING_LIMIT = 500`）：与来源、请求头、部署拓扑**都无关**的兜底。
   触顶返回 429 并打 WARNING 日志。它防的不是「某个来源刷量」（那是账号名 + 来源两层的事），
   而是「不管谁在刷，待审核队列无限膨胀」——受伤的是管理员的审核负担与库表体积，不会误伤具体用户。
4. **待审核状态**：复用 `User.status`，新增 `2 = 待审核`；注册创建的账号 `status = 2`。
5. **登录拦截**：`status = 2` 的账号在密码登录与 API Key / 会话两条路径上都无法登录，并给出「审核中」的明确提示。
6. **审核能力**：用户管理页新增「待审核」页签与数量徽章；审核操作复用 `PATCH /users/{id}/status`，并支持**可选**地在通过时分配业务角色。
7. **待审核计数接口**：`GET /users/pending-count`，与 3.3 的积压上限共用同一计数口径。
8. **移动端适配**：登录 / 注册面板可滚动（`min-h-0 overflow-y-auto`）、视口高度用 `100dvh`、移动端从顶部排布、
   输入框移动端 `text-base`（防 iOS 聚焦自动放大）与 `py-3`（触摸目标 ≥44px）。
9. **测试**：前后端契约测试 + 迁移契约测试 + 前端类型检查。

### 限流的三层结构

| 层 | Key / 依据 | 阈值 | 是否依赖来源判定 | 退化时的表现 |
|---|---|---|---|---|
| 账号名 | `auth:register:name:<小写名>` | 3/小时 | 否 | —— 拓扑无关，始终生效，是**主力** |
| 来源地址 | `auth:register:ip:<地址>` | 5/小时 | **是** | 判定失效时**整层跳过**（不会误伤） |
| 待审核积压 | `COUNT(status=2)` | 500 | 否 | —— 拓扑无关，是**兜底** |
| 预检 | `auth:register:check:ip:<地址>` | 60/小时 | 否（刻意） | 退化表现为「边输边查停用」，前端静默降级，用户不受阻 |

判定顺序：**账号名 → 来源地址**。账号名命中即返回，所以同一账号名反复重试只吃账号名名额，
不再叠加消耗来源额度（旧实现先判来源，会出现「重试 3 次同名后换个名字只剩 1 次机会」）。

限流的窗口、恢复与失败语义：

- **固定窗口**，从本窗口第一次尝试起算（`INCR` 后仅当 `count == 1` 时 `EXPIRE`）。命中后重试会继续自增但**不刷新 TTL**，
  因此不会自我延长，最坏等满 1 小时。提示语统一说「1 小时」是保守表述，未返回 `Retry-After`。
- **一切尝试都计数**，包括后续会因格式 / 等保 / 重名而失败的请求（限流刻意排在业务校验之前，否则可用非法请求绕开）。
  已确认的取舍：填错几次就进冷却。`test_register_counts_validation_failures_against_the_quota` 钉住该行为，
  若将来改为「只有走到建号才计数」，用例会失败并提醒同步改文档与提示语。
- **Redis 不可用即 fail-open**（`_register_counter_hit` 捕获异常后放行），与登录失败限流同一取舍。


### 不做（已决策）

| 项 | 理由 |
|---|---|
| 新增 `approval_status` 独立字段 | 复用 `status` 改动面最小；`verify_api_key` 本就硬性要求 `status == 1`，待审核账号天然拿不到会话，无需额外字段 |
| `ALTER TABLE` 改 `status` 列注释 | 用户明确要求不做 DDL；语义写在模型注释与后端常量里 |
| 审核人 / 审核时间 / 审批意见落库 | 需求未要求；`system_config_history` 是配置审计，套用到用户审核是过度设计。审核动作只更新 `updated_at` |
| 图形验证码 | 已决策：仅做 Redis 频率限制 |
| 待审核 / 审核结果通知 | 已决策：不接通知链路，只做用户管理页的数量徽章 |
| 给待审核账号发「审核通过」邮件 / 短信 | 同上 |
| 注册表单选择角色 | 角色在审核通过时由管理员决定；注册阶段不暴露角色概念 |
| 审核记录（谁审的、审了几次）的独立流水表 | 需求未要求；`updated_at` + 管理页现有行为足够 |

## 3. 状态模型

### 3.1 `User.status` 语义

| 值 | 含义 | 登录 | 说明 |
|---|---|---|---|
| `0` | 禁用 | ❌ 403「账户已被禁用」 | 含**审核被拒**的永久禁用 |
| `1` | 启用 | ✅ | 审核通过的终态 |
| `2` | **待审核（新增）** | ❌ 403「账号正在审核中」 | 注册后的初始态 |

常量落在 `app/models/user.py`：

```python
USER_STATUS_DISABLED = 0
USER_STATUS_ENABLED = 1
USER_STATUS_PENDING_REVIEW = 2
```

`app/models/user.py:23` 的列注释同步改为 `# 0=disabled, 1=enabled, 2=pending_review`（**仅代码注释，不动数据库**）。

### 3.2 状态流转

```
                 ┌──────────────┐
  注册申请 ──────►│ 2 待审核     │
                 └──┬────────┬──┘
                    │        │
        审核「通过」│        │ 审核「禁用」
                    ▼        ▼
            ┌──────────┐  ┌──────────┐
            │ 1 启用   │  │ 0 禁用   │  ← 终态，不可再申请同名账号
            └──────────┘  └──────────┘
```

- 「启用 → 禁用」「禁用 → 启用」沿用现有用户管理能力，不受本次改动限制。
- **账号名唯一性对所有状态生效**：`status ∈ {1, 0, 2}` 的任意账号占用账号名后，注册同名一律拒绝。

## 4. 后端设计

### 4.1 迁移

**MySQL `db-prod/V162-add-user-self-registration-config.sql`**

```sql
-- V162: 账号自主注册申请开关（默认关闭）
INSERT IGNORE INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
(
  'user_registration_enabled',
  'false',
  '账号自主注册申请开关（默认关闭）。开启后登录页展示「申请账号」入口，申请人可自助提交注册申请；申请提交后账号为待审核状态，须由管理员在「用户管理 › 待审核」中审核通过后方可登录。',
  'general',
  0
);
```

**PostgreSQL `db-prod-pg/V63-add-user-self-registration-config.sql`**

```sql
-- V63: 账号自主注册申请开关（默认关闭）
INSERT INTO system_configs (key, value, description, category, is_secret) VALUES
(
  'user_registration_enabled',
  'false',
  '账号自主注册申请开关（默认关闭）。开启后登录页展示「申请账号」入口，申请人可自助提交注册申请；申请提交后账号为待审核状态，须由管理员在「用户管理 › 待审核」中审核通过后方可登录。',
  'general',
  0
)
ON CONFLICT (key) DO NOTHING;
```

两侧都**不覆盖已存在的行**（`INSERT IGNORE` / `ON CONFLICT DO NOTHING`，与既有 `V147`/`V48` 同一写法），
避免部署时把管理员已开启的开关改回 `false`。该 key 是全新配置项，无需更新历史行的 description。

### 4.2 配置校验

`app/services/config_service.py` 的 `validate_config_update` 增加分支，与既有 `sandbox_auto_warm` 同一写法：

```python
USER_REGISTRATION_ENABLED_KEY = "user_registration_enabled"

elif key == USER_REGISTRATION_ENABLED_KEY:
    normalized = str(value or "").strip().lower()
    if normalized not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
        raise ValueError("user_registration_enabled 仅允许 true/false")
```

### 4.3 公开配置

`app/api/portal/endpoints/auth.py` 的 `get_public_config`（当前 `:753`）增加：

```python
from app.services.config_service import ConfigService, USER_REGISTRATION_ENABLED_KEY

user_registration_enabled = await ConfigService.get(USER_REGISTRATION_ENABLED_KEY) == "true"
```

并在返回体的 `data` 中追加 `"user_registration_enabled": user_registration_enabled`。

### 4.4 注册接口

`POST /api/portal/auth/register`，无鉴权依赖。

- `app/api/portal/api.py` 中 `auth.router` 是**唯一没有** `dependencies=[Depends(require_api_key)]` 的路由组（`/login`、`/config/public`、`/branding` 都在里面），因此把新端点直接加进 `auth.router` 即为公开接口，**无需改路由注册或中间件**。
- 端点自身用配置开关 + 限流做保护，不引入任何宽松的默认放行。

**请求体** `RegisterRequest`：

| 字段 | 类型 | 约束 |
|---|---|---|
| `user_name` | `str` | 必填，3–32 字符，`^[A-Za-z][A-Za-z0-9._-]{2,31}$` |
| `real_name` | `str` | 必填，去首尾空白后 1–50 字符 |
| `password` | `str` | 必填，须通过 `AuthService.validate_password_complexity` |
| `remark` | `Optional[str]` | 可选，≤255 字符 |

**处理顺序**（顺序即错误优先级）：

1. **开关检查** — 配置非 `true` → `403 {"detail": "系统当前未开放账号注册，请联系管理员开通"}`。
2. **频率限制** — 见 4.5，超限 → `429`。
3. **格式校验** — 账号名正则、姓名长度、备注长度 → `400` + 具体字段原因。
4. **密码等保校验** — 直接把 `validate_password_complexity(password, username=user_name)` 返回的中文原因作为 `detail` 回传 → `400`。
5. **重名检测** — 大小写不敏感地查全状态账号：

   ```python
   stmt = select(User.id).where(func.lower(User.user_name) == user_name.lower())
   ```

   命中 → `400 {"detail": "该账号名已被占用，请更换后重试"}`。

   - **必须显式用 `lower()` 比对**：MySQL 侧 `ai_agent_users` 是 `utf8mb4_unicode_ci`（大小写不敏感），而 PostgreSQL 默认区分大小写。不做归一化会导致两个库行为不一致，PG 上会直接撞唯一索引抛 `IntegrityError`（500）而不是给出友好提示。
   - 提示语不区分对方状态（启用/禁用/待审核），避免拿注册接口探测用户名。
6. **创建账号** — `role="user"`、`status=2`、写入 `password_hash` 与 `password_updated_at`，并**同时签发 API Key**。

**关于 API Key（必须做，否则功能不成立）**：现有密码登录流程在认证成功后必须 `get_decrypted_api_key(user_id)` 才能下发会话 Cookie，取不到会直接 `500 "User has no valid API Key for session"`。管理员创建用户（`POST /users`）与第三方同步都走 `generate_api_key`，注册用户必须一致。

实现方式：给 `AuthService.generate_api_key`（当前 `:42`）增加一个默认参数 `status: int = 1`，透传给 `User(status=...)`；注册接口传 `status=2`。默认值保证所有既有调用方行为不变。

**响应**：

```json
{ "status": "success", "message": "注册申请已提交，请等待管理员审核" }
```

**不回显**任何用户 ID、账号名、API Key 等信息。

### 4.5 频率限制

复用登录失败计数那套 Redis 限流（`app/services/auth_service.py`）的写法：固定窗口计数、**fail-open**（Redis 不可用时不阻断注册，与登录限流同策略）。
三层结构、窗口与恢复语义见 2.「限流的三层结构」，此处只展开来源判定。

#### 来源地址可信度（`AuthService.resolve_rate_limit_source`）

`request.client.host` 是 **TCP 对端**，只有 uvicorn 的 `ProxyHeadersMiddleware` 信任该对端时才会被 `X-Forwarded-For`
改写成真实客户端 IP。信任范围取 `FORWARDED_ALLOW_IPS` 环境变量，**未设置时缺省 `127.0.0.1`**；
本仓库任何地方都没有配置该变量，于是 `client.host` 的含义完全取决于部署形态：

| 部署形态 | `client.host` | 按它限流的后果 |
|---|---|---|
| 无反代 | 真实客户端 | 正确 |
| 反代与应用**同机** | 真实客户端（同机对端落在默认信任范围内，uvicorn 从 XFF 解析出真实客户端；nginx 默认「追加」而非「替换」，伪造 XFF 无效） | 正确 |
| 反代在**异机 / 异 Pod**（如 k8s ingress） | **反代自己**，且它不在 XFF 链里 | **全平台共用一个计数器**，「5 次/小时」静默退化成「全平台每小时只能注册 5 个」 |

最后一种无法从单个请求直接观测，因此判定规则取「**宁可不限，也不误伤**」，两条必要条件缺一不可：

1. `client.host` 必须是**公网**地址（回环 / 私网 / 链路本地 / CGNAT / 保留段一定是基础设施，不是终端用户）。
   反代恰好没设 XFF 时，这是唯一能拦住「全局共用计数器」的一道网。
2. 若请求带 XFF，则 `client.host` 必须**出现在 XFF 链里**（说明 uvicorn 确实从 XFF 解析出了它）。
   不在链里即说明它是「不被信任的对端地址」——这一条拦住「反代有公网地址」的形态（云 LB / CDN）。

不满足时 **`client_ip = None`，整层跳过**（并打一条 INFO 日志），账号名维度与积压上限照常生效。

> **运维动作（可选，但推荐）**：在部署里设置 `FORWARDED_ALLOW_IPS` 为**反代 / ingress 所在网段**
> （例如 `10.244.0.0/16`），让来源维度真正生效。
> ⚠️ **不要设成 `*`**：uvicorn 在 always-trust 分支直接取 XFF 的**最左**值，而 nginx 默认是追加，
> 客户端自己塞一个假 IP 就在最左 —— 每个请求换一个假 IP 即可完全绕过来源限流。
> 指定网段则走「从右往左找第一个不受信任的地址」，正好取到反代追加的真实客户端 IP，伪造无效。
> 未配置时的代价：**服务部署在纯内网、用户也都是私网地址时，来源维度等于关闭**，
> 仅由「账号名 + 积压上限」两层兜底。这比误判成全平台封禁安全得多。

#### 不受信任形态下的取舍

| 维度 | 是否判可信度 | 理由 |
|---|---|---|
| 提交 · 来源 | **是** | 退化后果是「谁都注册不了」，必须判 |
| 提交 · 账号名 | 不适用 | 拓扑无关 |
| 预检 · 来源 | **否**（无条件生效） | 退化后果只是「边输边查停用」，前端静默降级、用户不受阻；而它是防用户名枚举的唯一手段，不能因拓扑不确定就关掉 |
| 待审核积压 | 不适用 | 拓扑无关 |

- 在**格式校验之前**计数并判定，防止绕过。
- 超限一律 `429`：提交侧 `{"detail": "注册申请提交过于频繁，请 1 小时后再试"}`，
  积压侧 `{"detail": "当前待审核的注册申请过多，请稍后重试或联系管理员先行处理"}` 并打 WARNING 日志。
- 注册**成功不重置**计数（固定窗口），避免「成功即清零」被用来无限注册。

### 4.6 登录拦截

`AuthService.verify_user_password`（当前 `:662`）当前的 `if user.status != 1` 分支合并了所有非启用状态，需拆开：

```python
if user.status == USER_STATUS_PENDING_REVIEW:
    return {"status": "pending_review", "message": "账号正在审核中，请等待管理员审核通过后再登录"}
if user.status != USER_STATUS_ENABLED:
    return {"status": "fail", "message": "账户已被禁用"}
```

端点侧（`app/api/portal/endpoints/auth.py` 的 `login`）在既有 `error_no_password` 分支旁增加：

```python
elif result["status"] == "pending_review":
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=result["message"])
```

**API Key 路径无需改动**：`verify_api_key` 已要求 `user.status == 1`，待审核账号的 API Key / 会话令牌一律判无效。

### 4.7 用户管理端点

**`PATCH /users/{user_id}/status`（复用，`management.py:567`）**

- `UpdateStatusRequest` 增加可选字段：

  ```python
  class UpdateStatusRequest(BaseModel):
      status: int  # 0=disabled, 1=enabled, 2=pending_review
      role_ids: Optional[List[int]] = None  # 仅 status=1 时生效：审核通过时顺带分配业务角色
  ```

- 增加取值校验（**当前代码不校验任何值，直接赋值给 `user.status`，是既有漏洞，本次一并收口**）：

  ```python
  if request.status not in (0, 1, 2):
      raise HTTPException(400, "status must be 0 (disabled), 1 (enabled) or 2 (pending_review)")
  ```

- 当 `request.status == 1` 且 `request.role_ids is not None` 时，在状态落库后调用
  `PermissionService(db).update_user_roles(user_id, request.role_ids)` 写入 `UserRoleRelation` 并失效权限缓存。
  - `role_ids = []` 表示显式清空业务角色；`role_ids` 未传（`None`）表示不触碰角色。
  - **写入前必须先校验角色存在性**：查 `Role.id.in_(role_ids)`，若存在不存在的 id 则整体拒绝并返回
    `400 {"detail": "包含不存在的业务角色，请刷新后重试"}`。否则 `UserRoleRelation` 的外键约束会抛
    `IntegrityError`，请求以 500 收场。
  - 仍由现有 `invalidate_user_auth_cache` 负责踢掉旧会话。
- 权限不变：`element:user:edit`。与既有 `POST /users`（本身即可带 `role_ids`）口径一致，**不引入新的提权面**。

**`GET /users/pending-count`（新增）**

```python
@router.get("/users/pending-count")
async def get_pending_user_count(
    admin: dict = Depends(require_permission("menu", "menu:system:users")),
    db: AsyncSession = Depends(get_db_session),
):
    total = (await db.execute(
        select(func.count()).select_from(User).where(User.status == USER_STATUS_PENDING_REVIEW)
    )).scalar()
    return {"count": total or 0}
```

- 只读计数，权限沿用列表接口的 `menu:system:users`（审核动作本身仍受 `element:user:edit` 保护）。
- 路由为单段路径，与现有 `/users`（列表）、`/users/{user_id}/permissions` 等两段路径**不冲突**，无需调整注册顺序。

**`GET /users`（无需改动）**：已支持 `status` 任意整数过滤，`?status=2` 直接可用于待审核列表。

## 5. 前端设计

### 5.1 `frontend/src/views/SystemConfig.vue`（常规设置）

1. `general` 分组排序数组（`:2468`）在 `password_expire_days` 之后插入 `'user_registration_enabled'`。
2. 布尔开关渲染分支（`:5120` 的 key 数组）加入 `'user_registration_enabled'`。
3. 描述兜底 map（`:2377` 附近）增加一行：
   `'user_registration_enabled': '账号自主注册申请开关（默认关闭）。开启后登录页展示「申请账号」入口；注册申请提交后为待审核状态，须管理员在用户管理页审核通过后才能登录。'`

### 5.2 `frontend/src/views/Login.vue`

**开关读取**：`fetchPublicConfig`（`:231`）读取 `response.data.data.user_registration_enabled`，落到新 ref `registrationEnabled`。

**入口**：在密码登录表单的提交按钮下方增加一行低调文字链接，仅当 `registrationEnabled && !isTwoFactorStep && activeTab === 'password'` 时显示：

```
还没有账号？申请账号
```

**注册视图**：同一右侧面板内切换（与 2FA 步骤同构，**不新增路由、不做弹窗**），`activeTab` 之外新增 `isRegisterStep`：

| 字段 | 控件 | 前端校验 |
|---|---|---|
| 账号名 | text | 必填，3–32 位，字母开头，仅字母/数字/`_`/`-`/`.` |
| 用户姓名 | text | 必填，≤50 字 |
| 密码 | password | 必填，实时展示等保提示 |
| 确认密码 | password | 必须与密码一致 |
| 备注 | textarea（可选） | ≤255 字，带字数计数 |

- 密码框下方常驻灰色提示：`8–32 位，须包含大写字母、小写字母、数字、特殊符号中的至少 3 种，且不能包含账号名`。
- 前端校验**只做体验**，判定权完全在后端。
- 提交成功 → 面板内切换为成功态：「注册申请已提交，请等待管理员审核」，附「返回登录」按钮。
- 提交失败 → 顶部红色错误条沿用现有样式；但**账号名重名**（后端 `400` 且 `detail` 含「已被占用」）例外，改为渲染在**账号名输入框正下方的字段级红色提示**，让用户一眼知道改哪个字段。

### 5.3 `frontend/src/views/Users.vue`（待审核页签）

**页签与徽章**：标题区下方增加 segmented 切换：

```
[ 用户列表 ]  [ 待审核 (3) ]
```

- 徽章数据来自 `GET /api/portal/management/users/pending-count`，在页面加载时拉取一次。
- `N > 0` 时徽章用琥珀色实心小圆点样式；`N = 0` 时不渲染徽章（不显示「(0)」）。

**待审核列表**：使用**独立的轻量卡片列表**，不复用主表格。

理由：主表格的复选框批量操作、API Key 查看、角色编辑、2FA 徽标、更多菜单对「待审核」全是噪音；且 `Users.vue` 已 3000+ 行，继续加条件分支会让它更难维护。待审核用户唯一可做的动作是「通过 / 禁用」。

- 数据源：`GET /api/portal/management/users?status=2&page=&size=`（复用现有列表接口）。
- 每张卡片展示：账号名、用户姓名、备注（为空显示「—」）、申请时间、`#id`。
- 操作按钮：
  - **`通过并启用`** — 打开轻量确认框：
    - 上方只读信息（账号名 / 姓名）
    - 下方「业务角色」多选，数据复用页面已有的 `fetchBusinessRoles()`（`GET /api/portal/roles`），**可以全不选**
    - 确认后调用 `PATCH /users/{id}/status` 带 `{ status: 1, role_ids: [...] }`
    - **权限降级**：`GET /api/portal/roles` 要求 `menu:system:roles`，而审核页签只要 `element:user:edit`。
      只持有用户管理权限的管理员加载不到角色列表（`Users.vue` 在 `onMounted` 已无条件调用该接口，
      403 被 catch 后 `businessRoles` 保持为空）。此时确认框降级为**纯「通过并启用」**，隐藏角色多选
      并提示「无可分配的业务角色（需具备角色管理权限）」，不阻塞审核动作。
      这与现有「创建用户」弹窗的行为完全一致，本次不为此新增端点。
  - **`禁用`** — 红色次要按钮，二次确认后调用 `PATCH /users/{id}/status` 带 `{ status: 0 }`；确认文案需明确「禁用后账号名将永久占用，该用户无法再次申请」。
- 操作成功后同时刷新待审核列表与徽章计数；列表为空时展示空态（「暂无待审核申请」）。
- 权限：无 `element:user:edit` 时只读（隐藏两个操作按钮），与主列表口径一致。

**主列表筛选**：状态筛选下拉（`:48`）增加 `<option value="2">待审核</option>`，与页签信息保持一致。

## 6. 数据流

```
① 管理员在系统配置页开启 user_registration_enabled
        └─► ConfigService.update_config_value → DB + Redis（TTL 300s）

② 申请人打开登录页
        └─► GET /api/portal/auth/config/public
              └─► user_registration_enabled = true → 渲染「申请账号」入口

③ 申请人提交表单
        └─► POST /api/portal/auth/register
              ├─ 开关检查（ConfigService.get）
              ├─ Redis 限流（IP / 账号名）
              ├─ 格式 → 等保 → 重名
              └─ 创建 User(role='user', status=2) + 签发 API Key
                    └─► 「注册申请已提交，请等待管理员审核」

④ 待审核账号尝试登录
        └─► POST /api/portal/auth/login
              └─► verify_user_password → status=2 → 403「账号正在审核中」

⑤ 管理员进入用户管理页
        └─► GET /users/pending-count → 徽章显示 N
              └─► 「待审核」页签 → GET /users?status=2
                    ├─ 通过：PATCH /users/{id}/status {status:1, role_ids:[...]}
                    │         └─► 更新 status + update_user_roles + 清认证/权限缓存
                    └─ 禁用：PATCH /users/{id}/status {status:0}
                              └─► 更新 status + 清认证/权限缓存

⑥ 审核通过后申请人登录
        └─► verify_user_password → status=1 → 正常下发会话
```

## 7. 错误处理与边界

| 场景 | 行为 |
|---|---|
| 注册开关关闭时直接调接口 | `403`，前端登录页本就不渲染入口 |
| Redis 不可用 | 限流 fail-open，注册照常，仅打 warning 日志 |
| 两名用户并发抢同一账号名 | 依赖 DB 唯一索引兜底；`IntegrityError` 需被捕获并转成 `400「该账号名已被占用，请更换后重试」`，不能漏成 500 |
| 密码含账号名 | 由 `validate_password_complexity` 拒绝（用户名长度 ≥3 时生效） |
| 密码含空格 | 同上拒绝 |
| 密码超 72 字节 | 同上拒绝（bcrypt 上限） |
| 账号名大小写不同但实际同名 | `lower()` 归一后命中重名 |
| 待审核账号持有 API Key 调业务接口 | `verify_api_key` 要求 `status == 1`，直接不认 |
| 管理员把待审核账号改成 `status=2`（回退） | 允许；已有会话由 `invalidate_user_auth_cache` 踢掉 |
| 审核通过时 `role_ids` 含不存在的角色 | 先校验角色存在性，命中则整体 `400`，避免 `UserRoleRelation` 外键抛 `IntegrityError`（500） |
| 管理员禁用自己 | 既有逻辑已拦截（`Cannot disable yourself`），不受影响 |

## 8. 测试计划

### 8.1 前端契约测试（新增）

`tests/frontend/test_user_registration_contract.py`

- 登录页存在「申请账号」入口，且渲染条件包含 `registrationEnabled`
- 注册表单包含账号名 / 用户姓名 / 密码 / 确认密码 / 备注五个字段
- 提交失败时重名错误走字段级内联提示（含「已被占用」判定逻辑）
- `SystemConfig.vue` 的 general 排序数组与布尔开关数组均包含 `user_registration_enabled`
- `Login.vue` 的公开配置读取包含 `user_registration_enabled`
- `Users.vue` 存在待审核页签与 `pending-count` 调用
- 待审核卡片只暴露「通过 / 禁用」两个操作，不出现 API Key / 批量选择

### 8.2 后端测试（新增）

`tests/test_user_registration.py`

- 开关关闭 → `403`
- 密码不合规（长度不足 / 含用户名 / 含空格）→ `400` 且 `detail` 为对应中文原因
- 账号名格式非法 → `400`
- 重名（分别覆盖已启用 / 已禁用 / 待审核三种既有状态，以及大小写变体）→ `400`
- 成功注册 → 用户 `status == 2`、`role == 'user'`、`api_key_hash` 非空
- 待审核账号密码登录 → `403`「账号正在审核中」
- 待审核账号持 API Key 调用受保护接口 → 拒绝
- 审核通过（`PATCH status=1`）后可正常登录
- 审核通过带 `role_ids` → `UserRoleRelation` 落库
- 审核禁用（`PATCH status=0`）后同名账号再次注册 → `400`
- 限流：同一 IP / 账号名超阈值 → `429`
- `PATCH status=3` → `400`

### 8.3 迁移契约测试（新增）

- `db-prod/V162-*.sql` 与 `db-prod-pg/V63-*.sql` 均存在
- 均包含 `user_registration_enabled`、`'false'`、`'general'`
- 均**不覆盖** `value`（PG 用 `ON CONFLICT ... DO UPDATE` 且 `SET` 列表不含 `value`）

### 8.4 回归

```bash
pytest --confcutdir=tests/frontend tests/frontend/
cd frontend && vue-tsc --noEmit -p tsconfig.app.json
```

按仓库规范，**不执行** `./dev.sh` 或任何部署 / 数据库脚本。

## 9. 影响面清单

| 文件 | 改动 |
|---|---|
| `db-prod/V162-add-user-self-registration-config.sql` | 新增 |
| `db-prod-pg/V63-add-user-self-registration-config.sql` | 新增 |
| `app/models/user.py` | 新增三个状态常量；更新 `status` 列注释 |
| `app/services/config_service.py` | 新增 `USER_REGISTRATION_ENABLED_KEY` 常量与 `validate_config_update` 分支 |
| `app/services/auth_service.py` | `generate_api_key` 增加 `status` 参数；新增注册限流辅助；`verify_user_password` 拆分待审核分支 |
| `app/api/portal/endpoints/auth.py` | 新增 `POST /register`；`get_public_config` 增加开关字段；`login` 增加 `pending_review` 分支 |
| `app/api/portal/endpoints/management.py` | `UpdateStatusRequest` 增加 `role_ids` 与取值校验；新增 `GET /users/pending-count` |
| `frontend/src/views/Login.vue` | 公开配置读取、申请入口、注册视图 |
| `frontend/src/views/SystemConfig.vue` | 常规设置新增开关（排序 + 渲染分支 + 描述兜底） |
| `frontend/src/views/Users.vue` | 待审核页签 + 徽章 + 待审核卡片列表 + 审核确认框 + 状态筛选新增选项 |
| `tests/frontend/test_user_registration_contract.py` | 新增 |
| `tests/test_user_registration.py` | 新增 |
| `tests/CHECKLIST.md` | 追加记录 |

## 10. 未决与已知边界

- **业务角色默认值**：审核通过时若不选角色，账号为「已启用但无任何业务角色」，登录后权限基本为空。这是与现有「创建用户」一致的行为，符合本次决策。
- **审核的不可逆性**：审核为「禁用」后为永久禁用，账号名不释放。若管理员误操作，只能手工把 `status` 改回 `1`（用户管理页的启用开关仍可用）。
- **无审核审计**：不记录审核人、审核时间与审批意见，只有 `updated_at`。若后续需要合规审计，需另立设计（新增审核流水表）。
- **多实例部署下的限流**：依赖共享 Redis，与登录限流同前提。
- **来源维度可能整体不生效**（见 4.5）：服务部署在纯内网、用户也都是私网地址，或反代在异机 / 异 Pod 而
  `FORWARDED_ALLOW_IPS` 未配置时，`resolve_rate_limit_source` 会返回 `None`，来源维度被跳过。
  这是刻意的 fail-safe：宁可少一层防护，也不能让全平台共用一个计数器而静默封禁注册。
  此时实际生效的是「账号名 3 次/小时」+「待审核积压 500 条」+「预检 60 次/小时」。
  **若发现来源维度长期未生效**（日志里出现「注册来源地址不可信，跳过按来源限流」），按 4.5 的运维动作配置网段。
- **校验失败的尝试也消耗额度**：限流排在业务校验之前（防绕过），因此密码拼错、姓名留空都会计入。
  用户视角是「填错几次就进 1 小时冷却」；这是已确认的取舍，有用例钉住以便将来改动时同步提示语。
- **积压上限是全局的**：`REGISTER_PENDING_LIMIT = 500` 打到就停掉所有人的新申请，直到管理员处理。
  这是兜底而非业务约束，正常运营下不应触及；若长期触顶，说明需要的是审核人力或关闭注册，而不是调大阈值。
- **注册页可探测账号名是否已存在**：这是「重名要提示」这一需求的必然结果，且**用户明确要求改为输入即校验**，
  因此已知且接受。对冲措施：预检接口仅在注册开关开启时存在、按来源限流 60 次/小时、只回
  `available / reason / message` 三个字段（不透露占用者身份）。若后续需要更强防护，方向是加图形验证码
  （已决策不做）或改回提交时才判定。
- **审核确认框的业务角色列表**：依赖调用方同时具备 `menu:system:roles`；只有用户管理权限时角色多选会被降级隐藏（见 5.3），审核本身不受影响。
- **路由注册无需改动**：新端点在 `auth.router` 内天然公开（见 4.4），新计数端点在 `management.router` 内天然受 `require_api_key` 保护。
