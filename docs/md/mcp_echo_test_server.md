# MCP Echo 测试服务使用说明

NanZi 内置 Echo 测试 MCP 用来验证一条真实的智能体 MCP 调用链路：智能体是否挂载成功、原有 `Authorization: Bearer` 是否发送、开启后是否发送 `X-Nanzi-User-Assertion`、业务端是否能验签并读取用户和智能体信息。

它不执行任何业务操作，工具只返回“已收到”和脱敏后的认证诊断。原始 Bearer Token、完整 JWS、签名私钥和 `jti` 不会返回给前端或 MCP 调用结果。

## 一、创建入口

管理员进入【MCP 管理】的【平台 MCP】页，点击【创建 Echo 测试 MCP】。

系统会幂等创建一个平台级服务：重复点击不会轮换既有凭证，只会确保服务已启用、`echo` 工具已发布。创建成功后，Echo MCP 会出现在平台 MCP 列表中，并标记为“平台测试 MCP / 所有智能体可挂载”。

Echo MCP 的固定 `Authorization: Bearer <Token>` 和 Ed25519 签名私钥均由系统自动生成并加密保存，用户不需要填写、复制或维护它们。

## 部署地址与生产环境配置

Echo MCP 使用 MCP SDK 的 DNS rebinding 防护校验请求的 `Host` 和 `Origin`。生产环境必须让后端知道平台对外访问地址，推荐在 Docker Compose 的 `.env` 中配置：

```dotenv
APP_PUBLIC_URL=http://103.79.25.80:8001
# 或：APP_PUBLIC_URL=https://your-domain.example.com
```

两个 Docker Compose 文件都会把该变量传入 API 容器。应用启动时会自动将地址中的 Host 加入 `allowed_hosts`，将完整 Origin 加入 `allowed_origins`，不需要在测试页面填写 `request.base_url` 或 `allowed_hosts`。

如果没有配置 `APP_PUBLIC_URL`，本地开发会继续使用当前请求地址，并保留 `localhost` 默认白名单。修改 Compose 环境变量后必须重新创建或重启 API 容器；已有 Echo 配置还需要回到【MCP 管理】点击一次【创建 Echo 测试 MCP】，更新数据库中的 `sse_url`。

### 生产环境常见错误

如果日志出现：

```text
HTTP/1.1 421 Misdirected Request
Invalid Host header
```

说明请求还没有进入 Echo 工具或 UserContext 验签逻辑，而是在 Host 校验阶段被拒绝。请依次检查：

1. `docker exec nanzi-ai-agent env | grep APP_PUBLIC_URL` 是否能看到公网地址；
2. `APP_PUBLIC_URL` 是否只包含协议、域名和可选端口，不包含 `/api` 或 `/mcp/echo/mcp` 路径；
3. 反向代理转发的 `Host` 是否与公网地址一致；
4. 修改配置后是否重新创建容器，并重新点击创建 Echo 测试 MCP。

## 二、挂载和调用

Echo MCP 是 `scope=global` 的平台 MCP，沿用现有 MCP 工具挂载机制：

1. 在 Echo MCP 的工具列表中确认 `echo` 为“已发布”；
2. 在智能体版本的工具配置中选择 Echo MCP 的 `echo` 工具并保存/发布版本；
3. 在该智能体对话中明确要求调用 `echo`；
4. 查看工具结果中的认证诊断。

只要是已发布且有权限的智能体，都可以挂载该平台 MCP。Echo MCP 不需要单独为每个智能体创建服务配置。

## 三、一次调用发送的 Header

当 Echo MCP 的用户身份传递开关开启时，平台后端实际发出的请求包含：

| Header | 来源 | 用途 |
| --- | --- | --- |
| `Authorization: Bearer <平台生成的固定 Token>` | Echo MCP 自身配置 | 认证请求方是已登记的 MCP 客户端；Token 只在服务端保存和使用 |
| `X-Nanzi-User-Assertion: <短期 JWS>` | 当前登录用户、当前智能体运行上下文 | 业务 MCP 验签后获取 NanZi 用户、智能体和请求信息 |
| `X-Request-ID: <request id>` | 本次工具调用 | 关联平台和 MCP 两侧日志 |

如果关闭用户身份传递，平台只发送已配置的原有 MCP 认证 Header，Echo 返回 `user_assertion_received=false`，用于验证“关闭开关时不透传”的兼容行为。对于普通业务 MCP，是否配置 Authorization 与是否透传用户身份也是两个独立设置；没有 Authorization 仍可以只发送用户断言。

## 四、工具返回示例

成功调用时返回类似下面的 JSON。示例中的用户信息是验签后的安全字段，不是原始凭证：

```json
{
  "message": "已收到",
  "diagnostics": {
    "authorization_valid": true,
    "authorization_masked": "Bearer echo***oken",
    "user_assertion_received": true,
    "user_assertion_valid": true,
    "user_assertion_masked": "eyJhbG***abc123",
    "request_id_received": true,
    "processing_log": [
      "已收到 Authorization 请求头",
      "Authorization Bearer Token 校验通过",
      "已收到 X-Nanzi-User-Assertion 请求头",
      "UserContext 签名校验通过",
      "已解析用户、扩展字段、智能体和请求信息"
    ],
    "verified_user_id": "123",
    "verified_user_context": {
      "user_id": "123",
      "user_name": "zhangsan",
      "real_name": "张三",
      "dept_code": "D001",
      "org_path": "/集团/销售部"
    },
    "custom_attributes": {
      "region": "east",
      "employee_level": "L3"
    },
    "verified_agent_context": {
      "agent_id": "agent-001",
      "agent_version_id": "version-001",
      "agent_name": "销售助手"
    },
    "verified_claims": {
      "issuer": "nanzi-platform",
      "audience": "mcp:<echo-server-id>",
      "subject": "nanzi:user:123",
      "key_id": "echo-<key-id>"
    },
    "request_context": {
      "request_id": "<request-id>",
      "request_id_header_received": true
    }
  }
}
```

`processing_log` 按实际处理顺序说明 Echo 做了哪些检查；`authorization_masked` 保留
`Bearer` 认证方案并对凭证中间脱敏，`user_assertion_masked` 对 JWS 原文首尾脱敏。
这两个字段只用于确认请求格式，永远不会返回完整凭证。用户断言缺失时，
`user_assertion_masked` 为 `null`，处理日志会显示未收到对应 Header。

`authorization_valid=false` 或 Bearer Token 错误时，Echo 会拒绝调用；用户断言缺失时不会伪造用户信息，而是返回 `user_assertion_received=false`；用户断言存在但签名、受众、签发方、有效期、主体或用户 ID 校验失败时，调用会被拒绝。

## 五、如何确认真的发送了用户身份

不能通过浏览器 Network 面板直接看到后端到 MCP 的 Header，因为这是平台服务端发出的请求。推荐使用以下方式确认：

1. 确认 Echo MCP 列表卡片显示“已启用用户身份签名”；
2. 在智能体中调用 Echo 的 `echo` 工具；
3. 查看工具结果：`user_assertion_received` 和 `user_assertion_valid` 是否为 `true`；
4. 同时检查 `verified_user_id`、`verified_agent_context.agent_id`、`request_context.request_id`。

如果连接了自己的业务 MCP，则应在业务 MCP 的安全审计日志中记录“是否收到 Header、验签结果、user_id、agent_id、request_id”，但不要记录完整 JWS 或 Bearer Token。

## 六、业务方如何复用这个验证思路

Echo 只是平台内置的测试服务。业务 MCP 仍然要在自己的服务端：

1. 按原有方式校验 `Authorization: Bearer`；
2. 读取 `X-Nanzi-User-Assertion`；
3. 根据页面提供的 JWKS 地址获取对应公钥，并按 `kid` 选择公钥；
4. 使用 Ed25519 / `EdDSA` 验证 JWS，校验 `iss`、`aud`、`iat`、`exp`、`jti`、`sub`；
5. 使用 `user_context.user_id` 关联业务用户，再执行业务自己的数据和操作权限判断；
6. 使用 `jti` 做短期防重放，并用 `request_id` 关联审计日志。

完整字段定义、Python/Java 示例和 JWKS 验签方式见：[MCP UserContext 接入指南](mcp_user_context_integration_guide.md) 和 [MCP 业务集成认证方案](../../architech/design/mcp-business-integration-authentication-design.md)。

## 七、安全边界

- Echo 的固定 Bearer Token 和签名私钥不进入 API 响应、前端页面或工具返回值。
- 工具返回的是验签后的最小诊断信息，不返回原始 `Authorization`、完整 `X-Nanzi-User-Assertion`、私钥或 `jti`。
- Echo 只做协议和身份透传验证，不替代业务 MCP 的用户映射、租户隔离、数据权限和操作权限。
- 关闭某个 MCP 的用户身份传递不会影响其他 MCP；未开启的 MCP 保持原有调用方式。

## 八、验证 MCP 工具进度流（`echo_slow`）

平台 Echo 除了 `echo`，还带一个 `echo_slow` 工具。它只做一件事：按 `steps` 逐步调用 `report_progress`，用来验收「工具卡进度条」这条链路是否真的通了。

### 为什么需要它

进度不是 MCP 协议强制的：服务端不实现 `notifications/progress`，调用方就一个帧都收不到（工具照常返回结果）。绝大多数第三方服务（票务、CRM）都没有实现，所以拿它们验收永远看不到进度。`echo_slow` 是同进程内的确定性样本，不需要另起服务、配置 URL 或网络白名单。

### 步骤

1. 重启后端（`echo_slow` 是新注册的工具）。
2. 管理员进入【MCP 管理】→【平台 MCP】，再点一次 **【创建 Echo 测试 MCP】**。该操作幂等：不会轮换既有 Token，只会补齐 `echo` 与 `echo_slow` 两个工具。
3. 把这个 Echo MCP 挂到测试智能体上。
4. 让智能体调用 `echo_slow`，参数 `{"steps": 10, "interval_ms": 800}`。

预期：工具卡在执行期间出现进度条，文案从 `1/10 第 1/10 步` 走到 `10/10`，约 8 秒；结束后进度条消失，只保留耗时。

### 三种形态

| 参数 | 预期表现 | 覆盖的远端实现 |
|---|---|---|
| `{"steps": 10, "interval_ms": 800}` | 进度条 + `a/b 文案` | 上报 progress 与 total |
| `{"steps": 6, "total": 0}` | 不确定态呼吸条 + 文案（不显示百分比） | 只上报 progress，不报 total |
| `{"steps": 3, "interval_ms": 0}` | 一闪而过或只有最终卡片 | 调用快于 250ms 节流窗口 |

### 验收要点

- **只在执行中显示**：`status` 离开 `pending` 后进度条必须消失，不能留一个 100% 的假进度条。
- **刷新不残留**：进度帧是瞬态事件，不写会话事件日志、不进 `process_timeline` 快照；刷新页面后看不到历史进度属于预期。
- **委派场景同样可见**：让主智能体委派一个挂载了 Echo MCP 的子智能体去调 `echo_slow`，主聊天的工具卡上也应出现进度（子代理事件桥接已放行 `tool_progress`）。
- **远端不上报时不受影响**：不实现进度的服务只是没有进度条，仍然转圈 + 实时秒表 + 安抚文案。

### 排查

如果 `echo_slow` 能跑、但没有进度条，按顺序检查：

1. 后端是否已重启（新工具、新映射逻辑都在后端）；
2. 前端是否已重建（`case "tool_progress"` 是新代码）；
3. 调用是否真的走了 MCP 工具（`echo_slow` 在工具列表里是否可见、是否已发布）；
4. 是否在「工具已完成」的历史视图里看——进度只在执行中出现。
