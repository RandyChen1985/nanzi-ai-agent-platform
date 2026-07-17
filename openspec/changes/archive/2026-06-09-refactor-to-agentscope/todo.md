# AgentScope 集成待办与决策备忘

> 整理自 2026-06-08 讨论，2026-06-08 更新实现状态。供后续排期参考，与 `tasks.md`（重构执行清单）互补：本文侧重**缺口分析、架构决策、优先级**。

---

## 一、当前已集成（摘要）

| 能力 | 现状 |
|------|------|
| `OpenAIChatModel` | `models.py` + `AgentScopeLLMHandle`，全平台 LLM 入口 |
| `Agent` + `Toolkit` + `ReActConfig` | 仅 `AssistantAgentRunner` 有工具路径 |
| 内置工具 | `Bash/Read/Write/Edit/Glob/Grep`（`registry` 别名映射 + `native_tool`） |
| 权限 ASK | `REQUIRE_USER_CONFIRM` → SSE `permission_required` + confirm API |
| **Permission pending Redis** | `pending_store.py` + `confirmations.py`；跨进程可从 snapshot 重建 runner |
| **AgentState 持久化** | `state_store.py`；General 工具链多轮续跑，只喂本轮新用户消息 |
| **ContextConfig 调优** | `agent_runtime.load_context_config()`，读 `agentscope_context_*` / `agentscope_tool_result_limit` |
| **fallback_model** | 系统全局 `llm_model_name` → `ModelConfig(fallback_model=...)`（与主模型相同时跳过） |
| 消息层 | `compat` + `RuntimeMessage` ↔ `Msg`（`chat.py`） |
| 辅助 LLM 调用 | router / intent / summarizer / prompt_ops 等经 `AgentScopeChatClient` |
| LangChain | 运行时代码已移除 |

**未接 AgentScope App**（`create_app` / Storage / MessageBus）：刻意选择，用自研 `AgentService` 替代。

---

## 二、关键架构决策（已达成共识）

### 2.1 不必整包迁移 AgentScope App

- 已有完整业务栈：多执行器、路由、LTM、`memory_search`、Portal 智能体配置、审计
- App 只有单一 Chat 路径，接不进去 ChatBI/RAG/OpenClaw
- **结论**：继续 `Agent()` 库 + 自研平台，只借鉴 App 里的成熟模式（state 持久化、session 锁）

### 2.2 MemoryService 与 AgentState 双轨并存

```
展示/跨会话层（保留）              运行时层（已接入 General）
─────────────────────             ─────────────────────
MemoryService (Redis LIST)        AgentStateStore (Redis)
  → UI 历史、session summary        → General 原生 Agent 续跑
  → memory_search 检索             → permission resume
LTM / MemoryIndexService          → tool 轨迹（compress_context 待接）
MySQL trace_buffer（审计）         （不替代审计）
```

- **不**用 AgentState 替换 MemoryService
- MemoryService 继续只存 `{role, content}` 最终文本；AgentState 存 ReAct 运行时细节

### 2.3 AgentState 隔离维度

- AgentScope：`user_id` + `agent_id` + `session_id`（Storage 层）
- 我方 Redis key：`conversation:{user_id}:{conversation_id}:agent_state:{agent_name}`
- `AgentState` 对象本身无 `user_id`，隔离靠外层 key

### 2.4 Token 影响（接入 AgentState 时）

| 场景 | 趋势 |
|------|------|
| 无工具简单模式 | 无影响（不接 AgentState） |
| 多轮纯聊天 | 持平或略降（压缩 vs 20 条截断） |
| 多轮工具型（Bash/Read/Grep） | **倾向 prompt token 变大**（tool result 进入 context） |
| 长会话 + compress_context 生效后 | 先涨后稳 |

控制手段：`tool_result_limit`、`ContextConfig` 压缩阈值（**已接入配置项**）、仅工具型 Agent 开启 state、监控 `prompt_tokens`。

---

## 三、AgentState 集成路线（推荐分阶段）

### 阶段 A：Permission 持久化 — ✅ 已完成（2026-06-08）

**问题**：`confirmations.py` 把 live `Agent` 放进程内存，多实例/重启后 confirm 失败。

**Redis key**：`nanzi:agent:{user_id}:pending:{request_id}`，TTL 600s

**持久化内容**（不存 `Agent` 对象）：
- `agent_state`（`agent.state.model_dump(mode="json")`）
- `reply_id`、`tool_call`、`trace_id`、`kind`（`permission` | `external`）
- `runner_context`：`config`、`system_content`、`max_steps`、`stream_state` 等

**恢复**：同进程保留 live `Agent`；跨进程 `AssistantAgentRunner.from_runner_context()` + `Agent(state=loaded)` → `UserConfirmResultEvent` 续跑

**改动文件**：
- [x] 新建 `app/services/ai/runtime/agentscope/pending_store.py`
- [x] 改 `confirmations.py`（Redis + 内存 fallback，`register` async，`pop_async`）
- [x] 改 `assistant_agent_runner.py` 注册/恢复逻辑
- [x] 改 `agent_service.resume_agentscope_permission_stream`（`pop_async` + runner 重建）
- [x] 测试：`test_assistant_executor.py` permission/resume；`test_agentscope_state_pending.py`

### 阶段 B：会话级 AgentState（General 工具链）— ✅ 已完成（2026-06-08）

**Redis key**：`conversation:{user_id}:{conversation_id}:agent_state:{agent_name}`，TTL 7 天

**Envelope**（`RuntimeStateEnvelope`）：
```json
{
  "schema_version": 1,
  "agent_name": "...",
  "agent_version": "...",
  "tools_fingerprint": "...",
  "model_name": "...",
  "updated_at": "...",
  "state": { "...AgentState..." }
}
```

**执行逻辑**（已实现）：
- 有有效 envelope → `Agent(state=loaded)`，**只喂本轮新用户消息**
- 无 state 或 fingerprint 失效 → 从 MemoryService history bootstrap（首条全量）
- 正常结束 → `save(agent.state)`；permission/external 中断时不写 state（pending snapshot 已含 state）
- 仍向 MemoryService 写 assistant 最终文本（UI 不变）

**改动文件**：
- [x] 新建 `app/services/ai/runtime/agentscope/state_store.py`
- [x] 新建 `app/services/ai/runtime/agentscope/agent_runtime.py`（ContextConfig / ModelConfig / fingerprint）
- [x] 改 `assistant_agent_runner._execute_with_agentscope_native_agent`
- [x] 删会话时同步 `delete` agent_state（`memory_service.delete_session_memory`）
- [ ] 配置项：仅工具型 agent 启用（可选开关，当前 General 有工具即启用）
- [ ] 测试：多轮 Read/Bash 不重复劳动（集成测试待补）；版本变更后 state 失效（`matches()` 已实现）

### 阶段 C：多实例 Session 锁 — ✅ 已完成（2026-06-08）

- Redis key：`conversation:{uid}:{cid}:agent_lock:{agent_name}`，`SET NX EX 300`
- General/Data 的 execute、permission resume、external resume 共用同一把锁
- 无 Redis 或缺少 `conversation_id` 时降级跳过；超时返回「当前会话正在处理中」
- 实现：`app/services/ai/runtime/agentscope/session_lock.py`

---

## 四、AgentScope 未集成功能清单

### 4.1 Agent 运行时

| 功能 | 优先级 | 状态 |
|------|--------|------|
| AgentState 持久化 | **高** | ✅ 阶段 B 完成 |
| `compress_context` / `ContextConfig` | 中 | ⚠️ 配置项已接，主动压缩逻辑仍依赖 AgentScope 内置（长会话待观察） |
| `offloader` / `LocalWorkspace` | 中 | ✅ 完整 workspace 模式（builtin/skills/mcp 从 workdir 装配 + offload） |
| `ModelConfig.fallback_model` | 中 | ✅ `get_fallback_llm` + `build_model_config` |
| Agent Middleware | 中 | 未接 |
| `observe()` | 低 | 未接 |
| `TracingMiddleware` (OTel) | 低 | 自有 trace_buffer，可选 |

### 4.2 事件 / SSE

当前 `AssistantAgentRunner` 已处理：
`THINKING_BLOCK_DELTA`、`TOOL_CALL_*`、`TOOL_RESULT_TEXT_DELTA/END`、**`TOOL_RESULT_DATA_DELTA`**、
`REQUIRE_USER_CONFIRM`、**`REQUIRE_EXTERNAL_EXECUTION`**、`TEXT_BLOCK_DELTA`、`EXCEED_MAX_ITERS`

| 事件 | SSE 类型 | 状态 |
|------|----------|------|
| `TOOL_RESULT_DATA_DELTA` | `tool_result_data` | ✅ 已映射 |
| `REQUIRE_EXTERNAL_EXECUTION` | `external_execution_required` | ✅ 已映射 + Redis pending（`kind=external`） |
| `ExternalExecutionResult` resume | `POST /external-executions/{id}/resume` | ✅ runner + `AgentService` + HTTP SSE 已接 |

**observability 事件**（`event_stream.stream_observability_agentscope_events`，调试/前端可选消费）：
- [x] `ReplyStart/End` → `agent_reply`
- [x] `ModelCallStart/End` → `model_call`（含 tokens/duration_ms）
- [x] `ThinkingBlockStart/End` → `thinking`
- [x] `CustomEvent(state_updated)` / `state.summary` 变化 → `context_update` / `context_compression`

**仍未映射**（低优先级）：
- [ ] `TextBlockStart/End`、`DataBlockStart/Delta/End`
- [ ] `ToolCallEnd`、`ToolResultStart`
- [ ] `HintBlockEvent`（inbox 提示）
- [ ] 前端对 `tool_result_data` / `external_execution_required` / observability 事件的 UI 处理

### 4.3 工具体系

| 功能 | 优先级 | 说明 |
|------|--------|------|
| 全量 `RuntimeToolSpec` 迁移 | 高 | `tasks.md` 3.2–3.6 未完成 |
| `ContextConfig.tool_result_limit` 调优 | 中 | ✅ 配置项 `agentscope_tool_result_limit`（默认 2000） |
| `ToolGroup` + `ResetTools` | 低 | 动态工具组 |
| `TaskCreate/List/Get/Update` | 低 | 后台长任务 |
| AgentScope `MCPTool` | 不必 | 已有 `mcp_factory` |
| AgentScope `SkillViewer` | 不必 | 已有自研 skills |
| `permission_scope=dangerous` | 待评估 | 曾移除 `DANGEROUS_TOOL_NAMES` |

### 4.4 Workspace / 沙箱

| 功能 | 建议 |
|------|------|
| `LocalWorkspace` 完整模式 | ✅ General 有 `conversation_id` 时启用；`Docker/E2B` 未接 |
| `build_workspace_key` | 已实现；workdir=`{root}/{user}/{conversation}` |

### 4.5 模型 / Formatter / Embedding

| 功能 | 建议 |
|------|------|
| 多厂商原生 Model（Anthropic/Gemini/DashScope…） | 不必，网关 OpenAI 兼容够用 |
| Formatter 模块 | 不必，自有 `normalize_messages_for_llm` |
| AgentScope Embedding | 不必，自有 `EmbeddingClient` + RediSearch |

### 4.6 AgentScope App 服务层（整体不接）

- [ ] ~~RedisStorage / SessionRecord~~
- [ ] ~~RedisMessageBus / SSE 回放 / inbox~~
- [ ] ~~ChatService / Scheduler / Team 工具~~
- [ ] ~~HTTP Router（`/agents` `/sessions` `/stream`）~~

### 4.7 执行器覆盖（`tasks.md` 未完成项）

- [x] `data_executor` → `DataAgentRunner`（AgentScope native Agent + 显式状态守卫）
- [x] `rag_executor` 保持现状，不迁移 `RagAgentRunner`
- [x] `openclaw_executor` 保持现状，不迁移 `OpenClawAgentRunner`
- [x] General 无工具路径、`citation`、`memory_search` 强制等（`tasks.md` 5.2–5.4）

---

## 五、集成度总览

```
AgentScope 能力树                 集成度（估）
─────────────────────────────────────────────
Agent 核心 (ReAct/Toolkit)        █████████░  ~90%  General + ChatBI 原生 Agent
AgentState / 压缩 / offloader     ███████░░░  ~70%  General+ChatBI state；workspace offloader
事件 / SSE 全覆盖                 ████████░░  ~80%  event_stream + observability + external resume
工具 (builtin)                    █████░░░░░  ~50%  General workspace 模式；ChatBI 平台 SQL 工具
工具 (MCP/Skill/Task/Group)       █░░░░░░░░░  ~10%  自研替代
Workspace 沙箱                    █████░░░░░  ~50%  LocalWorkspace + offloader 已接
模型 (OpenAI)                     ████████░░  ~80%
模型 (多厂商原生)                   ░░░░░░░░░░   0%   网关够用
Formatter / Embedding             ░░░░░░░░░░   0%   自研替代
Middleware / Tracing              ░░░░░░░░░░   0%
App (Storage/Bus/Scheduler/Team)  ░░░░░░░░░░   0%   刻意不接
执行器覆盖                        ████████░░  ~80%  General + ChatBI；RAG/OpenClaw 保持现状
测试 (tests/ai)                   ██████████  100%  211/211 通过
```

---

## 六、建议排期（参考）

| 顺序 | 事项 | 预估 | 状态 |
|------|------|------|------|
| 1 | 阶段 A：permission Redis 持久化 | 1–2 天 | ✅ 完成 |
| 2 | 完成 `tasks.md` 工具全量 RuntimeToolSpec（3.2–3.6） | 按原任务 | 待做 |
| 3 | 阶段 B：General 工具链 AgentState | 3–5 天 | ✅ 完成 |
| 4 | 补关键事件映射（`ToolResultDataDelta`、ExternalExecution、observability） | 1–2 天 | ✅ 完成 |
| 5 | `ContextConfig` + token 监控 | 1 天 | ⚠️ 配置已接；`context_compression` 日志已接，指标待做 |
| 6 | ChatBI `DataAgentRunner` | 大项，见 tasks.md §8；当前已落地并通过离线测试 | ✅ 完成 |
| 7 | 阶段 C：分布式 session 锁 | 按需 | ✅ 完成 |
| 8 | External execution HTTP resume 端点 | 0.5–1 天 | ✅ 完成 |
| 9 | 多轮工具链集成测试（Read/Bash 不重复劳动） | 0.5 天 | 待做 |

---

## 七、相关文件索引

| 路径 | 说明 |
|------|------|
| `app/services/ai/runners/assistant_agent_runner.py` | 原生 Agent 主路径（state load/save、事件映射、resume） |
| `app/services/ai/runners/data_agent_runner.py` | ChatBI 原生 Agent（state/workspace/permission 对齐 General） |
| `app/services/ai/runtime/agentscope/event_stream.py` | General/ChatBI 共享事件映射（permission、DataDelta、external、observability） |
| `app/services/ai/runtime/agentscope/session_lock.py` | 分布式 session 锁（execute/resume 互斥） |
| `app/services/ai/runtime/agentscope/agent_runtime.py` | ContextConfig / ModelConfig / tools_fingerprint |
| `app/services/ai/runtime/agentscope/state_store.py` | AgentState Redis 存取 |
| `app/services/ai/runtime/agentscope/pending_store.py` | Permission / external pending Redis 快照 |
| `app/services/ai/runtime/agentscope/confirmations.py` | pending 注册表（Redis + 内存 fallback） |
| `app/services/ai/config.py` | `get_fallback_llm()` |
| `app/services/ai/agent_service.py` | `resume_agentscope_permission_stream` / `resume_agentscope_external_execution_stream` |
| `app/services/ai/memory_service.py` | UI/跨会话历史；删会话时清 agent_state |
| `app/core/llm/client.py` | `AgentScopeLLMHandle` |
| `tests/ai/runtime/test_agentscope_state_pending.py` | state/pending 单元测试 |
| `openspec/changes/refactor-to-agentscope/tasks.md` | 重构执行 checklist |
| `/Users/chenxiaolong/workspace/agentscope` | 上游源码参考 |

### 可配置项（ConfigService / DB）

| key | 默认 | 说明 |
|-----|------|------|
| `agentscope_context_trigger_ratio` | `0.8` | 触发 context 压缩的 token 占比 |
| `agentscope_context_reserve_ratio` | `0.1` | 压缩后保留占比 |
| `agentscope_tool_result_limit` | `2000` | 单条 tool result 字符上限 |
| `agentscope_workspace_root` | `data/agent_workspaces` | LocalWorkspace 根目录 |
| `llm_model_name` | `deepseek-chat` 等 | AgentScope `ModelConfig.fallback_model`（全局默认，主模型相同时不启用） |

---

## 八、待用户后续拍板的问题

- [ ] 是否默认仅「含 Bash/Read/Write 等内置工具」的 Agent 开启 AgentState？（当前：General 有工具即启用）
- [ ] `agent_state` 与 `conversation_id` 是否还要按 `agent_name` 拆分（多智能体同会话换 agent 时）？（当前：已按 `agent_name` 拆分 key）
- [x] ChatBI 走原生 AgentScope `Agent` + `DataAgentRunner` 显式状态守卫，不做 `DataTeamRunner` 手写状态机。
- [ ] 是否恢复 `dangerous` 权限 scope（拒绝自动执行）？
- [ ] 多实例部署时间表（决定阶段 C 紧迫性）？
- [x] External execution 由客户端执行，结果经 `POST /external-executions/{id}/resume` 回传（独立端点，不复用 permission confirm）。
