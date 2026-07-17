# Agent App (调试客户端) 设计文档

## 1. 概述 (Overview)

**Agent App** (目前体现为 `AgentDebug.vue`) 是南孜中台智能体平台的核心交互终端。它不仅是一个聊天窗口，更是一个全功能的调试与运行环境。它旨在让开发者和最终用户能够与智能体进行实时交互，并提供深度的可视化调试能力。

本文档旨在梳理其核心设计、功能模块、接口协议及实现细节，以便第三方系统集成或参考实现自定义的 Agent 客户端。

## 2. 核心功能 (Core Features)

### 2.1 智能路由与多模式 (Routing & Modes)
- **自动路由 (Auto Router)**: 用户的意图首先经过 `RouterService` (分类器)，自动分发给最合适的专业智能体（如 ChatBI, KnowledgeBase 等）。
- **指定智能体 (Specific)**: 允许用户强制指定与某个特定版本或特定 ID 的智能体对话，用于测试特定能力。

### 2.2 实时流式响应 (Real-time Streaming)
- **打字机效果**: 基于 Server-Sent Events (SSE) 协议，实现字符级的实时输出。
- **混合数据流**: 在同一个 SSE 连接中，不仅传输文本内容，还混传结构化日志、状态更新、调试信息等。

### 2.3 深度调试能力 (Deep Debugging)
- **思维链可视化**: 并不只是展示最终结果，而是实时展示 Agent 的“思考过程”（如意图识别、工具调用、SQL 生成、数据查询等）。
- **原始 Prompt 查看**: 允许开发者查看发送给 LLM 的最终原始 Prompt，方便 Prompt Engineering 调优。
- **完整 Trace 日志**: 每次会话生成唯一的 `trace_id`，可回溯完整的执行堆栈。

### 2.4 富文本与交互 (Rich UI)
- **Markdown 渲染**: 支持标题、列表、代码块（带语法高亮）、表格的完美渲染。
- **上下文管理**: 可视化展示当前的会话 Context（如提取的变量、SQL 上下文）。

## 3. 架构设计 (Architecture)

### 3.1 前端架构 (Frontend)
- **技术栈**: Vue 3 (Composition API) + TailwindCSS.
- **核心组件**:
    - `AgentDebug.vue`: 主控制器，负责 WebSocket/SSE 连接管理、消息列表状态维护。
    - `TraceLogViewer.vue`: 侧边/弹窗组件，用于展示详细的 Trace 日志。
    - `AgentContextStack.vue`: 展示当前的上下文变量状态。
- **状态管理**: 使用 Reactive State 管理消息队列 (`messages`) 和 当前输入 (`userInput`)。

### 3.2 后端服务 (Backend)
- **入口**: `app/api/v1/endpoints/chat.py`
- **核心服务**:
    - `AgentService`: 编排层，负责组装 Prompt、调用 LLM、执行工具。
    - `DataQueryExecutor` / `KnowledgeExecutor` / `AssistantExecutor`: 具体的执行器。
    - `RouterService`: 路由层。
- **通信协议**: HTTP + SSE (text/event-stream)。

## 4. 接口协议 (API Specification)

任何想要实现 Agent App 的客户端，需对接以下核心接口：

### 4.1 获取欢迎语
- **URL**: `GET /api/v1/chat/greeting`
- **功能**: 获取动态生成的 Agent 欢迎语。
- **响应**: `{"greeting": "..."}`

### 4.2 获取可用智能体列表
- **URL**: `GET /api/portal/agents/` (注：此接口由南孜・业务服务后端 `yovole-yunshu-dataweb-backend` 提供，不在 AI 平台核心服务内)
- **功能**: 获取用于“指定智能体”模式的下拉列表。

### 4.3 发送对话 (核心)
- **URL**: `POST /api/v1/chat/completions`
- **Header**: `Accept: text/event-stream`
- **Body**:
  ```json
  {
    "messages": [{"role": "user", "content": "..."}],
    "stream": true,
    "agent_id": "可选，指定agent_id",
    "debug_options": {
        "return_raw_prompt": true // 是否返回原始Prompt用于调试
    }
  }
  ```

### 4.4 SSE 数据流协议 (Data Protocol)

`/api/v1/chat/completions` 接口通过 SSE 协议返回混合数据流。客户端必须监听 `message` 事件并解析 `data` 字段。每一行数据以 `data: ` 开头，内容为 JSON 对象。

以下是完整的事件类型定义与**真实数据示例**：

#### 1. 初始化 (Init)
会话建立时发送的第一条消息，包含本次请求的唯一追踪 ID。
```json
// data:
{
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "init"
}
```
*客户端动作*: 记录 `trace_id`，用于后续关联日志或调用 `GET /logs/{trace_id}`。

#### 2. 元数据 (Meta)
告知当前实际工作的智能体身份。
```json
// data:
{
  "type": "meta",
  "agent_name": "ChatBI",
  "model": "DeepSeek-V3"
}
```
*客户端动作*: 更新聊天窗口顶部的 Agent 名称或图标。

#### 3. 结构化日志 (Structure Logs) - 核心调试能力
这是 Agent Debug 的核心，用于展示 AI 的思考过程。主要分为三类：

**A. 意图识别 (Intent)**
```json
// data:
{
  "type": "log",
  "title": "意图识别",
  "details": "检测到数据查询意图，且当前智能体支持数据查询。原因: 用户提到了“服务器”和“数量”关键字。",
  "status": "success"
}
```

**B. 工具调用 - 开始 (Tool Call Pending)**
```json
// data:
{
  "type": "log",
  "id": "call_AbC123...",
  "title": "调用工具: execute_sql_query",
  "details": "参数: {\"sql\": \"SELECT count(*) FROM server_info WHERE status = 'active'\"}",
  "status": "pending"
}
```
*客户端动作*: 在时间轴上绘制一个“加载中”的节点。

**C. 工具调用 - 完成 (Tool Call Result)**
```json
// data:
{
  "type": "log",
  "id": "call_AbC123...", // 与 Pending 状态的 ID 一致
  "title": "工具完成: execute_sql_query",
  "details": "结果: [{\"count(*)\": 42}]",
  "status": "success" // 或 "error"
}
```
*客户端动作*: 更新对应节点的图标为“对号”或“叉号”，并显示结果详情。

#### 4. 上下文更新 (Context Update)
当 AI 修改了某些全局上下文变量（如 Dashboard 筛选条件）时触发。
```json
// data:
{
  "type": "context_update",
  "data": {
    "sql": "SELECT * FROM servers LIMIT 10",
    "last_count": 42
  }
}
```
*客户端动作*: 实时更新侧边栏或浮动窗口中的变量视图。

#### 5. 调试信息 (Debug Info)
仅在请求体中 `debug_options.return_raw_prompt = true` 时返回。
```json
// data:
{
  "type": "debug",
  "subtype": "raw_prompt",
  "data": "System: You are an AI...\nUser: Hello"
}
```
*客户端动作*: 存储该数据，提供一个“查看 Prompt”按钮供开发者点击查看。

#### 6. 内容增量 (Content)
标准的打字机文本流。
```json
// data:
{"content": "根"}
// data:
{"content": "据"}
// data:
{"content": "查"}
// data:
{"content": "询"}
```
*客户端动作*: 将字符拼接到当前消息内容中，并触发 Markdown 重渲染。

#### 7. 结束流 (Done)
```
data: [DONE]
```
*客户端动作*: 关闭 SSE 连接，标记本次生成结束。

## 6. 数据可视化 (Data Visualization)

第三方客户端完全可以基于接口数据实现自定义的图表展示。主要有两种实现模式：

### 模式 A：基于 Agent 输出渲染 (推荐)
让 Agent 直接返回 Markdown 表格或前端可识别的图表配置代码（如 ECharts JSON）。这是最简单的方式，因为 Agent 已经完成了数据的清洗和摘要。

- **表格**: 客户端支持 Markdown 表格渲染即可（Agent 默认行为）。
- **图表**: 可以在 Prompt 中要求 Agent 输出 `<chart type="bar" data="{...}" />` 格式，客户端拦截并渲染。

### 模式 B：基于原始数据渲染 (Raw Data)
如果客户端需要获取数据库返回的**原始完整数据**（例如 1000 条记录）来绘制高精度图表，而不是依赖 LLM 的摘要：

1. **监听数据流**: 捕获 `execute_sql_query` 的工具调用日志。
2. **获取 ID**: 从日志中获取 `trace_id`。
3. **异步获取详情**: 调用 `GET /api/v1/chat/logs/{trace_id}`。
4. **解析数据**: 在返回的完整日志中，`tool_output` 字段包含未截断的原始 JSON 数据（如 SQL 查询结果）。
5. **本地渲染**: 客户端使用这些原始数据驱动 Chart.js / D3.js 等库进行渲染。

> **注意**: 实时流中的 `log.details` 字段为了性能会被截断（约 500 字符），因此对于大数据量展示，必须使用 **模式 B** 获取完整 Log。


### 5.1 渲染引擎
- 必须使用 robust 的 Markdown 渲染库（如 `markdown-it`）。
- **表格支持**: 必须支持 GitHub Flavored Markdown (GFM) 表格，因为 ChatBI 经常输出数据表格。
- **代码高亮**: SQL 代码块需要高亮显示。

### 5.2 流式解析
- SSE 可能会在一个 TCP 包中包含多行，或者一行数据被拆分到两个包中。
- 建议使用标准的 SSE 客户端库，或者手动处理 buffer：按 `\n\n` 分割消息，处理 `data: ` 前缀。

### 5.3 错误处理
- 如果 SSE 连接中断或返回非 200 状态码，应在对话框中以 System 角色显示错误信息，而不是静默失败。
- 对于 `type: "log", status: "error"` 的事件，应由红色高亮显示，提示用户该步骤失败。

### 5.4 性能
- **自动滚动**: 随着流式内容的生成，聊天窗口应自动滚动到底部。
- **防止抖动**: 频繁的 Markdown 重绘可能导致页面抖动，建议在 Vue/React 中使用适当的 diff 机制或防抖。

---

*此文档供设计与集成参考，具体实现请以最新代码库为准。*
