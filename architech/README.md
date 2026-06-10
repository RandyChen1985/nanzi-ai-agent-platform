# 云枢智能体平台 · 架构文档

## 对话与提示词（推荐入口）

**[design/chat/](./design/chat/README.md)** — 聊天端到端流程、提示词分层、`PLATFORM_GLOBAL_SYSTEM_PROMPT`

## 设计（`design/`）

| 文档 | 说明 |
|------|------|
| [AGENTSCOPE_RUNTIME.md](./design/AGENTSCOPE_RUNTIME.md) | **AgentScope 运行时**（事件映射、工具、权限、ChatBI 守卫） |
| [tool-call.md](./design/tool-call.md) | 工具调用协议与 SSE 格式 |
| [GLOBAL_SYSTEM_OVERVIEW.md](./design/GLOBAL_SYSTEM_OVERVIEW.md) | 全局系统概览 |
| [AI_AGENT_SYSTEM_DESIGN.md](./design/AI_AGENT_SYSTEM_DESIGN.md) | AI 智能体系统设计（偏早期总览，含历史 LangChain 章节） |
| [AGENT_ROUTING_DESIGN.md](./design/AGENT_ROUTING_DESIGN.md) | 智能体路由 |
| [AGENT_APP_DESIGN.md](./design/AGENT_APP_DESIGN.md) | Embed / V1 API |
| [agent_execution_flow_review.md](./design/agent_execution_flow_review.md) | 执行流评审（K1/K2/K3） |
| [CHAT_BI_DESIGN.md](./design/CHAT_BI_DESIGN.md) | ChatBI |
| [redis_key_design.md](./design/redis_key_design.md) | Redis Key |

## 提示词草稿（`prompts/`）

运营侧 Markdown 草稿与归档，见 [prompts/README.md](./prompts/README.md)。

## Schema / API（`meta-schemal/`、`api-schemal/`、`tools-schemal/`）

元数据样例、外部 API 说明、工具 Schema 等。
