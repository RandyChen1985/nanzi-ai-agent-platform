# MCP 服务台安全审计与全局 Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐 OAuth 安全事件审计、审计时间筛选、调用趋势、Redis 限流和管理员全局 Client 管理。

**Architecture:** OAuth 安全事件使用独立审计表，避免混入 MCP 调用统计；入站 MCP 请求在已验证身份后按 Client/用户执行 Redis 固定窗口限流。服务台管理员查询全局 Client 并关联用户显示名，普通用户保持自身范围；审计查询统一支持时间范围和趋势聚合。

**Tech Stack:** FastAPI、SQLAlchemy async、MySQL/PostgreSQL SQL、Redis、Vue 3、TypeScript、pytest。

---

### Task 1: 添加失败契约测试与迁移契约

**Files:**
- Modify: `tests/api/test_mcp_service_desk_contract.py`
- Modify: `tests/frontend/test_frontend_mcp_service_desk_contract.py`
- Modify: `tests/services/mcp/test_platform_mcp.py`
- Modify: `tests/test_platform_mcp_migrations.py`

- [x] **Step 1: 写失败测试**

断言 OAuth 安全事件模型/写入入口、`/audit/security`、时间筛选参数、趋势字段、全局 Client 管理与用户显示名、Redis 限流配置和 429 行为。

- [x] **Step 2: 运行测试确认失败**

```bash
PYTHONPATH=. .venv/bin/pytest tests/api/test_mcp_service_desk_contract.py tests/services/mcp/test_platform_mcp.py tests/test_platform_mcp_migrations.py -q
pytest --confcutdir=tests/frontend tests/frontend/test_frontend_mcp_service_desk_contract.py -q
```

### Task 2: 数据模型、迁移和安全事件写入

**Files:**
- Modify: `app/models/platform_mcp.py`
- Modify: `app/api/mcp_platform.py`
- Modify: `app/api/portal/endpoints/mcp_service.py`
- Create: `db-prod/V141-mcp-oauth-security-audit.sql`
- Create: `db-prod-pg/V42-mcp-oauth-security-audit.sql`

- [x] **Step 1: 增加 OAuth 安全事件模型和脱敏序列化**

保存事件类型、Client、用户、结果、错误码、请求 ID、IP 摘要和时间，不保存凭证或原始 Header。

- [x] **Step 2: 在 OAuth 授权、Token、Refresh、Revoke 及服务台 Client/Scope/Secret 操作写事件**

写审计失败只记录 warning，不影响既有 OAuth 或管理操作。

- [x] **Step 3: 增加 MySQL/PostgreSQL 迁移**

只新增安全事件表及必要索引，不直接连接数据库执行。

### Task 3: 审计时间筛选、趋势和限流

**Files:**
- Modify: `app/api/portal/endpoints/mcp_service.py`
- Modify: `app/services/mcp/platform_mcp.py`
- Modify: `app/core/config.py` 或现有限流配置位置

- [x] **Step 1: 给调用审计和安全审计增加时间范围参数**

支持开始/结束时间和快捷周期，管理员看全局，普通用户按当前用户过滤。

- [x] **Step 2: 增加趋势聚合接口**

返回按小时/天的调用总数、成功、失败、拒绝，按选择周期自动选择粒度。

- [x] **Step 3: 在 MCP 入口增加 Client/用户 Redis 固定窗口限流**

默认 Client 120 次/分钟、用户 60 次/分钟；任一超限返回 429，并写入安全事件/调用审计可追溯信息。

### Task 4: 管理员全局 Client 和用户标签

**Files:**
- Modify: `app/api/portal/endpoints/mcp_service.py`
- Modify: `frontend/src/views/McpServiceDesk.vue`

- [x] **Step 1: 管理员查询、修改、停用、删除和重置全局 Client**

普通用户继续使用 `created_by` 隔离；Client 返回 `owner_user_id`、`owner_user_name`、`owner_real_name`。

- [x] **Step 2: 总览数量和 Client 卡片显示所属用户**

管理员显示全局计数；卡片展示真实姓名，缺失时回退用户名/用户 ID。

### Task 5: 回归验证

**Files:**
- Modify: `tests/CHECKLIST.md`

- [x] **Step 1: 运行定向测试、类型检查和差异检查**

```bash
PYTHONPATH=. .venv/bin/pytest tests/api/test_mcp_service_desk_contract.py tests/services/mcp/test_platform_mcp.py tests/test_platform_mcp_migrations.py -q
pytest --confcutdir=tests/frontend tests/frontend/test_frontend_mcp_service_desk_contract.py -q
(cd frontend && ./node_modules/.bin/vue-tsc --noEmit)
git diff --check
```

真实 Redis、数据库迁移、OAuth 浏览器和外部 MCP 客户端验收由用户在控制台执行。
