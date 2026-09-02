# NanZi Platform MCP Knowledge Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 NanZi 中落地统一的 Platform MCP 入站基座，使用 OAuth2 标准授权关联外部 Client 与 NanZi 用户，并先端到端实现 `knowledge.search`。

**Architecture:** NanZi 同时作为 OAuth2 Authorization Server 和 MCP Resource Server，完整 OIDC 作为后续扩展。外部系统使用 Authorization Code + PKCE 完成用户授权，使用 Refresh Token 续期同一用户的授权，获得 opaque Bearer Access Token；Platform MCP 的 TokenVerifier 从数据库恢复并校验 token，再将 `client_id`、已验证用户身份、scope 和资源信息注入 `McpPrincipal`。知识库检索复用现有 RAGFlow 客户端，目标知识库由当前用户角色权限和请求范围共同决定。服务台作为独立 `/dashboard/mcp-service` 菜单，由 `menu:mcp_service` 和 `element:mcp_service:*` 控制。

**Tech Stack:** FastAPI、FastMCP、SQLAlchemy 2.x async、Pydantic 2、Vue 3 + TypeScript、MySQL/PostgreSQL 版本化 SQL 迁移。

---

## 1. 认证与领域模型（RED → GREEN）

- [x] 新增入站 OAuth Client、授权码、Access Token、Refresh Token、授权关系和调用审计 ORM 模型。
- [x] 新增 OAuth2 token 签发/校验服务；当前 Access Token 使用 opaque Token，不复用用户 API Key，不信任请求体中的 `user_id`。
- [x] 新增 OAuth2 authorize、token、revoke 和 RFC 发现端点；首期只接受 Confidential Client，并校验 redirect URI、PKCE、scope、resource；完整 OIDC 端点后续扩展。
- [x] 先写单元测试覆盖 client_secret 哈希校验、PKCE、过期/撤销边界、用户绑定 Token、scope 上限，并完成 RED → GREEN。

## 2. Platform MCP Resource Server 与 `knowledge.search`

- [x] 新增 `McpPrincipal` 和 FastMCP `TokenVerifier`，只接受 `Authorization: Bearer <OAuth Access Token>`，强制校验 token、resource、过期时间、Client 状态和 Platform MCP 总开关。
- [x] 建立统一方法注册表，当前实际发布 `knowledge.search`，保留 `agent.*`、`conversation.*`、`metadata.*` 扩展点；每个方法声明 scope、能力组和是否需要用户身份。
- [x] 新增 `knowledge.search` 工具：执行当前用户知识库权限 ∩ 请求知识库范围；没有已验证用户的 Token 直接拒绝。
- [x] 返回结构化结果与 citations，不能返回连接密钥、内部存储地址或未授权知识库内容；记录脱敏调用审计。
- [x] 挂载 `/.well-known/oauth-protected-resource`、`/.well-known/oauth-authorization-server` 和 `/mcp/platform`，确保不被 SPA catch-all 或旧 API 鉴权误拦截；当前 opaque Token 不提供 JWKS。
- [x] 先写 verifier、scope、知识库交集和 MCP tool contract 测试，再实现并运行聚焦测试。

## 3. 管理 API、菜单权限与服务台页面

- [x] 新增 `/api/portal/mcp-service` 管理 API：读取 MCP 专属服务配置、Client 列表/创建/编辑/启停/重置 Secret、能力组开关和方法目录；调用审计查询作为后续增量。
- [x] 后端每个接口先校验 `menu:mcp_service`，再校验相应 `element:mcp_service:*`；不把非 admin 用户排除在外，admin 继续按既有权限机制可用。
- [x] 前端新增独立“`MCP 服务台`”菜单和 `/dashboard/mcp-service` 页面，不修改现有“`MCP 工具集`”的出站 MCP 逻辑；按只读/可操作权限显示按钮和状态。
- [x] 页面增加独立“服务配置”Tab，展示并修改 MCP 专属总开关和能力组开关；概览页只展示状态。页面展示 OAuth2 discovery 地址、MCP 地址和 Client 信息；Secret 只在创建/重置时显示一次并支持复制。
- [x] 新增前端路由/权限契约测试，确认旧 MCP 工具集入口和新服务台入口互不混淆。

## 4. 数据库迁移与文档同步

- [x] 新增 MySQL `V137` 迁移，创建 `sys_mcp_platform_config`、方案中定义的 `sys_mcp_oauth_*` 和 `sys_mcp_inbound_audit_logs` 表，注册 `menu:mcp_service` 与所有 `element:mcp_service:*` 权限；不再向通用 `system_configs` 写入 Platform MCP 开关。
- [x] 新增 PostgreSQL `V38` 迁移，语义与 MySQL 一致；不直接修改任何运行中的数据库。
- [x] 更新 `architech/design/mcp-platform-inbound-service-design.md`，标注已实现边界、实际路由和首期 `knowledge.search` 行为。
- [x] 更新 README/FAQ 的入口说明，保持“固定 Authorization Bearer Token”与入站 OAuth Access Token 的区别。
- [x] 运行 `git diff --check`、后端聚焦 pytest、前端契约测试和 `vue-tsc --noEmit`，明确未做 live OAuth 浏览器、真实 RAGFlow、数据库迁移和部署验收。

## 5. 收尾检查

- [x] 检查未授权、scope 不足、Client 禁用、能力组关闭、用户无资源权限等错误均不泄露资源存在性。
- [x] 检查日志和审计不记录明文 client_secret、access_token、refresh_token 或完整查询敏感内容。
- [ ] 请求代码审查；用户明确要求后再提交/推送，当前不自动 commit。

## 6. MCP 专属服务配置收敛

- [x] 先补充 `McpPlatformConfig`、配置服务、迁移和服务台配置 Tab 的失败契约测试。
- [x] 新增固定 `id = 1` 的 `sys_mcp_platform_config` 单例表，保存总开关、能力组开关、创建人和最后修改人。
- [x] Platform MCP Resource Server 和服务台管理 API 改为读取/更新专属配置表，移除五个 `system_configs` 初始化项及 `ConfigService` 写入链路。
- [x] 将服务总开关和能力组开关从服务总览移动到“服务配置”Tab，保留 `config:read`、`config:edit`、`capability:manage` 权限边界。
- [x] 补充 MySQL/PostgreSQL 字段注释、迁移契约和 MCP 全量回归验证。
