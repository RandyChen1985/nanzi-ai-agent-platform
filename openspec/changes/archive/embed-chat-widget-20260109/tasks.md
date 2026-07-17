# 任务清单：嵌入式聊天组件 (Tasks: Embedded Chat Widget)

## 前端实现 (Frontend Implementation)

- [x] **创建嵌入式布局 (Create Embed Layout)**
  - 创建 `frontend/src/layouts/EmbedLayout.vue` (极简全屏布局)。
  - 注册布局逻辑 (在路由或 App.vue 中处理)。

- [x] **创建嵌入式视图 (Create Embed View)**
  - 创建 `frontend/src/views/EmbedChat.vue`。
  - 实现基础聊天 UI (复用 MessageList + Input)。
  - 重构或复用 `AgentDebug.vue` 的核心对话逻辑 (推荐提取 `useChat` composable)。

- [x] **实现 PostMessage 协议 (Implement PostMessage Protocol)**
  - 在 `EmbedChat.vue` 中添加 `message` 事件监听器。
  - 处理 `INIT_CONFIG` 以设置 Token、Agent ID、Theme 和 Instance ID。
  - 处理 `UPDATE_CONTEXT` 以更新本地状态。
  - 处理 `RESET_SESSION` 以重置会话。
  - 处理 `SEND_COMMAND` 以触发指令。
  - 挂载时发送 `NANZI_WIDGET_READY`。

- [x] **实现上下文注入 (Context Injection Support)**
  - 更新 API 调用逻辑，将 `injected_context` 包含在请求中。
  - 验证后端能正确接收并记录此上下文。

- [x] **实现高级主题与快捷指令 (Theming & Slash Commands)**
  - 实现 `applyTheme(theme, styleVars)` 函数，动态应用 CSS 变量。
  - 确保快捷指令菜单在嵌入页中可用且适配。

- [x] **健壮性与重连 (Resilience)**
  - 实现 WebSocket/SSE 断线自动重连逻辑。
  - 在界面上展示简短的连接状态提示。

- [x] **移动端适配 (Mobile Styling)**
  - 添加媒体查询适配移动端视图。
  - 优化输入框和日志表格在小屏下的表现。

## 路由配置 (Router Configuration)

- [x] **注册路由 (Register Route)**
  - 在 `frontend/src/router/index.ts` 中添加 `/embed/chat`。
  - 确保该路由在提供 Token 的情况下跳过标准登录重定向。

## 文档 (Documentation)

- [x] **集成指南 (Integration Guide)**
  - 创建 `docs/integration_guide.md`，提供包含 IFrame 示例、PostMessage 协议表和最佳实践（Z-Index 等）的中文文档。
