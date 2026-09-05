# 快捷结果追问路由修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 保留会话上下文的同时，让来自可信查数结果的通用 `quick:` 快捷追问重新进入查数取证链。

**Architecture:** 在前端给快捷事件增加可选的来源元数据，仅对带可信 ChatBI 结果的消息设置 `requires_fresh_data`；后端以该元数据作为当前轮路由/证据合同信号。普通快捷按钮继续走原有发送逻辑，内部元数据不进入用户可见文本。

**Tech Stack:** Vue 3 + TypeScript、FastAPI/Python 3.11、pytest、现有 ChatBI/grounding/TurnDecision 链路。

---

### Task 1: 锁定快捷事件与请求元数据契约

**Files:**
- Modify: `frontend/src/components/MessageRenderer.vue`
- Modify: `frontend/src/views/EmbedChat.vue`
- Test: `tests/frontend/test_quick_result_routing_contract.py`

- [x] **Step 1: Write the failing test**

断言共享渲染器的快捷事件支持可选来源载荷；消息级快捷按钮能传递 ChatBI 结果来源；欢迎卡等普通入口仍调用普通处理器。

- [x] **Step 2: Run test to verify it fails**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_quick_result_routing_contract.py -q`

Expected: FAIL，因为当前事件只发出字符串，消息级绑定也没有区分 ChatBI 来源。

- [x] **Step 3: Write minimal implementation**

将事件载荷设计为向后兼容的对象：`{ question, source?: "chatbi_result", resultId?: string, requiresFreshData?: boolean }`。只有 `msg.chatbiInsight?.result_id` 或已确认的查数结果状态存在时，EmbedChat 才生成该载荷；普通快捷入口仍传字符串。

- [x] **Step 4: Run test to verify it passes**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_quick_result_routing_contract.py -q`

Expected: PASS。

### Task 2: 将来源标记接入发送请求但不暴露给页面

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue`
- Test: `tests/frontend/test_quick_result_routing_contract.py`

- [x] **Step 1: Write the failing test**

断言 `handleQuickQuestion` 或其发送快照能保存一次性 ChatBI 追问元数据，`sendMessageInternal` 将其放入请求体的内部 `turn_metadata`，且用户消息内容只保留问题文本。

- [x] **Step 2: Run test to verify it fails**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_quick_result_routing_contract.py -q`

Expected: FAIL，因为当前请求体没有快捷来源字段。

- [x] **Step 3: Write minimal implementation**

增加一次性 `quick_context` 状态并在发送前消费；请求体只增加内部元数据字段，不拼接到用户问题，不改变普通按钮的 `agent_id` 选择。

- [x] **Step 4: Run test to verify it passes**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_quick_result_routing_contract.py -q`

Expected: PASS。

### Task 3: 服务端识别 ChatBI 快捷追问并建立当前轮查数要求

**Files:**
- Modify: `app/services/ai/quick_result_context.py`
- Modify: `app/services/ai/context_manager.py`
- Modify: `app/services/ai/agent_service.py`
- Modify: `app/api/v1/endpoints/chat.py`
- Test: `tests/ai/test_quick_result_routing.py`

- [x] **Step 1: Write the failing test**

覆盖带 `source=chatbi_result` 且 `requires_fresh_data=true` 的请求：必须得到内部数据能力、禁止复用旧结果，并在没有当前轮凭证时进入安全引导；不带标记的普通请求保持原决策。

- [x] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/pytest tests/ai/test_quick_result_routing.py -q`

Expected: FAIL，因为当前请求决策没有接收快捷来源元数据。

- [x] **Step 3: Write minimal implementation**

在现有 `TurnDecision`/请求决策边界增加结构化、可选的快捷上下文字段。仅当来源为 `chatbi_result` 且明确要求新鲜数据时，将当前轮升级为数据查询/查数证据要求；历史结果只作为查询条件上下文，不作为当前轮凭证。

- [x] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/pytest tests/ai/test_quick_result_routing.py -q`

Expected: PASS。

### Task 4: 回归验证其他快捷入口和现有证据链

**Files:**
- Modify: `tests/CHECKLIST.md`

- [x] **Step 1: Run targeted frontend and backend tests**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_quick_result_routing_contract.py tests/frontend/test_general_message_continue_analysis_contract.py -q`。

Run: `PYTHONPATH=. .venv/bin/pytest tests/ai/test_quick_result_routing.py tests/ai/runners/test_assistant_agent_grounding_gate.py tests/ai/grounding/test_tool_result_envelope.py -q`。

Expected: all targeted tests pass。

- [x] **Step 2: Run static checks for the intended diff**

Run: `git diff --check`。

Expected: no whitespace errors；不运行 `./dev.sh`，不执行数据库迁移或生产操作。
