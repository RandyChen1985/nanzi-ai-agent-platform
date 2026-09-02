# MCP OAuth 默认 Redirect URI 设计

## 目标

当创建 Confidential Client 时，用户未填写 Redirect URI，系统自动补充一个安全、确定性的默认地址，避免 Authorization Code 模式因空列表无法创建。

## 现状与根因

服务台页面把空文本解析为 `redirect_uris: []`。`McpOAuthClientCreate` 清洗空值后仍得到空列表，而 Authorization Code 模式的模型校验要求至少存在一个精确回调地址，因此接口返回 `authorization_code 模式必须配置至少一个 redirect_uri`。

## 方案

在后端创建请求模型的 `redirect_uris` 字段清洗逻辑中统一兜底：清洗后为空时返回 `['https://localhost/oauth/callback']`。这样前端页面和其他 API 调用方共享同一行为，不依赖某个 UI 实现。

保留现有安全约束：

- 非空地址继续去除首尾空白并去重。
- 含通配符的地址继续拒绝。
- OAuth 授权阶段仍要求请求中的 `redirect_uri` 与已注册地址精确匹配。
- 默认地址只是占位注册值；业务系统实际使用 OAuth 时必须把授权请求和 Token 换取中的 `redirect_uri` 都设为已注册的真实回调地址，或后续编辑 Client 配置真实地址。

前端提示同步说明：未填写时会使用默认回调地址，避免用户误以为没有任何注册地址。

## 测试

- 更新现有创建请求契约测试：空列表不再抛出缺少 `redirect_uri` 的异常，并断言得到默认地址。
- 保留并验证通配符地址仍被拒绝。
- 增加前端契约断言，确保页面提示包含默认回调行为。
- 运行相关后端和前端契约测试；类型检查或服务启动不在本次范围内。

## 不做的事情

- 不放宽 OAuth 授权阶段的精确 URI 校验。
- 不修改数据库结构或迁移。
- 不自动启动 `./dev.sh`，不做真实浏览器 OAuth 流程验收。
