# MCP 业务集成认证方案

![MCP 用户身份透传认证架构图](../../docs/images/mcp_auth.png)

## 1. 文档信息

- **适用系统**：NanZi AI Agent Platform 与自有业务 MCP 服务
- **方案版本**：v1.0
- **编写日期**：2026-09-01
- **适用范围**：平台登录用户调用由平台或业务团队自行控制的 HTTP MCP 服务
- **本期认证能力**：可选的 MCP 自身认证 Header + 可选的 NanZi 签名 UserContext
- **非目标**：本方案不实现第三方 OAuth 用户授权，不把 NanZi 长期登录 API Key 直接转发给 MCP

## 2. 背景与问题

NanZi 已经在用户登录后完成平台身份认证，并在后端运行上下文中保存当前用户信息，例如 `user_id`、租户、组织和用户名。平台调用 MCP 时，需要让业务 MCP 知道“这是哪个 NanZi 用户发起的调用”，从而完成业务系统内部的用户关联、数据权限判断和审计。

需要同时满足以下要求：

1. 复用 NanZi 已认证的用户身份，避免让用户在每个 MCP 中重复登录。
2. 不能仅通过明文 `user_id` 或普通请求头建立信任，防止调用方伪造其他用户。
3. 固定 Authorization Bearer Token 与用户身份信息分离。
4. 自有 MCP 能解析并验证用户身份；不支持该扩展的第三方 MCP 仍能正常调用。
5. 不把固定 Authorization Bearer Token 或签名 UserContext 暴露给模型、浏览器、SSE 前端事件或业务日志。
6. 为后续租户隔离和工具 scope 保留扩展点，同时具备短期有效期、重放防护、密钥轮换和审计追踪能力。

## 3. 方案结论

采用两种相互独立的认证能力：

```text
Authorization Bearer Token（按 MCP 配置，可选）
    证明：请求来自已登记的 NanZi 平台客户端

签名 UserContext
    证明：NanZi 平台认证后的当前用户是谁，内容未被篡改
```

如果 MCP 配置了 Authorization，NanZi 后端发送：

```http
Authorization: Bearer <fixed-mcp-token>
X-Nanzi-User-Assertion: <signed-user-context-jws>
X-Request-ID: <request-id>
```

业务 MCP 按配置校验 Authorization（未配置则跳过），再通过签名 UserContext 识别当前用户；之后仍然由业务 MCP 根据用户、租户和资源执行最终的业务权限判断。

## 4. 信任边界

### 4.1 NanZi 内部可信来源

当前用户身份必须来自 NanZi 服务端认证结果：

```text
HTTP Authorization / Cookie / Embed Ticket
    ↓
NanZi 认证依赖
    ↓
User / Principal
    ↓
AgentContext.user_id
```

以下数据不能覆盖或产生当前用户身份：

- 模型生成的工具参数；
- 前端 `business_context`；
- Embed 宿主消息中的 `user_id`、`role`、`tenant_id`；
- MCP 工具 arguments 中的 `user_id`；
- 未签名的 `X-User-ID`、`X-Tenant-ID` 请求头。

### 4.2 跨系统信任边界

MCP 服务无法直接访问 NanZi 的进程内 `AgentContext`。跨 HTTP 边界后，普通 `user_id` 只是一个可篡改的字符串。因此 NanZi 必须把认证结果转换为 MCP 可以独立验证的签名断言。

```text
AgentContext.user_id
    ↓
NanZi 使用私钥签名
    ↓
MCP 使用公钥验签
    ↓
MCP 创建自己的 McpPrincipal
```

### 4.3 凭证职责分离

| 凭证 | 用途 | 来源 | 是否传给模型 | 是否传给 MCP |
| --- | --- | --- | --- | --- |
| NanZi 登录 API Key / JWT | 登录 NanZi | 用户或宿主 | 否 | 否 |
| Authorization Bearer Token | 按配置证明调用方是 NanZi 平台 | MCP 管理配置 | 否 | 配置时发送 |
| 签名 UserContext | 证明当前业务用户身份 | NanZi 后端临时生成 | 否 | 自有 MCP 是 |
| MCP 业务 Access Token | 本方案不强制要求 | MCP 自己签发 | 否 | 后续可扩展 |

## 5. 总体架构

```text
┌──────────────────────┐
│      NanZi 前端       │
│ 用户登录 / 发起对话   │
└──────────┬───────────┘
           │ 已认证请求
           ▼
┌──────────────────────┐
│ NanZi API / Agent     │
│ 认证、权限、AgentContext│
└──────────┬───────────┘
           │ user_id + tenant + 当前工具
           ▼
┌──────────────────────┐
│ MCP Identity Service   │
│ 构造并签名 UserContext │
└──────────┬───────────┘
           │ 固定 Authorization Bearer Token + 签名断言
           ▼
┌──────────────────────┐
│ NanZi MCP Client       │
│ SSE / Streamable HTTP  │
└──────────┬───────────┘
           │ HTTPS
           ▼
┌──────────────────────┐
│ 业务 MCP 服务          │
│ 验证凭证、解析用户、授权│
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ 业务数据库 / 业务 API  │
│ 租户、资源、数据权限   │
└──────────────────────┘
```

## 6. 端到端调用流程

### 6.1 用户登录

1. 用户登录 NanZi。
2. NanZi 完成 API Key、JWT 或 Embed Ticket 校验。
3. 后端生成可信的 `UserContext`，并写入当前 `AgentContext`。
4. 前端展示的当前用户信息只能来自服务端响应。MCP 的认证配置不从用户登录上下文中继承，而是读取当前 MCP 实例自己的配置。

示例内部上下文：

```python
user_context = {
    "user_id": 123,
    "user_name": "zhangsan",
    "real_name": "张三",
    "dept_code": "sales",
    "org_path": "/集团/销售部",
    "agent_id": "agent-sales-assistant",
    "agent_version_id": "agent-version-2026-01",
}
```

### 6.2 平台权限预检

在 MCP 调用前，NanZi 先判断：

```text
当前用户是否可以使用这个 MCP Server
当前智能体版本是否绑定这个 MCP 工具
当前会话资源范围是否允许这个 MCP 工具
当前用户是否可以执行这个工具
```

权限不通过时，不生成 User Assertion，也不访问 MCP。

本层负责“能不能调用这个 MCP 工具”，不替代业务 MCP 的资源级授权。

### 6.3 构造本次调用上下文

```python
class McpCallContext:
    user_context: UserContext
    server_id: str
    server_audience: str
    tool_name: str
    request_id: str
    conversation_id: str | None
```

`McpCallContext` 是 NanZi 后端内部对象，不进入模型上下文。

### 6.4 签发短期 User Assertion

NanZi 使用签名私钥生成 JWS/JWT。每次 MCP 调用生成一个新的 `jti`，有效期建议为 60 秒，最多不超过 5 分钟。

示例 Payload：

```json
{
  "iss": "nanzi-platform",
  "aud": "mcp:5b8d9c2e-2ef5-4f4a-8d0c-1a2b3c4d5e6f",
  "sub": "nanzi:user:123",
  "user_context": {
    "user_id": "123",
    "user_name": "zhangsan",
    "real_name": "张三",
    "dept_code": "sales",
    "org_path": "/集团/销售部"
  },
  "custom_attributes": {
    "employee_level": "L3",
    "region_code": "east"
  },
  "agent_id": "agent-sales-assistant",
  "agent_version_id": "agent-version-2026-01",
  "agent_name": "销售助手",
  "request_id": "req-20260901-001",
  "jti": "assertion-uuid",
  "iat": 1788230000,
  "exp": 1788230060
}
```

### 6.4.1 Payload 字段说明

| 字段 | 类型 | 当前是否必传 | 来源 | MCP 用途 |
| --- | --- | --- | --- | --- |
| `iss` | string | 是 | NanZi 固定配置 | 识别签发方，必须是可信的 NanZi 平台 |
| `aud` | string | 是 | MCP Server 登记配置 | 限制断言只能用于指定 MCP，防止跨 MCP 转用 |
| `sub` | string | 是 | 当前认证用户的 `user_id` | 用户稳定身份，建议格式为 `nanzi:user:<id>` |
| `user_context.user_id` | string | 是 | 当前认证用户 | 便于业务 MCP 直接建立用户映射 |
| `user_context.user_name` | string | 是 | 当前认证用户 | 业务方展示或用户映射，不作为权限依据 |
| `user_context.real_name` | string | 否 | 当前认证用户 | 展示操作人名称，不作为权限依据 |
| `user_context.dept_code` | string | 否 | 当前认证用户资料 | 业务方组织维度，不代表租户 |
| `user_context.org_path` | string | 否 | 当前认证用户资料 | 业务方组织维度，不代表租户 |
| `custom_attributes` | object | 否 | 当前认证用户的服务端扩展字段 | 业务方按约定读取的可扩展 key-value，不作为 NanZi 核心权限依据 |
| `agent_id` | string | 是 | NanZi 当前执行配置 | 标识哪个智能体发起调用，用于审计和可选策略 |
| `agent_version_id` | string | 有值时 | NanZi 当前执行配置 | 标识实际生效的智能体版本 |
| `agent_name` | string | 否 | NanZi 当前执行配置 | 展示和审计，不作为权限依据 |
| `request_id` | string | 是 | NanZi 本次请求链路 | 关联 NanZi 与 MCP 两侧日志 |
| `jti` | string | 是 | NanZi 每次调用随机生成 | 断言唯一编号，可用于防重放 |
| `iat` | number | 是 | NanZi 签发时间 | 断言生效时间 |
| `exp` | number | 是 | NanZi 签发策略 | 断言过期时间，建议有效期约 60 秒 |

当前第一期不传 `tenant_id` 和 `scope`：平台尚未形成统一租户模型和 MCP scope 映射。后续具备对应能力后，可以在不改变基础调用链的前提下增加这两个扩展字段。

### 6.4.2 扩展字段规则

用户扩展 JSON 来源于 NanZi 服务端认证用户资料中的扩展字段，例如现有用户 `extra_data`。它必须放在固定的 `custom_attributes` 对象中，不得平铺到 Payload 顶层：

```json
{
  "custom_attributes": {
    "employee_level": "L3",
    "region_code": "east",
    "cost_center": "CC-1001"
  }
}
```

规则如下：

1. 只读取 NanZi 服务端保存的扩展字段，不接受前端或模型临时提交的扩展字段。
2. `custom_attributes` 只能包含 JSON object 的 key-value，不能覆盖 `sub`、`user_context`、`agent_id`、`agent_version_id`、`aud` 等保留字段。
3. 第一期开启 UserContext 后默认发送过滤敏感字段后的全部安全扩展字段，不提供扩展字段白名单配置。
4. 建议限制总大小不超过 8 KiB；超出时拒绝签发并记录告警，避免静默产生不完整的身份上下文。
5. 禁止传递密码、API Key、Session Token、Cookie、私钥、数据库凭证和完整权限树。
6. JWS 只能保证完整性和来源，不能保证内容保密。若扩展字段包含敏感信息，应改用加密 JWE 或不传递。
7. 业务 MCP 将扩展字段视为业务属性，不得仅凭扩展字段授予跨用户或跨租户权限。

建议只传业务 MCP 必需的字段。禁止放入：

- NanZi API Key；
- 密码；
- 固定 Authorization Bearer Token；
- 完整权限树；
- 数据库连接信息；
- 内部 Prompt 或会话历史。

### 6.5 调用 MCP

```http
POST /mcp HTTP/1.1
Host: crm.example.com
Content-Type: application/json
Accept: application/json, text/event-stream
Authorization: Bearer <fixed-mcp-token>（如果配置）
X-Nanzi-User-Assertion: <signed-jwt>（如果开启）
X-Request-ID: req-20260901-001

{
  "jsonrpc": "2.0",
  "id": 17,
  "method": "tools/call",
  "params": {
    "name": "query_customer",
    "arguments": {
      "customer_id": "C-10001"
    }
  }
}
```

用户身份不放到 `arguments`：

```json
{
  "customer_id": "C-10001"
}
```

而不是：

```json
{
  "customer_id": "C-10001",
  "user_id": 123
}
```

### 6.6 MCP 验证和解析

业务 MCP 按顺序执行：

1. 校验 HTTPS 或内部安全网络连接。
2. 如果配置了 Authorization Bearer Token，校验 `Authorization: Bearer` Header 中的 Token 值；未配置时跳过。
3. 如果开启了用户身份传递，读取 `X-Nanzi-User-Assertion`。
4. 使用 NanZi 公钥或 JWKS 验证签名。
5. 校验 `iss`。
6. 校验 `aud` 是否匹配当前 MCP。
7. 校验 `iat`、`exp`、`nbf`。
8. 校验 `jti` 是否已使用。
9. 校验 `sub` 与 `user_context.user_id` 一致。
10. 生成 MCP 内部 `McpPrincipal`。
11. 进入业务逻辑和资源级权限判断；业务 MCP 如有自己的租户模型，在此处按业务用户映射执行。

### 6.7 业务资源授权

MCP 验证用户身份后，还需要查询自己的权限：

```text
McpPrincipal.user_id
工具参数 customer_id
    ↓
业务权限服务
    ↓
是否允许当前用户访问该客户
```

UserContext 的 `user_id` 只回答“是谁”，不能直接回答“能访问所有什么”。

## 7. 签名算法和密钥方案

### 7.1 推荐算法

生产环境推荐非对称签名：

```text
Ed25519 / EdDSA
```

也可以使用：

```text
RS256 / ES256
```

不建议把“salt”作为签名密钥的称呼。Salt 主要用于密码哈希；本方案使用的是签名私钥、公钥或 HMAC 共享密钥。

### 7.2 按 MCP 实例隔离的密钥存储

签名配置属于 MCP 实例，不属于全局环境，也不按每个用户单独生成：

```text
平台 CRM MCP
  fixed_token_encrypted
  user_assertion_private_key_encrypted
  user_assertion_key_id
  user_assertion_issuer
  user_assertion_audience

用户自己的 GitLab MCP
  fixed_token_encrypted
  user_assertion_private_key_encrypted
  user_assertion_key_id
  user_assertion_issuer
  user_assertion_audience
```

NanZi 为每个 MCP 实例保存一套独立配置。平台 MCP 由管理员维护，用户 MCP 由所属用户维护；用户身份仍然从当前后端 `AgentContext` 动态写入断言。

私钥只能由 NanZi 服务端使用，数据库中必须加密保存；业务 MCP 只保存本 MCP 对应的公钥。禁止把签名私钥放在全局 `.env`，也禁止不同 MCP 共享同一个生产私钥。

### 7.3 JWKS

建议 NanZi 为每个 MCP 实例提供独立的公钥发现地址：

```http
GET /.well-known/nanzi/mcp/{mcp_server_id}/jwks.json
```

MCP 根据 JWT Header 中的 `kid` 选择公钥：

```json
{
  "alg": "EdDSA",
  "kid": "nanzi-key-2026-01",
  "typ": "JWT"
}
```

### 7.4 密钥轮换

第一期每个 MCP 只维护一个当前签名密钥；下面是后续增加密钥轮换能力时的兼容流程，当前不作为页面操作项：

轮换流程：

1. NanZi 为目标 MCP 实例生成新密钥对。
2. 该 MCP 的 JWKS 同时发布旧公钥和新公钥。
3. 该 MCP 配置切换到新的 `kid` 签发。
4. 等待旧 User Assertion 全部过期。
5. 业务 MCP 删除该 MCP 的旧公钥。

不允许直接替换唯一公钥后立即让旧 Token 全部失败。

## 8. 固定 Authorization Bearer Token 管理

固定 Authorization Bearer Token 用于认证 NanZi 这个客户端，不用于表达用户身份。

管理规则：

- 后台只显示“已配置 / 未配置”；
- 创建或重置后只展示一次完整 Token；
- 数据库加密保存，不保存明文；
- API 返回必须脱敏；
- 日志不能打印 Token 值；
- 连接失败信息不能回显 Token；
- 支持重置和立即失效；
- 不传给浏览器、模型或第三方前端。

## 9. 管理页面设计

当前页面已有“平台 MCP”“我的 MCP”、服务登记、鉴权请求头、工具同步和测试能力。本方案在每个 MCP Server 新增/编辑弹窗中增加独立认证配置。

### 9.1 服务基础信息

保留现有字段：

```text
服务名称
SSE / Streamable HTTP URL
作用域：平台 / 个人
启用状态
备注
```

### 9.2 认证模式

MCP 自身认证与 UserContext 是两个相互独立的 MCP 级配置：

```text
Authorization：关闭 / 开启
开启用户身份传递：关闭 / 开启
```

Authorization 开启时，页面固定显示 `Bearer` 前缀，用户只填写 Token 内容；关闭时不发送 Authorization。UserContext 开启时，在 MCP 自身认证 Header 基础上增加签名 UserContext。

### 9.3 MCP 自身认证 Header 配置

Authorization 使用独立开关管理，不与其他 Header 混在动态列表中。除 Authorization 外的 Header 仍可在“其他 Header”区域按键值配置。创建时可录入新值；编辑时服务端只返回配置状态和脱敏值 `********`，已有值需要点击“编辑”后重新填写，未编辑的值由后端保留。

该页面调整只复用现有 `auth_headers` 和 `fixed_token_encrypted` 字段，不新增数据库字段、不需要数据迁移；历史 `auth_headers` 中的 Authorization 仍兼容读取。

### 9.4 UserContext 配置

当“开启用户身份传递”开关打开时，仅显示以下 MCP 级信息：

```text
MCP Audience：系统按当前 MCP ID 自动生成，例如 mcp:<server_id>
签名 Issuer：系统固定显示 nanzi-platform
公钥获取地址（JWKS）：当前 MCP 的独立 JWKS 地址，可复制
```

Audience、Issuer 和 JWKS 地址均为只读信息，并提供复制按钮。业务方将 Audience 配置为验签时的 `aud` 期望值，将 Issuer 配置为 `iss` 期望值，并使用 JWKS 地址获取公钥。Key ID 由系统自动生成，仅出现在 JWT Header 和 JWKS 中，业务方根据 `kid` 自动选择公钥，不需要用户手工维护。

`X-Nanzi-User-Assertion` 是系统固定使用的请求 Header，不作为页面输入项；签名私钥由系统按 MCP 独立生成并加密保存，用户不需要填写。

页面在 JWKS 信息下提供“一键生成调用模拟代码”按钮。代码弹框支持 Python 和 Java 两种示例，并自动带入当前 MCP 的 Audience、Issuer 和 JWKS 地址。业务方复制代码后，放入自己的认证中间件，完成固定 Authorization Bearer Token 校验、JWKS 公钥验签、`iss`/`aud`/时间窗口/`jti` 校验，最后从 `user_context.user_id` 关联业务用户。

MCP 工具测试台的“运行测试”也属于实际 MCP 出站调用：UserContext 开启时，后端使用当前登录用户和测试台调用来源生成断言并发送。前端测试结果只展示脱敏状态，例如 `X-Nanzi-User-Assertion: ********`，同时展示 Audience、Issuer 和 Key ID；完整 JWS 不返回浏览器，避免被复制或重放。

第一期不在页面提供扩展字段白名单输入，使用平台默认透传规则；在“默认透传字段”旁提供问号说明：

```text
user_id      默认开启
user_name    默认开启
real_name    可选
dept_code    可选
org_path     可选
```

默认规则同时包括 `custom_attributes` 中的安全扩展 key-value，以及当前 `agent_id`、`agent_version_id`、`agent_name`、请求追踪和时效字段。系统自动过滤 `password`、`token`、`api_key` 等敏感字段。

当前第一期不配置和发送 `tenant_id`、`scope`；这两个字段属于后续租户模型和 MCP 权限模型的扩展。

不允许配置敏感字段：

```text
api_key
password
token
permissions
is_admin
```

### 9.5 工具权限说明

工具发布页面增加说明：

```text
平台工具权限只决定用户能否调用此工具。
业务数据权限仍由 MCP 业务系统根据已验证用户身份独立判断。
```

### 9.6 在线测试

管理员点击“测试连接”或“同步工具”时，只执行 MCP 服务探活和工具发现，沿用原有 MCP 认证 Header，不发送用户断言；这类操作不代表某个业务用户执行了业务工具。

在 MCP 工具测试台点击“运行测试”时，才属于实际的用户关联调用：

1. 先使用当前登录用户的服务端认证身份生成测试断言。
2. 发送已配置的 Authorization Bearer Token（如果有）和 User Assertion（如果开启）。
3. 显示工具结果和脱敏认证结果。
4. 不显示完整 Token、完整 JWT 或私钥。

测试结果应明确区分：

```text
固定 Authorization Bearer Token 认证失败
UserContext 签名验证失败
Audience 不匹配
工具 scope 不足
业务资源权限不足
```

### 9.7 平台级 Echo 测试 MCP

为方便管理员验证上述设计是否真正贯通，平台提供内置的 **Echo 测试 MCP**。在【MCP 管理】的【平台 MCP】页点击【创建 Echo 测试 MCP】后，系统幂等创建一个全局 MCP 服务，并自动发布无副作用的 `echo` 工具。

Echo MCP 的固定 `Authorization: Bearer` Token 和 Ed25519 私钥由系统自动生成、按 MCP 配置加密保存，用户不需要填写或复制。Echo 工具调用时会校验：

| 检查项 | 诊断结果 |
| --- | --- |
| 固定 `Authorization: Bearer` | `authorization_valid` |
| Authorization 脱敏展示 | `authorization_masked` |
| `X-Nanzi-User-Assertion` 是否收到 | `user_assertion_received` |
| JWS 是否通过签名、Issuer、Audience、有效期、主体和用户 ID 校验 | `user_assertion_valid` |
| 用户断言脱敏展示 | `user_assertion_masked` |
| 认证和解析处理步骤 | `processing_log` |
| 当前用户 | `verified_user_id`、`verified_user_context`、`custom_attributes` |
| 当前智能体 | `verified_agent_context` |
| 调用链路 | `request_context.request_id` |

Echo 只返回验签后的安全诊断，不返回原始 Bearer Token、完整 JWS、私钥或 `jti`；`authorization_masked` 和 `user_assertion_masked` 仅保留首尾片段，中间使用 `***`，`processing_log` 按顺序记录认证与解析步骤。它是平台级 MCP，所有已发布且有权限的智能体都可以沿用现有机制挂载；浏览器无法直接观察后端到 MCP 的 Header，应以 Echo 返回的诊断字段确认实际透传结果。详细操作见：[MCP Echo 测试服务使用说明](../../docs/md/mcp_echo_test_server.md)。

## 10. 后端模块边界

建议增加以下服务边界：

```text
app/services/mcp/
  ├── identity.py              UserContext / McpPrincipal 类型
  ├── assertion_service.py     签名、Claims、短期断言
  ├── key_service.py           私钥读取、JWKS、kid、轮换
  ├── auth_policy_service.py   MCP Server 认证策略和 scope
  └── audit_service.py         MCP 身份和授权审计
```

MCP Client 只负责调用：

```python
await mcp_client.call_remote_tool(
    server_id=server_id,
    tool_name=tool_name,
    arguments=arguments,
    user_context=current_user_context,
)
```

MCP Client 不应自己从前端读取用户信息，也不应自己解析宿主传入的身份字段。

实际流程由统一服务完成：

```python
call_context = mcp_auth_policy_service.build_call_context(
    principal=principal,
    server=server,
    tool=tool,
)

headers = await mcp_assertion_service.build_headers(call_context)
```

## 11. MCP 业务方实现方式

业务 MCP 应提供统一的认证中间件，不让每个工具重复解析 Header。

完整的 Python 和 Java 中间件示例见：[MCP UserContext 接入指南](../../docs/md/mcp_user_context_integration_guide.md)。

### 11.1 Python 示例

每个业务 MCP 只配置并信任自己对应的固定 Authorization Bearer Token、公钥和 Audience。不要使用一套供所有 MCP 共用的 Token 或签名密钥。

```python
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class McpPrincipal:
    user_id: str
    user_name: str | None
    real_name: str | None
    custom_attributes: dict[str, Any]
    agent_id: str
    agent_version_id: str | None
    request_id: str
    jti: str


async def require_nanzi_principal(request) -> McpPrincipal:
    fixed_token = read_bearer_token(request)
    if not constant_time_compare(fixed_token, settings.NANZI_CRM_MCP_FIXED_AUTHORIZATION_BEARER_TOKEN):
        raise HttpUnauthorized("invalid MCP client credential")

    assertion = request.headers.get("X-Nanzi-User-Assertion")
    if not assertion:
        raise HttpUnauthorized("missing NanZi user assertion")

    claims = verify_jws(
        assertion,
        jwks_url=settings.NANZI_CRM_MCP_JWKS_URL,
        issuer=settings.NANZI_CRM_MCP_ISSUER,
        audience=settings.NANZI_CRM_MCP_AUDIENCE,
    )

    if is_replayed(claims["jti"]):
        raise HttpUnauthorized("replayed user assertion")

    context = claims["user_context"]
    if claims["sub"] != f"nanzi:user:{context['user_id']}":
        raise HttpUnauthorized("user subject mismatch")

    return McpPrincipal(
        user_id=context["user_id"],
        user_name=context.get("user_name"),
        real_name=context.get("real_name"),
        custom_attributes=claims.get("custom_attributes") or {},
        agent_id=claims["agent_id"],
        agent_version_id=claims.get("agent_version_id"),
        request_id=claims["request_id"],
        jti=claims["jti"],
    )
```

### 11.2 工具使用方式

```python
async def query_customer(ctx, customer_id: str):
    principal = ctx.mcp_principal
    await crm_permission_service.check_customer_access(
        user_id=principal.user_id,
        customer_id=customer_id,
    )
    return await crm_service.query_customer(customer_id=customer_id)
```

工具从认证中间件得到的 `McpPrincipal` 读取用户，不从工具参数读取 `user_id`。当前第一期的 NanZi Payload 不包含统一的 `tenant_id` 和 `scope`，业务 MCP 如有自己的租户模型，应根据用户映射和业务权限服务自行判断。

### 11.3 错误返回

```text
401 Unauthorized：已配置的 Authorization Bearer Token 错误或 User Assertion 无效
403 Forbidden：业务资源权限不足
400 Bad Request：请求格式或 MCP 参数错误
```

## 12. 第三方 MCP 兼容策略

不支持 `X-Nanzi-User-Assertion` 的第三方 MCP 可以继续使用：

```http
Authorization: Bearer <fixed-mcp-token>
```

管理页面为每个 MCP 配置：

```text
是否发送 NanZi User Assertion：是 / 否
```

第三方 MCP 设置为“否”时，NanZi 不发送用户身份 Header，避免向不需要的外部系统泄露用户信息。

如果第三方 MCP 收到但忽略该扩展 Header，通常也不会影响 MCP 调用；但是它无法获得 NanZi 用户级身份，只能按照固定平台账号执行。

## 13. 连接池和会话隔离

静态认证模式的 MCP 客户端连接仍可按 `server_id` 复用。启用 UserContext 的调用使用 `server_id + user_id + call_id` 创建临时会话，并在本次工具调用结束后关闭，避免 SSE 传输或远端 session 复用旧的 JWS / `jti`。因此业务 MCP 不应依赖 NanZi 的 `mcp-session-id` 作为用户身份，用户身份始终以验签后的 User Assertion 为准。

并且：

- 用户 A 的 session 不能被用户 B 复用；
- session 过期时重新初始化；
- 用户退出或权限撤销时清理用户 session；
- MCP `mcp-session-id` 不能代替 User Assertion；
- 固定 Authorization Bearer Token 不能被模型或浏览器持有。

## 14. 权限模型

本方案采用四层控制：

```text
第一层：NanZi 登录认证
  当前请求是谁

第二层：NanZi MCP 工具权限
  当前用户能否使用某个 MCP 和工具

第三层：签名 UserContext
  MCP 能否确认请求代表哪个用户

第四层：业务 MCP 资源权限
  这个用户能否访问具体业务数据
```

任何一层失败都不能继续执行。

### 14.1 后续 scope 映射

建议工具发布时维护 MCP scope：

```text
MCP 工具：crm:query_customer
平台 scope：crm.customer.read
```

当前第一期不把 `scope` 放进 User Assertion。后续增加 scope 模型后，NanZi 只把当前工具所需的最小 scope 放进去，不把用户全部平台权限传过去。

## 15. 审计和安全日志

NanZi 和 MCP 双方都应记录：

```text
request_id
trace_id
server_id
tool_name
platform_user_id
  mcp_key_id
assertion_jti
result: success / denied / error
failure_reason
timestamp
```

禁止记录：

```text
固定 Authorization Bearer Token 原文
User Assertion 原文
NanZi 私钥
用户登录 API Key
密码
```

日志中可以记录：

```text
token_present=true
assertion_kid=<当前 MCP 的 key_id>
assertion_jti=...
```

## 16. 异常处理

### 16.1 固定 Authorization Bearer Token 错误

MCP 返回 401，NanZi 标记本次调用失败，并提示管理员检查 MCP 管理页面的固定 Authorization Bearer Token 配置。不能自动把用户登录 API Key 当作替代凭证。

### 16.2 User Assertion 过期

MCP 返回 401。NanZi 只允许在同一次调用链内重新签发一次短期断言并重试，禁止无限重试。

### 16.3 签名密钥轮换失败

如果 MCP 找不到 `kid` 对应公钥，应返回明确的认证错误；NanZi 可以刷新 JWKS 并重试一次。

### 16.4 第三方 MCP 不支持用户断言

如果该 MCP 的配置为“不发送用户身份”，只使用原有认证 Header（如果有），不把错误解释成用户身份认证失败。

## 17. 数据库和配置建议

在现有 `sys_mcp_servers` 基础上增加认证策略字段，建议包括：

```text
credential_mode
fixed_token_encrypted
user_assertion_enabled
user_assertion_header
user_assertion_audience
user_assertion_key_id
user_assertion_issuer
user_assertion_private_key_encrypted
scope_mapping
```

其中：

- `fixed_token_encrypted`：加密存储；
- `user_assertion_enabled`：是否发送签名 UserContext；
- `user_assertion_header`：默认 `X-Nanzi-User-Assertion`；
- `user_assertion_audience`：系统按 MCP ID 自动生成，例如 `mcp:<server_id>`；
- `user_assertion_key_id`：当前 MCP 的独立签名密钥版本；
- `user_assertion_issuer`：固定为 `nanzi-platform`；
- `user_assertion_private_key_encrypted`：当前 MCP 的签名私钥密文，只能由 NanZi 服务端解密使用；
- `scope_mapping`：后续工具到业务 scope 的映射。

数据库变更只新增对应版本迁移 SQL，不直接修改已部署数据库。

## 18. 测试和验收标准

### 18.1 NanZi 后端测试

- 已认证用户能够生成正确的 `sub` 和 `user_context.user_id`。
- User Assertion 签名内容被修改后，MCP 验签失败。
- 使用其他用户 ID 但沿用旧签名时，验签失败。
- 过期 Assertion 被拒绝。
- 错误 `aud` 被拒绝。
- 错误 `iss` 被拒绝。
- 不同 MCP 不会共用签名私钥、固定 Authorization Bearer Token 或 Audience。
- 不同用户的调用不会共享用户绑定 session。
- 固定 Authorization Bearer Token 不出现在前端响应、Prompt、SSE 事件和普通日志。
- 未开启用户断言时不发送 `X-Nanzi-User-Assertion`。

### 18.2 MCP 业务端测试

- 已配置的 Authorization Bearer Token 正确且用户断言正确时，成功获得 `McpPrincipal`。
- 已配置的 Authorization Bearer Token 错误返回 401。
- User Assertion 缺失返回 401。
- 签名错误返回 401。
- Audience 错误返回 401。
- 资源级权限不足返回 403。
- 相同 `jti` 重复使用时，第二次被拒绝。
- （后续密钥轮换版本）JWT 密钥轮换期间旧、新 `kid` 均按预期工作。

### 18.3 第三方兼容测试

- 不解析扩展 Header 的 MCP 仍能使用原有认证方式完成调用；如果未配置 Authorization，也可以按业务自身方式处理请求。
- 配置为不发送用户断言后，请求不带 `X-Nanzi-User-Assertion`。
- 第三方错误不会导致 NanZi 把用户身份信息写入前端错误消息。

## 19. 分阶段落地计划

### 阶段一：内部协议闭环

1. 定义 `UserContext`、`McpCallContext`、`McpPrincipal`。
2. 增加 Ed25519 私钥签发和 JWKS 公钥发布。
3. 增加 MCP Server 认证策略字段。
4. 在 MCP Client 调用边界生成 User Assertion。
5. 在一个自有业务 MCP 中实现验证中间件。
6. 完成查询类只读工具联调。

### 阶段二：管理页面和权限配置

1. 增加认证模式选择。
2. 增加固定 Authorization Bearer Token 安全配置和重置。
3. 增加系统生成的 Audience、固定 Issuer、JWKS 地址和默认透传字段说明。
4. 增加测试连接和身份认证结果展示。
5. 增加工具 scope 映射和审计查询。

### 阶段三：高风险工具和会话治理

1. 增加 jti 防重放缓存。
2. 增加写操作二次授权或确认策略。
3. 完善用户 session 隔离和清理。
4. 增加密钥自动轮换。
5. 评估 OAuth 用户授权和 Token Exchange。

## 20. 明确不采用的做法

以下做法不作为正式方案：

```http
X-User-ID: 123
X-Role: admin
X-Tenant-ID: tenant-001
```

也不采用：

- 把 `user_id` 放进 MCP 工具 arguments 作为身份依据；
- 把 NanZi 登录 API Key 直接传给 MCP；
- 把固定 Authorization Bearer Token 放入 Prompt 或 `injected_context`；
- 把完整权限树放入用户断言；
- 使用一个所有系统共享的长期 HMAC 密钥作为生产方案；
- 仅验证“Token 正确”而不验证 `aud` 和业务资源权限；
- 只按 `server_id` 复用明确绑定用户的 MCP session。

## 21. 最终原则

```text
NanZi 内部：
  使用已认证的 AgentContext.user_id

跨 MCP 边界：
  使用固定 Authorization Bearer Token 证明平台客户端身份
  使用签名 UserContext 证明当前业务用户身份

MCP 内部：
  验签并解析 McpPrincipal
  再执行业务系统自己的用户、组织和资源级权限
```

一句话总结：

> `user_id` 可以继续作为 NanZi 内部可信身份使用，但跨到业务 MCP 时，必须由 NanZi 后端把它封装进短期签名 UserContext；MCP 验证固定客户端 Token 和 UserContext 后，才能把用户身份安全地关联到自己的业务逻辑。
