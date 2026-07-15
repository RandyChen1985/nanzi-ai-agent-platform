# EmbedChat 思考过程分层展示设计

## 目标

只优化 `EmbedChat` 思考面板**摘要条**文案：进行中显示业务进度短句，结束后显示「思考完成」。展开后的步骤列表保持原 timeline，不改 `AgentDebug`。

## 非目标

- 不把步骤列表改成阶段聚合 UI
- 不改后端 turn log 协议
- 不改 `AgentDebug.vue`

## 行为

| 状态 | 摘要条标题 |
|------|------------|
| 进行中 | 按当前/最近 log 归类：正在理解问题… / 正在选择处理方式… / 正在调用工具… / 正在生成回答… |
| 已完成 | 思考完成 |

展开列表仍使用 `getDisplayLogs(msg)` 的原始步骤 UI。

## 实现

- `frontend/src/utils/embedThoughtStages.ts`：摘要文案（内部可复用阶段分类）
- `frontend/src/views/EmbedChat.vue`：`getThoughtPanelTitle` 改用 `getEmbedThoughtSummaryTitle`
