## 为什么

当前 AI 执行层已经深度依赖 LangChain：模型工厂返回 `ChatOpenAI`，执行器使用 LangChain Message、Tool、`bind_tools()`、`astream()` 组织 ReAct 循环，工具注册也以 `@tool`、`StructuredTool`、`BaseTool` 为核心抽象。随着 ChatBI、RAG、通用对话、MCP、技能发现、多模态、审计 trace 等能力逐渐叠加，执行器内部承担了过多状态管理、事件转换、工具调用和业务纠偏逻辑，继续在现有执行器上追加能力会让维护成本和回归风险持续上升。

本变更选择在独立分支上进行一次性底座替换：以 AgentScope 2.0 作为新的 AI 运行时，重建模型、消息、工具、事件、权限、执行器和测试体系。目标交付态不保留 LangChain 执行路径，不再依赖 `langchain-core`、`langchain-community`、`langchain-openai`、`langchain` 运行核心 AI 流程。

## 变更内容

- **运行时替换**：新增 `app/services/ai/runtime/agentscope/`，统一封装 AgentScope 模型、消息、工具、事件、权限和执行上下文。
- **模型工厂替换**：将 `app/core/llm/client.py` 与 `AgentConfigProvider` 从 LangChain `ChatOpenAI` 切换为 AgentScope `ChatModelBase`，继续兼容现有模型管理、环境变量、debug override、主模型/总结模型配置。
- **消息结构替换**：用平台内部中立消息结构承接历史、附件、多模态内容、系统指令和工具结果，再转换为 AgentScope `Msg` / block 格式。
- **工具体系替换**：将静态工具、通用 API 工具、MCP 工具、Jira 工具、知识库工具、记忆工具、系统执行工具统一包装为 AgentScope `ToolBase` / `Toolkit`，不再暴露 LangChain Tool 对象。
- **执行器重建**：
  - 通用对话重建为 `AssistantAgentRunner`。
  - ChatBI 重建为 `DataAgentRunner`，以 AgentScope native Agent + Toolkit 和显式运行状态守卫承接现有 ChatBI 约束。
  - RAG 不重建 Runner，保留现有 RAGFlow 直连能力和引用事件，仅清理 LangChain 残留并做兼容性验证。
  - OpenClaw 不重建 Runner，保留现有 OpenClaw API 代理与总结链路，仅清理 LangChain 残留并做兼容性验证。
- **事件协议兼容**：新增 AgentScope Event 到现有 SSE chunk 的适配层，前端 `/api/v1/chat/completions` 协议、日志事件、引用事件、`context_update`、结束事件保持兼容。
- **审计与 trace 兼容**：保留 `AgentExecutionHistory`、`AgentExecutionTrace` 数据结构，新的 AgentScope 事件必须写入现有审计表。
- **部署升级**：将 Python 容器基础镜像升级到 AgentScope 支持的 Python 3.11+，补齐 AgentScope storage/service/workspace 相关依赖。
- **彻底清理**：所有运行路径切到 AgentScope 后，移除 LangChain 依赖、导入、测试桩和旧执行器实现。

## 非目标

- 不改前端聊天协议和主要交互入口。
- 不改 MySQL 业务表结构，除非后续单独提出审计字段或运行时配置字段变更。
- 不直接查询底层业务数据；ChatBI 仍遵循现有数据 API、本地 SQL 权限、行级权限和 SELECT-only 约束。
- 不重写业务智能体的提示词内容；只迁移承载方式、运行时注入方式和工具能力描述。

## 影响范围

- 后端 AI 核心：`app/core/llm/`、`app/services/ai/`、`app/services/ai/tools/`、`app/api/v1/endpoints/chat.py`。
- 测试：`tests/ai/` 中依赖 LangChain Message 和 Tool 的测试需要整体替换为 AgentScope 或平台中立测试桩。
- 部署：`requirements.txt`、`docker/Dockerfile`、相关启动/诊断脚本。
- 文档：OpenSpec、`tests/CHECKLIST.md`、架构文档中的 LangChain 描述。

## 成功标准

- `rg "langchain|LangChain|ChatOpenAI|StructuredTool|BaseTool|ToolMessage|SystemMessage|HumanMessage|AIMessage" app tests` 不再命中运行代码和测试代码中的 LangChain 依赖。
- `/api/v1/chat/completions` 的流式输出对前端保持兼容。
- 通用对话、ChatBI 通过 AgentScope/runtime 自动化测试；RAG、OpenClaw 保持现有实现并通过兼容性测试。
- ChatBI 保留现有关键安全与准确性能力：数据集权限、行级权限、SELECT-only、SQL 错误自纠、空结果复查、最终总结、图表/结构化结果输出。
- Docker 镜像使用 Python 3.11+ 并可安装 AgentScope 所需依赖。
