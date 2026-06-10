# 设计文档：多智能体并行执行架构

## 1. 概述
本设计旨在引入“并行执行 + 结果聚合”模式，使系统能够处理复杂的复合意图查询。通过升级路由层和编排层，实现多个专家智能体同时工作并由系统统一汇总输出。同时，前端提供开关控制，允许用户按需开启此高级功能。

## 2. 架构变更

### 2.1 路由层 (Router Service)
**变更点**: `app/services/ai/router_service.py`
**逻辑**:
- Prompt 调整 (V6): 明确告知 LLM 可以返回 `primary_agent` 和 `secondary_agents`。
- 输出结构：
  ```json
  {
    "thought": "用户既问了 A 又问了 B...",
    "primary_agent": "agent-a",
    "secondary_agents": ["agent-b"],
    "strategy": "parallel"
  }
  ```
- **开关控制**: 接收前端传递的 `enable_multi_agent` 参数。若为 False，即使 LLM 返回多个，也强制截断为单 Agent。
- 兼容性：保留原有的单 Agent 返回格式，确保向后兼容。

### 2.2 编排层 (Agent Service)
**变更点**: `app/services/ai/agent_service.py`
**流程**:
1.  **路由决策**: 获取 `RouteResult`。
2.  **分支判断**:
    - **单 Agent**: 走原有流程 (Direct Stream)。
    - **多 Agent** (且开关开启): 进入并行流程 (Parallel Execution)。
3.  **并行执行**:
    - 实例化多个 `AssistantExecutor`。
    - 使用 `asyncio.create_task` 启动每个 Executor。
    - **日志处理**: 由于 SSE 是单通道，需要拦截每个 Executor 的 `yield`，给日志加上 `[AgentName]` 前缀后转发给前端。
    - **结果捕获**: 收集每个 Executor 的最终文本输出（非流式部分）。
4.  **最终聚合**:
    - 构造 Prompt：
      ```text
      用户问题：...
      【Agent A 结果】：...
      【Agent B 结果】：...
      请汇总...
      ```
    - 调用 `synthesis_llm` 生成最终流式回复。

### 2.3 数据结构
**变更点**: `app/schemas/agent.py`
- `RouteResult` 新增 `secondary_agents: List[str] = []`。

## 3. 前端变更 (Frontend)

### 3.1 主对话界面 (EmbedChat.vue)
- **设置**: 在设置面板或输入框附近增加 "多智能体协同 (Multi-Agent)" 开关。
- **API**: 调用 `/api/v1/chat/completions` 时，Payload 增加 `enable_multi_agent: boolean`。
- **日志**: 优化日志渲染，如果日志 Title 包含 `[AgentName]` 前缀，尝试进行高亮或分组显示。

### 3.2 调试界面 (AgentDebug.vue)
- **控制**: 增加显式的 "Enable Multi-Agent Routing" 复选框。
- **展示**: 确保能清晰看到 Router 返回的 JSON（包含 secondary_agents）以及并行的执行日志流。

## 4. 异常处理
- **部分失败**: 如果 Agent A 成功，Agent B 失败。
  - 策略：聚合层应忽略 B 的错误或在回答中简要提及“无法获取 B 的信息”，但保留 A 的结果。不要让整个请求崩溃。
- **超时控制**: 并行任务应设置总超时时间。

## 5. Token 消耗控制
- 聚合阶段的 Context Window 是瓶颈。
- 策略：如果子 Agent 输出过长（例如 > 2000 tokens），需进行截断或摘要后再送入聚合 Prompt。