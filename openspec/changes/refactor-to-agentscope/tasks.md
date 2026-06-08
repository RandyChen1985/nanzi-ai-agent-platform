## 1. 环境与依赖基线

- [x] 1.1 将 `docker/Dockerfile` Python 基础镜像升级到 Python 3.11+。
- [x] 1.2 在 `requirements.txt` 新增 AgentScope 运行所需依赖，并在最终清理阶段移除 LangChain 直接依赖。
- [x] 1.3 增加依赖安装验证：确认 AgentScope model、tool、event、permission、workspace、Redis storage/message bus 可 import。
- [x] 1.4 更新 `tests/CHECKLIST.md`，加入 AgentScope 全量替换验收项。

## 2. 建立平台中立运行时

- [x] 2.1 新建 `app/services/ai/runtime/__init__.py` 和 `app/services/ai/runtime/agentscope/` 包。
- [x] 2.2 实现 `messages.py`：定义平台中立消息、内容块、工具调用、工具结果 DTO。
- [x] 2.3 实现 `models.py`：封装 AgentScope 模型创建，保留现有模型配置优先级。
- [x] 2.4 实现 `events.py`：将 AgentScope event 映射为现有 SSE chunk。
- [x] 2.5 实现 `errors.py`：统一模型、工具、权限、超时、取消错误。
- [x] 2.6 实现 `workspace.py`：按 trace/conversation 隔离 AgentScope LocalWorkspace。
- [x] 2.7 为消息转换、模型配置、事件映射、错误转换、workspace key 生成写单元测试。

## 3. 工具体系迁移

- [x] 3.1 实现 `RuntimeToolSpec`，统一工具名称、描述、参数 schema、来源、权限范围、执行函数、超时配置。
- [ ] 3.2 将 `app/services/ai/tools/registry.py` 改为返回 `RuntimeToolSpec`，不再返回 LangChain Tool。
- [ ] 3.3 迁移 `@tool` 静态函数工具到 runtime tool spec。
- [ ] 3.4 迁移 `BaseTool` 类工具到 runtime tool spec。
- [ ] 3.5 迁移 `generic_api.py` 通用 API 工具到 runtime tool spec。
- [ ] 3.6 迁移 `mcp_factory.py` MCP 工具到 runtime tool spec。
- [x] 3.7 实现 AgentScope Toolkit 包装器，将 runtime tool spec 注册为 AgentScope Tool。
- [ ] 3.8 在工具包装器中注入权限检查、审计 trace、耗时、错误、citation、context_update。（已完成权限、耗时、错误审计基础；citation/context_update 仍待接入）
- [ ] 3.9 为静态工具、类工具、通用 API 工具、MCP 工具分别写调用测试。

## 4. 模型调用迁移

- [x] 4.1 重写 `app/core/llm/client.py`，返回 AgentScope 模型句柄。
- [x] 4.2 重写 `app/services/ai/config.py`，保留 `AgentConfigProvider` 对主模型、总结模型、debug override、模型管理表的兼容。
- [x] 4.3 迁移 router、intent、turn classifier、conversation summarizer、prompt ops 中的单次 LLM 调用。
- [x] 4.4 为模型优先级、缺少 API key、base_url/model_id 映射、总结模型 fallback 写单元测试。

## 5. 通用对话执行器重建

- [x] 5.1 新建 `GeneralAgentRunner`，承接通用对话 runner 边界。
- [ ] 5.2 保留无工具直接回答路径。
- [ ] 5.3 保留有工具 ReAct 路径、XML tool call 兜底兼容、工具结果总结。
- [ ] 5.4 保留知识库 citation、记忆检索强制调用、工具失败用户可读提示。
- [x] 5.5 将 `chat_executor.py` 改为调用 `GeneralAgentRunner`。
- [x] 5.6 迁移 `tests/ai/executors/test_chat_executor.py` 到 AgentScope/runtime 测试桩。

## 6. RAG 执行器重建

- [ ] 6.1 新建 `RagAgentRunner`，保留现有 RAGFlow 客户端调用方式。
- [ ] 6.2 将 RAGFlow answer、citation、log、error 事件转为现有 SSE chunk。
- [ ] 6.3 将 `rag_executor.py` 改为调用 `RagAgentRunner`。
- [ ] 6.4 迁移 RAG executor 测试，确保 citation 和 synthesis log 兼容。

## 7. OpenClaw 执行器重建

- [ ] 7.1 新建 `OpenClawAgentRunner`，保留现有 OpenClaw API 代理。
- [ ] 7.2 保留任务状态、日志、错误、最终总结事件。
- [ ] 7.3 将 `openclaw_executor.py` 改为调用 `OpenClawAgentRunner`。
- [ ] 7.4 增加 OpenClaw API 错误、SSE 非 JSON 行、正常回答的测试。

## 8. ChatBI DataTeam 重建

- [ ] 8.1 新建 `DataTeamRunner` 和阶段上下文对象，显式记录 question、metadata、sql、permission、result、review、synthesis。
- [ ] 8.2 迁移追问识别和独立查询改写逻辑。
- [ ] 8.3 迁移 dataset menu、授权数据集过滤、技能准备和必须读技能约束。
- [ ] 8.4 实现 `MetadataAgent`，承接 schema/指标/表字段检索。
- [ ] 8.5 实现 `SqlAgent`，承接 SQL 生成、计划说明、SQL 语法错误自纠。
- [ ] 8.6 实现 `PermissionReview`，承接 SELECT-only、危险 SQL、数据集/表权限、行级权限、执行前计划要求。
- [ ] 8.7 实现 `SqlExecution`，调用现有数据 API / 本地 SQL 工具并记录结构化结果。
- [ ] 8.8 实现 `ResultReview`，承接空结果复查、schema miss、异常比例复查、强制 SQL 后重试。
- [ ] 8.9 实现 `SynthesisAgent`，保留最终回答、图表说明、结构化结果摘要、失败兜底。
- [ ] 8.10 将 `data_executor.py` 改为调用 `DataTeamRunner`。
- [ ] 8.11 迁移 `tests/ai/executors/test_data_executor.py` 和 `tests/ai/test_data_executor_robustness.py` 到新 runner。

## 9. AgentService 与 API 接入

- [x] 9.1 调整 `AgentService`，移除对 LangChain Message 的直接依赖。
- [x] 9.2 保留路由日志、dry_run、return_raw_prompt、debug option、历史保存、多智能体综合回答。
- [ ] 9.3 确认 `/api/v1/chat/completions` SSE 输出格式不变。
- [ ] 9.4 增加 API 级流式快照测试，覆盖 content、log、citation、context_update、error、finish。

## 10. 测试与文档回归

- [x] 10.1 全量替换 `tests/ai/` 中的 LangChain Message 测试桩。
- [ ] 10.2 运行 `pytest tests/ai -q`。
- [ ] 10.3 运行 `python3 -m compileall app tests`。
- [x] 10.4 搜索 `app` 和 `tests`，确认没有 LangChain 运行时代码残留。
- [ ] 10.5 更新架构文档中 LangChain 相关描述。
- [x] 10.6 更新 `tests/CHECKLIST.md` 的最终验收结果。

## 11. LangChain 彻底清理

- [x] 11.1 从 `requirements.txt` 移除 `langchain-core`、`langchain-community`、`langchain-openai`、`langchain`。
- [x] 11.2 删除或改名旧 LangChain 专用 helper、mock、注释和文档。
- [ ] 11.3 重新安装依赖并运行全量后端测试。
- [ ] 11.4 构建 Docker 镜像，确认 Python 3.11+ 与 AgentScope 依赖可用。
- [ ] 11.5 最终执行 `rg "langchain|LangChain|ChatOpenAI|StructuredTool|BaseTool|ToolMessage|SystemMessage|HumanMessage|AIMessage" app tests requirements.txt`，确认无残留。
