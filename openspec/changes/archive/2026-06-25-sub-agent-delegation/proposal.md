## Why

当前系统缺乏子代理（Sub-agent）静默委派调用的原生支持。当通用智能体（Main Agent）需要处理诸如 ChatBI 查数、知识库检索等垂直业务时，无法在后端通过工具链的方式静默唤起另一个专有业务智能体去执行并拉取结果。这导致了以下问题：
1. **上下文污染与 Token 浪费**：如果直接在主会话中集成所有工具，主会话历史中会混杂大量的 SQL 语句、表结构信息及底层 ReAct 思考步骤，大幅消耗 Token 并容易混淆大模型。
2. **多智能体协作不自然**：现有的多智能体协同（`_execute_multi_agent`）是前端并行的，不符合由主助手统一控制、静默派发子任务的交互习惯。

因此，亟需引入子代理委派机制，允许主助手以“工具”形式在后台唤起子智能体，执行隔离任务并去噪返回结果，同时将运行步骤以流式日志实时反馈给前端。

## What Changes

1. **新增静默委派系统工具**：在 `app/services/ai/tools/` 目录下新增 `sub_agent_call(agent_name: str, query: str)` 系统工具，主助手可以在提问中直接调用此工具。
2. **多会话上下文隔离**：调用子代理时，在后端为其分配全新的消息列表（仅含其专属 System Prompt 与主助手派发的 Query），保证其与主对话历史完全解耦，防止上下文交叉污染。
3. **流式日志穿透**：通过修改日志输出管道，将子代理在运行期间吐出的步骤日志（`type: "log"`）进行捕获和重写（例如加上 `[子代理-名称]` 标识），并实时穿透合并发送到当前主会话的 SSE 消息流中。
4. **输出内容去噪与清洗**：在子代理最终结果返回给主助手前，使用过滤器剥离 `<sql_plan>` 等图形化卡片标签（或转换为易读文本），避免干扰主助手的理解。

## Capabilities

### New Capabilities
- `sub-agent-delegation`: 负责子智能体的后端动态挂载、独立上下文运行、流式进度日志拦截与穿透，以及输出结果去噪。

### Modified Capabilities
无

## Impact

1. **新增核心工具**：在 `app/services/ai/tools/` 下新建 `agent_delegate_tool.py`。
2. **工具注册**：在 `app/services/ai/tools/registry.py` 中将该委派工具注册为隐式系统工具，对主助手默认启用。
3. **提示词增强**：在 `app/services/ai/agent_prompts.py` 中，支持在主助手 System Prompt 拼装中动态包含子智能体清单及使用说明。
4. **Executor 适配**：保证 Executor 能够在非 API 入口直接运行且能安全 yield 步骤日志包。
