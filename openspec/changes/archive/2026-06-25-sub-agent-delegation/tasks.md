## 1. 运行时上下文与隔离机制 (Context & Isolation)

- [x] 1.1 在 `AgentContext` 中扩展 `delegation_depth` 深度属性，用于防范子代理的循环调用。
- [x] 1.2 实现子代理独立 `messages` 上下文的组装函数，确保不夹带主智能体长会话历史。

## 2. 子代理委托核心工具实现 (Sub-Agent Tool Implementation)

- [x] 2.1 新建 `app/services/ai/tools/agent_delegate_tool.py` 并注册 `sub_agent_call` 工具。
- [x] 2.2 在工具内部通过 `AgentDispatcher.dispatch` 动态获取目标子代理的 Executor 并发起流式执行。
- [x] 2.3 在工具内加入 `asyncio.wait_for` 处理子代理任务超时机制，并返回友好出错结果。

## 3. 日志流实时穿透 (SSE Log Forwarding)

- [x] 3.1 改造工具消费子 Executor 流的逻辑，实时拦截并过滤 `type: "log"` 事件包。
- [x] 3.2 对拦截到的日志包重写标题（前缀加上 `[{子代理展示名}]`），并实时 Yield 穿透传递给主 SSE 消息通道。

## 4. 输出内容过滤清洗 (Output Sanitization)

- [x] 4.1 编写正则表达式过滤函数，专门清除子代理（如 ChatBI）输出中的 `<sql_plan>...</sql_plan>` 等图形化卡片标签。
- [x] 4.2 对子代理的最终返回文本设置最大字数截断防护，防止主助手上下文瞬间溢出。

## 5. 工具注册与提示词增强 (Registry & Prompt Nudges)

- [x] 5.1 在 `app/services/ai/tools/registry.py` 中注册 `sub_agent_call` 工具为系统隐式工具。
- [x] 5.2 在 `app/services/ai/agent_prompts.py` 中配置提示词模板，让主助手在 System Prompt 中动态感知可用子代理清单并掌握其调用规范。

## 6. 测试与集成验证 (Testing & Verification)

- [x] 6.1 在 `tests/` 目录下编写子代理委托调用的集成单元测试。
- [x] 6.2 运行测试套件，验证隔离、深度限制、日志合并和去噪能力。
