# AgentScope 集成架构设计文档

> 文档版本：v1.0  
> 更新时间：2026-06-11  
> 代码路径：
> - AgentScope 上游：`/Users/chenxiaolong/workspace/agentscope/src/agentscope/`
> - 平台集成层：`app/services/ai/runtime/agentscope/`
> - 平台 Runner 层：`app/services/ai/runners/`

---

## 一、AgentScope 核心架构

### 1.1 整体结构

```
agentscope/
├── agent/          # 核心 Agent 调度（_agent.py, _config.py）
├── state/          # AgentState / ToolContext / TaskContext
├── message/        # 消息类型（Msg / TextBlock / ToolCallBlock / ToolResultBlock 等）
├── event/          # 事件系统（ReplyStart/End, ModelCallStart/End, ToolCall/Result 等）
├── tool/           # Toolkit / ToolBase / ToolChunk / ToolResponse
├── permission/     # PermissionEngine / PermissionDecision（ALLOW/ASK/DENY/PASSTHROUGH）
├── formatter/      # 多模型消息格式化（OpenAI/Anthropic/DashScope/Gemini 等）
├── middleware/     # MiddlewareBase（5 个生命周期钩子）
├── model/          # ChatModelBase / ChatResponse / ChatUsage
├── workspace/      # LocalWorkspace / Offloader（文件工具 + 工具结果卸载）
├── mcp/            # MCP 工具集成
├── skill/          # Skill 系统（读取 SKILL.md 指令）
└── embedding/      # 向量 Embedding
```

### 1.2 Agent 核心类（`agent/_agent.py`）

`Agent` 是 AgentScope 的核心调度单元，构造参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `name` | `str` | Agent 标识名 |
| `system_prompt` | `str` | 系统提示词 |
| `model` | `ChatModelBase` | 调用的 LLM 模型 |
| `toolkit` | `Toolkit` | 工具注册中心（唯一来源） |
| `middlewares` | `list[MiddlewareBase]` | 可选中间件列表 |
| `state` | `AgentState` | 运行时状态（可传入恢复） |
| `offloader` | `Offloader` | 上下文卸载器（文件工具结果卸载到磁盘）|
| `model_config` | `ModelConfig` | 重试/fallback 配置 |
| `context_config` | `ContextConfig` | 上下文压缩配置 |
| `react_config` | `ReActConfig` | 推理-行动循环配置（max_iters）|

### 1.3 `AgentState`（状态对象）

```python
class AgentState(BaseModel):
    session_id: str                    # 会话 ID（UUID）
    summary: str | list[TextBlock | DataBlock]  # 历史压缩摘要
    context: list[Msg]                 # 当前未压缩上下文（喂给 LLM 的消息列表）
    reply_id: str                      # 当前 reply 的 ID
    cur_iter: int                      # 当前推理-行动循环迭代次数
    permission_context: PermissionContext  # 工具权限上下文
    tool_context: ToolContext          # 文件读取缓存（支持 LRU 淘汰）
    tasks_context: TaskContext         # 任务上下文
```

**关键**：`AgentState` 可 `model_dump(mode="json")` 序列化，也可 `AgentState.model_validate(dict)` 反序列化，支持跨请求持久化。

### 1.4 `ContextConfig`（上下文压缩配置）

```python
class ContextConfig(BaseModel):
    trigger_ratio: float = 0.8       # Token 占 context_size 达 80% 时触发压缩
    reserve_ratio: float = 0.1       # 压缩后保留最近 10% Token 不被压缩
    tool_result_limit: int = 3000    # 单条工具结果最大 Token 数（超出则截断）
    compression_prompt: str = ...    # 引导 LLM 生成结构化摘要的提示词
    summary_template: str = ...      # 摘要注入格式模板
    summary_schema: dict = ...       # 结构化摘要输出 Schema（5 字段，各 ≤300 字）
```

---

## 二、AgentScope 运行链路（单轮推理）

```
Agent.reply(inputs) / Agent.reply_stream(inputs)
  │
  ├─ [Step 1] _handle_incoming_messages(msgs)
  │     将用户消息写入 state.context
  │
  ├─ [Step 2] while cur_iter < react_config.max_iters:
  │
  │   ├─ [Step 2.1] _check_next_action()
  │   │     检查 context 中最后一条 assistant 消息的工具调用状态
  │   │     → "exit"      有未完成的 awaiting 工具调用，暂停等待外部事件
  │   │     → "reasoning" 无工具调用，进入推理
  │   │     → "acting"    有可执行工具调用，进入工具执行
  │   │
  │   ├─ [Step 2.2] compress_context()  ← 每次推理前自动触发
  │   │     1. count_tokens() 精确计算当前 Token 数
  │   │     2. 若 tokens < trigger_ratio × model.context_size → 跳过
  │   │     3. 从后向前遍历 context，找到 reserve_ratio 的切割点
  │   │     4. 处理边界（避免 ToolCall/ToolResult 配对被拆分）
  │   │     5. 调用 LLM generate_structured_output() 生成结构化摘要
  │   │     6. state.summary 更新，旧消息从 state.context 清除
  │   │
  │   ├─ [Step 2.3] _reasoning()  → 调用 _prepare_model_input() → 调用 LLM
  │   │     _prepare_model_input() 组装消息：
  │   │       [SystemMsg(system_prompt)]
  │   │       + [UserMsg(summary)]  ← 若存在历史摘要
  │   │       + state.context       ← 当前未压缩上下文
  │   │     → 流式返回 Events（TextBlockDelta / ToolCallBlock / ThinkingBlock）
  │   │     → _save_to_context(blocks)  将 assistant 输出写入 context
  │   │
  │   └─ [Step 2.4] _execute_tool_call(tool_call)
  │         1. 解析工具调用参数（含 JSON 修复）
  │         2. PermissionEngine.check_permission() → ALLOW/ASK/DENY
  │            - ALLOW：直接执行
  │            - ASK：yield RequireUserConfirmEvent，暂停 Agent
  │            - DENY：返回 error 工具结果
  │         3. 工具执行（toolkit.call_tool）
  │         4. _split_tool_result_for_compression()
  │            → 若结果 Token > tool_result_limit：截断 + 追加 <<<TRUNCATED>>> 提示
  │            → 若有 Offloader：卸载至磁盘，提示文件路径
  │         5. _save_to_context([ToolResultBlock])
  │
  └─ [Step 3] yield ReplyEndEvent + AssistantMsg（最终回复）
```

### 2.1 Middleware 生命周期钩子（5 个）

| 钩子 | 模式 | 拦截点 |
|---|---|---|
| `on_reply` | 洋葱（Onion） | 整个 reply 过程 |
| `on_reasoning` | 洋葱 | 推理（LLM 调用）阶段 |
| `on_acting` | 洋葱 | 工具执行阶段（仅包含纯 I/O，不含权限和 context 写入）|
| `on_model_call` | 洋葱 | 原始 LLM API 调用 |
| `on_system_prompt` | 变换管道（Pipeline） | 系统提示词变换（多个 middleware 串行处理）|
| `on_compress_context` | 洋葱 | 上下文压缩（可完全替换压缩逻辑）|

---

## 三、平台如何集成 AgentScope

### 3.1 集成层文件结构

```
app/services/ai/runtime/agentscope/
├── agent_runtime.py      # 构建 ContextConfig / ModelConfig / 工具指纹
├── chat.py               # 消息格式转换（RuntimeMessage ↔ AgentScope Msg）
├── compat.py             # LangChain-like 兼容消息类型（HumanMessage/AIMessage 等）
├── confirmations.py      # 工具审批/外部执行挂起请求注册（内存 + Redis 双层）
├── event_stream.py       # AgentScope Event → 平台 SSE 事件映射
├── messages.py           # 平台 RuntimeMessage / RuntimeContentBlock 数据类
├── models.py             # 平台模型适配（LLM Handle → AgentScope ChatModelBase）
├── pending_store.py      # 挂起请求 Redis 持久化（支持跨进程恢复）
├── session_lock.py       # Redis 分布式锁（防并发 AgentState 写冲突）
├── state_store.py        # AgentState Redis 持久化（含版本/工具指纹校验）
├── stream_reconcile.py   # 流式对齐（SSE 已发内容 vs AgentState 最终文本 diff）
├── text_sanitize.py      # 流式文本清洗（去除 XML 工具调用残留）
├── tools.py              # RuntimeToolSpec / AgentScopeRuntimeTool / 权限决策
└── workspace.py          # LocalWorkspace 初始化（文件工具 + Offloader）
```

### 3.2 工具适配层（`tools.py`）

平台定义了统一的工具描述格式 `RuntimeToolSpec`，并通过适配器对接 AgentScope 工具协议：

```
RuntimeToolSpec（平台工具描述）
       │
       ▼
AgentScopeRuntimeTool（适配 AgentScope 工具协议）
  - check_permissions()  → PermissionDecision（ALLOW/ASK/DENY）
  - check_read_only()    → 是否只读
  - __call__(**kwargs)   → ToolChunk（包含 TextBlock 结果）

AgentScopeNativeApprovalTool（包装 AgentScope 原生工具，如 Bash/Read/Write）
  - 代理原生工具的所有属性
  - 覆盖 check_permissions()（注入平台 approval_mode 逻辑）
```

**权限范围（`RuntimePermissionScope`）**：

| 范围 | 行为 | 典型工具 |
|---|---|---|
| `read` | 直接 ALLOW | `web_search_baidu`、`system_http_request`、`get_dataset_schema` 等 |
| `ask` | 请求用户确认（ASK）| 未配置 approval_mode 的业务工具 |
| `write` | 请求用户确认（ASK）| 文件写入类工具 |
| `dangerous` | 直接 DENY | 标记为危险的工具 |

**`approval_mode` 覆盖逻辑**（Agent 后台配置级）：

| approval_mode | 行为 |
|---|---|
| `allow` | 所有工具跳过审批，直接 ALLOW |
| `deny` | 所有工具直接拒绝 |
| `ask`（默认）| 按 `permission_scope` 决定 |

`READ_ONLY_TOOL_NAMES` 白名单中的工具始终自动 ALLOW，无需审批：
```python
READ_ONLY_TOOL_NAMES = {
    "get_current_time", "get_dataset_schema", "search_knowledge_base",
    "memory_search", "fetch_user_long_term_memory", "web_search_baidu",
    "system_http_request", "fetch_static_web_url", ...
}
```

### 3.3 AgentState 持久化（`state_store.py`）

AgentScope 的 `AgentState` 本身不提供持久化，平台实现了 Redis 持久化方案：

```
每轮对话结束后：
  agent.state.model_dump(mode="json")
    → AgentStateStore.save(
        user_id, conversation_id, agent_name,
        tools_fingerprint,   ← SHA256(agent_name + model + tools列表) 前16位
        model_name,
        state,
        ttl=7天
      )
    → Redis Key: nanzi:{uid}:{conv_id}:agent_state:{agent_name}

下一轮对话开始时：
  AgentStateStore.load(user_id, conversation_id, agent_name)
    → 校验 schema_version + agent_name + tools_fingerprint
    → AgentState.model_validate(persisted.state)
    → 传入 Agent(state=restored_state)

工具集变更时（指纹不匹配）：
  → 丢弃旧 state，重置为全新 AgentState
  → 向前端发送 "智能体配置变更：历史会话状态已重置" 日志
```

**重要**：AgentState.context 中保存了完整的多轮对话历史（含工具调用/结果），这是 AgentScope 原生的上下文管理机制，与平台 Redis 存储的 `memory_service` 聊天历史相互独立。

### 3.4 会话分布式锁（`session_lock.py`）

防止同一会话的多个并发请求同时修改 AgentState：

```
Redis Lock Key: nanzi:{uid}:{conv_id}:agent_lock:{agent_name}
TTL: 120 秒（请求级别，请求结束释放）
等待超时: 15 秒（轮询间隔 100ms）
```

超时抛出 `SessionLockTimeout`，上层作为错误处理。

### 3.5 工具挂起与恢复（`confirmations.py` + `pending_store.py`）

当工具执行需要用户确认（`RequireUserConfirmEvent`）或外部执行（`RequireExternalExecutionEvent`）时，Agent 会暂停并等待外部触发：

```
[正向流程] AgentScope Agent 暂停
  → PermissionEngine → ASK → yield RequireUserConfirmEvent
  → stream_pending_tool_interrupt() 在 event_stream.py 中处理
  → confirmations.register(kind="permission", ...)
      → PendingAgentScopeSnapshot 序列化（含 agent_state + tool_call + stream_state）
      → Redis Key: nanzi:{uid}:pending:{request_id}  TTL=600s
      → 内存注册表 _items[request_id] = PendingAgentScopeConfirmation
  → 向前端 SSE 发送 permission_required 事件（含 permission_request_id）

[恢复流程] 用户点击确认
  → API: POST /tools/confirm/{request_id}
  → pending_agentscope_confirmations.pop_async(request_id)
      → 同进程：从内存 _items 取（含 agent 对象引用，直接继续执行）
      → 跨进程/重启：从 Redis 反序列化快照，重建 agent + runner 后继续
  → agent.reply(UserConfirmResultEvent)  ← 恢复 Agent 执行
```

### 3.6 事件映射（`event_stream.py`）

平台将 AgentScope 的内部事件流转换为前端 SSE 事件：

| AgentScope 事件 | 平台 SSE 类型 | 说明 |
|---|---|---|
| `REPLY_START` | `agent_reply phase=start` | 推理开始 |
| `REPLY_END` | `agent_reply phase=end` | 推理结束 |
| `MODEL_CALL_START` | `model_call phase=start` | LLM 调用开始 |
| `MODEL_CALL_END` | `model_call phase=end`（含 token 数、耗时）| LLM 调用结束，同时检查是否触发上下文压缩 |
| `THINKING_BLOCK_START/END` | `thinking phase=start/end` | 思考块开始/结束 |
| `THINKING_BLOCK_DELTA` | `thinking status=continuing` | 思考内容增量 |
| `TOOL_CALL_START` | `log status=pending` | 工具调用开始 |
| `TOOL_CALL_DELTA` | （内部缓存 args） | 工具参数增量（不发送）|
| `TOOL_RESULT_TEXT_DELTA` | （内部缓存 output） | 工具结果增量（不发送）|
| `TOOL_RESULT_DATA_DELTA` | `tool_result_data` | 工具结果数据块（图片/音频等）|
| `TOOL_RESULT_END` | （触发 on_tool_result_end 回调）| 工具结果完成 |
| `REQUIRE_USER_CONFIRM` | `permission_required` | 需要用户审批 |
| `REQUIRE_EXTERNAL_EXECUTION` | `external_execution_required` | 需要外部执行 |
| `EXCEED_MAX_ITERS` | `content`（MAX_STEPS_REACHED 文案）| 超出最大迭代数 |

**上下文压缩通知**：当 `MODEL_CALL_END` 或 `TOOL_RESULT_END` 后检测到 `agent.state.summary` 长度增长时，向前端发送 `context_compression` 事件（含摘要预览）。

### 3.7 Workspace / Offloader（`workspace.py`）

AssistantAgentRunner 为每个会话初始化一个 `LocalWorkspace`：

```python
workspace = LocalWorkspace(
    workdir=f"data/agent_workspaces/{uid}/{conv_id}/",
    skill_paths=[...],  # 平台 Skills 目录（含 SKILL.md 的子目录）
)
```

`LocalWorkspace` 同时扮演两个角色：
1. **工具提供者**（`list_tools()`）：提供 `Bash/Read/Write/Edit/Glob/Grep` 六个文件操作工具
2. **Offloader**（`offload_context()` / `offload_tool_result()`）：当工具结果超过 `tool_result_limit` 时，将截断内容写入本地文件，Agent 可按需通过文件路径读回

### 3.8 大模型调用指标统计中间件（`ModelCallStatsMiddleware`）

平台使用 AgentScope 的中间件机制，实现了一套对底层大模型（LLM）原始调用指标进行深度统计与追踪的方案：

#### 3.8.1 中间件设计与实现 (`middleware.py`)
- **钩子拦截**：继承自 `MiddlewareBase` 并覆写 `on_model_call` 钩子，包裹每一次真实的 LLM API 请求。
- **流式兼容**：针对流式模型调用，提供 `_stream_with_stats` 包装器。在消费完异步生成器的所有 text/tool 块后，拦截并汇总最终的 Token 使用数（`usage`）及耗时。
- **数据结构**：每次模型调用产生一条 `ModelCallStatDetail` 记录，包含：
  - `call_index`: 本轮运行中第几次调用（1-indexed）。
  - `timestamp`: 调用发起的 ISO 8601 格式时间戳（例如 `2026-06-11T02:21:42.123456+00:00`）。
  - `model_name`: 大模型名称。
  - `input_message_count`: 传入的消息轮数。
  - `input_tokens` / `output_tokens` / `cache_input_tokens`: 输入、输出及缓存命中的 Token 数量。
  - `has_tool_calls` & `tool_names`: 产生的工具调用及工具名列表。
  - `elapsed_ms`: 本次调用总耗时（ms）。
  - `trace_id`: 当前执行的 Trace ID。

#### 3.8.2 Redis 指标追加与存储
- **异步写入**：使用 `asyncio.ensure_future` 触发 Redis 异步追加写入（List 结构），保障不会阻塞大模型的核心流式生成。
- **Key 格式**：`nanzi:{user_id}:{conversation_id}:model_call_stats`
- **生命周期**：TTL 设为 7 天（604,800 秒），与 AgentState 保持同步。

#### 3.8.3 前后端统计交互
1. **接口暴露**：在 `chat.py` 中提供 `GET /conversation/{conversation_id}/model_calls` 接口，支持通过可选的 `trace_id` 过滤 Redis 中的列表记录，供前端拉取明细。
2. **UI 看板展示**：前端（`EmbedChat.vue` 与 `AgentDebug.vue`）将消息底部的 Token 计数字段渲染为可点击按钮，点击后弹窗展示当前 Trace 下大模型调用的精细看板：
   - 包含调用总次数、总耗时、总输入、总输出 Token。
   - 卡片式列出每一次模型调用的详情、具体的大模型名称、耗时、关联工具以及**精确的调用时间戳**。

---

## 四、哪些 Runner 用了 AgentScope

### 4.1 AssistantAgentRunner ✅（深度集成）

**使用的 AgentScope 能力**：
- Agent 原生推理-行动循环（`Agent.reply_stream`）
- AgentState 持久化/恢复（Redis，含工具指纹校验）
- ContextConfig 自适应上下文压缩（`trigger_ratio=0.8`、`tool_result_limit=2000`）
- LocalWorkspace（文件操作工具 + Offloader）
- PermissionEngine（工具审批/外部执行挂起）
- ModelConfig（fallback 模型、重试）
- 流式 SSE 事件映射（event_stream.py 全量）

**补充的平台逻辑**：
- 历史截断：`history[-20:]`（最近 10 轮，配合 AgentScope Agent 内部截断）
- `strip_thought=True`（`_clean_assistant_text` 清洗 Thought 块）
- 流式对齐（`stream_reconcile.py`）：SSE 已发文本 vs AgentState 最终文本 diff 兜底

### 4.2 DataAgentRunner ✅（中度集成）

**使用的 AgentScope 能力**：
- Agent 原生推理-行动循环（`Agent.reply_stream`）
- AgentState 持久化/恢复（Redis，含工具指纹校验）
- ContextConfig 自适应上下文压缩（同上）
- LocalWorkspace（Offloader）
- PermissionEngine
- 流式 SSE 事件映射

**平台专属逻辑（ChatBI 特有，实现于 `app/services/ai/runners/chatbi/`）**：
- Schema 检索前置（`schema_prefetch`，预取表结构）
- SQL 计划推演（`sql_gates.should_require_sql_plan`，须 `debug_options.enable_sql_plan=true` 才启用 G6/G11）
- 意图改写触发的历史截断（`native_turn`，当 `standalone_query ≠ user_question` 时只保留最近 3 轮 Human/AI）
- DataBlock 图表块提取（`data_tools.py`）
- 流式 `<sql_plan>` 块由前端 `SqlPlanCard` 结构化展示

域模块索引见 `app/services/ai/runners/chatbi/README.md`。

### 4.3 KnowledgeAgentRunner ✅（深度集成）

KnowledgeAgentRunner 已继承自 `AssistantAgentRunner`，并使用了 `AgentScope Agent`，通过自动检索 + ReAct 的模式进行工作：

```
search_knowledge_base 自动前置检索 → AgentScope ReAct → 回复
```

**使用的 AgentScope 能力**：
- ✅ Agent 原生推理-行动循环（`Agent.reply_stream`）
- ✅ AgentState 持久化/恢复（Redis，含工具指纹校验）
- ✅ ContextConfig 自适应上下文压缩
- ✅ 完整的工具审批系统与流式事件映射

**平台补充的历史与注入管理**：
- ReAct 开始前平台侧**自动**调用 `search_knowledge_base`，检索结果注入 system prompt。
- 历史截断：`history[-10:]`（保留最近 5 轮）
- `strip_thought=True`（清洗思考块）

---

## 五、上下文管理全貌

### 5.1 AssistantAgent / DataAgent（两层压缩）

```
Redis 聊天历史（memory_service，最多 100 条）
  ↓ agent_max_context_messages 截断（默认 20 条，入口层）
  ↓ 传入 Runner → 组装 runtime_messages
  ↓
  ┌─────────────────────────────────────────────────────┐
  │ AgentScope Agent（内部管理 AgentState.context）      │
  │                                                      │
  │ 每轮推理前：compress_context()                       │
  │   ← Token 精确计数（model.count_tokens）             │
  │   ← 超 80% 触发 LLM 摘要压缩                        │
  │   ← 摘要存 state.summary，旧消息清除                 │
  │                                                      │
  │ 每次工具结果：tool_result_limit = 2000 token         │
  │   ← 超限截断 + <<<TRUNCATED>>> 提示                  │
  │   ← 有 Offloader：写磁盘，告知文件路径               │
  │                                                      │
  │ AgentState 整体序列化存 Redis（每轮结束）             │
  └─────────────────────────────────────────────────────┘
```

### 5.2 KnowledgeAgent（手动截断）

```
Redis 聊天历史 → history[-10:]（最近 5 轮）→ LLM
```

### 5.3 平台配置参数（可在后台动态调整）

| 配置 Key | 默认值 | 说明 |
|---|---|---|
| `agent_max_context_messages` | `20` | 入口层：从 Redis 拉取的历史最大条数 |
| `agentscope_context_trigger_ratio` | `0.8` | 触发 AgentScope 上下文压缩的 Token 比率 |
| `agentscope_context_reserve_ratio` | `0.1` | 压缩后保留最近 Token 的比率 |
| `agentscope_tool_result_limit` | `2000` | 单条工具结果最大 Token 数 |
| `agentscope_workspace_root` | `data/agent_workspaces` | Workspace 根目录 |

---

## 六、消息格式转换链路

```
前端 HTTP Body
  ↓
平台 RuntimeMessage（role/content/tool_call_id）
  ↓ chat.py: compat_to_runtime_messages()
LangChain-like BaseMessage（HumanMessage/AIMessage/ToolMessage）
  ↓ chat.py: to_agentscope_messages()
AgentScope Msg（name/role/content=[TextBlock/DataBlock/...]）
  ↓
AgentScope Formatter（多模型格式化，如 OpenAI/Anthropic/DashScope）
  ↓
LLM API
```

---

## 七、与平台其他模块的边界关系

| 模块 | 与 AgentScope 的关系 |
|---|---|
| `memory_service` | 存储对话历史（Human/AI 消息），独立于 AgentState.context |
| `session_summary_service` | 每轮异步更新会话摘要（独立于 AgentScope 压缩摘要）|
| `turn_classifier.py` | 意图路由（DataAgent/KnowledgeAgent/AssistantAgent），决定走哪个 Runner |
| `agent_service.py` | 最外层编排，调用各 Runner，管理 `agent_max_context_messages` 截断 |
| `tools/registry.py` | 工具注册中心，将系统工具转换为 RuntimeToolSpec |
| `executors/common.py` | 历史消息预处理（`_clean_assistant_text` / `_compress_markdown_tables`）|
