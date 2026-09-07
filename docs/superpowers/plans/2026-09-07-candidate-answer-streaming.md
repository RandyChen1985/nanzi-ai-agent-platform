# Candidate Answer Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不削弱取证、工具调用和多智能体既有边界的前提下，让无须当轮证据的一般对话首段正文直接流入回答气泡；若随后出现非 `todo_write` 工具调用，则撤回候选正文并将其作为过程说明保留。

**Architecture:** 由 `AssistantAgentRunner` 在建立 AgentScope 流状态时一次性判定是否允许候选正文；`process_narration` 状态机负责候选正文、撤回和过程说明的原子转换；前端沿用已有的 `answer_delta` 与 `retraction` 消费路径，保证 rAF 缓冲不会在撤回后回写旧文本；性能采集分别记录首个可见事件和首个正文片段。

**Tech Stack:** Python 3.11、FastAPI/AgentScope、Vue 3 + TypeScript、pytest、vue-tsc。

---

### Task 1: 先以状态机测试锁定“候选正文 -> 撤回 -> 过程说明”的事件序列

**Files:**
- Modify: `tests/ai/runtime/test_process_narration.py`
- Modify: `app/services/ai/runtime/agentscope/event_stream.py`
- Modify: `app/services/ai/runtime/agentscope/process_narration.py`

- [x] **Step 1: 写会失败的状态机用例。** 在 `test_process_narration.py` 新增三类测试：
  1. `new_native_stream_state(candidate_answer_enabled=True)` 的首个文本增量仅产生 `{"type": "answer_delta", "phase": "candidate"}`，并同步进入 `full_content`；
  2. 候选文本后开始普通工具时，事件严格为 `retraction(final=False, content=<撤回后的已确认正文>)`、`process_narration`、`process_narration_commit`，候选文本不再留在 `full_content`，但存在于 `process_narration`；
  3. 候选文本后仅调用 `todo_write` 并结束时，不产生 `retraction`，不重复 `answer_delta` 或 `process_narration_promote`，正文保留一次。

- [x] **Step 2: 执行该文件，确认当前实现在新断言上失败。**

  Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/runtime/test_process_narration.py`

  Expected: 新增的 candidate 相关断言失败；既有保守流式用例仍通过。

- [x] **Step 3: 为状态补齐显式字段。** 在 `new_native_stream_state()` 增加参数 `candidate_answer_enabled: bool = False`，并写入 `candidate_answer_enabled`、`candidate_reply_start`；候选正文复用既有 `pending_reply_text`，避免重复维护文本状态。默认值必须保持 `False`，从而保证其他调用方和多智能体链路仍使用现有“过程说明后提升”的协议。

- [x] **Step 4: 实现不泄露正文的原子状态转换。** 在 `process_narration.py`：
  - 在 `on_text_delta()` 中，仅当开关开启、尚未有非记账工具、且当前模型回复没有被工具打断时，把增量作为 `answer_delta`（加 `phase: "candidate"`）直接输出；同时记录候选起点和候选文本，并把该文本写进 `full_content` 以保持 reconcile 对齐。
  - 在 `on_tool_call_start()` 遇到非 `todo_write` 时，先把 `full_content` 截断到候选起点，清除候选标记，再输出 `retraction`（`final=False`，内容为仍然确认的正文），最后调用现有 `_commit_pending_as_narration()` 把原候选改为过程说明并输出 commit。严格保持 SSE 顺序：撤回在过程说明之前。
  - `todo_write` 保持现有“不触发撤回”语义。
  - 在 `on_model_call_end()` 中，成功结束的候选只清理候选临时字段，不再发送 `process_narration_promote`；保守链路维持原有 promote 行为。
  - 在 `on_model_call_start()` 中只清理上一模型调用未完成的候选临时字段，不能丢弃已经确认的 `full_content` 或已提交的过程说明。

- [x] **Step 5: 验证 reconcile 保护。** 候选成功时 `full_content` 已含正文，既有 `compute_stream_reconcile_gap` 返回空差额；候选撤回后正文已截断，既有旁白剥离逻辑只处理已提交的 `process_narration`。`test_stream_reconcile.py` 已回归通过，无需扩展提取函数。

- [x] **Step 6: 重新运行状态机测试。**

  Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/runtime/test_process_narration.py`

  Expected: 全部通过；候选、撤回、todo 与旧的保守提升逻辑均被覆盖。

### Task 2: 只在安全的一般对话链路开启候选正文

**Files:**
- Modify: `tests/ai/runners/test_assistant_agent_grounding_gate.py`
- Modify: `app/services/ai/runners/assistant_agent_runner.py`

- [x] **Step 1: 写失败的 eligibility 单元测试。** 对一个独立的 runner 判断方法断言：
  - `turn_decision.turn_kind == "general"`、当前轮 `FactRequirement.required is False`、`buffer_output is False` 时返回 `True`；
  - `data_query`、`knowledge` / `web` 取证、严格缓冲、`grounding_requirement.required is True` 或 `evidence_contracts` 非空时均返回 `False`；
  - 未携带 `turn_decision` 时返回 `False`（宁可保守，不让旧入口意外放开）。

- [x] **Step 2: 执行该测试，确认新增断言失败。**

  Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/runners/test_assistant_agent_grounding_gate.py`

  Expected: 仅因尚无 eligibility 方法或开关传递而失败。

- [x] **Step 3: 添加一个窄的私有判断方法。** 在 `AssistantAgentRunner` 添加 `_should_enable_candidate_answer_streaming(...)`，显式接收 `grounding_requirement`、`buffer_output`、`evidence_contracts`；它只允许确认的 `general` 轮次且没有证据责任，不能根据自然语言猜测放开 data / knowledge / search 路由。

- [x] **Step 4: 将判断结果传入原生流状态。** 扩展 `_execute_with_agentscope_native_agent()` 的参数，接收该布尔值，并在 `new_native_stream_state(...)` 调用中传入。调用原生执行的主路径以同一轮已计算的 `grounding_requirement`、`buffer_output` 和实际 `evidence_contracts` 计算开关；恢复执行、严格工具预检、委派/综合等无法完整证明同一前提的入口显式传 `False`。

- [x] **Step 5: 用 runner 测试验证 eligibility 边界。** 断言安全 general 路径返回 `True`，其余四类路径及缺少 `turn_decision` 时均为 `False`；主执行路径将该结果直接传入 `new_native_stream_state(...)`，不需要启动模型、服务或数据库。

- [x] **Step 6: 回归两个后端测试文件。**

  Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/runtime/test_process_narration.py tests/ai/runners/test_assistant_agent_grounding_gate.py`

  Expected: 全部通过。

### Task 3: 前端确认撤回先使 rAF 正文缓冲失效，再走公共 dispatcher

**Files:**
- Modify: `tests/frontend/test_chat_shared_helpers_behavior.py`
- Modify: `frontend/src/views/EmbedChat.vue`
- Modify: `frontend/src/utils/agentscopeSseHandlers.ts`（仅在测试暴露不足时调整；不复制已有 retraction 逻辑）

- [x] **Step 1: 添加前端契约测试。** 新增静态/行为断言：`EmbedChat.vue` 的 `handleBufferedBodyEvent` 对 `retraction` 清空 `pendingContentBuffer` 并取消 `contentRafId`，且在主 SSE 循环中发生在 `dispatchAgentscopeStreamEvent(...)` 前；`answer_delta` 带 `phase: "candidate"` 仍按普通正文进入气泡；公共 dispatcher 对 `retraction(final=false)` 不结束思考态且将正文替换为服务端提供的确认内容。

- [x] **Step 2: 运行前端契约测试，先确认实际行为。**

  Run: `PYTHONPATH=. .venv/bin/pytest --confcutdir=tests/frontend -q tests/frontend/test_chat_shared_helpers_behavior.py`

  Expected: 新增断言可验证现有路径；若任一事件序序或 payload 行为不符则先失败。

- [x] **Step 3: 仅在失败处做最小实现。** 新契约已证明既有实现满足该顺序，未增加第二套前端协议。

- [x] **Step 4: 确保 Debug 页面复用公共 dispatcher。** 保持复用公共 dispatcher，未增加第二套 candidate 协议。

- [x] **Step 5: 运行前端契约与类型检查。**

  Run: `PYTHONPATH=. .venv/bin/pytest --confcutdir=tests/frontend -q tests/frontend/test_chat_shared_helpers_behavior.py`

  Run: `./node_modules/.bin/vue-tsc --noEmit`（工作目录：`frontend`）

  Expected: 两项均通过；没有把候选正文重放到过程时间线，也没有撤回后 rAF 回填旧正文。

### Task 4: 将性能语义从单一 TTFT 扩展为可见活动与首段正文两个不含内容的指标

**Files:**
- Modify: `tests/ai/test_execution_observability.py`
- Modify: `app/services/ai/runtime/execution_observability.py`

- [x] **Step 1: 增加失败的时间序列测试。** 使用可控 `clock` 断言：`process_narration` 首次出现设置 `first_visible_activity_ms` 但不设置 `ttft_ms`；随后 `process_narration_promote` 设置正文 TTFT；候选 `answer_delta` 同时设置两项；`log`、`reasoning_content`、空内容和 `retraction` 都不设置任何首见指标。

- [x] **Step 2: 运行观测测试，确认新字段和事件语义尚未实现。**

  Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/test_execution_observability.py`

  Expected: 新增断言失败；现有 TTFT 行为作为兼容基线保留。

- [x] **Step 3: 实现不记录正文的双指标。** 保留 `ttft_ms` 字段名作为“首个可见正文片段”，将 `process_narration_promote` 纳入正文事件集合；新增 `first_visible_activity_ms`，仅统计用户实际可见的 `process_narration`、`process_narration_promote`、普通正文和 `answer_delta`。两项均只存时间，不存文本。

- [x] **Step 4: 运行观测测试。**

  Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/test_execution_observability.py`

  Expected: 全部通过，输出快照不包含任何正文内容。

### Task 5: 做跨责任链回归与提交前卫生检查

**Files:**
- Modify: `tests/CHECKLIST.md`（仅在其中已有对应聊天流式清单时补充候选-撤回回归项）
- Verify: `app/services/ai/runtime/agentscope/process_narration.py`
- Verify: `app/services/ai/runners/assistant_agent_runner.py`
- Verify: `frontend/src/views/EmbedChat.vue`
- Verify: `frontend/src/utils/agentscopeSseHandlers.ts`

- [ ] **Step 1: 以公共责任链跑定向测试集。**

  Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/runtime/test_process_narration.py tests/ai/runtime/test_stream_reconcile.py tests/ai/test_execution_observability.py tests/ai/test_multi_agent_orchestrator.py tests/ai/runtime/test_conversation_run_cancel.py`

  Run: `PYTHONPATH=. .venv/bin/pytest --confcutdir=tests/frontend -q tests/frontend/test_chat_shared_helpers_behavior.py tests/frontend/test_conversation_run_status_contract.py`

  Expected: 通过。失败时先将其分类为本次候选协议回归、既有失效测试，或环境缺失；不得以跳过测试掩盖。

- [ ] **Step 2: 运行类型检查和差异卫生检查。**

  Run: `./node_modules/.bin/vue-tsc --noEmit`（工作目录：`frontend`）

  Run: `git diff --check`

  Expected: 无 TypeScript 错误、无空白错误。

- [ ] **Step 3: 人工按事件表检查三条 SSE 序列。** 对照测试输出或 mock 流确认：
  - general 无工具：`answer_delta(candidate)*`，结束后没有 promote/retraction；
  - general 后普通工具：`answer_delta(candidate)* -> retraction(final=false) -> process_narration -> process_narration_commit -> 工具事件 -> 保守后续正文`；
  - data/knowledge/search 或要求证据：从第一个字符起仍是原 `process_narration -> promote` 协议。

- [ ] **Step 4: 报告未覆盖的真实环境边界。** 明确说明本轮不启动 `./dev.sh`，也不调用真实模型、外部工具、Redis 或浏览器；这些仅能在用户启动服务后用实际对话验收。
