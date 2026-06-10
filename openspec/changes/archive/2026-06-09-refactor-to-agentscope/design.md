## 上下文

本设计不沿用旧 LangChain 执行器，也不做长期双路径兼容。重构完成后，AI 运行时只保留 AgentScope。为了降低一次性替换的不可控风险，实现过程必须先建立平台中立边界，再替换底层实现，最后清理 LangChain。每个阶段都要求有自动化测试证明当前边界可用。

## 设计原则

1. **外部协议稳定**：前端 SSE、聊天 API、审计查询、历史会话、工具管理入口保持不变。
2. **内部抽象中立**：业务层不直接依赖 AgentScope 具体事件或消息类，统一通过平台 runtime DTO 交互。
3. **工具优先迁移**：先把所有工具从 LangChain 对象迁移为平台中立定义，再包装到 AgentScope Toolkit。
4. **ChatBI 安全优先**：ChatBI 的权限、SQL 安全、空结果复查、错误自纠、最终总结必须作为显式状态机迁移，不能只依赖提示词。
5. **最终彻底清理**：交付态必须删除 LangChain 依赖、导入、旧执行器和测试替身。

## 目标架构

### 运行时分层

新增目录：`app/services/ai/runtime/agentscope/`

- `models.py`：创建 AgentScope `ChatModelBase`，封装模型管理表、系统配置、debug override、主模型/总结模型选择。
- `messages.py`：定义平台中立 `RuntimeMessage`、`RuntimeContentBlock`、`RuntimeToolCall`、`RuntimeToolResult`，负责历史、多模态附件、系统指令和工具结果转换。
- `tools.py`：定义 `RuntimeToolSpec` 和 AgentScope Tool 包装器，统一静态工具、通用 API 工具、MCP 工具、Jira 工具、知识库工具、记忆工具。
- `events.py`：将 AgentScope `EventType` 映射为现有 SSE chunk 和审计 trace。
- `permissions.py`：把 `DataPermissionMiddleware`、SELECT-only 校验、工具级权限检查接到 AgentScope 工具执行前后。
- `workspace.py`：管理 `LocalWorkspace`，按 `trace_id` / `conversation_id` 隔离临时文件和工具执行上下文。
- `errors.py`：统一模型失败、工具失败、权限拒绝、超时、取消和用户可读错误文案。

业务执行器不再直接 import AgentScope 或 LangChain，而是依赖 runtime 层提供的 runner、toolkit、event adapter。

### 执行器替换

旧目录 `app/services/ai/executors/` 保留模块边界，但替换实现：

- `assistant_executor.py`：改为薄封装，调用 `AssistantAgentRunner`。保留现有“无工具直接回答 / 有工具 ReAct / 工具结果总结 / citation”语义。
- `data_executor.py`：改为薄封装，调用 `DataAgentRunner`。ChatBI 内部采用 AgentScope native Agent + Toolkit，并辅以显式运行状态守卫：
  1. `QuestionNormalizer`：判断追问、复用上一轮结构化结果、生成独立查询。
  2. `ContextPreparation`：构建 dataset menu、上下文动作和 few-shot/schema 检索词。
  3. `NativeAgent`：通过 AgentScope ReAct + ChatBI Toolkit 搜索数据集、表、字段、指标口径并生成 SQL。
  4. `RuntimeGuard`：守卫 schema-before-sql、执行前计划、SQL 错误、空结果、schema miss。
  5. `SqlExecution`：调用现有数据 API / 本地 SQL 工具。
  6. `Synthesis`：生成最终中文回答、追问总结、结构化结果摘要和兜底。
- `rag_executor.py`：保持现有 RAGFlow 直连实现，不新增 `RagAgentRunner`；仅确认 SSE、citation、错误事件兼容并清理 LangChain 残留。
- `openclaw_executor.py`：保持现有 OpenClaw API 代理实现，不新增 `OpenClawAgentRunner`；仅确认任务状态、日志、错误和总结事件兼容并清理 LangChain 残留。
- `common.py`：只保留平台中立的历史转换、多模态处理、token usage 抽取和 SSE 工具函数；删除 LangChain Message 相关逻辑。

### 模型与提示词

- `app/core/llm/client.py` 不再返回 `ChatOpenAI`，改为返回 AgentScope `OpenAIChatModel` 或公司网关兼容模型。
- `AgentConfigProvider` 保留现有配置优先级：runtime override、debug override、agent config、系统配置、模型管理表。
- Prompt 内容不做业务改写，只迁移注入位置：
  - 系统提示词作为 AgentScope agent `system_prompt`。
  - 动态约束作为 runtime message 或 middleware instruction 注入。
  - 最终总结模型使用独立 model factory 和独立 agent。

### 工具迁移

工具迁移分两层：

1. **工具定义层**：将现有工具统一抽象为 `RuntimeToolSpec`，字段包括 `name`、`description`、`parameters_schema`、`source_type`、`permission_scope`、`callable`、`timeout_seconds`。
2. **AgentScope 适配层**：将 `RuntimeToolSpec` 包装为 AgentScope Tool，并在执行前后统一记录审计、权限、耗时、错误、引用和上下文更新。

所有现有工具源必须迁移：

- 静态 Python 工具：`data_api.py`、`knowledge_tool.py`、`memory_*`、`system_*`、`dashboard_tools.py`、`task_manager_tools.py`。
- 类工具：Jira、通知工具。
- 动态工具：`generic_api.py`、`mcp_factory.py`。
- 工具注册中心：`registry.py` 改为返回 runtime tool spec，不再返回 LangChain Tool。

### 事件与审计

AgentScope event 必须转换为现有事件：

- `TEXT_BLOCK_DELTA` -> `{"content": "..."}`
- `THINKING_BLOCK_*` -> `{"type": "thinking", "status": "continuing"}`
- `TOOL_CALL_START` / `TOOL_CALL_END` -> `{"type": "log", "category": "tool", ...}`
- `TOOL_RESULT_*` -> 工具完成 log，必要时生成 `citation` 或 `context_update`
- `MODEL_CALL_END` -> token usage 写入 trace
- `REQUIRE_USER_CONFIRM` -> 当前阶段先转为权限拒绝或平台错误，不引入前端新交互
- 异常事件 -> `{"type": "error", "content": "..."}`

审计表保持不变，新的 trace 仍写入 `router`、`thought`、`tool_call`、`synthesis` 等现有 `event_type`，避免后台日志页面同步大改。

### 部署与依赖

- Docker Python 基础镜像升级到 `python:3.11-slim` 或更高。
- `requirements.txt` 新增 AgentScope 及需要的 extras：至少覆盖 model、storage、service、workspace 能力。
- 迁移完成后移除 `langchain-core`、`langchain-community`、`langchain-openai`、`langchain`。
- Redis 继续使用当前部署；AgentScope Redis key 必须带平台前缀，避免污染现有业务 key。

## 迁移顺序

1. 建立 runtime DTO、AgentScope 模型工厂、消息转换、事件转换的测试。
2. 迁移工具注册中心，让工具先变成平台中立 `RuntimeToolSpec`。
3. 实现 AgentScope Toolkit 包装器和工具执行审计。
4. 重建通用对话 executor。
5. RAG executor 保持现有 RAGFlow 直连实现，做兼容性测试和 LangChain 残留清理。
6. OpenClaw executor 保持现有 API 代理实现，做兼容性测试和 LangChain 残留清理。
7. 重建 ChatBI DataAgentRunner，逐项迁移现有 DataQueryExecutor 的安全与纠偏能力。
8. 将 `AgentService`、router、intent、prompt ops、summarizer 等剩余 LangChain LLM 调用切到新模型接口。
9. 替换测试桩，更新 `tests/CHECKLIST.md`。
10. 删除 LangChain 依赖和旧代码，执行全量搜索与测试。

## 风险与应对

- **ChatBI 回归风险最高**：用 `DataAgentRunner` 显式状态守卫和现有测试迁移降低风险，每个旧关键行为必须映射到新 runner 测试。
- **AgentScope API 变动风险**：通过 runtime 层隔离业务代码，业务执行器不直接依赖 AgentScope 细节。
- **Python 版本风险**：先升级 Docker 和 CI/本地环境，再安装 AgentScope。
- **SSE 前端兼容风险**：事件适配器先写快照测试，确保 chunk 格式不变。
- **工具参数兼容风险**：工具注册中心迁移时保留原始 JSON schema，并对每类工具写调用测试。
