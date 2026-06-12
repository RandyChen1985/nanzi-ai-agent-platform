# 对话与提示词架构文档

本目录描述**从用户发消息到模型回复**的端到端流程，以及**提示词如何分层、拼接、注入**。以当前代码为准（含 `PLATFORM_GLOBAL_SYSTEM_PROMPT`、通用请求分类 `turn_classifier`、ChatBI 请求类别分析 `data_query_turn_classifier`）。

| 文档 | 说明 |
|------|------|
| [CHAT_FLOW.md](./CHAT_FLOW.md) | 业务流：前端 → API → AgentService → Dispatcher → Executor → Runner；Redis 记忆、`memory_search`、权限 |
| [PROMPT_LAYERS.md](./PROMPT_LAYERS.md) | 提示词流：`system_prompt` 栈、独立 system 消息、执行器追加、路由/意图 LLM |
| [../AGENTSCOPE_RUNTIME.md](../AGENTSCOPE_RUNTIME.md) | AgentScope 事件映射、工具挂起恢复、AgentState |

## 相关文档（其他目录）

| 文档 | 说明 |
|------|------|
| [../agent_execution_flow_review.md](../agent_execution_flow_review.md) | 执行流评审与请求类别边界（非主规范，含历史 P0/P1 状态） |
| [../AGENT_ROUTING_DESIGN.md](../AGENT_ROUTING_DESIGN.md) | 智能体路由产品设计 |
| [../AGENT_APP_DESIGN.md](../AGENT_APP_DESIGN.md) | V1 API、Embed、`debug_options` |
| [../../prompts/README.md](../../prompts/README.md) | 运营侧智能体提示词草稿（ChatBI V8、DevOps V5 等） |

## 代码入口（速查）

- 编排：`app/services/ai/agent_service.py`
- Runner：`assistant_agent_runner.py`、`knowledge_agent_runner.py`、`data_agent_runner.py`
- AgentScope：`app/services/ai/runtime/agentscope/`
- 平台全局提示词：`app/services/ai/agent_prompts.py` → `PLATFORM_GLOBAL_SYSTEM_PROMPT`
- 执行器提示词：`app/services/ai/executors/prompts.py`
- 路由 / 意图：`router_service.py`、`intent_service.py`（内置代码，不进主对话 messages）

*文档版本：2026-06-09*
