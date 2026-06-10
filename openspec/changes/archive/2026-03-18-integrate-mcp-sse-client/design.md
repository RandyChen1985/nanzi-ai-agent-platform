## Context

当前系统支持静态硬编码工具和基于通用 HTTP API 的工具。随着 MCP (Model Context Protocol) 的兴起，需要一种标准化的方式来集成第三方工具集。我们选择仅支持 MCP over SSE 模式，以简化分布式环境下的进程管理和部署。

## Goals / Non-Goals

**Goals:**
- 实现 MCP SSE 客户端，能够发现并调用外部 MCP Server 的工具。
- 提供全局 MCP 服务端管理界面及 API。
- 支持工具元数据的本地缓存，确保配置智能体时的响应速度。
- 实现“全局配置 + 局部覆盖”的凭证管理模式。

**Non-Goals:**
- 不支持 MCP Stdio (子进程) 模式。
- 不支持 MCP Resources 或 Prompts 扩展（本次仅限 Tools）。
- 不支持复杂的 OAuth2 握手流（仅支持静态 Token 注入）。

## Decisions

### 1. 数据库模型设计
为了支持 MCP 服务管理、工具同步及发布流，表结构设计如下：

- **`sys_mcp_servers`**:
    - `id`: UUID (Primary Key)
    - `server_name`: String (唯一名称)
    - `sse_url`: String (SSE 握手地址)
    - `auth_headers`: Text (JSON, 存储全局鉴权信息)
    - `enabled_status`: Integer (0: 离线, 1: 在线)
    - `last_sync_at`: DateTime

- **`sys_mcp_tool_cache`**:
    - `id`: UUID (Primary Key)
    - `server_id`: UUID
    - `tool_name`: String (工具全名，如 `github:create_issue`)
    - `tool_description`: Text
    - `parameter_schema`: Text (JSON Schema)
    - `is_published`: Boolean (默认 False，只有发布后智能体才能看到并勾选)

### 2. SSE 连接管理 (懒加载策略)
为了节省资源，不采用持久长连接，而是实现 **“按需握手 + 闲置释放”**：
- **握手**：当有工具调用或同步请求时，`McpClientService` 检查连接池。若无活跃连接，则发起 SSE 握手，缓存 `endpoint` URL。
- **释放**：每个连接关联一个 `last_used_at` 戳。后台任务每分钟扫描一次，释放超过 5 分钟未使用的连接。
- **错误透明度**：如果握手失败或 Server 返回错误，系统将原始错误透传给 `AssistantExecutor`，并由其向用户输出“工具暂时不可用：[原因]”。

### 3. 工具管理发布流
1. **同步**：点击“同步”仅抓取元数据存入 `sys_mcp_tool_cache`。
2. **发布**：管理员在 MCP 管理界面勾选同步回来的工具，将其状态改为 `is_published = True`。
3. **可见性**：只有已发布的 MCP 工具才会出现在智能体编辑页的选择列表中。

### 4. UI 结构定义
- **系统配置层级**: 在 `System Settings` 中新增 `MCP Config` Tab。用于配置 MCP Server URL、鉴权及执行工具同步/发布管理。
- **智能体配置层级**: 在智能体版本管理的工具集区域，新增一个 `MCP Tools` 独立 Tab（与现有 `Static Tools` 并列）。用户在此处勾选已发布的 MCP 工具。

## Risks / Trade-offs

- **[Trade-off] 懒加载导致首跳延迟** → **[Rationale]** 第一次调用工具需要进行 SSE 握手，会有约 500ms-1s 的额外延迟，但换取了极高的系统资源利用率。
- **[Decided] 系统级统一管理** → **[Rationale]** 简化了凭证泄露风险，智能体作为纯粹的使用方，不感知底层的 Token 细节。

## Migration Plan

1. 执行 SQL 脚本创建 `sys_mcp_servers` 和 `sys_mcp_tool_cache`。
2. 后端部署新增的 `McpClientService` 及其配套 API。
3. 前端部署工具管理 Tab 及智能体配置增强逻辑。
4. 运行一个示例 MCP SSE Server（如 `mcp-server-sqlite`）进行集成测试。
