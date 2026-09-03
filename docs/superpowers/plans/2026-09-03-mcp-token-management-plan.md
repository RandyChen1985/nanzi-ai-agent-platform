# MCP Client Token 管理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 MCP Client 提供准确的 Access Token 生命周期统计、显式管理入口和单条/批量物理删除能力。

**Architecture:** 后端在现有 Client 可见范围和 Token 权限校验基础上计算统计，并新增只删除 `McpOAuthAccessToken` 的接口；前端复用现有 Token 生命周期弹窗，增加状态筛选、删除操作和卡片摘要，不改变 Grant/Refresh Token 语义。

**Tech Stack:** FastAPI、SQLAlchemy 2.x async、pytest 契约测试、Vue 3、TypeScript、Tailwind CSS、vue-tsc。

---

### Task 1: 后端 Token 统计与删除契约

**Files:**
- Modify: `app/api/portal/endpoints/mcp_service.py`
- Test: `tests/api/test_mcp_service_desk_contract.py`

- [ ] **Step 1: 写失败契约测试**

在 API 契约测试中断言 Client 序列化包含总数、有效、临期、过期、撤销统计，并存在单条物理删除路由；同时断言删除只针对 `McpOAuthAccessToken`，不会调用 Grant/Refresh 删除。

- [ ] **Step 2: 运行测试确认失败**

运行：`pytest --confcutdir=tests/frontend tests/api/test_mcp_service_desk_contract.py -q`

预期：新增字段和 `DELETE /clients/{client_id}/tokens/{token_id}` 断言失败。

- [ ] **Step 3: 实现最小后端逻辑**

为 `_serialize_client` 增加 `token_total_count`、`expiring_token_count`、`expired_token_count`、`revoked_token_count`；在 `list_clients` 按当前用户对共享 Client 的可见 Token 范围统计，临期定义为未来 24 小时内到期。新增 DELETE 路由，复用 Client/Token 所有权校验，写脱敏审计后删除 Access Token 行并提交。

- [ ] **Step 4: 运行测试确认通过**

运行：`pytest --confcutdir=tests/frontend tests/api/test_mcp_service_desk_contract.py -q`

预期：API 契约测试通过。

### Task 2: 前端 Client 卡片和 Token 管理交互

**Files:**
- Modify: `frontend/src/views/McpServiceDesk.vue`
- Test: `tests/frontend/test_frontend_mcp_service_desk_contract.py`

- [ ] **Step 1: 写失败前端契约测试**

断言 Client 类型包含生命周期统计字段，卡片提供独立 Token 管理入口，弹窗包含状态筛选、统计摘要、单条物理删除和批量物理删除调用。

- [ ] **Step 2: 运行测试确认失败**

运行：`pytest --confcutdir=tests/frontend tests/frontend/test_frontend_mcp_service_desk_contract.py -q`

预期：新增 Token 管理字段、筛选和 DELETE 调用断言失败。

- [ ] **Step 3: 实现最小前端逻辑**

在 Client 卡片展示五类数量并将“Token 管理”提升为独立按钮；Token 弹窗增加状态筛选和批量选择，已过期/已撤销可直接物理删除，有效 Token 删除必须确认；删除后刷新 Token 列表和 Client 列表。

- [ ] **Step 4: 运行前端验证**

运行：`pytest --confcutdir=tests/frontend tests/frontend/test_frontend_mcp_service_desk_contract.py -q` 和 `cd frontend && ./node_modules/.bin/vue-tsc --noEmit`

预期：前端契约和类型检查通过。

### Task 3: 集成回归检查

**Files:**
- Modify: `tests/CHECKLIST.md`（仅在本次功能清单确实需要补充时更新，保留现有未提交内容）

- [ ] **Step 1: 运行 MCP 服务台相关 API/前端测试**

运行：`pytest --confcutdir=tests/frontend tests/api/test_mcp_service_desk_contract.py tests/frontend/test_frontend_mcp_service_desk_contract.py -q`

- [ ] **Step 2: 检查差异和工作区范围**

运行：`git diff --check`、`git status --short`，确认不运行服务、不执行真实数据库删除，并保留用户已有的其他未提交修改。
