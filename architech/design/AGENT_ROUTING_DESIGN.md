# 智能体路由分发设计 (Agent Routing Design)

本文档详细说明了南孜智能体平台如何将用户的自然语言请求分发给最合适的智能体。

## 1. 核心路由策略

平台采用 **“上下文感知的 LLM 路由机制” (Context-Aware LLM Routing)**。当请求未显式指定智能体时，路由器会结合可用智能体清单、最近多轮对话、上一轮处理智能体和当前用户输入，选择最合适的主智能体。

路由层只负责选择最合适的智能体 / 执行器，并可输出通用会话标签作为 hint；它不负责 ChatBI 内部请求类别判断。ChatBI 的「新数据查询 / 复用上一轮结果 / 上下文动作 / 技能执行」由 `DataQueryExecutor` 内部的 `DataQueryTurnClassifier` 判定。

### 1.1 路由流程图

```mermaid
graph TD
    A[用户输入 User Input] --> B[上下文组装 Context Assembly]
    B --> C{是否显式指定智能体<br/>agent_id / agent_name / 专家模式}
    C -- 是 --> D[直接加载指定 Agent<br/>route_hints.direct_agent_selection=true]
    C -- 否 --> H1{启发式短路}
    H1 -- 纯问候 --> G1[通用助手 无 LLM]
    H1 -- 联网/外部搜索 --> G1
    H1 -- 上轮 ChatBI 但本轮非数据粘性 --> G1
    H1 -- 否 --> E[RouterService LLM 路由]
    E --> F{LLM 决策结果}
    F -- 匹配成功 --> G[路由至主 Agent]
    F -- 低置信 / 未知 / 异常 --> H[兜底路由: 通用对话助手]
    D --> I[进入 Executor 内部流程]
    G --> I
    G1 --> I
    H --> I
```

## 2. 逻辑详解

### 2.1 上下文感知 (Context Awareness)
- **机制**：系统在路由阶段会自动提取当前会话的最近 **6 轮** 对话历史。
- **作用**：解决多轮对话中的指代消解问题（如用户问“那里的温度呢？”中的“那里”）。
- **实现**：在 `RouterService.route_query` 接口中通过 `history` 参数接收上下文。
- **边界**：多轮上下文用于避免把连续追问路由到错误智能体；进入 ChatBI 后，是否新查数、是否复用上一轮结果，由 ChatBI 执行器内部再次分析。

### 2.2 LLM 语义路由 (LLM Semantic Routing)
- **模块**：`RouterService`
- **提示词**：`app/services/ai/router_service.py` 内置 `DEFAULT_SYSTEM_PROMPT`
- **输入数据**：
    - **Agent 列表**：从数据库 `ai_agents` 实时获取所有可用智能体的名称、描述和能力标签。
    - **会话上下文**：最近历史对话记录，并带上一轮处理智能体。
    - **当前 Query**：用户最新的提问。
- **决策逻辑**：LLM 扮演高级调度员角色，阅读所有智能体的“简历”后，决定哪个 Agent 的能力最能满足当前用户意图。
- **输出结构**：
    - `agent_id`：主智能体 ID。
    - `secondary_agents`：可选辅助智能体 ID 列表，仅用于明确复合意图。
    - `confidence`：主智能体选择置信度。
    - `reasoning`：指代消解、会话连续性和命中理由。
    - `turn_labels`：通用会话标签，如 `new_business_request`、`continuation_followup`、`topic_switch`、`context_action`、`meta_action`、`business_related`、`same_topic`、`multi_intent`、`general_chat`、`ambiguous`。
    - `relation_to_previous`：与上一轮关系，如 `new_topic`、`followup`、`topic_switch`、`standalone`、`unknown`。
    - `user_action_type`：通用动作类型，如 `ask_business_data_or_task`、`ask_knowledge`、`transform_context`、`save_or_export_context`、`manage_agent_or_skill`、`chat`、`unknown`。

这些通用标签只作为后续 executor 的参考 hint。是否采用、如何采用，由各智能体 executor 自己决定：

- `AssistantExecutor` 会把标签注入为弱 system hint，帮助 LLM 理解本轮是否追问、是否上下文动作、是否话题切换；但不基于标签写死业务分支，最终仍由完整对话和模型判断。
- `DataQueryExecutor` 不消费这些通用标签来决定 ChatBI 查数流程。ChatBI 仍会在执行器内部执行自己的 `DataQueryTurnClassifier` 请求类别分析。
- `KnowledgeExecutor` 由 `TurnType=KNOWLEDGE` 驱动，不依赖路由标签硬分支。

### 2.4 启发式短路（无 LLM）

在调用路由 LLM 之前，`RouterService.route_query` 会按顺序尝试以下短路（实现见 `router_service.py` + `intent_service.py`）：

| 短路 | 判定函数 | 目标 | 说明 |
|------|----------|------|------|
| 纯问候 | `looks_like_greeting()` | 通用助手 | 短句寒暄/自我介绍/致谢，且无复合业务词 |
| 联网/外部搜索 | `looks_like_web_search_query()` | 通用助手 | 明确公网/新闻/搜索引擎语义，走 `web_search` 等工具 |
| ChatBI 会话粘性打断 | 上轮为 data_query 智能体 **且** `not should_inherit_data_agent_session()` | 通用助手 | 避免「上一轮查 PUE、本轮问 GitHub 小星星」仍粘 ChatBI |

**`should_inherit_data_agent_session()`** 正向条件（满足其一即**沿用** ChatBI 会话）：

- 对已有查数结果的加工追问（`looks_like_data_followup` / `looks_like_pure_result_followup`）；
- 仍含明确内部业务库查数信号（`looks_like_strong_business_data_request`）。

反向排除：元操作、上下文动作、技能执行类请求不继承 ChatBI 粘性。

LLM 路由阶段的历史上下文（`_build_history_context`）会在「应打断 ChatBI 粘性」时注入 **禁止机械沿用** 提示，避免模型仅因上一轮是数据智能体就继续选 ChatBI。

### 2.5 专家直选与 Guard 边界

当请求携带 `agent_id`、`agent_name`，或 Embed **专家模式**选定智能体时：

- `AgentService` 设置 `route_hints.direct_agent_selection = True`；
- **跳过**自动路由 LLM（直接加载指定智能体）；
- 主通用助手的**数据反幻觉 Guard 不启用**（专家/指定智能体按自身能力回答，含 Bash、联网等）。

### 2.6 兜底机制 (Fallback Strategy)
- 当路由系统无法做出明确决策，或后端服务出现异常时，请求将统一分发至 **`general-chat`（通用助手 Agent，执行层为 AssistantExecutor）**。
- 确保系统始终有响应，不中断用户体验。

## 3. 关键组件与位置

- **路由逻辑入口**：`app/services/ai/router_service.py` -> `route_query()`
- **通用意图/追问启发式模块**：`app/services/ai/intent_service.py`
- **路由提示词定义**：`app/services/ai/router_service.py`
- **智能体元数据**：数据库 `ai_agents` 表

## 4. 相关配置与测试

| 配置键 | 默认值 | 说明 |
|--------|--------|------|
| （无 DB 项） | — | 路由 `DEFAULT_SYSTEM_PROMPT` 写死在 `router_service.py` |

测试映射：

- `tests/services/ai/test_router_service.py` — 问候/联网短路、ChatBI 会话打断、LLM 路由与 fallback
- `tests/ai/test_router_context.py` — 长对话上下文与指代

## 5. 优化方向

1. **动态热更新**：支持在不重启服务的情况下，通过修改 `ai_agents` 表的描述实时影响路由倾向。
2. **多意图并行**：未来计划支持单条指令拆分为多个任务并由不同智能体协同处理（Orchestration 增强）。
