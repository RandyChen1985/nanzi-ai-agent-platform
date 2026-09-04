# MCP Client 资源白名单 Review 修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 MCP Client 资源白名单的服务端输入校验、策略详情泄露、旧授权码复活和 Agent 存在性泄露问题，并补齐测试、审计摘要与前端成功反馈。

**Architecture:** 在服务台 API 边界严格校验白名单输入，并通过现有 Agent、KnowledgeBase、Metadata 权限服务验证资源 ID；Client 列表只向所有者/管理员返回白名单明细，其他用户返回策略摘要。所有安全策略变化在同一事务中撤销 Token、Refresh Token、Grant 和未消费授权码，不新增数据库字段或迁移。

**Tech Stack:** Python 3.11、FastAPI、Pydantic 2、SQLAlchemy 2.x async、pytest、Vue 3、TypeScript、Tailwind CSS。

---

### Task 1: 固化白名单输入和资源归属校验

**Files:**
- Modify: `app/api/portal/endpoints/mcp_service.py:85-92, 149-292, 1628-1670`
- Test: `tests/api/test_mcp_service_desk_contract.py`

- [x] **Step 1: Write failing tests**

增加以下行为断言：字符串不能被拆成字符；非字符串 ID 被拒绝；资源候选必须来自当前用户可访问且启用的资源集合；创建和更新共用校验入口。

- [x] **Step 2: Run the focused tests and verify failure**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/api/test_mcp_service_desk_contract.py -k 'resource_policy or payload'
```

Expected: 新增的严格输入和资源归属测试失败，当前实现会把字符串拆成字符并接受不存在 ID。

- [x] **Step 3: Implement the smallest fix**

将 `_normalize_resource_ids` 改为只接受 `None` 或 `list`，列表元素必须是字符串；抽取按 `agent`、`knowledge_base`、`metadata_dataset` 返回当前用户可访问启用 ID 的异步 helper。创建接口和更新接口在持久化前调用同一 helper，发现不存在、停用或越权 ID 时返回 400，不把资源详情写入错误信息。

- [x] **Step 4: Run tests and verify pass**

Run the same focused command and then the complete API contract file. Expected: all tests pass.

---

### Task 2: 隐藏非所有者 Client 的白名单明细

**Files:**
- Modify: `app/api/portal/endpoints/mcp_service.py:294-337, 1210-1238`
- Modify: `frontend/src/views/McpServiceDesk.vue:9-329`
- Test: `tests/api/test_mcp_service_desk_contract.py`
- Test: `tests/frontend/test_frontend_mcp_service_desk_contract.py`

- [x] **Step 1: Write failing tests**

断言 `_serialize_client` 支持资源策略摘要，普通用户列举共享 Client 时不返回三个 `allowed_*_ids` 明细字段，所有者/管理员仍可编辑并收到明细。

- [x] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/api/test_mcp_service_desk_contract.py -k 'client_resource or client_list'
```

Expected: 当前序列化逻辑始终返回三个完整数组，新增测试失败。

- [x] **Step 3: Implement the smallest fix**

增加 `include_resource_policy` 参数和稳定的 `resource_policy_summary`，列表序列化时仅当 `admin` 或 `created_by == current_user_id` 才返回白名单数组；其他用户只返回每类的 `mode` 与 `count` 摘要。前端摘要和按钮颜色读取摘要字段，所有者的编辑弹框继续使用明细字段。

- [x] **Step 4: Run tests and type-check**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/api/test_mcp_service_desk_contract.py tests/frontend/test_frontend_mcp_service_desk_contract.py
cd frontend && ./node_modules/.bin/vue-tsc --noEmit
```

Expected: API/前端契约和类型检查全部通过。

---

### Task 3: 安全策略变化时失效未消费授权码

**Files:**
- Modify: `app/api/portal/endpoints/mcp_service.py:1740-1762`
- Modify: `app/services/mcp/platform_oauth.py:404-440`
- Test: `tests/api/test_mcp_service_desk_contract.py`
- Test: `tests/services/mcp/test_platform_oauth.py`

- [x] **Step 1: Write failing tests**

增加测试断言：安全策略变化更新该 Client 的未消费授权码后，兑换接口返回 `invalid authorization code`；授权码已经消费或已过期的记录不受影响。覆盖资源白名单、Scope、Redirect URI 等已有安全变化路径。

- [x] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/services/mcp/test_platform_oauth.py tests/api/test_mcp_service_desk_contract.py -k 'authorization_code or security_changed or resource_policy'
```

Expected: 当前未消费授权码仍可兑换，并会由 `_get_or_create_grant` 重新激活 Grant。

- [x] **Step 3: Implement the smallest fix**

在 `security_changed` 分支使用现有 `McpOAuthAuthorizationCode` 和 `expires_at`/`consumed_at` 字段，使该 Client 的未消费授权码立即失效；保持与 Token、Refresh Token、Grant 撤销处于同一个数据库事务，不新增迁移。必要时在兑换入口保留现有过期/消费判断，不再允许旧码重新激活 Grant。

- [x] **Step 4: Run focused OAuth tests**

Expected: 新增授权码失效测试和既有 OAuth 测试通过。

---

### Task 4: 统一 Agent 不存在和无权限错误

**Files:**
- Modify: `app/services/mcp/platform_mcp.py:504-530`
- Test: `tests/services/mcp/test_platform_mcp_methods.py`

- [x] **Step 1: Write failing tests**

用可控的 fake DB/Agent 权限结果调用 `_load_authorized_agent`，断言不存在、停用、Client 白名单拒绝和用户权限拒绝对外使用同一资源不可访问错误语义，并且不会先向调用方暴露 `agent_not_found`。

- [x] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/services/mcp/test_platform_mcp_methods.py -k 'agent'
```

Expected: 当前不存在/停用 Agent 抛出 `agent_not_found`，白名单拒绝抛出 `agent_forbidden`，测试失败。

- [x] **Step 3: Implement the smallest fix**

在 Platform MCP 边界统一映射为资源不可访问的 `PermissionError`，审计内部可以保留分类字段，但 MCP 响应不区分不存在、停用和无权限；不改变 Agent 管理页等其他历史调用路径。

- [x] **Step 4: Run method tests**

Expected: 新增 Agent 错误统一测试和现有方法测试通过。

---

### Task 5: 补齐审计、前端反馈、测试清单和旧工具名回归

**Files:**
- Modify: `app/api/portal/endpoints/mcp_service.py:1766-1776`
- Modify: `frontend/src/views/McpServiceDesk.vue:425-439`
- Modify: `tests/CHECKLIST.md`
- Modify: `tests/services/mcp/test_platform_mcp.py`
- Test: `tests/api/test_mcp_service_desk_contract.py`
- Test: `tests/frontend/test_frontend_mcp_service_desk_contract.py`

- [x] **Step 1: Write failing tests**

断言白名单审计包含资源类型和新值数量但不包含 ID；保存成功调用现有 `showToast`；清单中的前端测试数量与实际测试一致；旧 `knowledge.search` 测试改为当前对外工具名 `knowledge_search`。

- [x] **Step 2: Run tests and verify failure**

Run the focused API/frontend tests and the affected MCP regression file. Expected: 当前缺少数量/Toast，清单仍写 58，旧工具名回归出现 3 个失败。

- [x] **Step 3: Implement minimal updates**

在安全审计 `details` 中记录每类白名单的新值数量；前端成功保存后显示成功 Toast；同步 CHECKLIST；只更新与当前已生效工具名相关的旧回归断言，不改动业务行为。

- [x] **Step 4: Run the complete focused verification**

```bash
PYTHONPATH=. .venv/bin/pytest -q \
  tests/api/test_mcp_service_desk_contract.py \
  tests/services/mcp/test_platform_oauth.py \
  tests/services/mcp/test_platform_mcp.py \
  tests/services/mcp/test_platform_mcp_methods.py \
  tests/test_platform_mcp_migrations.py

pytest --confcutdir=tests/frontend -q tests/frontend/test_frontend_mcp_service_desk_contract.py
cd frontend && ./node_modules/.bin/vue-tsc --noEmit && npm run build
git diff --check
```

Expected: 本次相关测试全绿；真实数据库、OAuth 浏览器、Redis、RAGFlow、生产 MCP 客户端和服务启动仍由用户验收。

---

## 完成标准

- [x] 原始 API 无法保存错误类型、非法、停用或越权资源 ID。
- [x] 非所有者/无管理权限用户看不到完整白名单数组。
- [x] 所有安全策略变化会失效未消费授权码，且不新增数据库迁移。
- [x] Agent 不存在、停用和无权限不再向 MCP 调用方泄露存在性差异。
- [x] 审计含资源类型/数量摘要，不含原始 ID 或凭证。
- [x] 前端保存成功有反馈，测试清单和工具名回归同步。
- [x] 定向测试、类型检查、构建和差异检查通过；不宣称 live 环境验收完成。
