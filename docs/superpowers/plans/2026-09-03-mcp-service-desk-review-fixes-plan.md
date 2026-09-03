# MCP 服务台审查问题修复实施计划

> **For agentic workers:** 本计划在当前工作区内由主代理按步骤执行，步骤使用复选框跟踪。

**Goal:** 修复用户指定的 Playground 默认参数、共享 Client 操作可见性、Redirect URI 变更失效策略、编辑弹窗关闭和 Token 回显问题。

**Architecture:** 保持现有 MCP 服务台页面与 API 结构不变，只在前端增加基于当前用户和 Client 所有权的操作判定，在后端复用现有 Token/Grant 撤销链；Playground 继续由服务端代理，但不再把完整 Token 返回给页面。

**Tech Stack:** Vue 3、TypeScript、Tailwind CSS、FastAPI、Pydantic 2、SQLAlchemy 2、pytest。

---

### Task 1: 为指定问题补充失败回归测试

**Files:**
- Modify: `tests/frontend/test_mcp_service_desk_methods_mobile_contract.py`
- Modify: `tests/frontend/test_frontend_mcp_service_desk_contract.py`
- Modify: `tests/api/test_mcp_service_desk_contract.py`

- [ ] 增加 Playground 默认参数、共享 Client 所有权操作、弹窗强制关闭和 Token 不回显的前端契约断言。
- [ ] 增加 Redirect URI 变化参与安全失效链的 API 契约断言。
- [ ] 运行新增测试并确认它们在当前实现上按预期失败。

### Task 2: 修复前端 Playground 与 Client 操作边界

**Files:**
- Modify: `frontend/src/views/McpServiceDesk.vue:80-325`
- Modify: `frontend/src/views/McpServiceDesk.vue:366-435`
- Modify: `frontend/src/views/McpServiceDesk.vue:1355-1359`
- Modify: `frontend/src/views/McpServiceDesk.vue:1825-1828`

- [ ] 将默认参数改为 `limit`、`top_k` 和 `message`，删除错误的 `page/page_size` 与 `prompt`。
- [ ] 从 `useUser()` 读取当前用户 ID，统一封装管理员/所有者判定，并让所有者专属操作和“一键撤销全部”使用该判定。
- [ ] 让保存成功路径通过强制关闭参数关闭编辑弹窗。
- [ ] 删除前端对 `token_used` 的读取和回填。

### Task 3: 修复 Redirect URI 变更的凭证失效策略并移除 Token 回显

**Files:**
- Modify: `app/api/portal/endpoints/mcp_service.py:1135-1179`
- Modify: `app/api/portal/endpoints/mcp_service.py:1470-1480`

- [ ] 根据旧值与新值计算 `redirect_uris_changed`，保留 Redirect URI 校验行为并把真实变化加入 `security_changed`。
- [ ] 保持只修改 Scope 时不触发无关 Redirect URI 校验。
- [ ] 保留脱敏 Token 摘要，但不在 Playground 响应中返回完整 `token_used`。

### Task 4: 回归验证

**Files:**
- No source files added.

- [ ] 运行 MCP API、服务、迁移和前端定向测试。
- [ ] 运行 `vue-tsc --noEmit`、Python 编译检查和 `git diff --check`。
- [ ] 汇报测试结果，并明确未执行服务启动、数据库迁移和浏览器视觉验收。
