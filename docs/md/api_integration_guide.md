# 南孜智能体平台集成指南 (Architecture & Integration Guide)

本文档旨在说明南孜智能体平台（NanZi AI Agent Platform）的核心架构、接入方式以及智能体编排的工作原理。

## 1. 核心运行逻辑 (Core Architecture)

### 1.1 系统架构图

```mermaid
graph TD
    User[用户/外部系统] --> API_Gateway[API Gateway / Nginx]
    
    subgraph "Agent Platform"
        API_Gateway --> |Route: /v1/chat/completions| Main[Default Main / Delegation]
        API_Gateway --> |Route: /v1/agents/{id}/chat| Direct[Direct Execution]
        
        Main --> |Answer or delegate by capability| Selected_Agent[Selected Agent]
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
- **场景**: 通用对话入口，模拟真实用户操作。无需指定具体的 Agent ID，请求会直接进入默认 `Main` 智能体，由 Main 自主回答或按需智能委派专家。支持**多智能体并行协作**。
- **参数**:
  - `messages`: 对话历史
  - `agent_id`: **不传** 或 `null` (表示直接进入默认 Main 的智能委派流程)
  - `enable_multi_agent`: **可选** (Boolean)，默认为 `true`。开启后，Main 可以同时调度多个专家处理相互独立的子任务。
- **请求示例**:
  ```json
  {
    "messages": [{"role": "user", "content": "帮我查询一下机房 PUE，并看看相关的节能制度文档"}],
    "enable_multi_agent": true,
    "stream": true
  }
  ```
- **工作流程**:
  1. **直接进入 Main**: 未指定 `agent_id` 时，后端加载默认 `Main`，不再先调用外层 Router LLM。
  2. **Main 判断是否委派**: Main 结合自身 Prompt、可用工具、专家能力目录、权限和委派深度门禁，决定直接回答，或调用 `sub_agent_call` / `sub_agent_batch_call`。
  3. **并行执行 (Parallel Execution)**: 批量委派时并行启动多个已授权专家执行器，各 Agent 的日志流会交错推送，并带上 `[AgentName]` 前缀。
  4. **结果聚合 (Synthesis)**: Main 将子代理返回的结果整合为一段连贯的回答。

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
- **工作流程**: 直接使用指定 Agent 的 Prompt 和 Tools 执行；指定 Agent 仍可按自身委派策略调用其他已授权子代理。

---

### 2.3 代码画布执行 (Code Canvas)

代码画布提供独立于聊天接口的短代码执行流，适合用户确认后的 Python / Shell 工作区操作：

```http
POST /api/v1/chat/code-executions/stream
Authorization: Bearer <api-key>
Content-Type: application/json
```

```json
{
  "language": "python",
  "code": "print('hello')",
  "conversation_id": "conversation-id"
}
```

响应为 SSE，包含状态、stdout、stderr 和完成结果。需要提前结束时调用：

```http
POST /api/v1/chat/code-executions/{execution_id}/stop
```

停止请求需带同一 `conversation_id`。当前默认单次超时 60 秒、输出上限 100 KB，执行在用户私有工作区内完成。语言别名、状态值、路径安全和工作区文件规则见 [代码画布与工作区执行指南](code_canvas_and_workspace_guide.md)。

## 3. 系统智能体 (System Agents)

平台初始化时内置了以下核心智能体，覆盖了主要业务场景：

| ID | 名称 | 功能描述 | 能力标签 (Capabilities) | 备注 | 
|---|---|---|---|---|
| `sys-agent-chatbi` | **数据智能助手** | 专注于数据查询、SQL 生成与报表分析。 | `data_query`, `sql_generation` | 核心 BI 能力 |
| `sys-agent-metadata` | **元数据专家** | 解析 DDL、定义业务口径、治理元数据。 | `metadata_parsing`, `ddl_analysis` | 运维/开发辅助 |
| `sys-agent-kb` | **知识库助手** | 解答运维规范、操作文档、故障排查流程。 | `knowledge_retrieval`, `qa` | 内部知识问答 |
| `sys-agent-chat` | **通用对话助手** | 处理闲聊、通用问答、代码辅助以及未分类请求。 | `general_chat`, `coding` | **默认 Main 入口** |

### 3.1 自动初始化
我们在开发阶段提供了重置脚本，可一键恢复上述初始状态：
```bash
python3 scripts/reinit_system_agents.py
```
> 该脚本会清空现有的 Agent 表并重新插入上述 4 个标准智能体。

---

## 4. 默认 Main 与智能委派逻辑

未传 `agent_id` / `agent_name` / `version_id` 时，编排入口直接解析为默认 `Main`，当前主链路不调用 `RouterService.route_query()`。

### 4.1 入口决策

| 条件 | 结果 |
|------|------|
| 未传 `agent_id` / `agent_name` / `version_id` | 直接加载默认 `Main`，进入智能委派 |
| 传入 `agent_id` / `agent_name` / `version_id` | 直接加载指定专家 |
| Embed 专家模式或 `@` 提及 | 直接加载被选中的专家 |

### 4.2 Main 的委派判断

Main 不会因为专家数量变化而机械地逐个调用专家。运行时会在委派工具可用时提供当前用户有权限且满足状态、能力和深度等门禁的候选信息，由 Main 按任务需要选择：

1. 能由 Main 自身完成的问候、通用问答、简单改写等任务，直接回答；
2. 需要垂直能力时调用 `sub_agent_call`，并通过 `agent_name` 指定目标专家；
3. 多个相互独立的子任务可调用 `sub_agent_batch_call` 并行执行；
4. 委派受专家可用性、用户权限、自委派/重复调用、超时、结果长度和最大嵌套深度限制；
5. 子代理只返回任务结果，Main 负责最终整合和对用户交付。

### 4.3 兼容说明

`RouterService` 仍保留用于兼容旧调用、路由常量和缓存失效；它不是未指定专家请求的当前运行时入口。`routingMode=auto` 等前端/API 兼容字段仍可存在，但产品语义是“智能委派”，不会额外增加一轮外层语义识别。

### 4.4 执行器边界

`TurnDecision` 仍是本轮外层决策快照，但默认来源是 `Main` 委派入口，指定专家来源是 `direct_agent_selection`。进入 DataQuery 后，才由 `DataQueryTurnClassifier` 继续判断新查询、数据追问、结果复用、结果分析或上下文动作；该内部分类不会替代或回写外层 `TurnDecision`。

当当前 Agent 没有 `data_query` capability，或 `allows_data_route=false` 时，系统不会因为 Agent 配置或模型输出而强行进入 DataQuery，而是按安全规则回退到通用执行路径。

---

## 5. UI 集成与调试

前端提供了 **智能体调试 (Agent Debug)** 界面，支持两种模式的实时切换：
- **🤖 智能委派 (Auto)**: 对应 API 的 `agent_id: null`，用于测试默认 Main 的直接回答与子代理委派能力。
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

### 7.2 执行选项
在 `POST /api/v1/chat/completions` 时，可以传递 `execution_options`：
```json
{
  "messages": [...],
  "execution_options": {
    "force_intent": "DATA_QUERY"
  }
}
```

> 兼容说明：该字段原名为 `debug_options`。旧客户端继续传 `debug_options` 仍然生效（两者同时出现时以 `execution_options` 为准），建议新集成统一使用 `execution_options`。

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
