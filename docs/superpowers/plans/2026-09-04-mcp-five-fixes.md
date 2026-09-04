# MCP 五项问题修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 MCP 凭据暴露、连接失败缓存残留、SSE 调用恢复、嵌套 Schema 丢失和工具结果输出不规范五项问题。

**Architecture:** 保留现有数据库字段和运行时边界。认证头采用现有加密管理器的带前缀加密值并兼容旧明文；Authorization 使用独立开关和固定 Bearer 前缀，其他 Header 在编辑时以脱敏值展示并通过增量更新保存，API 响应不回传原文。MCP 工具保留原始 JSON Schema，运行时对传输错误只做一次重连重试，并在模型边界统一序列化和截断结果。

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, SQLAlchemy 2 async, pytest, Vue 3, TypeScript.

---

### Task 1: 认证头安全读写与响应脱敏

**Files:**
- Modify: `app/services/mcp/mcp_auth_policy.py`
- Modify: `app/api/portal/endpoints/mcp.py`
- Modify: `app/models/mcp.py`
- Modify: `frontend/src/components/system/McpServerRegistry.vue`
- Test: `tests/services/mcp/test_mcp_auth_policy.py`
- Test: `tests/frontend/test_mcp_user_context_management_contract.py`

- [x] **Step 1: Write failing tests**

增加以下行为断言：加密格式可被读取，旧 JSON 明文仍可读取；Server 列表响应不包含认证头原文；编辑表单在未触碰认证区域时不发送 `auth_headers`；触碰后才发送新值。

- [x] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/services/mcp/test_mcp_auth_policy.py tests/frontend/test_mcp_user_context_management_contract.py`

Expected: 新增的脱敏/加密断言失败，现有行为可能仍通过。

- [x] **Step 3: Implement minimal credential protection**

在认证策略模块使用带明确前缀的 `encrypt_mcp_auth_headers()` / `resolve_mcp_auth_headers()`；对非前缀值按历史 JSON 明文兼容读取。写入接口通过统一 helper 保存加密值。`McpServerResponse` 不再暴露原始 `auth_headers`，改为提供 Authorization 配置状态和其他 Header 脱敏摘要；创建/更新响应同样脱敏。前端将 Authorization 拆为独立开关，固定展示 `Bearer` 前缀；其他 Header 编辑时展示 `********`，点击编辑后提交 `auth_headers_patch` 增量，未修改项由后端保留。

- [x] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/services/mcp/test_mcp_auth_policy.py tests/frontend/test_mcp_user_context_management_contract.py`

Expected: PASS。

### Task 2: 连接失败清理

**Files:**
- Modify: `app/services/ai/tools/mcp_client.py`
- Test: `tests/ai/tools/test_mcp_p0_p1_p2_fixes.py`

- [x] **Step 1: Write the failing test**

新增测试：mock `_load_server` 返回服务器，mock `McpSseSession.connect` 抛出异常，断言 `get_session()` 抛出原异常、失败对象 `close()` 被调用且对应 cache key 已移除。

- [x] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/ai/tools/test_mcp_p0_p1_p2_fixes.py -k failed_connection_cleanup`

Expected: FAIL，当前失败对象仍留在 `_sessions`。

- [x] **Step 3: Implement minimal cleanup**

在 `get_session()` 的 `await session.connect()` 外增加异常处理；关闭失败会话，并在锁保护下仅当 `cls._sessions.get(cache_key) is session` 时移除缓存，然后重新抛出原异常。

- [x] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/ai/tools/test_mcp_p0_p1_p2_fixes.py -k failed_connection_cleanup`

Expected: PASS。

### Task 3: SSE 工具调用一次恢复重试

**Files:**
- Modify: `app/services/ai/tools/mcp_client.py`
- Test: `tests/test_mcp_session_recovery.py`

- [x] **Step 1: Write failing tests**

新增两个测试：第一次 SSE `call_tool()` 抛出 `ConnectionError` 后，关闭、重连并成功返回；第一次返回 MCP `isError` 业务结果时，不发生重连且只调用一次。

- [x] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_mcp_session_recovery.py -k "sse_tool_call"`

Expected: 传输异常测试 FAIL，当前直接关闭并抛错；业务错误测试用于锁定不重试语义。

- [x] **Step 3: Implement minimal retry**

抽取 SSE `call_tool` 单次调用 helper。仅捕获连接/传输异常集合；关闭并重新连接后最多重放一次。MCP 正常返回的 `isError` 仍直接转为业务错误，不触发重试；两次传输失败后保留现有错误包装。

- [x] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_mcp_session_recovery.py -k "sse_tool_call"`

Expected: PASS。

### Task 4: 保留并导出原始嵌套 JSON Schema

**Files:**
- Modify: `app/services/ai/tools/mcp_factory.py`
- Modify: `app/services/ai/runtime/agentscope/tools.py`
- Modify: `app/services/ai/runtime/agentscope/chat.py`
- Test: `tests/ai/runtime/test_agentscope_tool_evidence.py`

- [x] **Step 1: Write the failing test**

构造含嵌套 object、array items、required 和 enum 的 `McpToolCache`，创建工具后分别检查 AgentScope runtime spec 和 OpenAI schema，断言嵌套结构与原 Schema 一致。

- [x] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/ai/runtime/test_agentscope_tool_evidence.py -k nested_mcp_schema`

Expected: FAIL，当前动态模型只导出裸 `dict`/`list`。

- [x] **Step 3: Implement minimal schema preservation**

在 MCP 工具包装对象上保存解析后的原始 Schema，并让 `_schema_from_legacy_tool()` 与 `legacy_tools_to_openai_schemas()` 对该标记字段优先返回原 Schema；动态 `args_schema` 仍保留用于执行参数校验。

- [x] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/ai/runtime/test_agentscope_tool_evidence.py -k nested_mcp_schema`

Expected: PASS。

### Task 5: 结果 JSON 化与统一截断

**Files:**
- Modify: `app/services/ai/runtime/agentscope/tools.py`
- Test: `tests/ai/runtime/test_agentscope_tool_evidence.py`

- [x] **Step 1: Write failing tests**

新增测试：调用返回字典时，`ToolChunk` 文本可被 `json.loads()` 解析且保留中文；返回超过现有上下文上限的结果时，文本长度受限并包含截断提示。

- [x] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/ai/runtime/test_agentscope_tool_evidence.py -k "json_result or truncate_result"`

Expected: FAIL，当前使用 Python `str()` 且没有在该边界截断。

- [x] **Step 3: Implement minimal serialization**

增加工具结果格式化 helper：字符串原样返回，其他值用 `json.dumps(..., ensure_ascii=False, default=str)`；调用现有 `truncate_for_context()`，截断后追加固定中文提示。保持审计/业务返回对象不变，只限制送入 AgentScope `TextBlock` 的文本。

- [x] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/ai/runtime/test_agentscope_tool_evidence.py -k "json_result or truncate_result"`

Expected: PASS。

### Task 6: 全量定向验证与文档清单

**Files:**
- Modify: `tests/CHECKLIST.md`（仅在新增自动化测试确实需要登记时更新）
- Review: `docs/superpowers/specs/2026-09-04-mcp-five-fixes-design.md`

- [x] **Step 1: Run backend MCP regression**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_mcp_session_recovery.py tests/ai/tools/test_mcp_p0_p1_p2_fixes.py tests/services/mcp/test_mcp_auth_policy.py tests/ai/runtime/test_agentscope_tool_evidence.py`

Expected: PASS，且没有新增警告。

- [x] **Step 2: Run frontend contracts and type check**

Run: `PYTHONPATH=. .venv/bin/python -m pytest --confcutdir=tests/frontend -q tests/frontend/test_mcp_user_context_management_contract.py`；再在 `frontend` 目录运行 `./node_modules/.bin/vue-tsc --noEmit`。

Expected: PASS。

- [x] **Step 3: Run quality checks**

Run: `PYTHONPATH=. .venv/bin/python -m ruff check app tests` 和 `git diff --check`。

Expected: PASS。

- [x] **Step 4: Report boundaries**

记录定向测试结果；明确未执行真实 MCP、OAuth、浏览器、数据库和服务启动验收。确认无迁移文件变更。
