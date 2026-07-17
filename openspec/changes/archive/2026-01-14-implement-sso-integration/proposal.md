# 提案：集成 Yovole SSO 统一认证

## 问题描述
目前系统仅支持本地 API Key 和用户名/密码认证。为了融入南孜智能体的生态系统并提升用户体验，需要支持 Yovole 统一认证系统（SSO）。

## 解决方案
采用“SSO 负责认证，本地负责权限”的解耦方案：
1.  **认证 (Authentication)**：通过 Yovole Laplace SSO 接口验证用户身份。
2.  **授权 (Authorization)**：认证通过后，检查本地 `ai_agent_users` 表，验证用户是否已开通权限及账号状态，并分发本地 API Key/Token。

## 影响范围
- **配置**：`app/core/config.py` 增加 SSO 相关环境变量。
- **服务层**：`AuthService` 增加 SSO 校验逻辑。
- **接口层**：新增 `/api/portal/auth/sso/login` 接口。
- **前端**：登录页面集成 SSO 登录选项。
