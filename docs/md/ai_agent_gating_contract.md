# AI Agent 门控契约 v1

本文档定义各类 Agent 在路由、工具调用、外部执行、数据真实性、SQL 安全和输出兜底上的门控契约。它的目标不是描述所有实现细节，而是把“哪些门控是必须的、哪些是不适用、哪些是当前缺口”显式写清楚，作为后续开发和测试验收的共同依据。

## 适用范围

当前契约覆盖以下执行路径：

- ChatBI / DataQuery：`DataQueryExecutor` + `DataAgentRunner`
- General Assistant：`AssistantExecutor` + `AssistantAgentRunner`
- Knowledge Agent：`KnowledgeExecutor` + `KnowledgeAgentRunner`
- RAGFlow 外部引擎：`RAGExecutor`
- OpenClaw 外部引擎：`OpenClawExecutor`

核心入口：

- `app/services/ai/dispatcher.py`
- `app/services/ai/agent_service.py`
- `app/services/ai/data_query_turn_classifier.py`
- `app/services/ai/runners/data_agent_runner.py`（薄编排与 Runner 门面）
- `app/services/ai/runners/chatbi/`（ChatBI 守卫与 ReAct 域实现，见 `README.md`）
- `app/services/ai/runners/assistant_agent_runner.py`
- `app/services/ai/tool_nudge_policy.py`
- `app/services/ai/skill_resolver.py`
- `app/services/ai/intent_service.py`
- `app/services/ai/router_service.py`
- `app/services/ai/runners/knowledge_agent_runner.py`
- `app/services/ai/runtime/agentscope/tools.py`
- `app/services/ai/runtime/agentscope/event_stream.py`

## 门控矩阵

| Agent 类型 | 路由/意图门控 | 工具权限门控 | 外部执行挂起 | 数据/知识真实性门控 | SQL 安全门控 | 输出安全/反幻觉 | 中断恢复 | 当前契约状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ChatBI / DataQuery | 必须。外层只选 DataQuery，内部以 `DataQueryTurnClassifier` 为最终判定 | 必须。AgentScope runtime tool scope 统一处理 | 必须。支持 `external_execution_required` | 必须。schema、few-shot、结果复用、空结果、异常结果均需可观测 | 必须。只读 SQL、schema-before-sql、静态风险、重复 SQL、修复轮次、最终 guard | 必须。未完成查数不得直接回答 | 必须。pending snapshot 保存 data run state | 基本完整，需收口测试和少数边界顺序 |
| General Assistant | 必须。使用会话级通用分类；专家直选（`direct_agent_selection`）跳过反幻觉 Guard | 必须。AgentScope runtime tool scope 统一处理；可选工具预检（`agent_tool_preflight_mode`） | 必须。支持 `external_execution_required` | 部分。无数据库连接时拦截疑似虚构业务数据 | 不适用 | 部分。仅主助手 + 自动路由 + 强查数信号时拦截；裸表格不单独触发 | 必须。AgentScope pending 恢复 | 基础完整，不等同 ChatBI 强门控 |
| Knowledge Agent | 必须。知识库问答可优先于普通对话 | 必须。AgentScope runtime tool scope 统一处理 | 必须。支持 `external_execution_required` | 必须。dataset 范围、自动检索、空召回、引用约束 | 不适用 | 必须。空召回或无引用事实回答要拦截 | 必须。AgentScope pending 恢复 | 基本完整，需明确 dataset 缺失与后续反幻觉测试关系 |
| RAGFlow 外部引擎 | 弱。适配器内只做查询提取和日志 | 不适用。外部引擎不走本地 AgentScope 工具权限 | 不适用。当前无统一挂起恢复 | 依赖 RAGFlow 返回引用和错误 | 不适用 | 弱。无本地二次反幻觉审计 | 不适用 | 非统一门控路径，需明确为外部引擎自管或补统一适配 |
| OpenClaw 外部引擎 | 弱。主要交给 OpenClaw 处理 | 不适用。外部引擎不走本地 AgentScope 工具权限 | 不适用。当前无统一挂起恢复 | 部分。透传用户可访问 dataset 给外部引擎 | 不适用 | 必须。输入和输出安全审计可配置 | 不适用 | 有安全审计，但非统一 AgentScope 门控 |

## 通用 AgentScope 工具门控契约

适用于 ChatBI、General Assistant、Knowledge Agent。

1. Runtime tool 必须声明 `permission_scope`，可选值包括 `read`、`write`、`ask`、`dangerous`。
2. `read` 工具默认允许自动执行。
3. `dangerous` 工具必须拒绝执行。
4. `write` / `ask` 工具默认必须触发用户确认，除非运行时 `approval_mode` 明确允许。
5. AgentScope `REQUIRE_USER_CONFIRM` 必须映射为 SSE `permission_required`。
6. AgentScope `REQUIRE_EXTERNAL_EXECUTION` 必须映射为 SSE `external_execution_required`。
7. 挂起请求必须保存 `agent_state`、`stream_state`、`runner_context`、`tool_call` 和用户/会话信息。
8. permission 恢复接口不得处理 external pending；external 恢复接口不得处理 permission pending。

相关文件：

- `app/services/ai/runtime/agentscope/tools.py`
- `app/services/ai/runtime/agentscope/event_stream.py`
- `app/services/ai/runtime/agentscope/pending_store.py`
- `app/services/ai/runtime/agentscope/confirmations.py`
- `app/services/ai/agent_service.py`

## ChatBI / DataQuery 契约

### 路由与分类

1. 只要 Agent 配置包含 `data_query` capability，dispatcher 可以进入 `DataQueryExecutor`。
2. 进入 `DataQueryExecutor` 后，ChatBI 内部行为必须以 `DataQueryTurnClassifier` 为最终判定，不能只依赖外层 router。
3. 分类类型必须覆盖：
   - `new_data_query`
   - `reuse_previous_result`
   - `context_action`
   - `skill_execution`
   - `clarification_or_non_data`
4. 明显非查数、寒暄、指代过于模糊的问题必须进入 clarification，而不是强行查数。
5. 复用上一轮结果必须同时满足：
   - 当前会话存在上一轮结构化查询结果
   - 最近对话仍有可信的数据结果上下文
6. 如果 LLM 误判为复用，但当前问题包含明确新查询诉求，应改按新数据查询处理。

### 新数据查询执行顺序

1. 新数据查询必须先进行用户需求分析，形成结构化 `DataQueryIntentFrame`（业务目标、指标、维度、筛选语义、时间范围、粒度）并派生 schema 检索词。
2. `DataQueryIntentFrame` 必须作为字段绑定自检和空结果修复的依据；若 LLM 直接给出的 keywords 无效，但意图帧有效，应优先从意图帧派生 keywords，而不是机械回退到原始长句。
3. 需要新 SQL 生成时必须进行 few-shot 案例检索；未命中或检索失败也必须有可观测日志。
4. 必须先调用 `get_dataset_schema`，再允许调用 `execute_sql_query`。
5. Agent 路径在 `execute_sql_query` 前必须通过 **SqlQueryBinding 预检**（`resolve_sql_schema_preflight_with_binding`）：字段/表名与 Schema 一致；Schema 外物理表仅在有元数据权限时放行，并回填 binding 的 `dataset_name`。
6. `execute_sql_query_core` 优先读取 ContextVar / 参数中的 `SqlQueryBinding` 做表归属、权限、字段与 data_perm 列 meta；Gate 已预检通过时设 `preflight_validated=True`，Core 不重复字段校验。HTTP/门户直连无 binding 时回退 MetaTable 查库。
7. schema prefetch 失败时：
   - 元数据服务不可用：终止
   - 无授权数据集：终止
   - 元数据未同步知识库：终止
   - 连续 schema miss 达到阈值：终止
8. schema 返回多个高置信候选且分数接近时，必须先澄清数据集或指标口径，禁止直接 SQL。

### 跨数据集联邦

1. 单源 `execute_sql_query` 检测到 SQL 引用不属于当前 `dataset_name` 的表，且涉及多个 dataset 时，必须自动升级为联邦查询（G12）。
2. 升级时必须构造 `SqlQueryBinding`（Schema + SQL 表 + MetaTable 反查），注入联邦 plan prompt 的【物理表与数据集绑定】块，并在解析 XML 后按 binding 修正 subquery 的 `dataset_name`。
3. 禁止仅依赖 LLM 从 Schema 文本猜测 subquery 的 `dataset_name` 而无平台侧表→dataset 约束。

### SQL 安全与修复

1. 只允许只读查询，SQL 必须以 `SELECT` 或 `WITH` 开头。
2. 禁止 `SELECT *`。
3. JOIN 必须有明确 `ON` 条件。
4. 明细查询建议尽量收窄时间、筛选条件或返回行数，但不作为统一硬拦截条件。
5. 重复执行同一条已成功 SQL 时，应拦截重复调用，并复用首次成功结果进行最终回答。
6. SQL 执行错误必须进入修复轮次，成功前不得回答。
7. 空结果必须先诊断筛选条件、时间范围或 JOIN 条件；诊断 SQL 不能作为最终结论。
   - 若存在 `DataQueryIntentFrame`，空结果修复必须结合原始筛选语义判断错值、错字段、别名、父级范围或分类条件，不能仅因候选值不包含用户原词就判定无数据。
8. 比率/占比异常必须触发对账复核。
9. 时间差/时延/时长异常必须触发复核时间字段方向、时区或单位换算。
10. 工具重复调用同参且无进展时必须触发循环熔断。
11. 如果模型在完成查数顺序前输出自然语言结论，最终 guard 必须拦截。

### 当前已知边界

1. SQL 静态风险门控目前会先于重复 SQL 门控触发。测试和产品预期需要统一：高风险重复 SQL 应优先报静态风险，还是优先复用缓存结果。
2. 部分 runner 测试未 mock 配置/数据库，容易在无 Redis/MySQL 的本地沙箱里失败；这些失败不应直接等同业务门控失败。

### 测试映射

- `tests/ai/test_turn_classifier.py`
- `tests/ai/runners/test_data_agent_runner.py`
- `tests/ai/test_sql_query_binding.py`
- `tests/ai/tools/test_data_api.py`
- `tests/ai/test_dispatcher_data_executor_boundary.py`
- `tests/ai/runtime/test_event_stream_observability.py`
- `tests/ai/runtime/test_external_execution_resume.py`

## General Assistant 契约

1. General Assistant 必须使用会话级通用分类结果，包括 data query 降级、knowledge、context action、skill execution、meta action、general。
2. 如果当前 Agent 无 `data_query` capability，但用户问题被识别为数据查询，必须按通用助手处理，并保留降级语义。
3. General Assistant 不得连接 ChatBI 数据库，也不得编造内部业务数据。
4. **数据反幻觉 Guard**（`AssistantAgentRunner.execute`）仅在以下条件**同时**满足时启用：
   - 当前智能体为 **主通用助手**（`is_main_general_agent()`）；
   - **非**专家直选 / `@` 提及 / 显式 `agent_id`（`route_hints.direct_agent_selection`）；
   - 用户问题**非**联网/外部搜索（`looks_like_web_search_query`）；
   - 轮次分类或意图为 `DATA_QUERY`，或问题含**强内部业务查数信号**（`looks_like_strong_business_data_request`）。
5. 拦截条件（须本轮**未**发起/待确认工具调用，且输出命中以下之一）：
   - 假装已转派 ChatBI / 正在检索内部数据集等话术；
   - Markdown 表格 **且** 含内网 IP；
   - Markdown 表格 **且** 含内部业务字段（主机名、资产、工单、数据集、表结构等）。
   - **裸 Markdown 表格 alone 不触发**；系统负载等可通过 Bash/工具执行的请求，若已出现 `permission_required` / `external_execution_required` 或 tool log，视为已尝试工具，不拦截。
6. 拦截后须生成可执行的智能体切换建议（`quick:/switch_agent_expert?agent_id=...`），而非仅文字提示。
7. **工具预检**（`tool_nudge_policy.resolve_tool_nudge`）：由已绑定工具的 `name + description` 与问题相关度驱动；配置 `agent_tool_preflight_mode`：`off` / `soft`（默认，注入便签）/ `hard`（便签 + 首步 `ToolChoice`）。问候、元操作、记忆类工具（`memory_search` 等）不促发。
8. **Skill 自动扫描**（仅主助手）：未挂载/解析技能时，按 `skill_auto_scan_*` 配置扫描技能库并注入摘要（全文仍须 `read_skill_instruction`）。
9. 绑定工具时必须走 AgentScope runtime tool 权限门控。
10. 没有工具时走 simple synthesis，不适用工具权限/外部执行挂起。

测试映射：

- `tests/ai/executors/test_chat_executor.py`
- `tests/ai/runners/test_assistant_agent_data_guard.py`
- `tests/ai/test_tool_nudge_policy.py`
- `tests/ai/test_skill_resolver.py`
- `tests/ai/runtime/test_agentscope_tooling.py`
- `tests/ai/runtime/test_event_stream_observability.py`

## Knowledge Agent 契约

1. 知识库问答必须确保 `search_knowledge_base` 工具可用。
2. 执行 ReAct 前必须自动调用 `search_knowledge_base` 预检索。
3. 检索范围必须来自显式参数、请求上下文、智能体绑定 dataset 或系统默认 dataset。
4. 如果要求显式 dataset 但未提供，必须在检索前终止。
5. 如果没有任何可用默认 dataset，也必须终止并提示管理员配置。
6. 知识库服务不可用时必须终止，不得继续让模型脑补。
7. 空召回时，如果模型输出长事实性回答，必须拦截。
8. 有召回但最终事实性回答没有有效引用标记时，必须拦截。
9. 模型输出中的无效引用标记必须过滤。
10. 绑定业务工具时必须走 AgentScope runtime tool 权限门控。

当前已知边界：

1. dataset 缺失门控位于服务不可用/反幻觉门控之前。测试后续分支时需要显式 mock dataset 配置或上下文，否则会被前置 dataset gate 拦截。

测试映射：

- `tests/ai/executors/test_chat_executor.py`
- `tests/ai/test_knowledge_utils.py`
- `tests/ai/tools/test_knowledge_tool.py`

## RAGFlow 外部引擎契约

1. RAGFlow Agent 由 `engine_type == "RAGFLOW"` 直接进入 `RAGExecutor`。
2. 当前路径不走本地 AgentScope runtime tool 权限门控。
3. 当前路径不产生本地 `permission_required` 或 `external_execution_required`。
4. 必须透出语义理解、知识库检索、引用和错误日志。
5. 流式失败且尚未输出内容时可以重试；已输出内容后不得简单重试以免重复回答。
6. 真实性主要依赖 RAGFlow 引擎返回内容和 citation，本地当前没有二次反幻觉审计。

当前已知边界：

1. 如果产品要求所有 agent 都统一支持工具确认、外部执行挂起和本地反幻觉审计，RAGFlow 需要新增适配层。
2. 如果保持外部引擎自管，文档和测试必须明确它不适用本地 AgentScope 工具门控。

## OpenClaw 外部引擎契约

1. OpenClaw Agent 由 `engine_type == "OPENCLAW"` 直接进入 `OpenClawExecutor`。
2. 当前路径不走本地 AgentScope runtime tool 权限门控。
3. 当前路径不产生本地 `permission_required` 或 `external_execution_required`。
4. 默认启用输入安全审计，除非配置关闭。
5. 默认启用输出安全审计，发现不安全输出时必须发送 retraction。
6. 如果存在用户信息，必须尽力解析并透传用户可访问的 RAGFlow metadata dataset。
7. dataset 解析失败不能阻断 OpenClaw 主流程，但必须记录 warning。

当前已知边界：

1. OpenClaw 的工具执行、检索、推理安全主要依赖外部引擎。
2. 如果要纳入统一门控，需要将外部工具调用事件标准化为本地 pending/permission 事件。

## 验收规则

新增或修改任一 Agent 门控时，至少满足以下要求：

1. 文档矩阵必须同步更新。
2. 明确该门控属于必需、不适用、外部自管或当前缺口。
3. 必须有正向和拦截路径测试。
4. ChatBI 新增门控必须覆盖：
   - 正常查数不被误拦截
   - 违规路径被拦截
   - 关联日志或 trace 可见
   - 修复或中断恢复路径不丢状态
5. Knowledge 新增门控必须覆盖：
   - dataset 缺失
   - 空召回
   - 服务不可用
   - 有召回但无引用的事实性回答
6. General 新增门控必须覆盖：
   - 普通聊天不被误拦截
   - 专家直选（`direct_agent_selection`）不触发数据 Guard
   - 已 pending 工具调用（含 `permission_required`）不拦截
   - 数据查询降级不编造数据
   - 工具预检 off/soft/hard 行为（若涉及）
   - 工具权限事件可恢复
7. 外部引擎新增统一门控时，必须补充 RAGFlow/OpenClaw 与本地 SSE 事件的契约测试。

## 当前建议优先级

1. 收口 ChatBI runner 测试夹具，避免无 Redis/MySQL 环境下误报。
2. 明确 SQL 静态风险门控和重复 SQL 门控的优先级，并同步测试。
3. 为 Knowledge runner 的 dataset 前置门控补充独立测试，避免和服务不可用/反幻觉测试互相遮挡。
4. 产品层决定 RAGFlow/OpenClaw 是“外部自管”还是“接入统一 AgentScope 门控适配层”。
