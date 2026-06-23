# AgentScope 运行时架构

> 自 2026-06 起，本地智能体（Assistant / Knowledge / ChatBI）执行层已迁移至 **AgentScope 2.x**，不再依赖 LangChain。RAGFlow、OpenClaw 仍保持各自直连实现。

## 1. 分层结构

```text
AgentService.chat_completion_stream()
  → AgentDispatcher.dispatch()
    → KnowledgeExecutor    → KnowledgeAgentRunner  → AgentScope Agent + 自动知识库检索
    → DataQueryExecutor    → DataAgentRunner       → AgentScope Agent + ChatBI 守卫
    → AssistantExecutor    → AssistantAgentRunner  → AgentScope Agent
    → RAGExecutor          → RAGFlow 直连
    → OpenClawExecutor     → OpenClaw API 代理
```

**薄 Executor**：`assistant_executor.py`、`knowledge_executor.py`、`data_executor.py` 仅转发到 Runner。

**Runner**：`assistant_agent_runner.py`、`knowledge_agent_runner.py`、`data_agent_runner.py` 负责消息准备、工具解析、事件流消费、状态持久化。

**Runtime 层**：`app/services/ai/runtime/agentscope/` — 模型、消息、工具、事件映射、权限挂起、会话锁、AgentState 存储。

## 2. AgentScope Agent 生命周期

1. `ToolRegistry.get_runtime_tools()` → `RuntimeToolSpec[]`
2. `build_toolkit(specs)` → AgentScope `Toolkit`（`AgentScopeRuntimeTool` 包装）
3. `Agent(name, system_prompt, model, toolkit, react_config=ReActConfig(max_iters=...))`
4. `async for event in agent.reply_stream(inputs):` — `inputs` 为 `UserMsg` / 历史 `Msg`，或恢复事件 `UserConfirmResultEvent`
5. `map_standard_agentscope_event()` 将事件转为平台 SSE chunk
6. 正常结束 → `agent_state_store.save()`；挂起 → `pending_agentscope_confirmations.register()`
7. 拦截统计 → `ModelCallStatsMiddleware` 拦截并异步向 Redis 写入 Token 消耗与时间戳数据

**会话锁**：`agentscope_session_lock.hold()` 防止同会话并发破坏 AgentState。

**runner_type 持久化**：Assistant 为 `"assistant"`（resume 仍兼容历史 `"general"`）。

## 3. 事件映射（AgentScope → SSE）

实现：`app/services/ai/runtime/agentscope/event_stream.py`

| AgentScope 事件 | 平台 SSE |
|-----------------|----------|
| `TEXT_BLOCK_DELTA` | `{ "content": "..." }` |
| `TOOL_CALL_START/DELTA` | 累积参数；`type: log` 待执行 |
| `TOOL_RESULT_*` + `TOOL_RESULT_END` | 工具完成 log、trace、可选 `tool_result_data` |
| `REQUIRE_USER_CONFIRM` | `permission_required` |
| `REQUIRE_EXTERNAL_EXECUTION` | `external_execution_required` |
| `REPLY_START/END` | `agent_reply` |
| `MODEL_CALL_START/END` | `model_call`（含 token、耗时）；流式结束时中间件将其异步追加写入 Redis 统计列表 |
| `THINKING_BLOCK_*` | `thinking` |
| `EXCEED_MAX_ITERS` | 超出步数提示文案 |

前端统一处理：`frontend/src/utils/agentscopeSseHandlers.ts` → `dispatchAgentscopeStreamEvent()`。

## 4. 工具体系

| 环节 | 路径 | 说明 |
|------|------|------|
| 业务函数 | `app/services/ai/tools/*.py` | `@tool`（`tool_compat.py`，非 LangChain） |
| 注册中心 | `app/services/ai/tools/registry.py` | `get_runtime_tool()` → `RuntimeToolSpec` |
| 运行时包装 | `app/services/ai/runtime/agentscope/tools.py` | `AgentScopeRuntimeTool`、`build_toolkit()` |
| ChatBI 工具集 | `app/services/ai/runtime/agentscope/data_tools.py` | schema / SQL / dashboard |

`RuntimeToolSpec` 字段：`name`、`description`、`parameters_schema`、`callable`、`permission_scope`（read / ask / dangerous）。

## 5. Assistant Agent 流程摘要

- **无工具**：`synthesis_llm.astream()` 直出，不走 AgentScope Agent。
- **有工具**：AgentScope ReAct；仅挂载 agent 配置的工具 + 系统隐式工具。
- **ASK 工具挂起**：用户确认后 `POST /api/v1/chat/permissions/{id}/confirm` → `UserConfirmResultEvent` → 继续 `reply_stream`。
- **不绑定** `search_knowledge_base`（知识库走 KnowledgeExecutor）。

## 6. Knowledge Agent 流程摘要

- ReAct 前平台自动 `search_knowledge_base`，结果注入 prompt。
- 继承 Assistant 的 AgentScope 流式与 citation 解析。
- 可扩展挂载 agent 配置的业务工具。

## 7. ChatBI 流程摘要

在 AgentScope ReAct 之上，`DataRunState`（`chatbi/run_state.py`）显式守卫；实现分布在 `app/services/ai/runners/chatbi/`，Runner 仅编排：

- 新查数：平台先生成 `DataQueryIntentFrame`（`schema_prefetch`），派生/校验 `get_dataset_schema` 检索词，最后 `execute_sql_query`
- **SqlQueryBinding**：Schema 解析为 `table_bindings` → execute 前 `build_sql_query_binding` → Gate 预检（`resolve_sql_schema_preflight_with_binding`）→ ContextVar → `execute_sql_query_core` 复用同一份 binding 做表归属/权限/字段/data_perm 校验
- `DataQueryIntentFrame` 注入 ReAct 上下文用于字段绑定自检，并在“用户需求分析”trace 卡片展示
- 查数完成前拦截最终回答（`react_stream`，`blocked_content`）
- SQL 错误 / 空结果 / 缺 SQL 计划 → `repair_policy` + `native_turn` 再跑 `reply_stream`；空结果修复结合意图帧
- 复用上一轮结果 → `turn_handlers` + `synthesis`，跳过 Agent

模块索引：`app/services/ai/runners/chatbi/README.md`

详见 [CHAT_BI_DESIGN.md](./CHAT_BI_DESIGN.md)、[CHATBI_GUARDS_REVIEW.md](./CHATBI_GUARDS_REVIEW.md)。

## 8. 相关文档

| 文档 | 说明 |
|------|------|
| [chat/CHAT_FLOW.md](./chat/CHAT_FLOW.md) | 端到端聊天流程 |
| [tool-call.md](./tool-call.md) | 工具调用协议 |
| [../tools-schemal/README.md](../tools-schemal/README.md) | 工具开发与注册 |
| `openspec/changes/archive/2026-06-09-refactor-to-agentscope/` | 迁移提案与设计 |

*文档版本：2026-06-23（SqlQueryBinding 统一绑定后更新）*
