## ADDED Requirements

### Requirement: AgentScope 作为唯一 AI 运行时

系统完成重构后，AI 执行链路 MUST 以 AgentScope 作为唯一运行时，不得继续依赖 LangChain 执行器、LangChain Message、LangChain Tool 或 `ChatOpenAI`。

#### Scenario: 运行代码无 LangChain 依赖

- **当** 开发者在 `app` 与 `tests` 目录搜索 LangChain 相关关键字
- **那么** 不应在运行代码或自动化测试中发现 `langchain`、`ChatOpenAI`、`StructuredTool`、`BaseTool`、`ToolMessage`、`SystemMessage`、`HumanMessage`、`AIMessage` 等 LangChain 依赖
- **并且** `requirements.txt` 不应包含 `langchain-core`、`langchain-community`、`langchain-openai`、`langchain`

#### Scenario: 运行时入口唯一

- **当** 用户通过 `/api/v1/chat/completions` 发起通用对话或 ChatBI 请求
- **那么** 请求必须进入 AgentScope runtime 与对应 runner
- **并且** 不得调用旧 LangChain executor 中的 `bind_tools()` 或 `astream()` 路径

#### Scenario: RAG 与 OpenClaw 保持现有代理实现

- **当** 用户通过 `/api/v1/chat/completions` 发起 RAG 或 OpenClaw 请求
- **那么** 系统应保持现有 RAGFlow / OpenClaw API 代理实现
- **并且** 不要求新增 `RagAgentRunner` 或 `OpenClawAgentRunner`
- **并且** 不得重新引入 LangChain 执行路径

### Requirement: 平台中立运行时边界

系统 MUST 提供平台中立的消息、工具、事件、错误和 workspace 抽象，业务执行器不得直接依赖 AgentScope 具体对象。

#### Scenario: 消息转换

- **当** 会话历史包含 system、user、assistant、tool 结果和图片附件
- **那么** runtime 必须先转换为平台中立消息结构
- **并且** 再由 AgentScope adapter 转换为 AgentScope `Msg` 与内容 block

#### Scenario: 工具转换

- **当** 工具来源是静态 Python 工具、通用 API 工具、MCP 工具或类工具
- **那么** 工具注册中心必须返回统一的 `RuntimeToolSpec`
- **并且** AgentScope Toolkit 只能从 `RuntimeToolSpec` 构建

### Requirement: 现有 SSE 协议兼容

AgentScope event MUST 在返回前转换为现有 SSE JSON chunk，前端无需因为底层运行时替换而修改聊天渲染逻辑。

#### Scenario: 文本增量

- **当** AgentScope 发出 `TEXT_BLOCK_DELTA`
- **那么** 服务端必须输出现有 `{"content": "..."}` chunk

#### Scenario: 工具日志

- **当** AgentScope 发出工具开始、工具结束或工具失败事件
- **那么** 服务端必须输出现有 `{"type": "log", ...}` chunk
- **并且** 工具名称、参数摘要、状态、耗时应写入审计 trace

#### Scenario: 引用和上下文更新

- **当** 工具结果包含知识库引用或结构化上下文更新
- **那么** 服务端必须继续输出现有 `citation` 或 `context_update` chunk

### Requirement: 审计 trace 兼容

AgentScope 运行时 MUST 继续写入现有审计表和事件类型，后台日志查询接口不得因运行时替换失效。

#### Scenario: 审计记录生成

- **当** 一次会话经历路由、模型思考、工具调用和最终总结
- **那么** 系统必须写入 `router`、`thought`、`tool_call`、`synthesis` 等现有类型的 trace
- **并且** `/api/v1/chat/logs/{trace_id}` 能按原格式查询

### Requirement: Python 与 AgentScope 部署兼容

系统部署环境 MUST 满足 AgentScope 运行要求。

#### Scenario: Docker 运行时版本

- **当** 构建后端 Docker 镜像
- **那么** Python 基础镜像必须是 Python 3.11 或更高版本
- **并且** AgentScope model、tool、event、permission、workspace、Redis storage/message bus 相关模块可 import
