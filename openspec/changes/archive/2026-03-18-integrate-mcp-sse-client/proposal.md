## Why

当前系统集成外部工具（如 Jira, GitHub, 数据库等）主要依赖于手写 API 包装器或静态配置，扩展性受限且维护成本高。支持 MCP (Model Context Protocol) 标准协议，可以让系统无缝接入成千上万现成的 MCP Server，极大地丰富智能体的能力生态。选择 SSE (Server-Sent Events) 模式而非 Stdio 模式，是为了更好地适配云原生架构，简化进程管理，并支持跨服务的工具调度。

## What Changes

- **MCP SSE 客户端支持**: 实现基于 HTTP SSE 协议的 MCP 客户端，支持与外部 MCP Server 建立长连接并调用其工具。
- **全局 MCP 服务管理**: 在系统管理后台新增 MCP 服务端配置，支持配置多个 SSE 目标地址及全局认证信息。
- **工具自动发现与同步**: 自动从配置的 MCP Server 获取工具元数据（Name, Description, Schema）并缓存，无需手动录入。
- **智能体级工具集成**: 智能体配置页面支持勾选已发现的 MCP 工具，并允许为特定智能体覆盖全局配置（如使用不同的 API Token）。
- **Executor 增强**: 升级 `ToolRegistry` 和 `AssistantExecutor`，支持动态路由和执行 MCP 类型的工具调用。

## Capabilities

### New Capabilities
- `mcp-sse-client`: 提供与外部 MCP Server 通信的核心协议实现，包括连接管理、工具发现和执行调度。
- `mcp-server-management`: 提供系统级的 UI 和 API，用于管理 MCP 服务端的生命周期、配置及工具缓存。

### Modified Capabilities
- `tool-management`: 扩展现有工具管理规范，将 MCP 工具纳入统一的工具注册和发现体系，支持混合调用。
- `agent-orchestration`: 增强智能体编排能力，使其能识别并正确构造针对 MCP 协议的提示词和参数。

## Impact

- **数据库**: 新增 `sys_mcp_servers` 表（存储服务端配置）和 `sys_mcp_tool_cache` 表（存储同步的工具元数据）。
- **API**: 新增 `/api/portal/tools/mcp/*` 系列接口，用于管理服务端及同步状态。
- **系统架构**: 引入对长连接（SSE）的生命周期管理，需要考虑连接池和异常重连机制。
- **智能体执行**: `AssistantExecutor` 的 ReAct 循环将增加对 `mcp:` 前缀工具的处理分支。
