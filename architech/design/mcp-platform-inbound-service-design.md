# NanZi 平台级 MCP 对外服务技术方案

> 文档状态：方案设计与第一期实现
>
> 适用版本：NanZi AI Agent Platform
>
> 更新时间：2026-09-02

## 1. 方案结论

NanZi 对外提供一个统一的 **NanZi Platform MCP**。它是一个平台级 MCP 服务入口，后续所有由 NanZi 对外提供的能力都以 MCP 工具方法的形式挂载在这个入口下。

本方案已确定的授权架构为：

```text
NanZi 本期自己提供 OAuth2 Authorization Server，预留 OIDC 扩展位置
NanZi 自己提供 Platform MCP Resource Server
用户授权页面复用当前 NanZi 登录会话
```

Platform MCP 的开关治理采用三层模型，三层全部启用：

```text
Platform MCP 总开关
    ↓
能力组开关（agent / conversation / knowledge / metadata）
    ↓
外部 Client 独立开关（CRM / OA / 工单系统）
```

第一期提供智能体、知识库和元数据三类方法；当前代码已将以下方法全部注册并接入现有权限和执行链：

```text
NanZi Platform MCP
├── agent_list_allowed
├── agent_invoke
├── conversation_continue
├── knowledge_search
├── metadata_list_datasets
├── metadata_search
├── metadata_get_dataset
├── metadata_get_schema
└── metadata_get_metrics
```

后续可以继续追加：

```text
task.get
report.get
notification.send
```

这些方法不是多个完全独立的 MCP Server，而是同一个平台级 MCP 的能力分组。它们共用：

- 一个 MCP 服务入口；
- 一个 OAuth2 授权中心（未来可扩展完整 OIDC）；
- 一个逻辑资源标识：`mcp:nanzi-platform`，实际 OAuth `aud` 使用 Platform MCP 的规范资源 URI；
- 一套客户端注册、用户授权、Token、撤销和审计体系；
- 统一的 `McpPrincipal` 调用上下文；
- 统一的客户端级、用户级、Scope 级和资源级鉴权链路。

只有在未来出现独立部署、独立安全域、独立团队维护或不同生命周期等明确需求时，才拆分为独立 MCP Server。

### 1.1 当前代码落地边界

当前代码已落地一个可运行、可继续扩展的统一能力入口：

| 已落地内容 | 当前行为 |
|---|---|
| OAuth2 发现与授权端点 | 提供 OAuth2 Authorization Code + PKCE、Refresh Token、Revoke 和 RFC 发现端点；本期不发布 OIDC Discovery、UserInfo、ID Token 或 JWKS，用户授权复用当前 `admin_token` 登录会话 |
| Access Token | 使用高熵 opaque Bearer Token，数据库只保存 SHA-256 摘要并支持过期、撤销和 Client 停用；后续可在不改变 MCP 调用协议的情况下切换为独立 JWT + JWKS |
| Platform MCP 入站入口 | `POST /mcp/platform`，通过 FastMCP Resource Server 校验 OAuth Bearer Token |
| 已注册方法 | 9 个方法均已注册并可按总开关、能力组开关和 OAuth Client Scope 发布；默认配置仍为关闭 |
| MCP 服务台 | 独立菜单 `/dashboard/mcp-service` 和 `/api/portal/mcp-service`，支持使用指南、总开关、能力组开关、Client 创建/编辑/停用/软删除/Secret 重置、Scope、智能体/知识库/元数据数据集三类资源白名单、方法查看、为当前登录用户生成个人 Access Token，以及入站调用审计查询；资源白名单按 Client 独立配置，运行时仍受当前用户权限约束；使用指南可复制通用 `mcpServers` JSON；用户授权关系与授权事件审计仍为后续切片 |
| 数据库 | MySQL `V137`、PostgreSQL `V38` 增加入站 OAuth Client、授权、Token、审计表及菜单/元素权限；MySQL `V138`、PostgreSQL `V39` 增加当前用户 Token 签发功能权限；Client 资源白名单复用已有 `allowed_agent_ids`、`allowed_knowledge_base_ids`、`allowed_metadata_dataset_ids` 三个 JSON 字段，不新增迁移；必须按迁移执行，代码不会自动改库 |

因此，服务台中 9 个方法的状态会根据实现状态和能力组开关显示为“已启用/已关闭”；总开关或对应能力组关闭时，方法不会出现在 MCP `tools/list`，直接调用也会被拒绝。当前 Platform MCP 默认关闭，完成迁移后由拥有服务台配置权限的人员按“总开关 → 能力组 → Client”顺序开启。

## 2. 背景和目标

NanZi 当前是智能体编排与执行平台，同时已经具备以下基础能力：

- 用户登录和用户 API Key 认证；
- 用户、角色和菜单/元素权限；
- 智能体管理、版本管理和用户可用智能体过滤；
- 智能体对话和会话执行链路；
- 数据集、表、字段、指标和元数据管理；
- 元数据资源权限；
- MCP Client，可以调用外部业务 MCP；
- Echo MCP，可以测试 MCP 连接和用户身份透传；
- AgentScope 运行时、ChatBI、知识库和审计能力。

本方案的目标是让 CRM、OA、工单系统、企业门户、自动化平台等外部系统能够通过标准 MCP 协议使用 NanZi 的平台能力。

目标调用形态：

```text
外部系统 / MCP Client
        │
        │ OAuth2 授权后的 Bearer Access Token
        ▼
NanZi Platform MCP
        │
        ├── agent.*
        ├── conversation.*
        ├── knowledge.*
        ├── metadata.*
        └── 后续平台级方法
```

### 2.1 核心目标

| 目标 | 说明 |
|---|---|
| 标准授权 | 使用 OAuth2 标准授权，不把 NanZi 用户 API Key 直接交给外部系统；完整 OIDC 作为后续扩展 |
| 用户关联 | 用户授权场景下，MCP 调用可以关联到具体 NanZi 用户 |
| 系统关联 | 可以识别是哪个外部系统在调用 |
| 细粒度授权 | 控制客户端能调用哪些方法、哪些智能体和哪些元数据资源 |
| 会话隔离 | 用户只能继续自己的会话，不能通过参数切换到其他用户 |
| 能力扩展 | 后续新增平台能力时只增加 MCP 方法和对应 Scope |
| 可审计 | 记录客户端、用户、方法、智能体、资源、结果和耗时 |
| MCP 兼容 | 遵循 HTTP MCP 授权发现、Bearer Token 和资源受众校验要求 |

### 2.2 非目标

第一期不包含：

- 直接复用当前 NanZi 用户 API Key 作为对外 MCP Token；
- 自定义、不兼容 OAuth2 的 Token Exchange 协议；
- 把用户管理、角色管理、MCP 管理直接暴露为普通 MCP 工具；
- 通过 MCP 执行任意 SQL；
- 通过 Metadata 方法组返回真实业务数据；
- 让外部系统传入明文 `user_id` 后由 NanZi 直接信任；
- 允许外部系统通过工具参数绕过 NanZi 现有用户和资源权限。
- 第一期开启浏览器、移动端或桌面端等无法安全保存密钥的 Public Client 直接接入；这类接入作为后续 PKCE-only 能力扩展。

## 3. 关键概念

### 3.1 OAuth2 与 OIDC 的职责

| 协议 | 在本方案中的职责 |
|---|---|
| OAuth2 | 授权外部系统访问 NanZi Platform MCP，以及限制 Scope |
| OIDC | 完整 OIDC Provider 能力的后续扩展；第一期不发布 OIDC Discovery、ID Token、Nonce、JWKS 或 UserInfo |
| OAuth2 Access Token | MCP 每次请求使用的 Bearer 访问凭证 |
| Authorization Code + PKCE | 外部系统代表具体用户调用时使用 |

第一期 Client 类型决策：

- **第一期只实现 `Confidential Client`**，面向 CRM、OA、工单系统等有后端能力、可以安全保存 `client_secret` 的外部系统。
- 所有调用都使用 Authorization Code + PKCE 建立用户授权关系；Refresh Token 只用于续期同一用户的授权。
- `Public Client` 不属于第一期验收范围。后续如果支持浏览器、移动端、桌面端或其他无法安全保存 Secret 的客户端，只允许使用 Authorization Code + PKCE，不生成也不依赖 `client_secret`。

外部系统身份和用户身份必须分开理解，但 Platform MCP 的每次业务调用都必须绑定用户：

```text
client_id + client_secret
    → 证明哪个外部系统

用户在 NanZi 的登录身份
    → 证明哪个用户

MCP Access Token
    → 把外部系统、用户、Audience、Scope 和会话绑定起来
```

### 3.2 用户授权模式

外部系统代表具体用户调用 NanZi。

```text
张三登录 CRM
    ↓
CRM 跳转 NanZi 授权页面
    ↓
NanZi 识别张三并取得授权
    ↓
CRM 使用授权码换取 Access Token
    ↓
CRM 调用 NanZi Platform MCP
```

Access Token 关联：

```text
client_id = crm-system
user_id = 123
scope = agent:invoke metadata:read
```

适用场景：

- 用户在 CRM 页面点击调用 NanZi 智能体；
- 需要使用用户自己的智能体权限；
- 需要关联用户自己的会话和数据资源；
- 需要按照用户权限过滤元数据。

## 4. 总体技术架构

```mermaid
flowchart LR
    U[外部系统用户] --> C[CRM / OA / Portal]
    C -->|Authorization Code + PKCE| AS[NanZi OAuth2 Authorization Server]
    AS -->|用户授权后的 Access Token| C
    C -->|Bearer Access Token| PM[NanZi Platform MCP]

    PM --> AUTH[OAuth2 Resource Server 验证]
    AUTH --> PRINCIPAL[McpPrincipal]
    PRINCIPAL --> SCOPE[Scope / Client Policy]
    SCOPE --> USERPERM[用户权限与资源范围]
    USERPERM --> AGENT[Agent Service]
    USERPERM --> META[Metadata Service]
    AGENT --> RUNTIME[AgentScope / ChatBI / Knowledge Runtime]
    RUNTIME --> OUT[MCP Client / 业务 MCP]
    OUT --> AUDIT[审计与 Trace]
    META --> AUDIT
    AGENT --> AUDIT
```

### 4.1 组件职责

| 组件 | 职责 |
|---|---|
| OAuth2 Authorization Server | 用户登录、授权确认、Client 注册、Authorization Code、Access Token 和 Refresh Token；未来可扩展 OIDC |
| MCP Protected Resource | 暴露 Platform MCP，返回授权发现信息和 `401/403` |
| MCP Resource Server | 当前通过 Token 摘要、Issuer/Audience、Scope、过期、撤销和 Client 状态验证 Access Token；未来切换 JWT 时再增加签名/JWKS 校验 |
| MCP Client Registry | 保存外部系统注册信息、Redirect URI、Client 类型和允许 Scope |
| Authorization Grant Service | 保存用户对外部系统的授权关系 |
| Agent MCP Tool Service | 暴露智能体发现、调用和会话继续方法 |
| Platform Metadata Tool Service | 暴露数据集、表、字段、指标和关联关系元数据方法 |
| Permission Service | 复用 NanZi 用户、角色、智能体、知识库和元数据权限 |
| Principal Context | 在一次 MCP 请求中保存可信客户端、用户和 Scope 信息 |
| Audit Service | 记录授权、Token、工具调用、资源访问和错误 |

## 5. MCP 服务入口与授权发现

### 5.1 服务入口

统一入口建议为：

```text
https://{nanzi-host}/mcp/platform
```

平台级 MCP 的工具名称使用命名空间，避免未来扩展时发生冲突：

```text
agent.list_allowed
agent.invoke
conversation.continue
knowledge.search
metadata.list_datasets
metadata.search
metadata.get_dataset
metadata.get_schema
metadata.get_metrics
```

### 5.2 Protected Resource Metadata

HTTP MCP Server 作为 OAuth2 Resource Server，需要提供受保护资源元数据，使 MCP Client 能够发现授权服务器。

建议提供：

```http
GET /.well-known/oauth-protected-resource/mcp/platform
```

返回示例：

```json
{
  "resource": "https://nanzi.example.com/mcp/platform",
  "authorization_servers": [
    "https://nanzi.example.com"
  ],
  "scopes_supported": [
    "agent:list",
    "agent:invoke",
    "conversation:continue",
    "knowledge:search",
    "metadata:search",
    "metadata:read",
    "metadata:metrics:read"
  ],
  "bearer_methods_supported": [
    "header"
  ]
}
```

NanZi 当前同时提供上述 RFC 9728 规范地址和 `/.well-known/oauth-protected-resource` 短路径兼容别名；网关必须将两者统一转发到同一份配置。

### 5.3 Authorization Server Metadata

建议提供：

```http
GET /.well-known/oauth-authorization-server
```

返回示例：

```json
{
  "issuer": "https://nanzi.example.com",
  "authorization_endpoint": "https://nanzi.example.com/oauth/authorize",
  "token_endpoint": "https://nanzi.example.com/oauth/token",
  "revocation_endpoint": "https://nanzi.example.com/oauth/revoke",
  "response_types_supported": ["code"],
  "grant_types_supported": [
    "authorization_code",
    "refresh_token"
  ],
  "code_challenge_methods_supported": ["S256"],
  "scopes_supported": [
    "agent:list",
    "agent:invoke",
    "conversation:continue",
    "knowledge:search",
    "metadata:search",
    "metadata:read",
    "metadata:metrics:read"
  ],
  "token_endpoint_auth_methods_supported": [
    "client_secret_basic",
    "client_secret_post"
  ]
}
```

### 5.4 OIDC 扩展边界

本期不发布 `/.well-known/openid-configuration`、`/oauth/userinfo`、ID Token 或 JWKS，因此外部系统应按 OAuth2 Resource Owner 授权流程接入，不要把当前 Access Token 当作 OIDC ID Token。未来如果需要完整 OIDC Provider 能力，再单独增加 `openid` Scope、ID Token 签发、Nonce、JWKS 和 UserInfo，并保持 Platform MCP 资源端点不变。

## 6. OAuth2 授权流程（OIDC 为后续扩展）

### 6.1 外部系统注册

拥有 `element:mcp_service:client:manage` 权限的用户在 NanZi 管理页面创建一个 OAuth Client。注册信息包括：

```text
client_id
client_secret
client_type = confidential
redirect_uris
允许的 scopes
允许的智能体
允许的知识库
允许的元数据资源
```

初期采用管理员审批的静态注册，只允许创建 `Confidential Client`，不开放匿名动态注册，避免任意系统注册后探测 NanZi 能力。`Public Client` 注册和不保存 Secret 的 PKCE-only 模式属于后续扩展。

### 6.2 用户授权流程

适用于 CRM、OA 等代表用户调用的场景。

#### 6.2.1 请求授权

CRM 将浏览器跳转到：

```http
GET /oauth/authorize?
  response_type=code&
  client_id=crm-system&
  redirect_uri=https%3A%2F%2Fcrm.example.com%2Foauth%2Fcallback&
  scope=agent%3Alist%20agent%3Ainvoke%20conversation%3Acontinue%20metadata%3Aread&
  resource=https%3A%2F%2Fnanzi.example.com%2Fmcp%2Fplatform&
  code_challenge=...&
  code_challenge_method=S256&
  state=...
```

必须校验：

- `client_id` 已注册；
- `redirect_uri` 与注册值精确匹配；
- `response_type` 为 `code`；
- `code_challenge_method` 为 `S256`；
- `resource` 为 `https://nanzi.example.com/mcp/platform`；
- 请求 Scope 是客户端允许的 Scope；
- 不能通过 Scope 请求超出客户端配置的能力。

#### 6.2.2 识别用户

授权页面优先使用 NanZi 当前登录会话识别用户。

```text
浏览器已有 NanZi admin_token Cookie
    ↓
NanZi 授权页通过现有登录依赖识别用户
    ↓
不需要把 admin_token 交给 CRM
```

如果用户没有登录：

1. 跳转 NanZi 登录页；
2. 用户完成当前 NanZi 登录流程（账号密码或 NanZi 已支持的登录方式）；
3. 返回授权确认页；
4. 用户确认授权。

NanZi 用户 API Key 只在 NanZi 自己的登录域中使用，不作为 OAuth Access Token 返回给外部系统。

#### 6.2.3 用户确认页

页面展示：

```text
CRM 系统申请访问 NanZi Platform MCP

申请权限：
  ✓ 查询您可使用的智能体
  ✓ 调用客服助手
  ✓ 继续您的 NanZi 会话
  ✓ 查看您有权限的数据集结构

申请方：CRM 系统
用户：张三

[拒绝] [同意授权]
```

不同 Scope 应转化为用户可理解的中文描述，不直接展示内部 Scope 字符串作为唯一说明。

#### 6.2.4 返回 Authorization Code

同意后生成一次性 Authorization Code：

```http
HTTP/1.1 302 Found
Location: https://crm.example.com/oauth/callback?
  code=one-time-code&
  state=original-state
```

Authorization Code 必须绑定：

```text
client_id
user_id
redirect_uri
scope
resource
code_challenge
expires_at
```

Authorization Code：

- 只能使用一次；
- 有效期建议 60 秒；
- 数据库只保存 hash；
- 使用后立即标记 consumed；
- 不能直接作为 MCP Bearer Token 使用。

#### 6.2.5 换取 Access Token

CRM 后端调用：

```http
POST /oauth/token
Content-Type: application/x-www-form-urlencoded
Authorization: Basic base64(client_id:client_secret)

grant_type=authorization_code&
code=one-time-code&
redirect_uri=https%3A%2F%2Fcrm.example.com%2Foauth%2Fcallback&
client_id=crm-system&
code_verifier=...&
resource=https%3A%2F%2Fnanzi.example.com%2Fmcp%2Fplatform
```

NanZi 验证通过后返回：

```json
{
  "access_token": "opaque-access-token-value",
  "token_type": "Bearer",
  "expires_in": 3600,
  "refresh_token": "refresh-token-value",
  "scope": "agent:list agent:invoke conversation:continue metadata:read"
}
```

当前实现的 Access Token 有效期为 3600 秒，Refresh Token 有效期为 30 天；Refresh Token 只由外部系统后端安全保存，不返回给浏览器页面。

### 6.3 Refresh Token

用户授权完成后可以使用 Refresh Token 续期同一用户的授权，不会改变用户身份。

Refresh Token 要求：

- 只保存 hash；
- 必须绑定 `client_id` 和授权关系；
- 每次刷新后轮换 Refresh Token；
- 旧 Refresh Token 立即失效；
- 检测到旧 Token 再次使用时，撤销同一授权关系下的 Refresh Token 链；
- 用户撤销外部应用授权时立即失效；
- 外部系统禁用时立即失效。

## 7. Access Token 设计

### 7.1 Token 类型

Access Token 使用 OAuth2 Bearer Token。当前实现使用高熵 opaque Token，服务端只保存 SHA-256 摘要并按数据库状态验证；这样可以支持 Token 撤销和 Client 停用后的即时失效。

```text
NanZi Platform MCP Access Token 密钥
    ≠ NanZi 用户登录 API Key
    ≠ 下游业务 MCP 的 User Assertion 私钥
```

如果未来切换为 JWT，Platform MCP Access Token 的签名密钥必须独立维护并通过 JWKS 发布公钥；当前 opaque Token 不提供 Access Token JWKS 地址，业务方不需要验签 Access Token。

### 7.1.1 人工登录快捷签发：当前用户个人 Token

OAuth2 Authorization Code + PKCE 适合 CRM、门户等程序化系统发起用户授权，但人工临时接入、联调和 Cursor/桌面客户端配置不需要用户先理解完整 OAuth 流程。服务台提供一个受权限控制的快捷入口：

1. 用户先正常登录 NanZi，进入【MCP 服务台】→【外部 Client】；
2. 在一个启用的 Client 上点击“生成当前用户 Access Token”，选择有效期和 Scope；
3. 后端从当前登录会话读取 `user_id`，不接受页面或请求体传入的 `user_id`，也不提供用户选择框；
4. 生成的 Token 绑定“当前用户 + 当前 Client + 当前 Scope”，只显示本次，调用时仍使用 `Authorization: Bearer <access_token>`。

生成的 Token 始终代表生成时登录的用户本人。该入口不是代发其他用户身份，也不是永久 Bearer Key；Token 仍写入 `sys_mcp_oauth_access_tokens`，支持过期、Client 停用和撤销。当前有效期可选 15 分钟、1 小时、8 小时、1 天、7 天、15 天或 30 天。生成成功后向导进入第二步，既可以单独复制 Access Token，也可以复制已经填入真实 Endpoint 和 Token 的完整 `mcpServers` JSON，直接粘贴到 Cursor、Claude Desktop 等客户端。

Client 的 `scope_version` 从 1 开始。实际编辑 `allowed_scopes` 时递增版本，并撤销该 Client 的历史 Token 和用户授权关系；新签发的 Access Token 保存签发时的 Scope 版本。服务台查询 Client 列表时，仅当当前登录用户没有未过期、未撤销且版本匹配的 Token，才返回 `needs_token_regeneration=true`，页面提示“Scope 已变更，请重新生成 MCP Access Token”。因此这个提示可以在刷新页面后继续保留，直到用户重新生成 Token；Token 过期后也会再次提示。

该快捷 Token 不创建 OAuth 用户授权 Grant、不签发 Refresh Token；它只复用同一 Resource Server 的 Bearer 校验和用户权限链路。需要长期无人值守、自动续期或多用户授权的外部程序，仍使用标准 OAuth2。

### 7.2 Access Token Payload（JWT 扩展设计）

以下 Payload 是未来 JWT 实现的扩展设计，不代表当前 opaque Token 可以被业务方直接 Base64 解析。当前调用方只应把 Token 原值放在 `Authorization: Bearer` Header 中，所有身份和 Scope 由 NanZi Resource Server 从数据库状态恢复。

用户授权模式示例：

```json
{
  "iss": "https://nanzi.example.com",
  "aud": "https://nanzi.example.com/mcp/platform",
  "sub": "nanzi:user:123",
  "client_id": "crm-system",
  "token_use": "mcp_access",
  "grant_type": "authorization_code",
  "user_id": "123",
  "scope": [
    "agent:list",
    "agent:invoke",
    "conversation:continue",
    "metadata:read"
  ],
  "session_id": "mcp-session-abc",
  "jti": "access-jti-abc",
  "iat": 1788300000,
  "exp": 1788300900
}
```

### 7.3 JWT Payload 字段说明（未来扩展）

| 字段 | 类型 | Authorization Code + PKCE | 服务台个人 Token | 用途 |
|---|---|---:|---:|---|
| `iss` | string | 必须 | 必须 | 标识 NanZi 授权服务器 |
| `aud` | string | 必须 | 必须 | 限制只能访问 Platform MCP |
| `sub` | string | 必须 | 必须 | 当前 NanZi 用户主体 |
| `client_id` | string | 必须 | 必须 | 识别外部系统或服务台绑定的 Client |
| `token_use` | string | 必须 | 必须 | 必须为 `mcp_access` |
| `grant_type` | string | 必须 | 必须 | `authorization_code` 或 `manual_user_token` |
| `user_id` | string | 必须 | 必须 | 关联 NanZi 用户，不能省略 |
| `scope` | array | 必须 | 必须 | 方法和能力范围 |
| `session_id` | string | 建议 | 可选 | 关联用户 MCP 会话 |
| `jti` | string | 必须 | 必须 | 撤销、重放和审计 |
| `iat` | number | 必须 | 必须 | 签发时间 |
| `exp` | number | 必须 | 必须 | 过期时间 |

### 7.4 不放入 Access Token 的信息

不建议把以下信息放入 Access Token：

- 用户密码；
- NanZi 用户 API Key；
- 完整权限树；
- 全部元数据资源列表；
- 会话历史；
- 数据库连接配置；
- 用户扩展字段中的敏感信息；
- 真实业务数据。

Token 只携带鉴权所需的最小信息，详细权限由 NanZi 服务端实时或缓存查询。

## 8. MCP 请求认证和鉴权链路

### 8.1 请求格式

每次 HTTP MCP 请求都必须携带 Bearer Token：

```http
POST /mcp/platform HTTP/1.1
Host: nanzi.example.com
  Authorization: Bearer opaque-access-token-value
Content-Type: application/json
MCP-Protocol-Version: 2025-06-18
X-Request-ID: external-request-id
```

Token 不允许放在：

- URL 查询参数；
- MCP 工具参数；
- Cookie 给跨系统后端使用；
- 日志正文；
- 用户可见的错误信息。

### 8.2 Resource Server 验证顺序

```text
收到 MCP 请求
    ↓
读取 Authorization: Bearer
    ↓
查询 Token 摘要和数据库状态（当前实现）
    ↓
校验 issuer、resource/audience、过期、撤销和 Client 状态
    ↓
校验 aud = https://nanzi.example.com/mcp/platform
    ↓
校验 iat / exp
    ↓
未来 JWT 实现额外校验签名、kid、alg、token_use 和 jti
    ↓
创建 McpPrincipal
    ↓
执行方法级 Scope 校验
    ↓
执行用户、智能体、会话或元数据资源权限校验
    ↓
执行业务方法
    ↓
写审计和 Trace
```

### 8.3 McpPrincipal

平台内部统一创建：

```python
class McpPrincipal:
    auth_type: str
    client_id: str
    subject: str
    user_id: str | None
    scopes: set[str]
    resource: str
    token_jti: str
    session_id: str | None
    request_id: str
```

其中：

- `auth_type`：固定为 `user_delegated`；
- `client_id`：外部系统或服务台绑定的 Client；
- `user_id`：必填，来自 NanZi 登录用户；
- `scopes`：Token 中的 Scope 与客户端策略的交集；
- `session_id`：用户授权模式可用于关联会话；
- `request_id`：本次 MCP 调用链路 ID。

后续业务方法不得自己解析 Authorization Header，也不得从工具参数中读取可信用户身份。

### 8.4 有效权限计算

最终有效权限不是单独由 Token 决定，而是多个边界的交集：

```text
有效权限
  = Token Scope
  ∩ Client 允许 Scope
  ∩ 用户平台权限
  ∩ 智能体 / 会话 / 元数据资源权限
  ∩ 方法自身安全策略
```

```text
user_id = Token.user_id
```

所有 Platform MCP 方法都必须基于已验证的 `user_id` 执行，不能通过参数补充、替换或伪造用户身份。

## 9. Platform MCP 方法设计

### 9.1 方法命名规则

方法名称使用小写命名空间：

```text
<domain>.<action>
```

示例：

```text
agent.invoke
metadata.get_schema
conversation.continue
```

新增方法必须同时定义：

- 方法名称；
- 方法描述；
- 输入 JSON Schema；
- 输出 JSON Schema；
- 所需 Scope；
- 是否要求用户身份；
- 是否涉及资源权限；
- 是否产生外部副作用；
- 审计字段；
- 超时和幂等规则。

### 9.2 `agent.list_allowed`

用途：查询调用方可以使用的智能体。

所需 Scope：

```text
agent:list
```

只返回当前用户有权使用的智能体。Client 的 `allowed_scopes` 只控制是否允许调用该 MCP 方法，不控制具体智能体。

输入：

```json
{
  "keyword": "客服",
  "limit": 20,
  "cursor": null
}
```

输入约束：

- `keyword` 可选，最大 100 个字符；
- `limit` 默认 20，最大 100；
- `cursor` 只能使用 NanZi 返回的分页游标；
- 不接受 `user_id`。

返回：

```json
{
  "items": [
    {
      "agent_id": "agent-customer-service",
      "name": "客服助手",
      "description": "处理客户咨询和工单总结",
      "version_id": "version-001",
      "enabled": true
    }
  ],
  "next_cursor": null,
  "request_id": "req_xxx"
}
```

### 9.3 `agent.invoke`

用途：调用 NanZi 指定智能体处理一条用户请求。

所需 Scope：

```text
agent:invoke
```

用户授权要求：

- Token 必须存在 `user_id`；
- 用户必须有权使用指定智能体；
- 智能体必须处于启用状态；
- 当前调用方 Client 必须允许调用该智能体；
- 会话如已存在，必须属于当前用户。

输入：

```json
{
  "agent_id": "agent-customer-service",
  "message": "请总结这个客户最近的工单",
  "conversation_id": null,
  "client_request_id": "crm-request-001"
}
```

输入字段：

| 字段 | 必须 | 说明 |
|---|---:|---|
| `agent_id` | 是 | NanZi 智能体 ID，不接受未授权名称猜测 |
| `message` | 是 | 用户问题，长度受平台配置限制 |
| `conversation_id` | 否 | 继续已有会话，必须校验归属 |
| `client_request_id` | 否 | 外部系统幂等键，建议使用 |

返回：

```json
{
  "request_id": "req_xxx",
  "conversation_id": "conv_xxx",
  "agent_id": "agent-customer-service",
  "status": "completed",
  "content": "客户最近的工单主要集中在……",
  "citations": [],
  "usage": {
    "input_tokens": 100,
    "output_tokens": 80
  }
}
```

调用规则：

- 不返回模型内部思考过程；
- 不把完整 Access Token 传入智能体 Prompt；
- 智能体可使用平台已有工具，但继续执行原有权限预检；
- 如果智能体调用下游业务 MCP，使用现有 NanZi 出站 User Assertion 机制；
- 下游 `X-Nanzi-User-Assertion` 的用户身份来自已验证的 `McpPrincipal.user_id`；
- 高风险写操作应通过后续独立的确认机制处理。

### 9.4 `conversation.continue`

用途：继续当前用户拥有的 NanZi 会话。

所需 Scope：

```text
conversation:continue
```

输入：

```json
{
  "conversation_id": "conv_xxx",
  "message": "继续按照刚才的条件分析第二季度数据",
  "client_request_id": "crm-request-002"
}
```

必须校验：

```text
conversation.user_id == McpPrincipal.user_id
```

不能仅凭 `conversation_id` 查询和继续会话。

### 9.5 `metadata.list_datasets`

用途：列出当前用户有权限查看的数据集。

所需 Scope：

```text
metadata:read
```

服务端只复用 NanZi 当前用户的元数据角色与权限；请求中的 `dataset_ids` 只能进一步缩小本次查询范围。

返回：

```json
{
  "items": [
    {
      "dataset_id": "dataset-sales",
      "name": "销售数据集",
      "description": "销售订单和客户经营分析数据",
      "data_source_type": "mysql",
      "table_count": 12
    }
  ],
  "request_id": "req_xxx"
}
```

不返回：

- 数据库连接密码；
- 数据源内部网络地址；
- 用户没有权限的数据集；
- 未发布或已删除的元数据。

### 9.6 `metadata.search`

用途：按照关键词搜索数据集、表、字段和指标。

所需 Scope：

```text
metadata:search
```

输入：

```json
{
  "query": "销售订单和客户",
  "dataset_ids": ["dataset-sales"],
  "resource_types": ["dataset", "table", "column", "metric"],
  "limit": 10
}
```

服务端处理：

1. 校验 `metadata:search`；
2. 根据当前用户权限过滤数据集，请求中的数据集范围只能进一步缩小结果；
3. 使用现有元数据检索服务；
4. 过滤无权限表和字段；
5. 对敏感字段按字段策略脱敏；
6. 返回匹配元数据和 `request_id`。

返回示例：

```json
{
  "items": [
    {
      "dataset_id": "dataset-sales",
      "dataset_name": "销售数据集",
      "table_name": "sales_order",
      "description": "记录销售订单及订单金额",
      "matched_fields": [
        {
          "name": "customer_id",
          "display_name": "客户ID",
          "type": "bigint",
          "description": "关联客户信息"
        },
        {
          "name": "order_amount",
          "display_name": "订单金额",
          "type": "decimal",
          "description": "订单总金额"
        }
      ]
    }
  ],
  "request_id": "req_xxx"
}
```

### 9.7 `metadata.get_dataset`

用途：获取一个已授权数据集的基本元数据。

所需 Scope：

```text
metadata:read
```

输入：

```json
{
  "dataset_id": "dataset-sales"
}
```

必须校验：

- 数据集存在且已发布；
- 当前用户和 Client 共同允许该数据集；
- 数据集未被删除；
- `dataset_id` 不来自未验证的跨租户输入。

### 9.8 `metadata.get_schema`

用途：获取数据集下允许查看的表和字段结构。

所需 Scope：

```text
metadata:read
```

输入：

```json
{
  "dataset_id": "dataset-sales",
  "table_names": ["sales_order"],
  "include_relationships": false
}
```

返回：

```json
{
  "dataset_id": "dataset-sales",
  "tables": [
    {
      "name": "sales_order",
      "display_name": "销售订单",
      "description": "记录销售订单及订单金额",
      "columns": [
        {
          "name": "order_id",
          "display_name": "订单ID",
          "type": "bigint",
          "nullable": false,
          "description": "销售订单唯一标识",
          "sensitive": false
        }
      ]
    }
  ],
  "request_id": "req_xxx"
}
```

第一期只返回元数据，不返回：

- 样例业务数据；
- 数据库账号；
- 连接密码；
- 任意 SQL 执行结果；
- 未授权字段的名称或描述。

### 9.9 `metadata.get_metrics`

用途：获取当前用户可查看的业务指标及其口径。

所需 Scope：

```text
metadata:metrics:read
```

返回示例：

```json
{
  "items": [
    {
      "metric_id": "metric-sales-amount",
      "name": "销售金额",
      "description": "统计指定时间范围内已确认订单的订单金额之和",
      "dataset_id": "dataset-sales",
      "definition": "sum(sales_order.order_amount)",
      "filters": ["order_status = confirmed"]
    }
  ],
  "request_id": "req_xxx"
}
```

指标口径返回前必须执行数据集权限校验，不能因为用户拥有 `metadata:metrics:read` 就返回全部指标。

### 9.10 `knowledge.search`

用途：在当前用户和外部 Client 共同允许的知识库范围内检索知识内容，供 CRM、OA、企业门户等外部系统直接展示或继续交给智能体处理。

所需 Scope：

```text
knowledge:search
```

用户授权要求：

- Token 必须存在 `user_id`；
- 用户必须拥有目标知识库和文档的访问权限；
- 当前 Client 必须允许检索目标知识库；
- 请求参数中的知识库 ID 不能扩大用户或 Client 的权限范围。

输入：

```json
{
  "query": "报销制度中差旅费住宿标准是什么？",
  "knowledge_base_ids": ["kb-finance"],
  "top_k": 5,
  "filters": {
    "document_type": "policy"
  },
  "client_request_id": "crm-request-003"
}
```

输入约束：

- `query` 必填，长度受平台配置限制；
- `knowledge_base_ids` 可选；未指定时只在当前用户有权限的知识库内检索；
- `top_k` 默认 5，设置上限，防止一次返回过多内容；
- `filters` 只能使用平台允许的文档属性过滤条件；
- 不接受 `user_id`、任意内部索引名或数据源连接信息。

有效检索范围：当前用户可访问的知识库和文档，再与请求指定的知识库范围（如果指定）取交集。

返回：

```json
{
  "items": [
    {
      "knowledge_base_id": "kb-finance",
      "knowledge_base_name": "财务制度库",
      "document_id": "doc-travel-policy",
      "document_title": "差旅费管理办法",
      "content": "住宿标准按出差城市和人员级别执行……",
      "score": 0.92,
      "citation": {
        "page": 4,
        "section": "第三章 住宿标准"
      }
    }
  ],
  "request_id": "req_xxx"
}
```

安全边界：

- 只返回权限交集范围内的内容片段；
- 未授权文档不能通过搜索结果、数量或错误信息被探测；
- 不返回知识库连接配置、向量索引配置、内部存储地址或管理凭证；
- 不提供上传、删除、发布、重建索引等管理操作；
- 对敏感内容按现有知识库脱敏策略处理；
- 记录 Client、用户、知识库、查询摘要、结果数、耗时和 `request_id`，不记录完整 Token。

## 10. 方法权限模型

### 10.1 Scope 清单

| Scope | 方法 | 是否要求用户身份 | 说明 |
|---|---|---:|---|
| `agent:list` | `agent.list_allowed` | 建议要求 | 查询可用智能体 |
| `agent:invoke` | `agent.invoke` | 用户模式必须 | 调用智能体 |
| `conversation:continue` | `conversation.continue` | 必须 | 继续用户会话 |
| `knowledge:search` | `knowledge.search` | 必须 | 搜索授权知识库 |
| `metadata:search` | `metadata.search` | 用户模式建议要求 | 搜索授权元数据 |
| `metadata:read` | `metadata.list_datasets`、`metadata.get_dataset`、`metadata.get_schema` | 用户模式建议要求 | 查看数据结构 |
| `metadata:metrics:read` | `metadata.get_metrics` | 用户模式建议要求 | 查看业务指标口径 |
| `metadata:relationships:read` | 后续 `metadata.get_relationships` | 用户模式建议要求 | 查看表关系 |

### 10.2 三层权限

#### 第一层：OAuth Client 权限

管理员给外部系统配置：

```text
CRM 允许：agent:list、agent:invoke、knowledge:search、metadata:read
OA 允许：agent:list、agent:invoke
数据分析平台允许：metadata:search、metadata:read、metadata:metrics:read
```

#### 第二层：Token Scope

用户授权时，Token 只能包含 Client 被允许的 Scope，并且不能超过用户同意的 Scope。

```text
Token Scope
  ⊆ Client Allowed Scope
```

#### 第三层：NanZi 业务权限

即使 Token 有 `agent:invoke`，仍然需要检查：

- 用户能否使用该智能体；
- 智能体是否启用；
- 智能体版本是否允许；
- 会话是否属于该用户；
- 元数据集是否授权给该用户；
- 字段是否受敏感字段策略限制。

### 10.3 资源权限规则

Client 可以分别配置智能体、知识库和元数据数据集白名单。三个字段均采用三态语义：`NULL` 表示该 Client 不增加限制，空数组表示禁止访问该类资源，非空数组表示只允许列出的资源。`allowed_scopes` 仍只表示该 Client 可以申请哪些 MCP 方法，不替代资源白名单。

所有 Platform MCP 方法都要求已验证的 NanZi 用户身份，执行规则如下：

- `agent.list_allowed` 和 `agent.invoke` 只使用当前用户有权使用且在 Client 智能体白名单内的智能体；
- `knowledge.search` 只检索当前用户有权限且在 Client 知识库白名单内的知识库和文档；请求指定的知识库范围只能进一步缩小结果；
- 元数据方法只返回当前用户有权限且在 Client 数据集白名单内的数据集、表、字段和指标；请求指定的数据集只能进一步缩小结果；
- 同一个 Client 被不同用户使用时，实际可访问资源随 Token 代表的用户身份变化；
- 白名单资源 ID 在服务台保存时必须存在、处于启用状态且属于当前操作者可访问范围；运行时资源不存在、停用或无权访问统一返回无权访问语义，不泄露资源是否存在；
- Client 列表对所有者/管理员返回白名单明细，对其他可见用户仅返回每类资源的策略摘要。

## 11. 会话、用户和下游 MCP 关联

### 11.1 会话关联

用户授权模式中：

```text
McpPrincipal.user_id = 123
McpPrincipal.session_id = mcp-session-abc
```

调用 `agent.invoke` 创建新会话时，保存：

```text
conversation.user_id = 123
conversation.created_by_client_id = crm-system
conversation.mcp_session_id = mcp-session-abc
```

继续会话时必须校验：

```text
conversation.user_id == McpPrincipal.user_id
```

不能只依据 `conversation_id`。

### 11.2 下游业务 MCP 关联

如果外部系统调用 NanZi Platform MCP 后，NanZi 智能体又调用业务 MCP：

```text
CRM
  ↓ OAuth2 Access Token
NanZi Platform MCP
  ↓ McpPrincipal.user_id
NanZi Agent Runtime
  ↓ X-Nanzi-User-Assertion
业务 MCP
```

用户身份链路为：

```text
CRM 授权用户
    ↓
NanZi Access Token.user_id
    ↓
McpPrincipal.user_id
    ↓
NanZi AgentContext
    ↓
下游 User Assertion.user_context.user_id
```

所有 Platform MCP 调用都要求 Access Token 已绑定 NanZi 用户：

```text
user_id = verified NanZi user
    ↓
生成用户级 X-Nanzi-User-Assertion（如后续调用下游业务 MCP）
```

历史上如果数据库中存在没有 `user_id` 的旧 Token，运行时会在 Resource Server 阶段拒绝；不能把 `client_id` 当成业务用户 ID。

## 12. 管理页面设计

### 12.1 页面结构

```text
智能体开发平台
├── MCP 工具集
│   └── 管理 NanZi 连接的外部业务 MCP
└── MCP 服务台
    ├── 服务总览
    ├── 服务配置
    ├── 外部 Client
    ├── 能力组与 Scope
    ├── 用户授权
    └── 调用审计
```

【MCP 服务台】是独立菜单，不放在【MCP 工具集】的 Tab 页面中。两个入口的职责分开：

| 菜单 | 方向 | 管理对象 | 可见范围 |
|---|---|---|---|
| MCP 工具集 | NanZi 调用外部 MCP | 平台出站 MCP 和个人出站 MCP | 继续沿用现有权限 |
| MCP 服务台 | 外部系统调用 NanZi | NanZi Platform MCP 入站服务 | 拥有 `menu:mcp_service` 的用户 |

`MCP 服务台`是一个统一的 NanZi Platform MCP 管理页面，不为 `agent.*`、`knowledge.*` 和 `metadata.*` 建立多套独立的 MCP Server 配置页面。当前已实现的管理 Tab 是“服务总览 / 使用指南 / 服务配置 / 外部 Client / 能力与 Scope / 审计日志”；使用指南解释 OAuth2 授权、Access Token 与 MCP Endpoint 的关系，并提供可复制的通用 `mcpServers` JSON；“审计日志”查询入站调用记录，按权限显示筛选、分页和详情。审计数据范围按调用身份隔离：管理员可查看全部记录，其他用户仅能查看 `user_id` 等于当前登录用户的记录。

#### 12.1.1 菜单与功能权限

菜单权限只控制是否可以看到并进入【MCP 服务台】，功能权限控制具体页面和操作。权限应分配给角色，用户通过角色继承权限，不直接依赖 `user.role == admin`。

菜单访问条件：

```text
用户已登录
    AND
用户具备 menu:mcp_service
```

建议权限清单：

| 权限标识 | 权限类型 | 作用 |
|---|---|---|
| `menu:mcp_service` | 菜单 | 显示并进入 MCP 服务台 |
| `element:mcp_service:overview:read` | 只读 | 查看服务状态、Endpoint、运行概况 |
| `element:mcp_service:config:read` | 只读 | 查看 OAuth、Resource、Audience 等配置 |
| `element:mcp_service:config:edit` | 操作 | 修改 Platform MCP 配置和总开关 |
| `element:mcp_service:client:read` | 只读 | 查看外部 Client 列表和详情 |
| `element:mcp_service:client:manage` | 操作 | 创建、编辑、启停和软删除 Client，配置 Scope |
| `element:mcp_service:client:secret_reset` | 高风险操作 | 重置 Client Secret |
| `element:mcp_service:client:token_issue` | 高风险操作 | 为当前登录用户生成个人 MCP Access Token，不允许指定其他用户 |
| `element:mcp_service:capability:read` | 只读 | 查看能力组、方法和 Scope |
| `element:mcp_service:capability:manage` | 操作 | 启停能力组、修改方法 Scope |
| `element:mcp_service:grant:read` | 只读 | 查看外部应用用户授权关系 |
| `element:mcp_service:grant:revoke` | 操作 | 撤销用户授权关系 |
| `element:mcp_service:audit:read` | 只读 | 查看授权审计和 MCP 调用审计 |

前端规则：

- 没有 `menu:mcp_service` 的用户不渲染该菜单；
- 没有对应 `element:*:read` 权限时隐藏页面或详情入口；
- 没有对应操作权限时隐藏或禁用按钮，并显示只读状态；
- 前端权限只用于交互控制，不能替代后端鉴权。

后端规则：

```text
所有 MCP 服务台接口：校验 menu:mcp_service
具体接口：再校验对应 element:mcp_service:* 权限
无权限：返回 403

外部系统调用 NanZi Platform MCP：
通过 OAuth2 授权，不要求外部用户是 NanZi 管理员
```

### 12.2 服务概览

拥有 `menu:mcp_service` 和 `element:mcp_service:overview:read` 的用户可以查看服务概览。服务概览只展示运行状态和接入信息，不直接修改开关。

页面展示：

| 内容 | 示例 |
|---|---|
| 服务名称 | NanZi Platform MCP |
| MCP Endpoint | `https://nanzi.example.com/mcp/platform` |
| Audience | `https://nanzi.example.com/mcp/platform` |
| 授权模式 | OAuth2 Authorization Code + PKCE、Refresh Token |
| 状态 | 启用 |
| 已发布方法数 | 当前为 9，后续方法按实现情况增加 |
| 授权客户端数 | 5 |
| 近 24 小时调用数 | 1,280 |

服务概览提供以下只读复制项：

- MCP Endpoint；
- Protected Resource Metadata 地址；
- Authorization Server Metadata 地址；
- Audience。

当前 Access Token 是 opaque Token，不需要业务方获取 JWKS 或验签；如果未来切换 JWT，再增加只读的 JWKS 地址。不显示签名私钥、不显示 Client Secret。

### 12.2.1 服务配置 Tab

拥有 `element:mcp_service:config:read` 的用户可以查看「服务配置」Tab；拥有 `element:mcp_service:config:edit` 的用户可以修改 Platform MCP 总开关；拥有 `element:mcp_service:capability:manage` 的用户可以修改能力组开关。

配置只保存到 MCP 专属的 `sys_mcp_platform_config` 单例表，不写入通用 `system_configs`。当前单例记录固定使用 `id = 1`。

页面提供以下开关：

| 开关 | 作用 |
|---|---|
| Platform MCP 总开关 | 控制整个入站 MCP 服务是否处理业务请求 |
| 智能体能力组 | 控制 `agent.*` 方法是否发布 |
| 会话能力组 | 控制 `conversation.*` 方法是否发布 |
| 知识库能力组 | 控制 `knowledge.*` 方法是否发布 |
| 元数据能力组 | 控制 `metadata.*` 方法是否发布 |

生效规则：

- 总开关关闭时，Platform MCP 不处理业务方法；
- 总开关开启但能力组关闭时，该能力组的方法不会出现在 `tools/list`；
- 能力组开关不替代 Client Scope、用户授权和资源权限校验；
- 配置 Tab 只显示给拥有 `config:read` 的用户，修改按钮再按 `config:edit` 或 `capability:manage` 控制；
- 关闭对外 Platform MCP 不影响 NanZi 作为 MCP Client 调用外部业务 MCP。

### 12.3 外部系统接入页

拥有 `element:mcp_service:client:read` 的用户可以查看外部 Client；拥有 `element:mcp_service:client:manage` 的用户可以创建、编辑、启停、软删除 Client 和配置权限范围。重置 Secret 还必须具备独立的 `element:mcp_service:client:secret_reset` 权限。上述权限只决定能否使用对应功能，不扩大 Client 的数据范围：每个用户（包括管理员）只能查看和操作自己创建的 Client。

列表字段：

| 字段 | 说明 |
|---|---|
| 接入名称 | CRM 系统、OA 系统 |
| `client_id` | 系统生成，可复制 |
| Client 类型 | Confidential（第一期） |
| 授权模式 | 用户授权（Authorization Code + PKCE）/ Refresh Token |
| 已授权 Scope | 方法权限 |
| 智能体范围 | 允许调用的智能体 |
| 知识库范围 | 允许检索的知识库 |
| 元数据范围 | 允许查看的数据集 |
| 状态 | 启用 / 禁用 |
| 最近调用 | 最近一次请求时间 |
| 操作 | 编辑、禁用、软删除、重置密钥、查看审计 |

### 12.4 创建外部系统

创建表单：

```text
接入名称：        [ CRM 系统                         ]
Client 类型：     [ Confidential                       ]
允许授权模式：    [ ☑ 用户授权（Authorization Code + PKCE） ]
Redirect URI：    [ https://crm.example.com/oauth/callback ]
允许 Scope：      [ ☑ agent:list                     ]
                  [ ☑ agent:invoke                   ]
                  [ ☑ conversation:continue          ]
                  [ ☑ metadata:read                  ]
允许智能体：      [ 客服助手、销售助手                 ]
允许知识库：      [ 产品知识库、客服知识库             ]
允许元数据集：    [ 销售数据集                       ]
状态：            [ 启用                             ]
```

创建成功后：

- `client_id` 可以重复查看和复制；
- `client_secret` 只明文显示一次；
- 页面提示管理员立即安全保存；
- 后续只能显示脱敏值；
- 停用 Client 前必须二次确认；停用后该 Client 下已有的 Access Token、Refresh Token 立即失效，重新启用后也需要重新获取 Token；
- 重置 Secret 前必须二次确认；重置后旧 Secret 立即失效，该 Client 下已有的 Access Token、Refresh Token 也立即失效；
- 删除 Client 前必须二次确认；删除采用软删除，状态变为 `deleted`，从默认 Client 列表隐藏；该 Client 下已有的 Access Token、Refresh Token 和 active 授权关系立即失效，且不能再次启用；Client 行和历史审计记录保留，便于追溯；
- 软删除复用现有 `sys_mcp_oauth_clients.status` 字段，不物理删除 Client、Token、授权关系或审计数据；新建 Client 的 `created_by` 保存 NanZi 用户 ID。历史数据保留，但不通过历史资源白名单恢复额外授权。
- 三类资源白名单分别通过独立的勾选弹框配置；每个弹框支持搜索、复选、全选当前结果、清空和恢复为“跟随用户权限”。保存只提交当前资源类型字段。
- 白名单或其他安全策略发生实际变化时，立即撤销该 Client 的 Access Token、Refresh Token、有效授权关系和未消费授权码，并记录资源类型与数量摘要；不递增仅用于方法 Scope 的 `scope_version`。
- 第一期不创建 Public Client；后续支持时，Public Client 不生成 Secret，必须使用 Authorization Code + PKCE。

### 12.5 方法与 Scope 页

拥有 `element:mcp_service:capability:read` 的用户可以查看方法与 Scope；拥有 `element:mcp_service:capability:manage` 的用户可以启用或关闭能力组、调整方法 Scope。具体资源范围在 Client 卡片中按智能体、知识库、元数据数据集分别配置，最终仍由当前用户角色与权限共同约束。

展示当前 Platform MCP 的完整方法清单。只有标记为“已发布”的方法才会出现在 MCP `tools/list` 中：

| 方法 | 中文说明 | Scope | 用户身份要求 | 风险等级 | 状态 |
|---|---|---|---:|---|---|
| `agent.list_allowed` | 查询可用智能体 | `agent:list` | 是；当前用户角色与权限 | 低 | 已发布 |
| `agent.invoke` | 调用智能体 | `agent:invoke` | 是 | 中 | 已发布 |
| `conversation.continue` | 继续会话 | `conversation:continue` | 是 | 中 | 已发布 |
| `knowledge.search` | 搜索知识库 | `knowledge:search` | 是 | 中 | 已发布 |
| `metadata.list_datasets` | 列出数据集 | `metadata:read` | 是；当前用户角色与权限 | 低 | 已发布 |
| `metadata.search` | 搜索元数据 | `metadata:search` | 是；当前用户角色与权限 | 低 | 已发布 |
| `metadata.get_dataset` | 获取数据集信息 | `metadata:read` | 是；当前用户角色与权限 | 中 | 已发布 |
| `metadata.get_schema` | 获取表字段结构 | `metadata:read` | 是；当前用户角色与权限 | 中 | 已发布 |
| `metadata.get_metrics` | 获取指标口径 | `metadata:metrics:read` | 是；当前用户角色与权限 | 中 | 已发布 |

新增平台能力时，必须先注册方法元数据，再进入客户端 Scope 配置。

#### 12.5.1 能力组开关

能力组开关统一放在“服务配置”Tab；“能力与 Scope”Tab 负责查看方法、Scope 和当前发布状态：

| 能力组 | 方法范围 | 关闭后的行为 |
|---|---|---|
| 智能体服务 | `agent.*` | 不在 `tools/list` 返回智能体方法，直接调用返回能力未启用 |
| 会话服务 | `conversation.*` | 不允许继续会话 |
| 知识库服务 | `knowledge.*` | 不在 `tools/list` 返回知识库方法，直接调用返回能力未启用 |
| 元数据服务 | `metadata.*` | 不在 `tools/list` 返回元数据方法，直接调用返回能力未启用 |
| 任务服务 | `task.*` | 后续增加 |

能力组关闭不删除 Client 配置、不删除用户授权，也不改变历史审计；重新开启后，原有授权是否继续有效由管理员配置和 Scope 校验共同决定。

### 12.6 用户已授权外部应用（后续切片）

`element:mcp_service:grant:read` 和 `element:mcp_service:grant:revoke` 是后续用户授权管理功能的预留权限；当前代码已保存授权关系，但服务台尚未提供授权关系查询/撤销页面和 API。现阶段只能通过 OAuth Token Endpoint 的标准 Token 撤销接口撤销具体 Token。

用户个人中心增加：

用户自己的授权查看和撤销入口不属于管理员专用 Tab，仍然放在：

```text
个人中心 → 已授权的外部应用
```

用户只能查看和撤销自己的授权关系；不能创建 OAuth Client、修改 Client Scope、查看 Client Secret 或修改 Platform MCP 能力开关。

列表展示：

| 外部应用 | 授权范围 | 授权时间 | 最近使用 | 操作 |
|---|---|---|---|---|
| CRM 系统 | 调用客服助手、查看销售元数据 | 2026-09-02 | 10:32 | 撤销 |
| OA 系统 | 调用办公助手 | 2026-09-01 | 09:21 | 撤销 |

用户撤销后：

- 授权关系标记 revoked；
- 该关系下的 Refresh Token 失效；
- Access Token 按 `jti` 加入撤销列表；
- 后续 MCP 请求返回 401；
- 审计记录保留。

### 12.7 调用审计页（已实现）

拥有 `element:mcp_service:audit:read` 的用户可以在服务台打开“审计日志”Tab。后端查询接口为 `GET /api/portal/mcp-service/audit`，查询结果只包含业务审计字段，不返回 Access Token、Client Secret、Refresh Token 或原始请求 Header。管理员可以查看全部用户的 MCP 入站调用记录；其他用户只能查看 `user_id` 等于当前登录用户的调用记录，即使调用使用的是其他用户创建的 Client，也不会扩大可见范围。

该表的审计粒度是“通过 Bearer 校验并进入 MCP 方法处理链的调用”。完全未通过认证、因此尚未解析出 Client 和用户身份的 401 请求不会写入本表，应通过网关或应用访问日志查看；方法执行后的成功、失败和权限拒绝会按调用链写入本表。

支持按以下条件过滤：

- 页面默认折叠筛选区；展开后使用“过滤对象”下拉列表选择一个条件，再在右侧输入值或选择枚举值；
- 可选过滤对象包括外部系统、NanZi 用户、MCP 方法、智能体、数据集、请求 ID、认证类型、结果状态和状态码；
- 条件值仍按字段保留，已填写的多个条件可以组合查询；查询和重置按钮固定在同一行，不需要横向滚动才能操作；
- 时间范围由后端接口保留支持，页面后续可按实际使用频率增加时间控件。

禁止在页面展示：

- 完整 Access Token；
- Client Secret；
- Refresh Token；
- 用户密码；
- 下游 MCP 固定认证 Token；
- 完整用户断言 JWS。

## 13. 数据表设计

数据库变更必须通过对应版本迁移 SQL，不能直接修改线上或本地数据库结构。

### 13.1 `sys_mcp_platform_config`

保存 NanZi Platform MCP 自身的服务开关。当前 Platform MCP 是一个统一服务，因此使用固定 `id = 1` 的单例记录；配置不写入通用 `system_configs`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | tinyint / smallint | 单例配置 ID，固定为 `1` |
| `platform_enabled` | boolean | Platform MCP 总开关 |
| `agent_enabled` | boolean | `agent.*` 能力组开关 |
| `conversation_enabled` | boolean | `conversation.*` 能力组开关 |
| `knowledge_enabled` | boolean | `knowledge.*` 能力组开关 |
| `metadata_enabled` | boolean | `metadata.*` 能力组开关 |
| `created_by` | varchar(64) | 首次创建人用户 ID |
| `updated_by` | varchar(64) | 最后修改人用户 ID |
| `created_at` | datetime / timestamp | 创建时间 |
| `updated_at` | datetime / timestamp | 最后更新时间 |

迁移执行时会自动初始化 `id = 1` 且所有开关为关闭。服务台「服务配置」Tab 通过后端 API 更新该记录，运行时的 MCP Resource Server 直接读取该表判断总开关和能力组开关。

### 13.2 `sys_mcp_oauth_clients`

保存外部系统注册信息。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | varchar(36) | 主键 |
| `client_id` | varchar(128) | OAuth Client ID，唯一 |
| `client_name` | varchar(200) | 外部系统名称 |
| `client_type` | varchar(20) | 第一期固定为 `confidential`；预留 `public` 供后续 PKCE-only 客户端扩展 |
| `client_secret_hash` | varchar(128) | Secret 哈希，Public Client 为空 |
| `redirect_uris` | JSON / TEXT | 精确匹配的回调地址列表 |
| `allowed_grant_types` | JSON / TEXT | 允许的 grant types |
| `allowed_scopes` | JSON / TEXT | 允许的 MCP 方法 Scope |
| `allowed_agent_ids` | JSON / TEXT | 智能体资源白名单；`NULL` 跟随用户权限，空数组禁止全部，非空数组限制为指定 ID |
| `allowed_knowledge_base_ids` | JSON / TEXT | 知识库资源白名单；三态语义同上 |
| `allowed_metadata_dataset_ids` | JSON / TEXT | 元数据数据集资源白名单；三态语义同上 |
| `status` | varchar(20) | `active` / `disabled` |
| `created_by` | varchar(64) | 创建人的 NanZi 用户 ID；服务台所有 Client 管理查询都按当前登录用户 ID 过滤，管理员也不能查看或操作其他用户的 Client |
| `created_at` | datetime | 创建时间 |
| `updated_at` | datetime | 更新时间 |
| `disabled_at` | datetime | 禁用时间 |

约束：

- `client_id` 唯一索引；
- `client_secret_hash` 不保存明文；
- `redirect_uris` 不能使用通配符；
- Disabled Client 不能换取新 Token。

### 13.3 `sys_mcp_oauth_grants`

保存“用户授权给哪个外部系统”的关系。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | varchar(36) | 主键 |
| `client_id` | varchar(128) | 外键逻辑关联 Client |
| `user_id` | varchar(64) | NanZi 用户 ID |
| `scopes` | JSON / TEXT | 用户同意的 Scope |
| `resource` | varchar(255) | Platform MCP 的规范资源 URI，逻辑标识为 `mcp:nanzi-platform` |
| `status` | varchar(20) | `active` / `revoked` |
| `consented_at` | datetime | 同意时间 |
| `last_used_at` | datetime | 最近使用时间 |
| `revoked_at` | datetime | 撤销时间 |
| `created_at` | datetime | 创建时间 |
| `updated_at` | datetime | 更新时间 |

唯一性建议：

```text
unique(client_id, user_id, resource)
```

### 13.4 `sys_mcp_oauth_authorization_codes`

保存一次性授权码的哈希。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | varchar(36) | 主键 |
| `code_hash` | varchar(128) | 授权码哈希，唯一 |
| `client_id` | varchar(128) | Client ID |
| `user_id` | varchar(64) | 用户 ID |
| `redirect_uri` | varchar(1000) | 本次回调地址 |
| `resource` | varchar(255) | 目标资源 |
| `scopes` | JSON / TEXT | 授权 Scope |
| `code_challenge` | varchar(255) | PKCE Challenge |
| `code_challenge_method` | varchar(20) | 必须为 `S256` |
| `expires_at` | datetime | 过期时间 |
| `consumed_at` | datetime | 使用时间 |
| `created_at` | datetime | 创建时间 |

### 13.5 `sys_mcp_oauth_access_tokens`

保存 Access Token 的生命周期和撤销索引。

如果采用 JWT，可以不保存 Token 明文，只保存其 `jti` 和必要的摘要信息。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | varchar(36) | 主键 |
| `jti` | varchar(128) | JWT ID，唯一 |
| `token_hash` | varchar(128) | 可选，Token 摘要 |
| `client_id` | varchar(128) | Client ID |
| `user_id` | varchar(64) | NanZi 用户 ID；当前运行时必须存在，历史兼容记录可为空 |
| `grant_id` | varchar(36) | OAuth 用户授权关系；服务台个人 Token 为空 |
| `resource` | varchar(255) | 目标 MCP 资源 |
| `scopes` | JSON / TEXT | Token Scope |
| `session_id` | varchar(128) | 用户 MCP 会话，可为空 |
| `issued_at` | datetime | 签发时间 |
| `expires_at` | datetime | 过期时间 |
| `revoked_at` | datetime | 撤销时间 |
| `created_at` | datetime | 创建时间 |

### 13.6 `sys_mcp_oauth_refresh_tokens`

保存 Refresh Token 哈希和轮换关系。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | varchar(36) | 主键 |
| `token_hash` | varchar(128) | Refresh Token 哈希，唯一 |
| `grant_id` | varchar(36) | 授权关系 |
| `client_id` | varchar(128) | Client ID |
| `user_id` | varchar(64) | 用户 ID |
| `rotated_from_id` | varchar(36) | 上一个 Refresh Token |
| `issued_at` | datetime | 签发时间 |
| `expires_at` | datetime | 过期时间 |
| `used_at` | datetime | 使用时间 |
| `revoked_at` | datetime | 撤销时间 |
| `created_at` | datetime | 创建时间 |

### 13.7 `sys_mcp_inbound_audit_logs`

保存入站 Platform MCP 的调用审计。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | varchar(36) | 主键 |
| `request_id` | varchar(128) | NanZi 请求 ID |
| `client_request_id` | varchar(128) | 外部幂等键，可为空 |
| `client_id` | varchar(128) | 外部系统 |
| `user_id` | varchar(64) | NanZi 用户 ID；当前运行时必须存在，历史兼容记录可为空 |
| `auth_type` | varchar(32) | 当前为 `user_delegated` |
| `method_name` | varchar(128) | MCP 方法 |
| `agent_id` | varchar(128) | 智能体 ID，可为空 |
| `conversation_id` | varchar(128) | 会话 ID，可为空 |
| `dataset_id` | varchar(128) | 数据集 ID，可为空 |
| `scopes` | JSON / TEXT | 本次有效 Scope |
| `status_code` | int | HTTP 或业务状态码 |
| `result_status` | varchar(32) | `completed` / `failed` / `denied` |
| `error_code` | varchar(64) | 失败代码，可为空 |
| `latency_ms` | int | 调用耗时 |
| `ip_hash` | varchar(128) | 可选，脱敏 IP 摘要 |
| `created_at` | datetime | 创建时间 |

禁止保存：

- Authorization Header 原文；
- Access Token 原文；
- Client Secret；
- Refresh Token；
- 用户密码；
- 完整工具入参中的敏感字段。

## 14. API 和内部服务边界

### 14.1 OAuth2 端点

| 端点 | 用途 |
|---|---|
| `GET /oauth/authorize` | 用户授权入口 |
| `POST /oauth/token` | Authorization Code 或 Refresh Token 换取用户 Access Token |
| `POST /oauth/revoke` | 撤销当前 Client 的具体 Access Token 或 Refresh Token；授权关系批量撤销为后续服务台能力 |
| `GET /.well-known/oauth-authorization-server` | OAuth2 授权服务器发现 |
| `GET /.well-known/oauth-protected-resource/mcp/platform` | RFC 9728 MCP Protected Resource Metadata |
| `GET /.well-known/oauth-protected-resource` | 兼容旧版客户端的 Protected Resource Metadata 别名 |

当前 Access Token 使用 opaque Token，因此当前版本不提供 `/.well-known/jwks.json`；只有未来采用 JWT Access Token 时才增加 JWKS 端点。业务方不需要自行解析或验签当前 Access Token，只需通过 Token Endpoint 获取，并在 MCP 请求中作为 `Authorization: Bearer <access_token>` 发送。

### 14.2 Platform MCP 端点

| 端点 | 用途 |
|---|---|
| `POST /mcp/platform` | Streamable HTTP MCP 请求 |
| `GET /mcp/platform` | 按 MCP 传输实现支持会话或流式请求 |
| `DELETE /mcp/platform` | 按传输实现关闭 MCP 会话 |

具体传输行为由 MCP SDK 和部署方式决定，但每次 HTTP 请求都必须携带 Bearer Access Token。

### 14.3 服务台管理接口

| 端点 | 用途 | 权限 |
|---|---|---|
| `GET /api/portal/mcp-service/overview` | 查看服务状态和外部接入信息 | `overview:read` |
| `GET /api/portal/mcp-service/config` | 查看 Platform MCP 开关 | `config:read` |
| `PATCH /api/portal/mcp-service/config` | 修改总开关或能力组开关 | `config:edit` 或 `capability:manage` |
| `GET /api/portal/mcp-service/clients` | 查看外部 Client | `client:read` |
| `POST /api/portal/mcp-service/clients` | 创建 Confidential Client | `client:manage` |
| `PATCH /api/portal/mcp-service/clients/{client_id}` | 编辑或启停 Client | `client:manage` |
| `GET /api/portal/mcp-service/clients/{client_id}/resource-options` | 查询当前用户可配置的智能体、知识库或元数据数据集选项 | `client:read` |
| `DELETE /api/portal/mcp-service/clients/{client_id}` | 软删除 Client，并撤销其 Token 与 active 授权关系 | `client:manage` |
| `POST /api/portal/mcp-service/clients/{client_id}/user-access-token` | 为当前登录用户生成短期个人 Access Token | `client:token_issue` |
| `GET /api/portal/mcp-service/audit` | 分页查询入站 MCP 调用审计 | `audit:read` |

服务台接口先校验 `menu:mcp_service`，再校验表中的元素权限；前端 Tab 和按钮隐藏仅用于交互控制，不能替代后端鉴权。Client 管理接口在权限检查之后还会统一追加 `created_by = 当前登录用户 ID` 条件；管理员不会因为角色而绕过这一所有权条件。审计查询则按调用身份区分数据范围：管理员查看全量，普通用户只查看 `McpInboundAuditLog.user_id = 当前登录用户 ID` 的记录；请求中的 `user_id` 筛选条件不能扩大普通用户的可见范围。Client 列表同时返回 `scope_version` 和 `needs_token_regeneration`，供前端持续提示 Scope 或资源策略变更后的重新生成操作；白名单明细仅返回给所有者/管理员，其他可见用户只返回资源策略摘要。

### 14.4 内部服务接口

建议拆分以下服务边界：

```text
McpOAuthService
    ├── register_client
    ├── create_authorization_code
    ├── exchange_authorization_code
    ├── issue_user_access_token
    ├── issue_current_user_access_token
    ├── rotate_refresh_token
    └── revoke_grant

McpInboundAuthService
    ├── authenticate_access_token
    ├── validate_resource_audience
    ├── resolve_principal
    └── require_scope

PlatformMcpToolService
    ├── list_allowed_agents
    ├── invoke_agent
    └── continue_conversation

PlatformMetadataMcpToolService
    ├── list_datasets
    ├── search_metadata
    ├── get_dataset
    ├── get_schema
    └── get_metrics

McpInboundAuditService
    ├── record_authorization_event
    ├── record_token_event
    ├── query_tool_calls
    └── record_tool_call
```

工具服务只接受 `McpPrincipal`，不接受未经验证的 `user_id` 作为身份参数。

## 15. 外部系统调用示例

### 15.1 用户授权后的 MCP 调用

CRM 获取用户授权后的 Access Token：

```python
import requests

response = requests.post(
    "https://nanzi.example.com/mcp/platform",
    headers={
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Request-ID": request_id,
    },
    json={
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "agent.invoke",
            "arguments": {
                "agent_id": "agent-customer-service",
                "message": "请总结这个客户最近的工单",
                "client_request_id": "crm-request-001",
            },
        },
    },
    timeout=120,
)
response.raise_for_status()
result = response.json()
```

CRM 不需要、也不应该在请求参数中传：

```json
{
  "user_id": "123"
}
```

NanZi 从 Access Token 得到用户身份。

### 15.1.1 人工登录后生成当前用户 Token

人工联调或 Cursor 等只支持静态 Header 的客户端，可以直接使用服务台生成的个人 Token：

```text
管理员登录 NanZi
    ↓ 点击“生成当前用户 Access Token”
NanZi 从登录会话得到 admin.user_id
    ↓ 生成短期 opaque Bearer Token
Authorization: Bearer <admin-token>
    ↓
POST /mcp/platform
    ↓
McpPrincipal.user_id = admin.user_id
```

demo 用户执行完全相同的流程时，`McpPrincipal.user_id` 是 demo 用户的 ID。外部调用方不需要、也不能通过 MCP 参数覆盖这个身份；业务方法继续按该用户的 NanZi 角色权限和 Client Scope 执行。

服务台生成 Token 的请求示例：

```http
POST /api/portal/mcp-service/clients/{client_id}/user-access-token
Authorization: Bearer <当前登录 NanZi 用户的 API Key 或登录态对应凭证>
Content-Type: application/json

{
  "scopes": ["agent:invoke", "knowledge:search"],
  "expires_in": 3600
}
```

注意：上面的 `Authorization` 是服务台管理接口调用者的当前登录凭证；返回 JSON 中的 `access_token` 才是后续 MCP 请求使用的 Bearer Token。请求体没有 `user_id` 字段，后端始终使用当前登录用户。

### 15.2 程序化用户授权换 Token

业务系统必须先引导具体用户完成 NanZi 登录和授权，再由后端使用 `client_id + client_secret + code + code_verifier` 换取用户 Access Token。Client Secret 只保存在业务方后端，浏览器、Cursor 或 MCP JSON 中只使用最终的 Bearer Access Token。

```python
# 运行在业务方后端 OAuth 回调中；callback_code 和 pkce_code_verifier
# 来自前面的授权跳转与 PKCE 流程。
import os
import requests

base_url = os.environ["NANZI_BASE_URL"].rstrip("/")
token_response = requests.post(
    f"{base_url}/oauth/token",
    auth=(os.environ["NANZI_CLIENT_ID"], os.environ["NANZI_CLIENT_SECRET"]),
    data={
        "grant_type": "authorization_code",
        "code": callback_code,
        "redirect_uri": os.environ["NANZI_REDIRECT_URI"],
        "code_verifier": pkce_code_verifier,
        "resource": os.environ["NANZI_RESOURCE"],
    },
    timeout=15,
)
token_response.raise_for_status()
access_token = token_response.json()["access_token"]
```

之后每次 MCP 请求只携带：

```http
Authorization: Bearer <access_token>
```

如果 Access Token 到期，业务后端使用同一授权关系的 Refresh Token 续期；Refresh Token 不会改变绑定的用户。

## 16. 错误处理

### 16.1 HTTP 状态码

| 状态码 | 场景 |
|---:|---|
| `400` | 授权请求格式错误、Redirect URI 不匹配、PKCE 参数错误 |
| `401` | 缺少 Token、Token 摘要无效、过期、Audience 错误、已撤销；JWT 模式另含签名错误 |
| `403` | Scope 不足、Client 未授权该方法、用户无智能体或资源权限 |
| `404` | 方法、智能体、会话或数据集不存在，且不会泄露未授权资源存在性 |
| `409` | 幂等键冲突或会话状态冲突 |
| `429` | 超出客户端、用户或方法级频率限制 |
| `500` | NanZi 内部错误 |
| `502` | 下游 Agent/MCP/模型服务不可用 |
| `504` | 执行超时 |

### 16.2 OAuth2 错误示例

```json
{
  "error": "invalid_token",
  "error_description": "The access token is expired"
}
```

Scope 不足：

```http
HTTP/1.1 403 Forbidden
WWW-Authenticate: Bearer error="insufficient_scope", scope="agent:invoke"
```

### 16.3 MCP 业务错误

工具业务错误统一返回结构化信息：

```json
{
  "error": {
    "code": "AGENT_FORBIDDEN",
    "message": "当前调用方无权使用该智能体",
    "request_id": "req_xxx"
  }
}
```

错误消息不能包含：

- Access Token；
- 用户 API Key；
- 数据库连接配置；
- 未授权资源名称；
- 下游 MCP 的完整认证信息。

## 17. 安全设计

### 17.1 Token 安全

- 全链路使用 HTTPS；
- Access Token 只通过 `Authorization` Header 传递；
- 禁止通过 URL 查询参数传递 Token；
- OAuth2 动态 Access Token 默认有效 1 小时；服务台人工个人 Token 可选 15 分钟、1 小时、8 小时、1 天、7 天、15 天或 30 天，且必须设置过期时间；
- Refresh Token 轮换；
- Client Secret 只保存哈希；
- 授权码只保存哈希且一次性使用；
- 当前 opaque Token 只保存 SHA-256 摘要，并通过数据库状态支持撤销和 Client 即时停用；
- 未来切换 JWT 时再使用非对称签名、JWKS、`kid` 和密钥轮换；
- 不记录 Token 原文；
- 访问日志自动脱敏。

### 17.2 OAuth2 安全

- 第一期外部系统只允许使用 `Confidential Client`；
- 用户授权必须使用 Authorization Code + PKCE；
- 后续支持 Public Client 时，Public Client 必须使用 PKCE，且不得保存或依赖 Client Secret；
- PKCE 只允许 `S256`；
- `state` 必须由 Client 生成并校验；
- 未来增加 OIDC 用户登录时使用 `nonce`；本期 OAuth2 授权不接收或校验 `nonce`；
- Redirect URI 精确匹配，不支持通配符；
- 授权码有效期建议 60 秒；
- 用户拒绝授权后不签发 Token；
- Client 禁用后不能继续换取新 Token；
- 用户撤销授权后立即撤销对应授权关系。

### 17.3 Audience 防混用

Platform MCP Access Token 必须绑定：

```text
aud = https://nanzi.example.com/mcp/platform
```

不能接受：

- NanZi 前端登录 API Key；
- 其他 API 的 Access Token；
- 下游业务 MCP 的 Token；
- 其他平台 MCP 的 Token；
- 外部系统自行签发的 Token。

### 17.4 用户身份安全

- 用户身份来自已验证的 OAuth2 授权结果；
- 不信任 MCP 工具参数中的 `user_id`；
- 不允许 Client 通过 Scope 声明自己是管理员；
- 不允许 Client 通过参数切换会话用户；
- 用户会话必须绑定用户 ID；
- 用户元数据访问必须复用 NanZi 资源权限；
- 用户退出、禁用或撤销授权后，按策略撤销 Token。

### 17.5 Knowledge 与 Metadata 方法组安全

Knowledge 和 Metadata 方法属于 Platform MCP 的方法分组，默认只读。

Knowledge 方法：

- 只搜索用户和 Client 共同允许的知识库；
- 只返回匹配内容片段、文档标识和引用信息；
- 不返回未授权文档内容，不能通过关键词探测未授权知识库是否存在；
- 不开放上传、删除、重建索引等管理操作；
- 搜索结果按知识库、文档和用户权限过滤。

Metadata 方法：

- 只返回授权数据集；
- 只返回授权表和字段；
- 敏感字段按策略脱敏；
- 不返回连接密码；
- 不返回真实业务数据；
- 不执行 SQL；
- 不返回完整内部网络拓扑；
- `metadata:read` 不自动包含 `metadata:metrics:read`；
- 搜索结果也必须执行权限过滤，不能先全量检索再直接返回。

### 17.6 速率和资源保护

限流维度：


| 维度 | 说明 |
|---|---|
| Client | 防止单个外部系统占满平台资源 |
| User | 防止单个用户滥用 |
| Method | 对 `agent.invoke`、`knowledge.search`、`metadata.search` 分别限流 |
| Agent | 对昂贵智能体单独限制并发 |
| Dataset | 防止大范围元数据扫描 |

## 18. 审计和可观测性

### 18.1 授权审计（规划）

后续授权事件审计计划记录：

- Client 注册、编辑、禁用、软删除；
- Client Secret 创建和重置；
- 用户授权同意或拒绝；
- 用户撤销外部应用；
- Authorization Code 签发和消费失败；
- Access Token 签发、刷新和撤销。

当前版本暂未建立独立的 OAuth 授权事件审计查询链路；服务台现阶段提供的是 18.2 所述的 MCP 方法调用审计。

### 18.2 调用审计

每次 MCP 方法调用至少记录：

```json
{
  "request_id": "req_xxx",
  "client_id": "crm-system",
  "user_id": "123",
  "auth_type": "user_delegated",
  "method": "agent.invoke",
  "agent_id": "agent-customer-service",
  "conversation_id": "conv_xxx",
  "scopes": ["agent:invoke"],
  "result_status": "completed",
  "status_code": 200,
  "latency_ms": 1850
}
```

### 18.3 日志脱敏

允许展示：

```text
Authorization: Bearer opaque***xYz
```

不允许展示：

```text
Authorization: Bearer <完整 Token>
```

生产日志中不保存完整请求 Prompt、完整用户断言和敏感元数据，必要时保存摘要或脱敏后的字段。

## 19. 密钥和凭证轮换

### 19.1 未来 JWT 模式的 Platform MCP Access Token 签名密钥

维护：

```text
current kid
previous kid
public JWKS
```

轮换流程：

1. 生成新密钥（仅在未来采用 JWT Access Token 时执行）；
2. 新 Token 使用新 `kid`；
3. JWKS 同时发布新旧公钥；
4. 等待旧 Token 最大有效期结束；
5. 删除旧公钥；
6. 记录轮换审计。

### 19.2 外部系统 Client Secret

管理员在接入管理页点击“重置密钥”：

```text
旧 Secret 立即失效
新 Secret 只显示一次
已有 Access Token 按原有效期处理
```

如果需要立即切断所有调用，应同时禁用 Client 或撤销该 Client 的全部 Token。

### 19.2.1 Client 资源策略变更

修改智能体、知识库或元数据数据集白名单属于 Client 安全策略变更：

```text
白名单实际发生变化
    ↓
撤销该 Client 的 Access Token、Refresh Token 和有效 OAuth Grant
    ↓
立即失效该 Client 尚未消费的 Authorization Code
    ↓
记录资源类型和数量摘要，不记录资源 ID 或凭证
```

资源白名单变化不递增 `scope_version`，但服务台仍通过 `needs_token_regeneration` 提示重新生成 Access Token。未消费授权码不能在白名单变化后重新激活旧 Grant。

### 19.3 用户授权撤销

用户在“已授权的外部应用”点击撤销：

```text
grant.status = revoked
refresh tokens = revoked
active access token jti = revoked
```

## 20. 与现有 NanZi 认证的关系

当前 NanZi 的用户登录 API Key 仍用于：

- NanZi 前端登录；
- 现有 Portal API；
- 内部用户上下文解析；
- 现有兼容接口。

新增 Platform MCP 后，职责分开：

| 凭证 | 使用范围 |
|---|---|
| NanZi 用户 API Key | NanZi 登录和现有平台 API |
| OAuth Client Secret | 外部系统身份认证和 Token 换取 |
| OAuth2 Access Token | 调用 NanZi Platform MCP |
| OIDC ID Token（后续） | 用户身份认证，不直接作为 MCP 业务访问凭证 |
| `X-Nanzi-User-Assertion` | NanZi 调用下游业务 MCP 时的出站用户断言 |

不做以下转换：

```text
外部系统拿到 NanZi 用户 API Key
    ↓
直接当 Platform MCP Bearer Token
```

正确转换是：

```text
用户在 NanZi 完成 OAuth2 授权
    ↓
NanZi 签发 audience=Platform MCP 的 Access Token
    ↓
外部系统携带 Access Token 调用 MCP
```

## 21. 典型完整调用时序

### 21.1 用户授权并调用智能体

```mermaid
sequenceDiagram
    participant User as 用户
    participant CRM as CRM 系统
    participant AS as NanZi OAuth2
    participant MCP as NanZi Platform MCP
    participant Agent as Agent Runtime
    participant Audit as Audit

    User->>CRM: 点击调用 NanZi 智能体
    CRM->>AS: Authorization Code + PKCE 授权请求
    AS->>User: 登录/确认授权页面
    User->>AS: 同意授权
    AS-->>CRM: 返回一次性 Authorization Code
    CRM->>AS: code + code_verifier + client authentication
    AS-->>CRM: user-bound Access Token + Refresh Token
    CRM->>MCP: tools/call + Bearer Access Token
    MCP->>MCP: 校验 Token 状态、iss、aud、exp、client、scope（JWT 模式额外校验签名/jti）
    MCP->>MCP: 创建 McpPrincipal(user_id=123)
    MCP->>MCP: 校验智能体和会话权限
    MCP->>Agent: 以用户 123 执行 agent.invoke
    Agent-->>MCP: 返回结果
    MCP->>Audit: 记录 client、user、method、agent、request_id
    MCP-->>CRM: 返回结构化 MCP 结果
```

### 21.2 元数据检索

```mermaid
sequenceDiagram
    participant Client as 外部系统
    participant MCP as NanZi Platform MCP
    participant Auth as Inbound Auth
    participant Perm as Permission Service
    participant Meta as Metadata Service

    Client->>MCP: metadata.search + Bearer Token
    MCP->>Auth: 验证 Access Token
    Auth-->>MCP: McpPrincipal(user_id=123, scopes)
    MCP->>Auth: require metadata:search
    MCP->>Perm: 查询用户 123 的元数据资源范围
    Perm-->>MCP: 允许的数据集和字段
    MCP->>Meta: 按权限范围执行搜索
    Meta-->>MCP: 脱敏后的元数据结果
    MCP-->>Client: 返回搜索结果和 request_id
```

## 22. 兼容和部署策略

### 22.1 客户端兼容

第一期优先支持遵循 MCP HTTP 授权发现、且能安全保存 Client Secret 的 Confidential Client：

- 能读取 Protected Resource Metadata；
- 能发现 Authorization Server；
- 支持 Authorization Code + PKCE；
- 每次请求使用 `Authorization: Bearer`。

对不支持自动 OAuth 发现的受控企业系统，可以由管理员手动配置：

- MCP Endpoint；
- Authorization Endpoint；
- Token Endpoint；
- Client ID；
- Client Secret；Public Client 的 PKCE-only 接入属于后续扩展，不作为第一期配置项。

### 22.2 多实例部署

多实例部署时：

- 当前 opaque Token 不需要 OAuth 签名密钥或 JWKS；未来 JWT 模式由统一 Secret 管理签名密钥；
- 未来 JWT 模式下，JWKS 在各实例一致；
- Authorization Code、Grant、Token 撤销状态存储在共享数据库或 Redis；
- 审计写入统一存储；
- 不依赖单实例内存保存授权状态；
- `state`、PKCE 和 Token 撤销必须跨实例有效。

### 22.3 网关

反向代理或 API Gateway 必须：

- 保留 `Authorization` Header 到 MCP 服务；
- 不把 Token 写入访问日志；
- 支持长连接和 Streamable HTTP；
- 正确透传 `X-Request-ID`；
- 配置请求体大小和超时；
- 不缓存带用户身份的 MCP 响应。

## 23. 测试方案

### 23.1 OAuth2 测试

- 用户未登录访问授权页时跳转登录；
- 已登录用户可以看到正确的授权确认页；
- `state` 不匹配时拒绝回调；
- PKCE `code_verifier` 错误时拒绝换 Token；
- Redirect URI 不匹配时拒绝；
- Authorization Code 重复使用失败；
- Authorization Code 过期失败；
- Client Secret 错误时换 Token 失败；
- Disabled Client 不能换取 Token；
- 用户拒绝授权时不签发 Token；
- 用户撤销授权后 Refresh Token 失效；
- Refresh Token 重放时撤销 Token 链。

### 23.2 Access Token 测试

- opaque Token 摘要错误返回 401；JWT 模式下签名错误返回 401；
- `iss` 错误返回 401；
- `aud` 不是 Platform MCP 返回 401；
- `alg` 不支持返回 401；
- `kid` 找不到返回 401；
- `exp` 过期返回 401；
- `jti` 被撤销返回 401；
- 缺少 `client_id` 返回 401；
- 用户授权 Token 缺少 `user_id` 时拒绝用户级方法；
- 没有 `user_id` 的 Token 在 Resource Server 阶段拒绝，不能调用任何 Platform MCP 方法。

### 23.3 鉴权测试

- Client 没有 `agent:invoke` 时返回 403；
- 用户无权使用智能体时返回 403；
- 用户不能继续其他用户会话；
- `user_id` 工具参数不能覆盖 Token 用户；
- 用户只能看到自己的授权元数据；
- 用户只能看到和访问自己角色权限允许且在 Client 白名单内的数据集；
- `NULL` 表示跟随用户权限，空数组表示禁止该类资源，非空数组表示与用户权限取交集；
- 白名单保存时拒绝不存在、停用或超出当前用户权限的资源 ID；
- 未授权或不存在的 Agent、知识库和数据集不得通过错误差异探测资源是否存在；
- `metadata:read` 不能自动获得指标 Scope；
- 未授权数据集不能通过搜索关键词探测存在性；
- `agent.list_allowed` 不返回未授权智能体。

### 23.4 方法测试

- `agent.list_allowed` 正确过滤智能体；
- `agent.invoke` 能关联用户会话；
- `conversation.continue` 校验会话归属；
- `metadata.search` 正确过滤数据集、表和字段；
- `knowledge.search` 正确过滤知识库、文档和内容片段；
- `metadata.get_schema` 不返回真实数据和连接凭证；
- `metadata.get_metrics` 按数据集权限过滤；
- 所有方法返回 `request_id`；
- 所有方法写入审计；
- 重复 `client_request_id` 不重复产生不可逆副作用。

### 23.5 链路测试

验证完整链路：

```text
OAuth2 用户授权
    → Access Token
    → Platform MCP
    → McpPrincipal
    → Agent Runtime
    → 下游业务 MCP User Assertion
```

Echo MCP 可以作为下游测试服务，用于确认用户授权模式下 NanZi 是否仍然正确生成和透传用户断言。

## 24. 实施阶段

### 阶段一：OAuth2 基座（OIDC 后续扩展）

1. 建立 Platform MCP 资源标识；
2. 建立 OAuth2 Authorization Server；
3. 增加 Protected Resource Metadata；
4. 增加 Authorization Server Metadata；未来 JWT 模式再增加 JWKS；
5. 增加 OAuth Client 管理；
6. 实现 Authorization Code + PKCE；
7. 实现 Access Token、Refresh Token 和撤销；
8. 实现 `McpPrincipal`；
9. 实现入站审计。

阶段一不实现 Public Client 注册；Public Client + PKCE-only 作为后续治理增强项。

### 阶段二：Platform MCP 智能体方法（已实现）

1. 实现 `agent.list_allowed`；
2. 实现 `agent.invoke`；
3. 实现 `conversation.continue`；
4. 接入现有智能体权限；
5. 接入会话归属校验；
6. 接入 AgentScope 执行链路；
7. 验证下游 User Assertion 透传；
8. 增加外部系统 Python/Java 示例。

### 阶段三：Platform MCP 元数据方法（已实现）

1. 实现 `metadata.list_datasets`；
2. 实现 `metadata.search`；
3. 实现 `metadata.get_dataset`；
4. 实现 `metadata.get_schema`；
5. 实现 `metadata.get_metrics`；
6. 复用现有元数据权限；
7. 增加敏感字段脱敏；
8. 增加元数据访问审计。

### 阶段四：Platform MCP 知识库方法（已实现）

1. 实现 `knowledge.search`；
2. 复用现有知识库和文档权限；
3. 过滤未授权文档和内容片段；
4. 返回引用信息和 `request_id`；
5. 增加知识库检索审计。

### 阶段五：治理增强

1. 增加异步 `task.get`；
2. 增加调用配额和速率限制；
3. 增加用户授权管理页；
4. 增加密钥自动轮换；
5. 增加高风险操作确认；
6. 评估动态 Client 注册；
7. 评估多租户和企业统一身份策略。

## 25. 验收标准

### 授权验收

- 外部系统能通过 OAuth2 Authorization Code + PKCE 完成用户授权；
- 第一期外部系统均为 Confidential Client，Client Secret 只保存在外部系统后端，不下发到浏览器、移动端或桌面端前端；
- 用户 API Key 不会返回给外部系统；
- Access Token 的 `aud` 精确绑定 Platform MCP；
- Access Token 过期、撤销均正确返回 401；未来 JWT 模式下签名错误也必须返回 401；
- 授权页可以显示用户可理解的权限说明；
- 用户可以查看并撤销已授权外部应用。

### 智能体方法验收

- 已授权用户可以调用 `agent.list_allowed`；
- 用户只能看到自己有权限的智能体；
- 用户可以调用授权范围内的 `agent.invoke`；
- 用户不能调用其他用户的 `conversation.continue`；
- 工具参数中的 `user_id` 不能覆盖 Token 用户；
- 智能体调用下游业务 MCP 时，用户身份链路保持一致。

### 元数据方法验收

- 用户只能检索授权数据集；
- Client 只能访问 Token 所代表用户有权限的数据集；
- `metadata.search`、`metadata.get_schema` 和 `metadata.get_metrics` 各自执行 Scope 校验；
- `knowledge.search` 执行 `knowledge:search` 和用户知识库权限校验；
- 知识库响应不包含未授权文档和管理配置；
- 元数据响应不包含真实业务数据和连接凭证；
- 敏感字段按平台策略脱敏；
- 所有元数据访问可按用户、Client、数据集和 request ID 审计。

## 26. 不建议的实现方式

### 26.1 直接把用户 API Key 作为 MCP Token

```text
CRM 保存 NanZi 用户 API Key
    ↓
直接 Authorization: Bearer <用户 API Key>
```

问题：

- 外部系统拿到的是平台通用用户凭证；
- 无法清晰区分 Client 和用户授权；
- 权限范围通常过大；
- 难以单独撤销某个外部应用；
- API Key 泄露后影响范围大。

### 26.2 只传明文 `user_id`

```http
X-User-ID: 123
```

或：

```json
{
  "user_id": "123"
}
```

问题：请求方可以篡改为其他用户，不能作为可信身份依据。

### 26.3 Client Secret 加明文用户 ID

```text
client_id + client_secret + user_id
```

`client_id + client_secret` 只能证明外部系统身份，明文 `user_id` 不能证明用户真的授权了该系统代表自己调用。Platform MCP 只接受由 NanZi 用户授权产生、并在服务端绑定 `user_id` 的 Access Token。

### 26.4 把全部平台权限放进 Token

Token 只应表达本次授权范围，不应复制完整角色权限树、全部数据集权限或所有管理权限。

## 27. 后续扩展原则

未来新增平台级 MCP 方法时，必须遵循：

1. 仍然挂载到 NanZi Platform MCP；
2. 使用 `<domain>.<action>` 命名；
3. 定义独立 Scope；
4. 明确是否需要用户身份；
5. 明确是否需要资源级权限；
6. 不接受明文用户身份作为可信来源；
7. 为方法增加管理页说明；
8. 为方法增加审计字段；
9. 为方法增加未授权和越权测试；
10. 需要独立部署时，再单独评估拆分 MCP Server。

新增方法示例：

```text
task.get
    Scope: task:read
    用户身份: 用户模式需要
    资源权限: 只能查看自己的任务

knowledge.search
    Scope: knowledge:search
    用户身份: 用户模式需要
    资源权限: 按知识库权限过滤

report.get
    Scope: report:read
    用户身份: 用户模式需要
    资源权限: 按报表归属和共享范围过滤
```

## 28. 参考资料和现有实现

- [MCP 业务集成认证方案](./mcp-business-integration-authentication-design.md)
- [MCP Echo 测试服务使用说明](../../docs/md/mcp_echo_test_server.md)
- [NanZi MCP 用户身份透传实现](/Users/chenxiaolong/workspace/nanzi-ai-agent-platform/app/services/mcp/user_context_assertion.py)
- [NanZi MCP 出站认证策略](/Users/chenxiaolong/workspace/nanzi-ai-agent-platform/app/services/mcp/mcp_auth_policy.py)
- [当前 NanZi 用户认证依赖](/Users/chenxiaolong/workspace/nanzi-ai-agent-platform/app/core/dependencies.py)
- [当前元数据模型](/Users/chenxiaolong/workspace/nanzi-ai-agent-platform/app/models/metadata.py)
- [MCP Authorization Specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)

## 29. 最终架构摘要

```text
一个 NanZi Platform MCP
    ├── 一个 MCP Endpoint
    ├── 一个 OAuth2 Authorization Server（未来可扩展 OIDC）
    ├── 一个 Platform MCP Audience
    ├── 多个方法命名空间
    │   ├── agent.*
    │   ├── conversation.*
    │   ├── knowledge.*
    │   ├── metadata.*
    │   └── 后续平台能力
    ├── 每个方法独立 Scope
    ├── 每个 Client 独立权限范围
    ├── 用户授权后自动绑定 user_id
    ├── 所有 Access Token 都绑定用户
    ├── 用户和资源权限仍由 NanZi 服务端执行
    └── 所有调用统一审计
```

核心原则：

> `client_id + client_secret` 证明“哪个外部系统”；OAuth2 用户授权证明“哪个用户”；最终的 MCP Access Token 把外部系统、用户、Platform MCP Audience、Scope 和会话绑定在一起。Metadata 是 NanZi Platform MCP 下的一组受权限控制的元数据方法，不是第二个独立 MCP Server。
