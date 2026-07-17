# 设计方案：SSO 集成

## 1. 架构概览
系统将集成 Yovole Laplace SSO 接口，作为一种新的认证方式与现有认证体系并行。

## 2. 详细设计

### 2.1 认证流程
1. 用户在前端输入用户名和密码。
2. 前端调用 `/api/portal/auth/sso/login`。
3. 后端 `AuthService` 发起异步 HTTP 请求至 `SSO_API_URL`。
4. SSO 返回成功后，后端查询本地 `ai_agent_users` 表。
5. 若用户存在且状态为启用（status=1），则模拟登录成功，设置 Cookie 并返回用户信息。

### 2.2 配置项 (Environment Variables)
- `SSO_API_URL`: SSO 接口地址。
- `SSO_ACCESS_TOKEN`: 固定访问令牌 `YOVOLE-LAPLACE-API-ACCESS-TOKEN`。
- `SSO_REQUEST_SYSTEM`: 请求系统标识（默认为 `NANZI_AI_AGENT_PLATFORM`）。
- `SSO_REQUEST_BUSINESS`: 业务标识（默认为 `USER-LOGIN`）。

### 2.3 接口定义
**POST `/api/portal/auth/sso/login`**
- **Request**: `{ "username": "...", "password": "..." }`
- **Response**: 同现有的 `/login` 接口，包含用户信息和 API Key。

## 3. 安全考虑
- **TLS 验证**：在开发环境可忽略，生产环境必须启用。
- **超时处理**：设置 30s 超时以防 SSO 服务不可用导致后端挂起。
- **日志安全**：严禁在日志中记录用户原始密码或 SSO Access Token。
