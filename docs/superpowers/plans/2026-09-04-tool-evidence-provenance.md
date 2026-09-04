# 工具证据来源与执行卡片一致性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让通用证据型工具在命中时强制当前轮调用，并在没有匹配证据时阻止具体事实，同时统一思考卡片和 trace 的工具可观测性。

**Architecture:** 在现有 `ToolMetadata`/`ToolNudge` 上补充证据型只读工具识别和预检异常兜底，在 Assistant runner 的预检 SSE 中传递当前轮证据要求；`_execute_raw()` 根据该事件暂存答案并复用现有 `EvidenceLedger`/`GroundingService` 做最终门禁，同时排除历史复用收据。工具日志和 trace 只做契约补齐，不引入新的数据库模型。

**Tech Stack:** Python 3.11、FastAPI runner、AgentScope 2.x、pytest、现有 Vue SSE 日志协议。

---

### Task 1: 扩展证据型工具 metadata 和通用 nudge

**Files:**
- Modify: `app/services/ai/tool_policy.py:848-870`
- Modify: `app/services/ai/tool_nudge_policy.py:1138-1158`
- Test: `tests/ai/test_tool_policy.py`
- Test: `tests/ai/test_tool_nudge_policy.py`

- [x] **Step 1: Write the failing tests**

增加一个只读 MCP `SimpleNamespace`，包含 `source_type="mcp"`、`permission_scope="read"` 和 `evidence_types={EvidenceType.EXTERNAL_TOOL}`；断言 metadata 的 `nudge_mode` 为 `evidence`。再增加一个通用 nudge 测试，断言命中该工具时 `should_force_first_call is True`。

- [x] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/test_tool_policy.py tests/ai/test_tool_nudge_policy.py`

Expected: 新增 MCP metadata 测试显示 `nudge_mode` 仍为 `fallback`，通用 nudge 测试显示没有强制首调用。

- [x] **Step 3: Write minimal implementation**

在 `resolve_tool_metadata()` 中，仅当工具已有 evidence types 且为只读，或来源为 MCP 且权限为 `read` 时返回动态证据 metadata；在通用 best-tool 分支先解析 metadata，再把 `force_first_call` 设置为 metadata 为 evidence 且工具具有 evidence types。

- [x] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/test_tool_policy.py tests/ai/test_tool_nudge_policy.py`

Expected: 新增测试及现有工具促发测试通过，未知工具仍保持中性、不强制。

补充边界：低相关度证据 nudge 也必须锁定具体工具；运行时写权限优先于按名称推断的已知 metadata。

### Task 2: 让预检日志表达当前轮证据要求

**Files:**
- Modify: `app/services/ai/runners/assistant_agent_runner.py:1375-1420`
- Test: `tests/ai/runners/test_assistant_agent_grounding_gate.py`

- [x] **Step 1: Write the failing test**

构造一个带 `evidence_types` 的证据型工具，调用 runner 的预检辅助逻辑或隔离预检事件构造逻辑，断言日志包含 `current_turn_evidence_required=True` 和对应的 `required_evidence_types`，普通 `todo_write` 不携带该标记。

- [x] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/runners/test_assistant_agent_grounding_gate.py -k evidence`

Expected: 预检日志缺少当前轮证据字段。

- [x] **Step 3: Write minimal implementation**

预检选中工具后，从已绑定工具按名称取出 evidence types；只有 nudge 强制首调用且 evidence types 非空时设置 `current_turn_evidence_required`，并把枚举值转换为 JSON 字符串列表。

- [x] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/runners/test_assistant_agent_grounding_gate.py -k evidence`

Expected: 证据型工具事件字段正确，其他预检事件保持兼容。

### Task 3: 增加当前轮证据最终门禁

**Files:**
- Modify: `app/services/ai/runners/assistant_agent_runner.py:335-345,900-1010`
- Test: `tests/ai/runners/test_assistant_agent_grounding_gate.py`

- [x] **Step 1: Write the failing tests**

增加两个异步测试：一个 fake core 先发送当前轮证据预检日志再发送具体事实，断言原事实不在最终 answer delta 中且存在 `grounding_blocked` 日志；另一个发送同样预检日志并预先向 ledger 写入匹配 `EXTERNAL_TOOL` 收据，断言具体事实正常放行。再覆盖“本轮没有匹配结果，无法确认”这类非事实文本可以放行。

- [x] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/runners/test_assistant_agent_grounding_gate.py -k current_turn`

Expected: 当前实现仍把具体事实直接发送给调用方，或无法识别预检事件。

- [x] **Step 3: Write minimal implementation**

在 `_execute_raw()` 的 merge loop 中识别证据预检事件；将答案 delta/无类型正文暂存，构造 required `FactRequirement` 并记录来源为 `tool_preflight`。结束时若 `GroundingService.audit()` 返回 warning，则丢弃暂存具体事实，发送一条错误状态 grounding 日志和安全 answer delta；非事实安全回复继续放行。

- [x] **Step 4: Run tests to verify it passes**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/runners/test_assistant_agent_grounding_gate.py -k current_turn`

Expected: 无证据具体事实被阻止，有匹配收据的回答放行，安全拒答放行。

补充边界：严格审计排除 `reuse_previous` 历史收据；预检异常且存在证据型只读工具时 fail-closed。

### Task 4: 补齐工具日志类别并修正 trace 步骤编号

**Files:**
- Modify: `app/services/ai/runtime/agentscope/event_stream.py:579-588`
- Modify: `app/services/ai/runners/assistant_agent_runner.py:2660-2670`
- Modify: `tests/ai/runtime/test_event_stream_observability.py`
- Modify: `tests/ai/runtime/test_tool_result_observability.py`

- [x] **Step 1: Write the failing tests**

断言标准 `TOOL_CALL_START` 日志的 `category` 为 `tool`；断言工具观察构造会递增 `step_counter`，并生成比调用前更大的 `step_number`。

- [x] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/runtime/test_event_stream_observability.py tests/ai/runtime/test_tool_result_observability.py`

Expected: 工具开始日志没有 category，工具 observation 复用旧 step number。

- [x] **Step 3: Write minimal implementation**

在工具开始和工具完成日志中显式设置 `category="tool"`；创建 `AgentExecutionStep` 时调用现有 `_increment_step()`，不改变 trace span 的其他字段。

- [x] **Step 4: Run tests to verify it passes**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/runtime/test_event_stream_observability.py tests/ai/runtime/test_tool_result_observability.py`

Expected: 工具日志和 trace 顺序契约通过。

### Task 5: 回归验证和文档清单

**Files:**
- Modify: `tests/CHECKLIST.md`

- [x] **Step 1: Run focused backend tests**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/test_tool_policy.py tests/ai/test_tool_nudge_policy.py tests/ai/runners/test_assistant_agent_grounding_gate.py tests/ai/runtime/test_event_stream_observability.py tests/ai/runtime/test_tool_result_observability.py`

Expected: 与本次逻辑相关的测试全部通过；旧测试契约失败要单独记录，不得混入本次修复结论。

- [x] **Step 2: Run diff checks and frontend contracts**

Run: `git diff --check` and `pytest --confcutdir=tests/frontend -q tests/frontend/test_internal_context_display_sanitization.py tests/frontend/test_reusable_result_contract.py`

Expected: 改动无空白错误，前端仍隐藏内部摘要且保留复用提示。

- [x] **Step 3: Update the checklist**

在主助手工具预检条目中补充“证据型只读工具默认强制首调用；最终门禁阻止无当轮证据的具体事实；复用结果单独标记来源”。

- [x] **Step 4: Report live acceptance boundary**

明确说明未启动服务、未连接真实 MCP、未执行浏览器/数据库验收；提醒用户手动启动后用一个未命名的只读 MCP 工具和一个明确刷新请求验证思考卡片与 trace 是否同时出现。
