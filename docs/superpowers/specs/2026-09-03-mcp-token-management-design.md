# MCP Client Token 管理设计

## 目标

让 MCP 服务台的每个 Client 能直观看到 Access Token 数量和生命周期状态，并提供明确的 Token 管理入口与单条物理删除能力。

## 已确认边界

- 物理删除对象仅为选中的 `McpOAuthAccessToken` 记录。
- 不删除或撤销对应的 OAuth Grant、Refresh Token 和 Client。
- 删除有效 Token 前必须二次确认；已过期和已撤销 Token 可直接删除或批量清理。
- 删除前写入脱敏安全审计事件，不记录 Token 原文或 Token Hash。
- 继续沿用现有 Client 所有权、管理员和共享 Client 权限边界。

## 用户界面

### Client 卡片

在 Client 卡片操作区把“Token 管理”提升为独立按钮，与“生成 MCP Access Token”并列，不再要求用户先打开“更多操作”。卡片摘要增加以下统计：

- Token 总数
- 有效 Token
- 24 小时内到期 Token
- 已过期 Token
- 已撤销 Token

统计只反映当前用户有权查看的 Token。管理员查看该 Client 的全部 Token；非管理员查看共享 Client 时只统计自己的 Token，与现有 Token 列表接口一致。

### Token 管理弹窗

- 顶部显示当前 Client 的 Token 状态统计。
- 提供状态筛选：全部、有效、24 小时内到期、已过期、已撤销。
- 表格保留授权用户、Scope、生成方式、生成时间、过期时间和状态。
- 每条记录提供“物理删除”；有效 Token 显示高风险确认文案。
- 提供批量删除当前筛选结果中的 Token，默认仅对已过期/已撤销状态开放；删除有效 Token 需要明确确认。
- 删除完成后刷新列表、Client 卡片统计和空状态。

## 后端接口与数据流

### Client 统计

扩展 Client 列表序列化结果，返回当前可见范围内的 Token 总数、有效数、临期数、过期数和撤销数。临期定义为 `expires_at > now` 且 `expires_at <= now + 24 hours`。

### 单条物理删除

新增 `DELETE /api/portal/mcp-service/clients/{client_id}/tokens/{token_id}`：

1. 按现有 Client 读取权限确认 Client 和 Token 属于当前可见范围。
2. 记录 Token 的 Client、用户、状态和操作人等脱敏信息。
3. 从 `sys_mcp_oauth_access_tokens` 删除指定记录。
4. 返回被删除的 Token ID 和状态。

该接口不删除 Refresh Token，因为 Refresh Token 属于 Grant 级凭证；如果需要让整个授权关系失效，使用现有 Grant 撤销接口。

### 批量删除

新增按 Client 和状态执行的批量物理删除接口，服务端重新计算状态并限制可删除范围，不能接受前端任意 Token ID 列表绕过权限。批量删除有效 Token 必须由前端显式确认，后端仍执行同样的权限校验。

## 测试与验收

- 后端契约测试覆盖统计字段、单条删除路由、权限校验、只删除 Access Token 不级联 Grant/Refresh。
- 前端契约测试覆盖独立 Token 管理入口、状态统计、临期筛选、单条删除和批量删除入口。
- 运行 MCP 服务台 API/前端契约测试、`vue-tsc --noEmit` 和 `git diff --check`。
- 不启动服务、不执行真实数据库删除；部署前由用户在测试环境验证实际删除和权限行为。
