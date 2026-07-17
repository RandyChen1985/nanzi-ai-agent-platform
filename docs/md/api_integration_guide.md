# 南孜智能体平台集成指南 (Architecture & Integration Guide)

本文档旨在说明南孜智能体平台（NanZi AI Agent Platform）的核心架构、接入方式以及智能体编排的工作原理。

## 1. 核心运行逻辑 (Core Architecture)

### 1.1 系统架构图

```mermaid
graph TD
    User[用户/外部系统] --> API_Gateway[API Gateway / Nginx]
    
    subgraph "Agent Platform"
        API_Gateway --> |Route: /v1/chat/completions| Router[Router Service]
        API_Gateway --> |Route: /v1/agents/{id}/chat| Direct[Direct Execution]
        
        Router --> |Analyze Intent + Capabilities| Selected_Agent[Selected Agent]
        Direct --> Selected_Agent
        
        Selected_Agent --> |Load Config & Tools| LLM[LLM Engine]
        LLM --> |Tool Call| Tools[Tools / Plugins]
        LLM --> |Response| Output
    end
    
    Tools --> |Query| DB[(Databases)]
    Tools --> |Search| KB[(Knowledge Base)]
```

---

## 2. 接入方式 (Access Methods)

### 2.1 智能编排模式 (推荐)
- **Endpoint**: `POST /api/v1/chat/completions`
- **场景**: 通用对话入口，模拟真实用户操作。无需指定具体的 Agent ID，系统会根据用户的问题自动分发给最合适的专家智能体。支持**多智能体并行协作**。
- **参数**:
  - `messages`: 对话历史
  - `agent_id`: **不传** 或 `null` (表示启用自动路由)
  - `enable_multi_agent`: **可选** (Boolean)，默认为 `true`。开启后，系统可以同时调度多个专家智能体处理复合意图。
- **请求示例**:
  ```json
  {
    "messages": [{"role": "user", "content": "帮我查询一下机房 PUE，并看看相关的节能制度文档"}],
    "enable_multi_agent": true,
    "stream": true
  }
  ```
- **工作流程**:
  1. **Router Service 介入**: 系统首先调用 Router LLM，分析用户意图。
  2. **智能路由 (多意图识别)**: Router 会判断问题是否跨域。如果跨域且开启了 `enable_multi_agent`，将返回一个主 Agent 和多个辅助 Agent。
  3. **并行执行 (Parallel Execution)**:
     - 系统并行启动多个 Agent 执行器。
     - 各 Agent 的日志流会交错推送，并带上 `[AgentName]` 前缀。
  4. **结果聚合 (Synthesis)**: 系统调用专门的聚合模型，将多方专家的输出整合为一段连贯的回答。

### 2.2 直接调用模式 (专家模式)
- **Endpoint**: `POST /api/v1/agents/{agent_id}/chat` (或 `/api/v1/chat/completions` 带 `agent_id`)
- **场景**: 明确知道需要使用哪个 Agent，或者在开发调试特定 Agent 时使用。
- **参数**:
  - `agent_id`: **必填** (例如 `sys-agent-chatbi`)
- **请求示例**:
  ```json
  {
    "agent_id": "sys-agent-chatbi",
    "messages": [{"role": "user", "content": "查询用户表"}],
    "stream": true
  }
  ```
- **工作流程**: 跳过 Router 分析，强制使用指定 Agent 的 Prompt 和 Tools 执行。

---

## 3. 系统智能体 (System Agents)

平台初始化时内置了以下核心智能体，覆盖了主要业务场景：

| ID | 名称 | 功能描述 | 能力标签 (Capabilities) | 备注 | 
|---|---|---|---|---|
| `sys-agent-chatbi` | **数据智能助手** | 专注于数据查询、SQL 生成与报表分析。 | `data_query`, `sql_generation` | 核心 BI 能力 |
| `sys-agent-metadata` | **元数据专家** | 解析 DDL、定义业务口径、治理元数据。 | `metadata_parsing`, `ddl_analysis` | 运维/开发辅助 |
| `sys-agent-kb` | **知识库助手** | 解答运维规范、操作文档、故障排查流程。 | `knowledge_retrieval`, `qa` | 内部知识问答 |
| `sys-agent-chat` | **通用对话助手** | 处理闲聊、通用问答、代码辅助以及未分类请求。 | `general_chat`, `coding` | **默认兜底路由** |

### 3.1 自动初始化
我们在开发阶段提供了重置脚本，可一键恢复上述初始状态：
```bash
python3 scripts/reinit_system_agents.py
```
> 该脚本会清空现有的 Agent 表并重新插入上述 4 个标准智能体。

---

## 4. 路由逻辑详解 (Router Logic)

Router Service 是编排的核心组件。未传 `agent_id` / `agent_name` 时决策顺序如下：

### 4.1 启发式短路（无 LLM）

| 条件 | 结果 |
|------|------|
| 纯问候/寒暄（`looks_like_greeting`） | 通用助手 |
| 联网/外部公网搜索（`looks_like_web_search_query`） | 通用助手（配合 `web_search` 等工具） |
| 上轮为 ChatBI，但本轮 **不**满足 `should_inherit_data_agent_session()` | 打断粘性 → 通用助手 |

`should_inherit_data_agent_session` 仅在对**已有查数结果**的追问，或含**强内部业务查数信号**时返回 true。

### 4.2 LLM 语义路由

1. **元数据加载**: 从数据库加载当前用户有权限的活跃 Agent 的 `name`, `description`, `capabilities`。
2. **上下文组装**: 最近约 6 轮历史（逐条截断、去表格/代码块）+ 上一轮智能体 + 必要时「禁止机械沿用 ChatBI」提示。
3. **意图分析**: 将用户输入 + Agent 列表 + 历史发送给路由 LLM（`DEFAULT_SYSTEM_PROMPT`，代码内置）。
4. **决策 (复合意图)**:
   - LLM 返回最匹配的 `agent_name` (Primary)。
   - 若 `enable_multi_agent` 且问题跨域，可返回 `secondary_agents`。
   - 附带 `turn_labels`、`relation_to_previous`、`user_action_type` 作为 executor 弱 hint。
5. **Fallback 机制**:
   - LLM 解析失败（最多重试 1 次）、置信度低或异常时，降级到 `sys-agent-chat`（通用对话助手）。

### 4.3 专家直选

传 `agent_id` / `agent_name` 或 Embed **专家模式** 时：**跳过** Router LLM，并设置 `route_hints.direct_agent_selection=true`（主助手数据反幻觉 Guard 不启用）。

---

## 5. UI 集成与调试

前端提供了 **智能体调试 (Agent Debug)** 界面，支持两种模式的实时切换：
- **🤖 自动路由 (Auto)**: 对应 API 的 `agent_id: null`，用于测试 Router 的分发准确性。
- **🎯 指定智能体 (Specific)**: 对应 API 的指定 `agent_id`，用于针对性调优某个 Agent 的 Prompt。

此外，聊天气泡会通过 `[AgentName]` 徽标展示当前正在服务的智能体来源。

---

## 6. 接口响应规范 (API Response Specification)

平台支持 **标准 JSON** 和 **SSE 流式 (Server-Sent Events)** 两种响应格式。

### 6.1 标准响应 (`stream: false`)
**结构**:
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "content": "回答的全文内容",
    "agent_name": "目标智能体名称",
    "trace_id": "唯一执行追踪 ID",
    "status": "success",
    "model": "使用的模型版本"
  }
}
```

### 6.2 流式响应 (`stream: true`)
使用 `text/event-stream` 格式。每个数据包以 `data: ` 开头，以 `\n\n` 结尾。

**流式数据包类型**:
1. **初始化包 (Init)**: 包含本次请求的 `trace_id`。
   ```json
   {"trace_id": "uuid-xxx", "status": "init"}
   ```
2. **元数据包 (Meta)**: 告知当前响应的智能体和模型。
   ```json
   {"type": "meta", "agent_name": "ChatBI", "model": "DeepSeek-V3.2"}
   ```
3. **内容包 (Content)**: 逐字返回的消息增量。
   ```json
   {"content": "正在"}
   ```
4. **日志包 (Log)**: 用于展示中间推理步骤（如意图识别、工具调用过程）。
   - **并行模式下**: 日志标题 `title` 会自动带上智能体前缀，如 `"[ChatBI] 正在执行 SQL"`。
   ```json
   {"type": "log", "title": "意图识别", "details": "检测到意图: DATA_QUERY", "status": "success"}
   ```
5. **结束标识**: `data: [DONE]`

---

## 7. 调试与审计 (Debug & Audit)

### 7.1 获取执行追踪详情
- **Endpoint**: `GET /api/v1/chat/logs/{trace_id}`
- **功能**: 回溯某次对话的完整“思考过程”，包含所有工具调用的输入输出和耗时。

### 7.2 调试选项
在 `POST /api/v1/chat/completions` 时，可以传递 `debug_options`：
```json
{
  "messages": [...],
  "debug_options": {
    "force_intent": "DATA_QUERY"
  }
}
```

---

## 8. 调用示例 (Examples)

### ❓ 问：查询机房 PUE 数据
**Request**:
```bash
curl -X POST http://localhost:8001/api/v1/chat/completions \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "帮我查下机房A昨天的PUE趋势"}],
    "stream": true
  }'
```

**Response (SSE Stream Snippets)**:
```text
data: {"trace_id": "8d3e...", "status": "init"}
data: {"type": "meta", "agent_name": "chat-bi", "model": "DeepSeek-V3.2"}
data: {"type": "log", "title": "意图识别", "details": "Detected Intent: DATA_QUERY...", "status": "success"}
data: {"content": "根据查询结果，机房A昨天的PUE运行在 1.25 - 1.30 之间..."}
data: [DONE]
```

---

## 9. 如何在 UI 上呈现“思考过程” (Thinking Process UI)

平台的一个核心特性是**逻辑透明化**。第三方集成方可以参考以下逻辑，在 UI 上复现“智能体思考中”的专业体验。

### 9.1 实时渲染逻辑 (基于 Log 数据包)
当集成方接收流式响应时，应建立一个**日志缓冲区**：
1. **捕获 `log` 事件**：监听 SSE 流中 `type: "log"` 的数据包。
2. **状态维护**：
   - 每收到一个 `status: "pending"` 的包，在前端展示一个新的加载条或日志行。
   - 每收到一个 `status: "success"` 或 `"error"` 的包，更新对应 ID 的日志行内容和图标。
3. **展示内容**：将 `details` 字段的内容渲染为日志块（支持多行文本）。

### 9.2 前端代码伪逻辑示例
```javascript
eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  
  // 1. 处理正文内容
  if (data.content) {
    message.text += data.content;
  }
  
  // 2. 处理“思考日志”
  if (data.type === 'log') {
    updateThinkingProcessUI({
      title: data.title,
      details: data.details,
      status: data.status, // 'pending' | 'success' | 'error'
      id: data.id
    });
  }
};
```

---

## 10. 数据可视化与结构化输出 (Data Visualization)

如果集成方希望通过图表（如 ECharts、G2）展示 AI 生成的数据，可以利用以下两种数据来源：

### 10.1 利用 Markdown 表格
智能体（特别是 ChatBI）默认会以 Markdown 表格形式输出数据。大多数现代前端 Markdown 组件都能将其渲染为美观的 HTML 表格。

### 10.2 利用结构化工具结果 (推荐用于专业图表)
对于复杂的趋势图、柱状图，推荐直接使用**原始 JSON 数据**：
1. **获取原始数据**：
   - 监听 `type: "log"` 数据包，找到 `execute_sql_query` 的执行结果。
2. **场景联动**：
   - 智能体通过调用 `update_dashboard_context` 工具发送 `type: "context"` 信号。
   - 前端接收到该信号后，可以自动更新页面上的实时看板。

### 10.3 响应包类型小结
| 类型 (type) | 用途 | 包含内容示例 |
|---|---|---|
| `content` | 文本对话 | "根据您的要求，查询结果如下..." |
| `log` | 思考/数据过程 | 原始 SQL 结果、意图分析详情 |
| `context` | UI/上下文联动 | `{"room_id": "102", "metric": "PUE"}` |
| `meta` | 指标信息 | 模型名称、智能体名称 |

## 11. 通用 HTTP 工具 (Generic API Tools)

除了系统内置的专用工具（如 SQL 查询、知识检索），平台还支持**“配置驱动”**的通用 API 工具。这允许通过简单的 UI 配置，将外部 RESTful API 接入到智能体能力中，而无需编写代码。

### 11.1 配置方式
在“系统设置” -> “工具管理”中，您可以添加新的 HTTP 工具。
- **URL Template**: 支持 `{param}` 占位符。例如 `https://api.example.com/weather?city={city}`。
- **参数 Schema**: 使用 JSON Schema 格式定义参数，帮助 LLM 理解参数含义。例如：
  ```json
  {
    "city": {
      "type": "string",
      "description": "城市名称，如 Shanghai"
    }
  }
  ```

### 11.2 调用原理
配置完成后，工具会自动注册到系统中。当智能体（如通用对话助手）需要回答与该工具相关的问题时，LLM 会：
1. 分析问题，决定调用该工具。
2. 根据 Schema 提取参数（如 `city="Shanghai"`）。
3. 系统自动替换 URL 占位符并发送请求。
4. 将 API 返回的 JSON 结果作为上下文反馈给 LLM。

### 11.3 示例场景
- **运维查询**: 配置一个查询 CMDB 的 API，让 Agent 能回答“服务器 X 的配置是什么”。
- **外部搜索**: 接入 Google Search API 或 Bing Search API。
- **业务操作**: 配置一个触发 Jenkins 构建的 webhook。
