## Why

当前云枢 EmbedChat 智能体对话输入框仅支持纯文本交互，缺少附件挂载或多模态上传能力，限制了智能体在处理包含图像、表格、文档等富上下文业务场景下的表现。为了提升用户体验，首期将重点打通“本地文件上传与托管”闭环，并在交互界面对“知识库”和“技能工作流”进行高阶占位设计，以支持渐进式功能整合。

## What Changes

- **✨ 前端交互升级 (`ChatInput.vue` & `EmbedChat.vue`)**：
  - 新增极客感输入框 `+` 动作按钮及毛玻璃质感下拉菜单。
  - 支持“本地上传文件 (Active)”、“选择知识库 (筹备中)”、“调用技能工作流 (筹备中)”三项分流。
  - 输入框上方新增附件预览挂载区，支持图片缩略图及常用文档卡片样式，具备便捷的 `X` 移除操作。
  - 支持多通道上传：点击动作菜单上传、直接向输入框拖拽文件挂载、粘贴板贴图自动上传。
  - 在发送消息时，扩展传输载荷，同步将挂载附件元数据列表 (`files`) 传递给后端。

- **🔒 后端托管与安全闭环 (`fastapi` & `static`)**：
  - 在 `app/main.py` 中公开挂载静态上传目录 `/static/uploads`，用于附件预览和下载。
  - 在 `app/api/v1/endpoints/chat.py` 中提供通用文件上传接口 `POST /upload`。
  - 实施大小限制（单个最大 20MB）、危险后缀黑名单阻断（禁止 `.exe`, `.bat`, `.sh` 等）以及文件名防冲突和路径穿越防护。
  - 更新 `/completions` 会话保存逻辑，支持附件元数据在 Redis 会话历史中的持久化与回显。

## Capabilities

### New Capabilities
- `embedchat-upload-attachments`: 提供 EmbedChat 对话输入端的文件上传、多维实体上下文挂载和静态资产托管服务，支持向智能体注入多模态与富文本附件上下文。

### Modified Capabilities
无

## Impact

- **前端组件**：`frontend/src/components/embed/ChatInput.vue` (重构输入区域与新增 Plus 菜单/预览栏)，`frontend/src/views/EmbedChat.vue` (联调发送逻辑与附件数据流传递)。
- **后端路由**：`app/api/v1/endpoints/chat.py` (新增上传端点，拦截安全风险)，`app/main.py` (配置静态资源映射托管)。
- **数据流与会话**：`app/services/ai/agent_service.py`，`app/services/ai/memory_service.py` (会话上下文持久化字段扩展)。
