# MCP 审计统计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 MCP 服务台总览页展示按权限过滤的审计调用统计。

**Architecture:** 新增独立 `/audit/summary` 聚合接口，不把审计数据混入仅有总览权限的 `/overview`。后端按管理员/普通用户复用审计可见范围，前端仅在拥有审计读取权限时请求并展示 24 小时、7 天、30 天统计。

**Tech Stack:** FastAPI、SQLAlchemy async、Vue 3、TypeScript、pytest 契约测试。

---

### Task 1: 增加聚合接口和前端展示契约

**Files:**
- Modify: `tests/api/test_mcp_service_desk_contract.py`
- Modify: `tests/frontend/test_frontend_mcp_service_desk_contract.py`

- [x] **Step 1: 写失败测试**

断言后端存在 `/audit/summary`，使用审计读取权限、时间范围校验、调用数/成功率/失败拒绝数/平均耗时/P95 字段；前端断言存在时间范围选择、汇总接口和四项统计文案。

- [x] **Step 2: 运行测试确认失败**

```bash
PYTHONPATH=. .venv/bin/pytest tests/api/test_mcp_service_desk_contract.py -q
pytest --confcutdir=tests/frontend tests/frontend/test_frontend_mcp_service_desk_contract.py -q
```

预期新增断言失败，既有无关审计筛选断言可能仍失败。

### Task 2: 实现后端审计汇总

**Files:**
- Modify: `app/api/portal/endpoints/mcp_service.py`

- [x] **Step 1: 提取审计可见范围和时间范围逻辑**

复用管理员查看全部、普通用户限定 `McpInboundAuditLog.user_id == current_user_id` 的规则；允许 `range` 为 `24h`、`7d`、`30d`，使用当前 UTC 时间计算起点。

- [x] **Step 2: 增加 `/audit/summary` 聚合查询**

使用 SQLAlchemy `count`、成功条件 `result_status == 'completed'`、失败/拒绝条件 `in_(['failed', 'denied'])`、`avg(latency_ms)` 和按耗时降序分页的 P95 近似值；无数据时返回 0 和 null/0 的稳定字段。

- [x] **Step 3: 运行后端测试确认通过**

```bash
PYTHONPATH=. .venv/bin/pytest tests/api/test_mcp_service_desk_contract.py -q
```

### Task 3: 接入总览统计卡片

**Files:**
- Modify: `frontend/src/views/McpServiceDesk.vue`

- [x] **Step 1: 增加统计状态和加载函数**

增加 `auditSummary`、`auditSummaryRange`，仅在 `canReadAudit` 时请求 `/api/portal/mcp-service/audit/summary`；切换范围后重新请求。

- [x] **Step 2: 在总览空白区域增加统计卡片**

展示调用次数、成功率、失败/拒绝次数、P95 耗时，并标明当前统计周期；无权限时不渲染统计区域。

- [x] **Step 3: 运行定向测试和类型检查**

```bash
pytest --confcutdir=tests/frontend tests/frontend/test_frontend_mcp_service_desk_contract.py -q
(cd frontend && ./node_modules/.bin/vue-tsc --noEmit)
git diff --check
```

真实数据库数据和浏览器视觉效果由用户启动服务后验收。
